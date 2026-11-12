# tests/test_executor.py
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from policy import Policy, decide, extract_host
from approval import ApprovalStore, fingerprint
from ledger import Ledger
from executor import execute_purchase

URL = "http://officesupplies.test"

def setup(policy=None):
    return Ledger(), ApprovalStore(), policy or Policy()

def buy(ledger, store, policy, amount=140, product="chair", url=URL,
        approval_id=None, lookup=None):
    return execute_purchase(
        ledger=ledger, store=store, policy=policy, url=url, amount=amount,
        currency="USD", product_id=product,
        price_lookup=lookup or (lambda pid: 140 if pid == "chair" else 120),
        pay=lambda amt, desc: "ORDER1", approval_id=approval_id)

def approved_id(store, host="officesupplies.test", amount=140, product="chair", ttl=900):
    aid = store.request(fingerprint(host, amount, "USD", product), ttl=ttl)
    store.approve(aid)
    return aid

def test_happy_path_needs_approval_then_paid():
    l, s, p = setup()
    r = buy(l, s, p)
    assert r.status == "PENDING_APPROVAL"
    s.approve(r.approval_id)
    assert buy(l, s, p, approval_id=r.approval_id).status == "PAID"
    assert float(l.spent) == 140

def test_unapproved_id_rejected():
    l, s, p = setup()
    aid = s.request(fingerprint("officesupplies.test", 140, "USD", "chair"))  # never approved
    assert buy(l, s, p, approval_id=aid).status == "REJECT_NOT_APPROVED_OR_REPLAY"
    assert l.spent == 0

def test_amount_changed_after_approval():
    l, s, p = setup()
    aid = approved_id(s, amount=140)
    r = buy(l, s, p, amount=145, lookup=lambda pid: 145, approval_id=aid)  # lying catalog
    assert r.status == "REJECT_MISMATCH" and l.spent == 0

def test_agent_claims_wrong_price():
    l, s, p = setup()
    assert buy(l, s, p, amount=40).status == "REJECT_PRICE_MISMATCH"

def test_merchant_changed_after_approval():
    l, s, p = setup()
    aid = approved_id(s)
    r = buy(l, s, p, url="http://localhost", approval_id=aid)   # allowed host, but not the approved one
    assert r.status == "REJECT_MISMATCH" and l.spent == 0

def test_product_changed_after_approval():
    l, s, p = setup()
    aid = approved_id(s, amount=140, product="chair")
    r = buy(l, s, p, amount=140, product="desk", lookup=lambda pid: 140, approval_id=aid)
    assert r.status == "REJECT_MISMATCH"

def test_replay_of_consumed_approval():
    l, s, p = setup()
    aid = approved_id(s)
    assert buy(l, s, p, approval_id=aid).status == "PAID"
    assert buy(l, s, p, approval_id=aid).status == "REJECT_NOT_APPROVED_OR_REPLAY"
    assert float(l.spent) == 140

def test_expired_approval():
    l, s, p = setup()
    aid = approved_id(s, ttl=-1)
    assert buy(l, s, p, approval_id=aid).status == "REJECT_EXPIRED"
    assert l.spent == 0

def test_parallel_replay_pays_once():
    l, s, p = setup()
    aid = approved_id(s)
    with ThreadPoolExecutor(20) as ex:
        results = list(ex.map(lambda _: buy(l, s, p, approval_id=aid).status, range(20)))
    assert results.count("PAID") == 1
    assert float(l.spent) == 140

def test_concurrent_spend_respects_budget():
    # budget fits exactly 3 purchases of $40
    l, s, p = setup(Policy(budget_total=120, per_purchase_cap=150, approval_threshold=75))
    with ThreadPoolExecutor(20) as ex:
        results = list(ex.map(
            lambda _: buy(l, s, p, amount=40, product="paper", lookup=lambda pid: 40).status,
            range(20)))
    assert results.count("PAID") == 3
    assert float(l.spent) == 120

@pytest.mark.parametrize("bad", [
    "http://officesupplies.test.evil.com/x",
    "HTTP://OFFICESUPPLIES.TEST.EVIL.COM",
    "http://officesupplies.test@evil.com",
    "http://evil.com/officesupplies.test",
    "evil.com/officesupplies.test",
    "officesupplies.test.evil.com",
    "https://pay.officesupplies-billing.test",
    "",
])
def test_lookalike_hosts_blocked(bad):
    d = decide(Policy(), 0, bad, 10, [])
    assert d.verdict == "block" and d.rule == "R1_ALLOWLIST"

@pytest.mark.parametrize("good", [
    "http://officesupplies.test", "officesupplies.test", "HTTPS://OfficeSupplies.TEST/x",
])
def test_legit_host_variants_allowed(good):
    assert decide(Policy(), 0, good, 10, []).verdict == "allow"

def test_over_cap_and_split_blocked():
    assert decide(Policy(), 0, URL, 200, []).rule == "R3_CAP"
    recent = [("officesupplies.test", 40)] * 3
    assert decide(Policy(), 120, URL, 40, recent).rule == "R5_SPLIT"
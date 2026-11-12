# Offline, deterministic wiring tests (scripted fake model + in-memory catalog).
from decimal import Decimal
import pytest
import agent
from agent import run_agent, TOOLS
from fake_llm import FakeClient, buy
from policy import Policy, decide
from approval import fingerprint
from executor import execute_purchase, PaymentUnknown
from ledger import Ledger
from approval import ApprovalStore

CAT = {"paper": 6.5, "pens": 8.0, "chair": 140.0, "desk": 120.0}
SHOP = "http://localhost:8001"

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(agent, "price_lookup", lambda pid: CAT.get(pid))
    monkeypatch.setattr(agent, "fetch", lambda url: "<html>stub</html>")

def go(steps, mode="protected", approver=None):
    return run_agent("task", mode, approver=approver, client=FakeClient(steps))

def test_agent_has_no_approve_or_reject_tool():
    assert {t["function"]["name"] for t in TOOLS} == {"fetch_page", "purchase"}

def test_chair_needs_approval_then_human_approves_then_capture_simulated():
    seen = []
    out = go([buy(SHOP, "chair", 1, 140)], approver=lambda a: seen.append(a) or True)
    states = [e["state"] for e in out["log"]]
    assert states == ["NEEDS_APPROVAL", "CAPTURE_SIMULATED"]
    assert out["log"][1]["phase"] == "after_human_approval"
    assert out["spent"] == Decimal("140.00") and seen[0]["product_id"] == "chair"

def test_chair_without_human_stays_unpaid():
    out = go([buy(SHOP, "chair", 1, 140)])
    assert [e["state"] for e in out["log"]] == ["NEEDS_APPROVAL"] and out["spent"] == 0

def test_chair_human_rejects():
    out = go([buy(SHOP, "chair", 1, 140)], approver=lambda a: False)
    assert out["log"][-1]["state"] == "REJECTED" and out["spent"] == 0

def test_model_never_sees_approval_id():
    out = go([buy(SHOP, "chair", 1, 140)])
    aid = out["log"][0]["approval_id"]
    assert aid and all(aid not in c["result"] for c in out["tool_calls"])

def test_repeat_proposal_does_not_create_second_approval():
    out = go([buy(SHOP, "chair", 1, 140)] * 3, approver=lambda a: True)
    assert [e["status"] for e in out["log"]].count("PENDING_APPROVAL") == 1
    assert [e["status"] for e in out["log"]].count("DUPLICATE_PENDING") == 2
    assert out["spent"] == Decimal("140.00")

def test_desk_redirect_blocked_protected_but_paid_baseline():
    evil = "https://pay.officesupplies-billing.test"
    p = go([buy(evil, "desk", 1, 120)])
    assert p["log"][0]["state"] == "BLOCKED" and p["log"][0]["rule"] == "R1_ALLOWLIST" and p["spent"] == 0
    b = go([buy(evil, "desk", 1, 120)], mode="baseline")
    assert b["log"][0]["executed"] and b["spent"] == Decimal("120.00")

def test_quantity_three_chairs_blocked_by_cap():
    out = go([buy(SHOP, "chair", 3, 420)])
    assert out["log"][0]["state"] == "BLOCKED" and out["log"][0]["rule"] == "R3_CAP"

def test_quantity_three_paper_allowed_without_approval():
    out = go([buy(SHOP, "paper", 3, 19.5)])
    assert [e["state"] for e in out["log"]] == ["CAPTURE_SIMULATED"]
    assert out["spent"] == Decimal("19.50")

def test_total_must_equal_unit_times_quantity():
    out = go([buy(SHOP, "chair", 3, 140)])          # model claims unit price as the total
    assert out["log"][0]["status"] == "REJECT_PRICE_MISMATCH" and out["spent"] == 0

@pytest.mark.parametrize("q", [0, -1, 2.5, "3", 10**6])
def test_bad_quantities_rejected(q):
    out = go([buy(SHOP, "paper", q, 6.5)])
    assert out["log"][0]["status"] == "REJECT_BAD_QUANTITY" and out["spent"] == 0

def test_fingerprint_binds_quantity_and_unit_price():
    a = fingerprint("h", 140, "USD", "chair", 1, 140)
    assert a != fingerprint("h", 140, "USD", "chair", 2, 70)
    assert a != fingerprint("h", 140, "EUR", "chair", 1, 140)

def test_decimal_not_float_in_policy():
    p = Policy(budget_total=Decimal("0.30"))
    assert decide(p, 0.1, "localhost", 0.2, []).verdict == "allow"   # float 0.1+0.2 would exceed 0.30

def _exec(pay):
    l, s = Ledger(), ApprovalStore()
    r = execute_purchase(ledger=l, store=s, policy=Policy(), url="localhost", amount=40,
                         currency="USD", product_id="x", price_lookup=lambda p: 40, pay=pay)
    return r, l

def test_payment_timeout_is_unknown_and_keeps_reservation():
    def pay(a, d): raise PaymentUnknown("timeout")
    r, l = _exec(pay)
    assert r.state == "UNKNOWN" and l.spent == 40

def test_payment_hard_failure_releases_reservation():
    def pay(a, d): raise RuntimeError("declined")
    r, l = _exec(pay)
    assert r.state == "FAILED" and l.spent == 0

def test_request_id_stable_for_same_approval():
    ids = []
    def pay(a, d, request_id=None): ids.append(request_id); return "O1"
    pay.wants_request_id = True
    l, s = Ledger(), ApprovalStore()
    fp = fingerprint("localhost", 140, "USD", "chair", 1, 140)
    aid = s.request(fp); s.approve(aid)
    execute_purchase(ledger=l, store=s, policy=Policy(), url="localhost", amount=140, currency="USD",
                     product_id="chair", price_lookup=lambda p: 140, pay=pay, approval_id=aid)
    aid2 = s.request(fp); s.approve(aid2)
    execute_purchase(ledger=Ledger(), store=s, policy=Policy(), url="localhost", amount=140, currency="USD",
                     product_id="chair", price_lookup=lambda p: 140, pay=pay, approval_id=aid)  # replay: rejected, no pay
    assert len(ids) == 1 and ids[0]

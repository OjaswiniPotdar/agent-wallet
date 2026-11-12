# R7: an identical purchase repeated in one session needs approval (opt-in via Policy flag).
from decimal import Decimal
import pytest
from policy import Policy, decide
from ledger import Ledger
from approval import ApprovalStore
from executor import execute_purchase

SHOP = "localhost"
P = Policy(duplicate_needs_approval=True)

def test_flag_off_by_default_so_old_behaviour_is_unchanged():
    r = [("localhost", Decimal("12"), "stapler")]
    assert decide(Policy(), 0, SHOP, 12, r, item="stapler").verdict == "allow"

def test_duplicate_triggers_r7_only_for_identical_item_and_amount():
    r = [("localhost", Decimal("12"), "stapler")]
    d = decide(P, 12, SHOP, 12, r, item="stapler")
    assert (d.verdict, d.rule) == ("needs_approval", "R7_DUPLICATE")
    assert decide(P, 12, SHOP, 12, r, item="pens").verdict == "allow"
    assert decide(P, 12, SHOP, 13, r, item="stapler").verdict == "allow"
    assert decide(P, 0, SHOP, 12, [], item="stapler").verdict == "allow"

def test_hard_blocks_still_win_over_r7():
    r = [("localhost", Decimal("12"), "stapler")]
    assert decide(P, 0, "evil.test", 12, r, item="stapler").rule == "R1_ALLOWLIST"

def test_two_element_recent_entries_still_supported():
    assert decide(P, 0, SHOP, 12, [("localhost", 12)], item="stapler").verdict == "allow"

def buy(l, s, aid=None):
    return execute_purchase(ledger=l, store=s, policy=P, url=SHOP, amount=12, currency="USD",
                            product_id="stapler", price_lookup=lambda p: 12, pay=lambda a, d: "O1",
                            approval_id=aid)

def test_executor_second_identical_purchase_needs_human_then_pays_once():
    l, s = Ledger(), ApprovalStore()
    assert buy(l, s).status == "PAID"
    r = buy(l, s)
    assert (r.status, r.rule) == ("PENDING_APPROVAL", "R7_DUPLICATE")
    assert l.spent == 12
    s.approve(r.approval_id)
    assert buy(l, s, r.approval_id).status == "PAID" and l.spent == 24
    assert buy(l, s, r.approval_id).status == "REJECT_NOT_APPROVED_OR_REPLAY"

def test_release_removes_the_item_entry():
    l = Ledger()
    l.reserve(P, SHOP, Decimal("12"), False, item="stapler")
    l.release(SHOP, Decimal("12"), "stapler")
    assert l.recent == [] and l.spent == 0

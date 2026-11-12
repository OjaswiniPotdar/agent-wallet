# Offline tests for the SQLite audit log, the approval page, and the web approver wiring.
import threading, time
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from web_login import signed_in
import agent, audit, approvals_web
from agent import run_agent
from fake_llm import FakeClient, buy

CAT = {"paper": 6.5, "chair": 140.0, "desk": 120.0}
SHOP = "http://localhost:8001"

@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setattr(agent, "price_lookup", lambda pid: CAT.get(pid))
    monkeypatch.setattr(agent, "fetch", lambda url: "<html>stub</html>")

def test_decision_is_single_use_and_atomic():
    audit.add_pending("a1", "localhost", "chair", 1, "140.00")
    assert audit.decide("a1", True) is True
    assert audit.decide("a1", False) is False            # cannot flip a decision
    assert audit.get_status("a1") == "approved"
    assert audit.decide("nope", True) is False

def test_parallel_decisions_only_one_wins():
    audit.add_pending("a2", "localhost", "chair", 1, "140.00")
    wins = []
    ts = [threading.Thread(target=lambda v=v: wins.append(audit.decide("a2", v))) for v in (True, False) * 10]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert wins.count(True) == 1

def test_expired_request_cannot_be_approved():
    audit.add_pending("a3", "localhost", "chair", 1, "140.00", ttl=-1)
    assert audit.get_status("a3") == "expired" and audit.decide("a3", True) is False

def test_audit_log_written_and_has_no_secrets(monkeypatch):
    fake = "sk" + "-" + "t" * 24          # built at runtime so the secret scanner does not flag this file
    monkeypatch.setenv("OPENAI_API_KEY", fake)
    run_agent("t", "protected", client=FakeClient([buy("https://pay.evil.test", "desk", 1, 120)]),
              audit_sink=audit.log_event)
    rows = audit.events()
    assert rows and rows[0]["state"] == "BLOCKED" and rows[0]["rule"] == "R1_ALLOWLIST"
    assert fake not in repr(rows)

def test_page_lists_pending_and_approve_button_records_decision():
    audit.add_pending("a4", "localhost", "chair", 1, "140.00")
    c = signed_in(approvals_web.app)
    page = c.get("/").text
    assert "Waiting for you (1)" in page and "SIMULATED" in page and "Sign-in required" in page
    r = c.post("/decide", data={"aid": "a4", "action": "approve", "csrf": c.csrf}, follow_redirects=False)
    assert r.status_code == 303 and audit.get_status("a4") == "approved"
    assert "Waiting for you (0)" in c.get("/").text

def test_page_escapes_untrusted_values():
    audit.add_pending("a5", "<script>x</script>", "<img src=x>", 1, "1.00")
    page = signed_in(approvals_web.app).get("/").text
    assert "<script>x</script>" not in page and "&lt;script&gt;" in page

def _run_with_web_human(decision, steps):
    """Run the agent with web_approver while a 'human' clicks on the page in another thread."""
    def human():
        for _ in range(100):
            p = audit.pending()
            if p:
                (c := signed_in(approvals_web.app)).post("/decide", data={"aid": p[0]["approval_id"], "action": decision, "csrf": c.csrf})
                return
            time.sleep(0.05)
    t = threading.Thread(target=human); t.start()
    out = run_agent("t", "protected", client=FakeClient(steps), audit_sink=audit.log_event,
                    approver=lambda a: agent.web_approver(a, timeout=10, poll=0.05))
    t.join()
    return out

def test_web_approve_then_capture_simulated():
    out = _run_with_web_human("approve", [buy(SHOP, "chair", 1, 140)])
    assert [e["state"] for e in out["log"]] == ["NEEDS_APPROVAL", "CAPTURE_SIMULATED"]
    assert out["spent"] == Decimal("140.00")
    assert [r["state"] for r in reversed(audit.events())] == ["NEEDS_APPROVAL", "CAPTURE_SIMULATED"]

def test_web_reject_pays_nothing():
    out = _run_with_web_human("reject", [buy(SHOP, "chair", 1, 140)])
    assert out["log"][-1]["state"] == "REJECTED" and out["spent"] == 0

def test_web_timeout_is_expired_and_unpaid():
    out = run_agent("t", "protected", client=FakeClient([buy(SHOP, "chair", 1, 140)]),
                    approver=lambda a: agent.web_approver(a, timeout=0.2, poll=0.05))
    assert out["log"][-1]["state"] == "EXPIRED" and out["spent"] == 0

def test_page_approval_cannot_authorize_a_different_purchase():
    """Human approves 1 chair on the page; replaying that approval for 2 chairs in the SAME store
    must fail the HMAC binding (REJECT_MISMATCH) and pay nothing."""
    from executor import execute_purchase
    from approval import ApprovalStore, fingerprint
    from ledger import Ledger
    from policy import Policy
    store, paid = ApprovalStore(), []
    aid = store.request(fingerprint("localhost", 140, "USD", "chair", 1, 140))
    audit.add_pending(aid, "localhost", "chair", 1, "140.00")
    c = signed_in(approvals_web.app)
    c.post("/decide", data={"aid": aid, "action": "approve", "csrf": c.csrf})
    assert audit.get_status(aid) == "approved"
    store.approve(aid)                                   # what agent.py does after the page says yes
    r = execute_purchase(ledger=Ledger(), store=store, policy=Policy(), url=SHOP, amount=280,
                         currency="USD", product_id="chair", quantity=2, price_lookup=lambda p: 140,
                         pay=lambda a, d: paid.append(a) or "X", approval_id=aid)
    assert r.status == "REJECT_MISMATCH" and not paid
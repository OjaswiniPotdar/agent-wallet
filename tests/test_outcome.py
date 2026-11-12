# The demo output must state what code did, not what the model said, and never show a bare "PAID".
import pytest
import agent
from agent import run_agent, final_outcomes
from fake_llm import FakeClient, buy

CAT = {"paper": 6.5, "chair": 140.0}
SHOP = "http://localhost:8001"

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(agent, "price_lookup", lambda pid: CAT.get(pid))
    monkeypatch.setattr(agent, "fetch", lambda url: "<html>stub</html>")

def go(steps, mode="protected", approver=None):
    return run_agent("task", mode, approver=approver, client=FakeClient(steps))

def test_log_never_shows_bare_paid():
    out = go([buy(SHOP, "paper", 1, 6.5), buy(SHOP, "chair", 1, 140)], approver=lambda a: True)
    statuses = [e["status"] for e in out["log"]]
    assert "PAID" not in statuses
    assert statuses.count("SIMULATED_CAPTURE") == 2

def test_outcome_reports_result_after_human_approval():
    out = go([buy(SHOP, "chair", 1, 140)], approver=lambda a: True)
    assert out["summary"] == "done"                         # model text is untouched
    assert final_outcomes(out["log"]) == ["1 x chair $140.00 @ localhost: CAPTURE_SIMULATED (after human decision)"]

def test_outcome_rejected_and_waiting():
    rej = go([buy(SHOP, "chair", 1, 140)], approver=lambda a: False)
    assert final_outcomes(rej["log"]) == ["1 x chair $140.00 @ localhost: REJECTED (after human decision)"]
    wait = go([buy(SHOP, "chair", 1, 140)])
    assert final_outcomes(wait["log"]) == ["1 x chair $140.00 @ localhost: NEEDS_APPROVAL (still waiting for a human)"]

def test_outcome_blocked_and_duplicate_pending_not_repeated():
    out = go([buy("https://pay.evil.test", "paper", 1, 6.5), buy(SHOP, "chair", 1, 140), buy(SHOP, "chair", 1, 140)])
    assert final_outcomes(out["log"]) == ["1 x paper $6.50 @ pay.evil.test: BLOCKED",
                                          "1 x chair $140.00 @ localhost: NEEDS_APPROVAL (still waiting for a human)"]

def test_baseline_outcome():
    out = go([buy("https://pay.evil.test", "chair", 1, 140)], mode="baseline")
    assert final_outcomes(out["log"]) == ["1 x chair $140.00 @ pay.evil.test: CAPTURE_SIMULATED"]
    assert out["log"][0]["status"] == "BASELINE"

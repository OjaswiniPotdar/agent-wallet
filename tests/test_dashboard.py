# The AG Studio dashboard's data API and AI proxy (approvals_web + dashboard_data). Offline: the
# OpenAI call is replaced with httpx.MockTransport. The dashboard must agree with results/summary.json,
# never expose a key, and never offer a way to approve or pay.
import json
from pathlib import Path
import httpx, pytest
from fastapi.testclient import TestClient
from web_login import signed_in
import approvals_web, audit, dashboard_data, lifecycle

ROOT = Path(__file__).resolve().parent.parent

@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setattr(lifecycle, "DB_PATH", str(tmp_path / "audit.db"))
    for k in ("AG_STUDIO_OPENAI_API_KEY", "OPENAI_API_KEY", "DASHBOARD_AI_MODELS"):
        monkeypatch.delenv(k, raising=False)

def test_eval_tables_match_results_summary():
    summary = json.loads((ROOT / "results" / "summary.json").read_text())
    runs = dashboard_data.eval_tables()["evalRuns"]
    assert len(runs) == 460
    camel = {f: dashboard_data._camel(f) for f in dashboard_data.FLAGS}
    checked = 0
    for key, modes in summary.items():
        st, kind = key.split("/")
        for mode, cols in modes.items():
            rs = [r for r in runs if r["set"] == st and r["kind"] == kind and r["mode"] == mode]
            for flag, kn in cols.items():
                k, n = map(int, kn.split("/"))
                assert (sum(r[camel[flag]] for r in rs), len(rs)) == (k, n), (key, mode, flag)
                checked += 1
    assert checked > 50

def test_outcome_labels_are_exclusive_and_complete():
    runs = dashboard_data.eval_tables()["evalRuns"]
    att = [r for r in runs if r["kind"] == "attack"]
    assert sum(r["outcome"] == "Unauthorized payment" for r in att if r["mode"] == "baseline") == 104
    assert sum(r["outcome"] == "Unauthorized payment" for r in att if r["mode"] == "protected") == 0
    assert {r["outcome"] for r in att} <= {"Unauthorized payment", "Blocked by code", "Held for human approval", "Model did not attempt", "Other"}

def test_decisions_name_the_check_that_rejected():
    rules = {d["rule"] for d in dashboard_data.eval_tables()["decisions"]}
    assert "R1_ALLOWLIST" in rules and "none (baseline)" in rules and "-" not in rules

def test_dashboard_data_api_without_key():
    audit.log_event({"phase": "agent_call", "product_id": "chair", "quantity": 1, "host": "localhost",
                     "amount": "140.00", "state": "NEEDS_APPROVAL", "rule": "R6_APPROVAL", "status": "PENDING_APPROVAL",
                     "approval_id": "abcdef1234567890"}, "protected", "run1")
    d = signed_in(approvals_web.app).get("/api/dashboard-data").json()
    assert d["ai"] is None and d["asOf"].endswith("+00:00")
    assert len(d["evalRuns"]) == 460 and len(d["scenarios"]) == 23
    assert d["auditEvents"][0]["item"] == "chair" and d["auditEvents"][0]["approvalId"] == "abcdef12"
    assert d["paypalOrders"] == []

def test_dashboard_data_never_contains_the_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-SECRET-123")
    monkeypatch.setenv("DASHBOARD_AI_MODELS", "gpt-5-nano, gpt-5-mini")
    r = signed_in(approvals_web.app).get("/api/dashboard-data")
    assert r.json()["ai"] == {"models": ["gpt-5-nano", "gpt-5-mini"]}
    assert "SECRET" not in r.text

def test_ai_proxy_refuses_without_key_and_unknown_model(monkeypatch):
    c = signed_in(approvals_web.app)
    assert c.post("/api/ai/responses", json={"model": "gpt-5-nano"}).status_code == 503
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert c.post("/api/ai/responses", json={"model": "gpt-4o"}).status_code == 400
    assert c.post("/api/ai/responses", content=b"[1,2]", headers={"content-type": "application/json"}).status_code == 400

def test_ai_proxy_adds_key_caps_tokens_and_streams(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen = {}
    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(b"data: hello\n\n"))
    real = httpx.AsyncClient
    monkeypatch.setattr(approvals_web.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}))
    r = signed_in(approvals_web.app).post("/api/ai/responses", json={"input": "hi", "max_output_tokens": 999999})
    assert r.status_code == 200 and r.text == "data: hello\n\n"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "gpt-5-nano" and seen["body"]["max_output_tokens"] == approvals_web.AI_MAX_OUTPUT_TOKENS

def test_dashboard_routes_are_read_only():
    paths = {getattr(r, "path", "") for r in approvals_web.app.routes}
    dash = {p for p in paths if p.startswith("/api/dashboard") or p.startswith("/api/ai") or p.startswith("/dashboard")}
    assert dash and not any("approve" in p or "decide" in p or "pay" in p for p in dash)

def test_dashboard_page_is_served():
    r = signed_in(approvals_web.app).get("/dashboard/")
    assert r.status_code == 200
    if (ROOT / "dashboard" / "out").is_dir():
        assert "Agent Wallet" in r.text

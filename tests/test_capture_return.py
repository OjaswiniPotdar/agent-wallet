# Real-capture wiring, offline: orders carry return/cancel URLs with OUR intent id, and the buyer's
# return to /paypal/return runs the guarded capture. PayPal responses are FAKE fixtures shaped like
# the documented API; nothing here contacts PayPal.
import json
from decimal import Decimal
import httpx, pytest
from fastapi.testclient import TestClient
import agent, approvals_web, audit, lifecycle, paypal
from approval import ApprovalStore
from executor import execute_purchase
from ledger import Ledger
from policy import Policy

INTENT = {"intent_id": "intent-1", "host": "localhost", "product_id": "chair", "quantity": 1,
          "unit": "140.00", "total": "140.00", "currency": "USD"}

@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(lifecycle, "DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.setattr(audit, "DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "cid"); monkeypatch.setenv("PAYPAL_SECRET", "s")
    monkeypatch.delenv("PAYPAL_MERCHANT_ID", raising=False)

def order(status, value="140.00", capture=None):
    u = {"custom_id": "intent-1", "amount": {"currency_code": "USD", "value": value}}
    if capture:
        u["payments"] = {"captures": [{"id": "CAP1", "status": capture, "amount": {"currency_code": "USD", "value": value}}]}
    return {"id": "ORDER1", "status": status, "purchase_units": [u]}

@pytest.fixture
def paypal_fake(monkeypatch):
    st = {"order": order("APPROVED"), "captures": []}
    monkeypatch.setattr(approvals_web, "_get_order", lambda oid: st["order"])
    def cap(oid, rid):
        st["captures"].append(rid)
        st["order"] = order("COMPLETED", capture="COMPLETED")
        return st["order"]
    monkeypatch.setattr(approvals_web, "_capture_order", cap)
    lifecycle.IntentStore().record(INTENT, "ORDER1", "https://sandbox.example/pay")
    return st

def test_create_order_sends_experience_context():
    seen = {}
    def h(req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "T"})
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"id": "O1", "status": "PAYER_ACTION_REQUIRED",
                                         "links": [{"rel": "payer-action", "href": "https://sandbox.example/pay"}]})
    o = paypal.create_order("140.00", "chair", "rid", custom_id="intent-1", http=httpx.Client(transport=httpx.MockTransport(h)),
                            return_url="http://localhost:8002/paypal/return?intent=intent-1",
                            cancel_url="http://localhost:8002/paypal/cancel?intent=intent-1")
    ctx = seen["body"]["payment_source"]["paypal"]["experience_context"]
    assert ctx["return_url"].endswith("intent=intent-1") and ctx["cancel_url"].endswith("intent=intent-1")
    assert ctx["user_action"] == "PAY_NOW" and paypal.approve_link(o) == "https://sandbox.example/pay"
    assert lifecycle.state_from_order(o)[0] == "ORDER_CREATED"      # PAYER_ACTION_REQUIRED is not paid

def test_agent_real_orders_pass_urls_with_intent_id(monkeypatch):
    monkeypatch.setenv("PAYPAL_RETURN_BASE", "http://localhost:8002/")
    seen = {}
    def fake_create(amount, desc, rid, custom_id=None, currency="USD", **urls):
        seen.update(urls, custom_id=custom_id)
        return {"id": "OX", "status": "PAYER_ACTION_REQUIRED", "links": [{"rel": "payer-action", "href": "https://x"}]}
    r = execute_purchase(ledger=Ledger(), store=ApprovalStore(), policy=Policy(), url="http://localhost:8001", amount=Decimal("6.50"),
                         currency="USD", product_id="paper", price_lookup=lambda p: 6.5,
                         pay=agent.make_pay(True, create_order=fake_create, intents=lifecycle.IntentStore()), intent_id="abc")
    assert r.state == "ORDER_CREATED"
    assert seen["return_url"] == f"http://localhost:8002/paypal/return?intent={seen['custom_id']}"
    assert seen["cancel_url"] == f"http://localhost:8002/paypal/cancel?intent={seen['custom_id']}"

def test_return_after_buyer_approval_captures_once(paypal_fake):
    c = TestClient(approvals_web.app)
    r = c.get("/paypal/return", params={"intent": "intent-1", "token": "ORDER1", "PayerID": "X"})
    assert r.status_code == 200 and "CAPTURE_CONFIRMED" in r.text
    assert lifecycle.IntentStore().get("intent-1")["state"] == "CAPTURE_CONFIRMED"
    c.get("/paypal/return", params={"intent": "intent-1"})            # buyer refreshes the page
    assert paypal_fake["captures"] == [lifecycle.capture_request_id("intent-1")]
    assert any(e["phase"] == "paypal_return" for e in audit.events())

def test_return_before_approval_does_not_capture(paypal_fake):
    paypal_fake["order"] = order("PAYER_ACTION_REQUIRED")
    r = TestClient(approvals_web.app).get("/paypal/return", params={"intent": "intent-1"})
    assert "buyer must approve" in r.text and paypal_fake["captures"] == []
    assert lifecycle.IntentStore().get("intent-1")["state"] == "ORDER_CREATED"

def test_return_with_tampered_amount_does_not_capture(paypal_fake):
    paypal_fake["order"] = order("APPROVED", value="1400.00")
    r = TestClient(approvals_web.app).get("/paypal/return", params={"intent": "intent-1"})
    assert "RECONCILIATION_MISMATCH" in r.text and paypal_fake["captures"] == []

def test_return_unknown_intent(paypal_fake):
    r = TestClient(approvals_web.app).get("/paypal/return", params={"intent": "nope"})
    assert "NO_SUCH_INTENT" in r.text and paypal_fake["captures"] == []

def test_return_escapes_untrusted_values(paypal_fake):
    r = TestClient(approvals_web.app).get("/paypal/return", params={"intent": "<script>x</script>"})
    assert "<script>x" not in r.text

def test_cancel_marks_cancelled_only_if_not_approved(paypal_fake):
    c = TestClient(approvals_web.app)
    c.get("/paypal/cancel", params={"intent": "intent-1"})                 # order is APPROVED: not cancelled
    assert lifecycle.IntentStore().get("intent-1")["state"] == "ORDER_CREATED"
    paypal_fake["order"] = order("PAYER_ACTION_REQUIRED")
    r = c.get("/paypal/cancel", params={"intent": "intent-1"})
    assert "CANCELLED" in r.text and lifecycle.IntentStore().get("intent-1")["state"] == "CANCELLED"
    assert paypal_fake["captures"] == []

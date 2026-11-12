# Offline tests for the PayPal lifecycle: client calls (httpx.MockTransport), reconciliation,
# guarded capture, webhook verification/dedupe/reconciliation, and the --real-orders wiring.
# No network, no keys. PayPal responses here are FAKE fixtures shaped like the documented API.
import json, threading
from decimal import Decimal
import httpx, pytest
from fastapi.testclient import TestClient
import agent, approvals_web, audit, lifecycle, paypal, payments
from approval import ApprovalStore
from executor import execute_purchase
from ledger import Ledger
from policy import Policy

INTENT = {"intent_id": "intent-1", "host": "localhost", "product_id": "chair", "quantity": 1,
          "unit": "140.00", "total": "140.00", "currency": "USD"}
HDRS = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://example.test/cert",
        "PAYPAL-TRANSMISSION-ID": "t1", "PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-TRANSMISSION-TIME": "2026-11-12T00:00:00Z"}

@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(lifecycle, "DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setattr(audit, "DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "cid")
    monkeypatch.setenv("PAYPAL_SECRET", "csecret")
    monkeypatch.delenv("PAYPAL_MERCHANT_ID", raising=False)

@pytest.fixture
def store():
    s = lifecycle.IntentStore()
    s.record(INTENT, "ORDER1", "https://www.sandbox.paypal.com/checkoutnow?token=ORDER1")
    return s

def order(status="APPROVED", value="140.00", currency="USD", custom_id="intent-1", oid="ORDER1", capture=None, payee=None):
    u = {"custom_id": custom_id, "amount": {"currency_code": currency, "value": value}}
    if payee: u["payee"] = payee
    if capture: u["payments"] = {"captures": [capture]}
    return {"id": oid, "status": status, "purchase_units": [u]}

def cap(status="COMPLETED", value="140.00"):
    return {"id": "CAP1", "status": status, "amount": {"currency_code": "USD", "value": value}}

def mock(handler):
    """httpx client whose OAuth call always succeeds; `handler(request)` answers the rest."""
    def h(req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "TOKEN"})
        return handler(req)
    return httpx.Client(transport=httpx.MockTransport(h))

# ---------------- paypal.py client ----------------

def test_create_order_sends_custom_id_and_stable_request_id():
    seen = {}
    def h(req):
        seen["hdr"], seen["body"] = req.headers, json.loads(req.content)
        return httpx.Response(201, json={"id": "O1", "status": "CREATED",
                                         "links": [{"rel": "approve", "href": "https://sandbox.example/approve"}]})
    o = paypal.create_order("140.00", "chair", "rid-1", custom_id="intent-1", http=mock(h))
    assert seen["hdr"]["PayPal-Request-Id"] == "rid-1"
    assert seen["body"]["purchase_units"][0]["custom_id"] == "intent-1"
    assert seen["body"]["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "140.00"}
    assert paypal.approve_link(o) == "https://sandbox.example/approve"

def test_capture_sends_given_request_id():
    seen = {}
    def h(req):
        seen["rid"] = req.headers["PayPal-Request-Id"]; seen["path"] = req.url.path
        return httpx.Response(201, json=order("COMPLETED", capture=cap()))
    paypal.capture_order("ORDER1", "cap-rid", http=mock(h))
    assert seen == {"rid": "cap-rid", "path": "/v2/checkout/orders/ORDER1/capture"}

def test_verify_signature_builds_documented_body_and_needs_success():
    seen = {}
    def h(req):
        seen["path"], seen["body"] = req.url.path, json.loads(req.content)
        return httpx.Response(200, json={"verification_status": "SUCCESS"})
    ev = {"id": "WH-1", "event_type": "X"}
    assert paypal.verify_webhook_signature(HDRS, ev, "WHID", http=mock(h)) is True
    assert seen["path"] == "/v1/notifications/verify-webhook-signature"
    assert seen["body"] == {"auth_algo": "SHA256withRSA", "cert_url": "https://example.test/cert", "transmission_id": "t1",
                            "transmission_sig": "sig", "transmission_time": "2026-11-12T00:00:00Z",
                            "webhook_id": "WHID", "webhook_event": ev}
    fail = mock(lambda r: httpx.Response(200, json={"verification_status": "FAILURE"}))
    assert paypal.verify_webhook_signature(HDRS, ev, "WHID", http=fail) is False

def test_verify_signature_missing_header_never_calls_paypal():
    calls = []
    c = mock(lambda r: calls.append(r) or httpx.Response(200, json={"verification_status": "SUCCESS"}))
    h = dict(HDRS); h.pop("PAYPAL-TRANSMISSION-SIG")
    assert paypal.verify_webhook_signature(h, {"id": "x"}, "WHID", http=c) is False and calls == []

def test_verify_signature_server_error_raises_not_verified():
    c = mock(lambda r: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        paypal.verify_webhook_signature(HDRS, {"id": "x"}, "WHID", http=c)

def test_live_capture_requires_stable_request_id():
    with pytest.raises(ValueError):
        payments.execute_payment("ORDER1", live=True)
    assert payments.execute_payment("ORDER1")["simulated"] is True

# ---------------- matching and states ----------------

def test_capture_request_id_stable_and_distinct():
    a = lifecycle.capture_request_id("intent-1")
    assert a == lifecycle.capture_request_id("intent-1") and a != lifecycle.capture_request_id("intent-2")

def test_completed_order_is_not_proof_of_payment():
    assert lifecycle.state_from_order(order("COMPLETED"))[0] == "UNKNOWN"
    assert lifecycle.state_from_order(order("COMPLETED", capture=cap("DECLINED")))[0] == "FAILED"
    assert lifecycle.state_from_order(order("COMPLETED", capture=cap("PENDING")))[0] == "UNKNOWN"
    assert lifecycle.state_from_order(order("COMPLETED", capture=cap()))[:2] == ("CAPTURE_CONFIRMED", "CAP1")
    assert lifecycle.state_from_order(order("APPROVED"))[0] == "ORDER_CREATED"
    assert lifecycle.state_from_order(order("VOIDED"))[0] == "FAILED"

@pytest.mark.parametrize("bad", [dict(value="141.00"), dict(currency="EUR"), dict(custom_id="other"), dict(oid="ORDER2"),
                                 dict(status="COMPLETED", capture=cap(value="1400.00"))])
def test_order_must_match_intent(store, bad):
    assert lifecycle.order_problems(store.get("intent-1"), order(**bad))

def test_payee_checked_when_merchant_configured(store, monkeypatch):
    it = store.get("intent-1")
    assert lifecycle.order_problems(it, order(payee={"merchant_id": "EVIL"})) == []
    monkeypatch.setenv("PAYPAL_MERCHANT_ID", "GOOD")
    assert lifecycle.order_problems(it, order(payee={"merchant_id": "EVIL"}))
    assert lifecycle.order_problems(it, order(payee={"merchant_id": "GOOD"})) == []

def test_illegal_transitions_refused(store):
    assert store.transition("intent-1", "CAPTURE_CONFIRMED", "CAP1")
    assert not store.transition("intent-1", "ORDER_CREATED")
    assert not store.transition("intent-1", "UNKNOWN")
    assert store.get("intent-1")["state"] == "CAPTURE_CONFIRMED"

# ---------------- reconcile + guarded capture ----------------

def test_no_capture_before_buyer_approval(store):
    caps = []
    r = lifecycle.capture(store, "intent-1", lambda oid: order("CREATED"), lambda *a: caps.append(a))
    assert not r["ok"] and "buyer must approve" in r["result"] and caps == []

def test_no_capture_when_order_differs_from_intent(store):
    caps = []
    r = lifecycle.capture(store, "intent-1", lambda oid: order(value="999.00"), lambda *a: caps.append(a))
    assert r["result"] == "RECONCILIATION_MISMATCH" and caps == []
    assert store.get("intent-1")["state"] == "ORDER_CREATED" and store.get("intent-1")["alert"]

def test_capture_confirmed_with_stable_request_id(store):
    caps = []
    def capture_order(oid, rid):
        caps.append((oid, rid)); return order("COMPLETED", capture=cap())
    r = lifecycle.capture(store, "intent-1", lambda oid: order("APPROVED"), capture_order)
    assert r["ok"] and r["state"] == "CAPTURE_CONFIRMED" and r["capture_id"] == "CAP1"
    assert caps == [("ORDER1", lifecycle.capture_request_id("intent-1"))]
    again = lifecycle.capture(store, "intent-1", lambda oid: order("COMPLETED", capture=cap()), capture_order)
    assert not again["ok"] and len(caps) == 1          # terminal: never captured twice

def test_capture_timeout_is_unknown_and_never_blindly_retried(store):
    caps = []
    def timeout(oid, rid):
        caps.append(rid); raise httpx.ReadTimeout("t")
    r = lifecycle.capture(store, "intent-1", lambda oid: order("APPROVED"), timeout)
    assert r["result"] == "UNKNOWN" and store.get("intent-1")["state"] == "UNKNOWN"
    # PayPal actually captured: the next attempt reads the order first and does NOT capture again
    r2 = lifecycle.capture(store, "intent-1", lambda oid: order("COMPLETED", capture=cap()), timeout)
    assert r2["result"] == "ALREADY_CAPTURED" and r2["state"] == "CAPTURE_CONFIRMED" and len(caps) == 1

def test_capture_retry_after_unknown_reuses_request_id(store):
    rids = []
    def flaky(oid, rid):
        rids.append(rid)
        if len(rids) == 1: raise httpx.ConnectError("x")
        return order("COMPLETED", capture=cap())
    lifecycle.capture(store, "intent-1", lambda oid: order("APPROVED"), flaky)
    r = lifecycle.capture(store, "intent-1", lambda oid: order("APPROVED"), flaky)   # still APPROVED -> safe retry
    assert r["state"] == "CAPTURE_CONFIRMED" and len(rids) == 2 and rids[0] == rids[1]

def test_reconcile_unreachable_changes_nothing(store):
    def down(oid): raise httpx.ConnectError("x")
    assert lifecycle.reconcile(store, "intent-1", down)["result"] == "UNKNOWN"
    assert store.get("intent-1")["state"] == "ORDER_CREATED"

# ---------------- webhooks ----------------

def event(eid="WH-1", etype="PAYMENT.CAPTURE.COMPLETED", value="140.00", custom_id="intent-1", order_id="ORDER1", payee=None):
    res = {"id": "CAP1", "status": "COMPLETED", "custom_id": custom_id,
           "amount": {"currency_code": "USD", "value": value},
           "supplementary_data": {"related_ids": {"order_id": order_id}}}
    if payee: res["payee"] = payee
    return json.dumps({"id": eid, "event_type": etype, "resource": res}).encode()

OK = lambda h, e, w: True

def test_webhook_refused_without_webhook_id(store):
    assert lifecycle.handle_webhook(store, HDRS, event(), OK, None)[0] == 503

def test_webhook_bad_signature_401_and_no_state_change(store):
    code, res = lifecycle.handle_webhook(store, HDRS, event(), lambda h, e, w: False, "WHID")
    assert code == 401 and store.get("intent-1")["state"] == "ORDER_CREATED"
    assert lifecycle.handle_webhook(store, HDRS, event(), OK, "WHID")[1]["result"] == "CAPTURE_CONFIRMED"  # not burned

def test_webhook_verify_outage_is_retryable(store):
    def boom(h, e, w): raise httpx.ConnectError("x")
    assert lifecycle.handle_webhook(store, HDRS, event(), boom, "WHID")[0] == 503
    assert lifecycle.handle_webhook(store, HDRS, event(), OK, "WHID")[1]["result"] == "CAPTURE_CONFIRMED"

def test_webhook_bad_json_400(store):
    assert lifecycle.handle_webhook(store, HDRS, b"not json", OK, "WHID")[0] == 400

def test_webhook_confirms_and_dedupes(store):
    assert lifecycle.handle_webhook(store, HDRS, event(), OK, "WHID")[1]["result"] == "CAPTURE_CONFIRMED"
    code, res = lifecycle.handle_webhook(store, HDRS, event(), OK, "WHID")
    assert code == 200 and res["result"] == "DUPLICATE"
    assert store.get("intent-1")["capture_id"] == "CAP1"

def test_webhook_parallel_redelivery_applied_once(store):
    results = []
    ts = [threading.Thread(target=lambda: results.append(lifecycle.handle_webhook(store, HDRS, event(), OK, "WHID")[1]["result"]))
          for _ in range(10)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count("CAPTURE_CONFIRMED") == 1 and results.count("DUPLICATE") == 9

@pytest.mark.parametrize("kw", [dict(value="1400.00"), dict(order_id="ORDER2"), dict(payee={"merchant_id": "EVIL"})])
def test_webhook_mismatch_does_not_confirm(store, monkeypatch, kw):
    monkeypatch.setenv("PAYPAL_MERCHANT_ID", "GOOD")
    code, res = lifecycle.handle_webhook(store, HDRS, event(**kw), OK, "WHID")
    assert code == 200 and res["result"] == "RECONCILIATION_MISMATCH"
    assert store.get("intent-1")["state"] == "ORDER_CREATED" and store.get("intent-1")["alert"]

def test_webhook_unmatched_and_ignored_types(store):
    assert lifecycle.handle_webhook(store, HDRS, event(custom_id="nobody"), OK, "WHID")[1]["result"] == "UNMATCHED"
    assert lifecycle.handle_webhook(store, HDRS, event("WH-2", etype="CHECKOUT.ORDER.APPROVED"), OK, "WHID")[1]["result"] == "IGNORED_EVENT_TYPE"
    assert store.get("intent-1")["state"] == "ORDER_CREATED"

def test_webhook_out_of_order_events(store):
    lifecycle.handle_webhook(store, HDRS, event("WH-1"), OK, "WHID")
    code, res = lifecycle.handle_webhook(store, HDRS, event("WH-2", etype="PAYMENT.CAPTURE.PENDING"), OK, "WHID")
    assert res["result"] == "OUT_OF_ORDER_IGNORED" and store.get("intent-1")["state"] == "CAPTURE_CONFIRMED"

def test_webhook_denied_marks_failed(store):
    res = lifecycle.handle_webhook(store, HDRS, event(etype="PAYMENT.CAPTURE.DENIED"), OK, "WHID")[1]
    assert res["result"] == "FAILED" and store.get("intent-1")["state"] == "FAILED"

def test_webhook_route_has_no_bypass(store, monkeypatch):
    c = TestClient(approvals_web.app)
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    assert c.post("/webhooks/paypal", content=event(), headers=HDRS).status_code == 503
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WHID")
    monkeypatch.setattr(approvals_web, "_verify", lambda h, e, w: False)
    assert c.post("/webhooks/paypal", content=event(), headers=HDRS).status_code == 401
    monkeypatch.setattr(approvals_web, "_verify", lambda h, e, w: True)
    r = c.post("/webhooks/paypal", content=event(), headers=HDRS)
    assert r.status_code == 200 and r.json()["result"] == "CAPTURE_CONFIRMED"
    assert "CAPTURE_CONFIRMED" in c.get("/").text
    assert any(e["phase"] == "webhook" for e in audit.events())

# ---------------- --real-orders wiring ----------------

def test_real_orders_records_intent_and_is_not_paid(tmp_path):
    seen = {}
    def fake_create(amount, desc, rid, custom_id=None, currency="USD"):
        seen.update(amount=amount, rid=rid, custom_id=custom_id, currency=currency)
        return {"id": "ORDERX", "status": "CREATED", "links": [{"rel": "payer-action", "href": "https://sandbox.example/pay"}]}
    intents = lifecycle.IntentStore()
    pay = agent.make_pay(True, create_order=fake_create, intents=intents)
    r = execute_purchase(ledger=Ledger(), store=ApprovalStore(), policy=Policy(), url="http://localhost:8001",
                         amount=Decimal("19.50"), currency="USD", product_id="paper", quantity=3,
                         price_lookup=lambda p: 6.5, pay=pay, intent_id="abc")
    assert r.state == "ORDER_CREATED" and r.order_id == "ORDERX"         # not CAPTURE_SIMULATED, not "paid"
    assert seen["custom_id"] == seen["rid"] and seen["amount"] == "19.50"
    it = intents.get(seen["rid"])
    assert (it["order_id"], it["quantity"], it["unit"], it["total"], it["state"]) == ("ORDERX", 3, "6.50", "19.50", "ORDER_CREATED")
    assert it["approve_url"] == "https://sandbox.example/pay"

def test_real_orders_create_timeout_is_unknown_and_keeps_reservation():
    def slow(*a, **k): raise httpx.ReadTimeout("t")
    l = Ledger()
    r = execute_purchase(ledger=l, store=ApprovalStore(), policy=Policy(), url="http://localhost:8001", amount=6.5,
                         currency="USD", product_id="paper", price_lookup=lambda p: 6.5,
                         pay=agent.make_pay(True, create_order=slow, intents=lifecycle.IntentStore()), intent_id="i")
    assert r.state == "UNKNOWN" and l.spent == Decimal("6.50")

def test_simulated_default_unchanged():
    r = execute_purchase(ledger=Ledger(), store=ApprovalStore(), policy=Policy(), url="http://localhost:8001", amount=6.5,
                         currency="USD", product_id="paper", price_lookup=lambda p: 6.5, pay=agent.make_pay(False))
    assert r.state == "CAPTURE_SIMULATED" and r.order_id.startswith("SIMULATED-")

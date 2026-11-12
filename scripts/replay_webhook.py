#!/usr/bin/env python
"""SIMULATED webhook replay: exercises lifecycle.handle_webhook (the same function the
/webhooks/paypal route calls) with a SAMPLE PAYMENT.CAPTURE.COMPLETED payload.

Everything here is SIMULATED and labeled so:
  - the intent and order id are made up and live in a throwaway SQLite file
  - the signature verifier is a local STUB, not PayPal (the real route always asks PayPal via
    /v1/notifications/verify-webhook-signature and has no bypass)
It shows the decision logic only: verified -> confirmed; duplicate -> deduped; bad signature ->
401; tampered amount / wrong order / other intent -> mismatch, state unchanged; late event -> ignored."""
import copy, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lifecycle

TAG = "[SIMULATED]"
SAMPLE = {   # shape per PayPal docs (capture resource: amount, custom_id, supplementary_data.related_ids.order_id)
    "id": "WH-SIM-0001", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource_type": "capture",
    "resource": {"id": "CAP-SIM-1", "status": "COMPLETED",
                 "amount": {"currency_code": "USD", "value": "140.00"},
                 "custom_id": "intent-sim-chair",
                 "supplementary_data": {"related_ids": {"order_id": "ORDER-SIM-1"}}}}
HEADERS = {"paypal-auth-algo": "SHA256withRSA", "paypal-cert-url": "https://api.sandbox.paypal.com/cert",
           "paypal-transmission-id": "sim-1", "paypal-transmission-sig": "sim", "paypal-transmission-time": "2026-11-12T00:00:00Z"}

def stub_verify(valid):                                   # SIMULATED verifier, NOT PayPal
    return lambda headers, event, webhook_id: valid

def main():
    db = Path(tempfile.mkdtemp()) / "replay.db"
    store = lifecycle.IntentStore(str(db))
    store.record({"intent_id": "intent-sim-chair", "host": "localhost", "product_id": "chair", "quantity": 1,
                  "unit": "140.00", "total": "140.00", "currency": "USD"}, "ORDER-SIM-1")
    print(f"{TAG} intent intent-sim-chair: chair x1 $140.00 USD, order ORDER-SIM-1, state", store.get("intent-sim-chair")["state"])

    def send(title, event, valid=True):
        code, res = lifecycle.handle_webhook(store, HEADERS, json.dumps(event).encode(), stub_verify(valid), "WH-ID-SIM")
        print(f"{TAG} {title:44} -> HTTP {code} {res.get('result')}"
              + (f" | {'; '.join(res['problems'])}" if res.get("problems") else "")
              + f" | intent state {store.get('intent-sim-chair')['state']}")

    def ev(eid, **res):
        x = copy.deepcopy(SAMPLE); x["id"] = eid; x["resource"].update(res); return x

    send("bad signature", ev("WH-SIM-0000"), valid=False)
    tampered = ev("WH-SIM-0002"); tampered["resource"]["amount"]["value"] = "1400.00"
    send("tampered amount (1400.00)", tampered)
    wrong = ev("WH-SIM-0003"); wrong["resource"]["supplementary_data"]["related_ids"]["order_id"] = "ORDER-OTHER"
    send("right intent, different order id", wrong)
    send("unknown custom_id", ev("WH-SIM-0004", custom_id="someone-else"))
    send("valid PAYMENT.CAPTURE.COMPLETED", SAMPLE)
    send("same event id again (redelivery)", SAMPLE)
    late = ev("WH-SIM-0005"); late["event_type"] = "PAYMENT.CAPTURE.PENDING"
    send("late PAYMENT.CAPTURE.PENDING after completed", late)
    print(f"{TAG} final:", {k: store.get("intent-sim-chair")[k] for k in ("state", "capture_id", "alert")})
    print(f"{TAG} stub verifier + made-up ids; no PayPal call was made and no money moved.")

if __name__ == "__main__":
    main()

# lifecycle.py -- what happens to a PayPal order AFTER execute_purchase() approved and created it.
# Intents (the approved purchase: host, product, quantity, unit, total, currency) are stored in SQLite.
# PayPal is only believed after its data is checked against the intent: custom_id, order id,
# amount, currency and (if PAYPAL_MERCHANT_ID is set) the payee. All money comparisons are Decimal.
#
# States used here: ORDER_CREATED, CAPTURE_CONFIRMED, FAILED, UNKNOWN, EXPIRED.
# CAPTURE_CONFIRMED is set ONLY from a real PayPal response (GET order / capture) or a webhook whose
# signature PayPal itself verified. Nothing here invents a confirmation.
import json, os, sqlite3, threading, time, uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
import httpx

DB_PATH = os.environ.get("AUDIT_DB", str(Path(__file__).resolve().parent / "audit.db"))

# legal transitions; anything else (e.g. a late PENDING event after COMPLETED) is ignored
TRANSITIONS = {
    "ORDER_CREATED": {"CAPTURE_CONFIRMED", "FAILED", "UNKNOWN", "EXPIRED"},
    "UNKNOWN": {"ORDER_CREATED", "CAPTURE_CONFIRMED", "FAILED"},
    # a verified, matching capture is the truth even if we had given up on the order
    "FAILED": {"CAPTURE_CONFIRMED"},
    "EXPIRED": {"CAPTURE_CONFIRMED"},
    "CAPTURE_CONFIRMED": set(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
  intent_id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
  host TEXT, product_id TEXT, quantity INTEGER, unit TEXT, total TEXT, currency TEXT,
  order_id TEXT UNIQUE, state TEXT NOT NULL, capture_id TEXT, approve_url TEXT,
  alert TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS webhook_events (
  event_id TEXT PRIMARY KEY, received REAL NOT NULL, event_type TEXT, result TEXT);
"""

def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"))

def capture_request_id(intent_id: str) -> str:
    """Stable PayPal-Request-Id for the capture of one intent (differs from the create id)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "agent-wallet-capture:" + intent_id))


class IntentStore:
    def __init__(self, path=None):
        self.path = path or DB_PATH
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        return _Closing(c)

    def record(self, intent: dict, order_id: str, approve_url=None, state="ORDER_CREATED"):
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute("INSERT OR IGNORE INTO intents (intent_id, created, updated, host, product_id, quantity,"
                      " unit, total, currency, order_id, state, approve_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (intent["intent_id"], now, now, intent["host"], intent["product_id"], int(intent["quantity"]),
                       str(money(intent["unit"])), str(money(intent["total"])), intent["currency"].upper(),
                       order_id, state, approve_url))

    def get(self, intent_id):
        with self._lock, self._conn() as c:
            r = c.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
        return dict(r) if r else None

    def all(self, limit=50):
        with self._lock, self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM intents ORDER BY created DESC LIMIT ?", (limit,))]

    def transition(self, intent_id, new_state, capture_id=None, detail="") -> bool:
        """Atomic compare-and-set along TRANSITIONS. Returns False (and changes nothing) if illegal."""
        with self._lock, self._conn() as c:
            r = c.execute("SELECT state FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            if r is None:
                return False
            cur = r["state"]
            if new_state == cur:
                return True
            if new_state not in TRANSITIONS.get(cur, set()):
                return False
            c.execute("UPDATE intents SET state=?, capture_id=COALESCE(?, capture_id), detail=?, updated=?"
                      " WHERE intent_id=? AND state=?", (new_state, capture_id, detail[:300], time.time(), intent_id, cur))
            return True

    def flag(self, intent_id, alert: str):
        with self._lock, self._conn() as c:
            c.execute("UPDATE intents SET alert=?, updated=? WHERE intent_id=?", (alert[:300], time.time(), intent_id))

    def claim_event(self, event_id, event_type) -> bool:
        """True the first time an event id is seen (atomic). Duplicates -> False."""
        with self._lock, self._conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO webhook_events (event_id, received, event_type) VALUES (?,?,?)",
                            (event_id, time.time(), event_type))
            return cur.rowcount == 1

    def finish_event(self, event_id, result):
        with self._lock, self._conn() as c:
            c.execute("UPDATE webhook_events SET result=? WHERE event_id=?", (result[:300], event_id))

    def release_event(self, event_id):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM webhook_events WHERE event_id=?", (event_id,))


class _Closing:
    """sqlite3's own context manager commits but never closes; this closes (Windows file locks)."""
    def __init__(self, c): self.c = c
    def __enter__(self): return self.c
    def __exit__(self, *a): self.c.close()


# ---------- matching PayPal data against the approved intent ----------

def _amount_problems(intent, amount: dict, where: str):
    p = []
    try:
        if money(amount.get("value")) != money(intent["total"]):
            p.append(f"{where} amount {amount.get('value')} != approved {intent['total']}")
    except (InvalidOperation, TypeError, ValueError):
        p.append(f"{where} amount unreadable")
    if str(amount.get("currency_code", "")).upper() != intent["currency"]:
        p.append(f"{where} currency {amount.get('currency_code')} != approved {intent['currency']}")
    return p

def _payee_problem(payee: dict, where: str):
    want = os.environ.get("PAYPAL_MERCHANT_ID")
    if want and (payee or {}).get("merchant_id") != want:
        return [f"{where} payee {(payee or {}).get('merchant_id')} != configured merchant"]
    return []

def order_problems(intent: dict, order: dict) -> list:
    """Empty list = the order is exactly the approved intent."""
    p = []
    if order.get("id") != intent["order_id"]:
        p.append(f"order id {order.get('id')} != {intent['order_id']}")
    units = order.get("purchase_units") or []
    if len(units) != 1:
        return p + [f"expected 1 purchase unit, got {len(units)}"]
    u = units[0]
    if u.get("custom_id") != intent["intent_id"]:
        p.append("custom_id does not match intent")
    p += _amount_problems(intent, u.get("amount") or {}, "order")
    p += _payee_problem(u.get("payee"), "order")
    for cap in ((u.get("payments") or {}).get("captures") or []):
        p += _amount_problems(intent, cap.get("amount") or {}, "capture")
    return p

def state_from_order(order: dict):
    """(state, capture_id, detail). A COMPLETED order is not proof of payment: the capture's own
    status decides (PayPal docs: check each capture's status)."""
    st = order.get("status")
    if st in ("CREATED", "SAVED", "APPROVED", "PAYER_ACTION_REQUIRED"):
        return "ORDER_CREATED", None, f"paypal order {st}"
    if st == "VOIDED":
        return "FAILED", None, "paypal order VOIDED"
    if st == "COMPLETED":
        caps = [c for u in order.get("purchase_units") or [] for c in ((u.get("payments") or {}).get("captures") or [])]
        if not caps:
            return "UNKNOWN", None, "order COMPLETED but no capture in response"
        c = caps[0]
        cs = c.get("status")
        if cs == "COMPLETED":
            return "CAPTURE_CONFIRMED", c.get("id"), "capture COMPLETED (from PayPal API)"
        if cs in ("DECLINED", "FAILED"):
            return "FAILED", c.get("id"), f"capture {cs}"
        return "UNKNOWN", c.get("id"), f"capture {cs}"
    return "UNKNOWN", None, f"unexpected order status {st}"


# ---------- reconciliation and guarded capture (real PayPal calls, injectable) ----------

def reconcile(store: IntentStore, intent_id: str, get_order) -> dict:
    """GET the order and move the intent to what PayPal says, if (and only if) it matches the intent.
    This is the required step before any retry of a payment that ended UNKNOWN."""
    it = store.get(intent_id)
    if not it:
        return {"ok": False, "result": "NO_SUCH_INTENT"}
    try:
        order = get_order(it["order_id"])
    except (httpx.TimeoutException, httpx.TransportError) as e:
        return {"ok": False, "result": "UNKNOWN", "detail": f"{type(e).__name__} reading order; try again"}
    probs = order_problems(it, order)
    if probs:
        store.flag(intent_id, "; ".join(probs))
        return {"ok": False, "result": "RECONCILIATION_MISMATCH", "problems": probs, "state": it["state"]}
    state, cap_id, detail = state_from_order(order)
    moved = store.transition(intent_id, state, cap_id, detail)
    return {"ok": moved, "result": state if moved else "TRANSITION_REFUSED", "paypal_status": order.get("status"),
            "state": store.get(intent_id)["state"], "detail": detail}

def capture(store: IntentStore, intent_id: str, get_order, capture_order) -> dict:
    """Real sandbox capture, guarded: the order must match the intent and be APPROVED by the buyer.
    Uses a stable request id. A timeout yields UNKNOWN; the next call reconciles via GET first and
    re-captures only if PayPal still says APPROVED (and with the same request id)."""
    it = store.get(intent_id)
    if not it:
        return {"ok": False, "result": "NO_SUCH_INTENT"}
    if it["state"] not in ("ORDER_CREATED", "UNKNOWN"):
        return {"ok": False, "result": f"NOT_CAPTURABLE_IN_{it['state']}", "state": it["state"]}
    rec = reconcile(store, intent_id, get_order)                  # always look before capturing
    if rec["result"] in ("RECONCILIATION_MISMATCH", "UNKNOWN", "TRANSITION_REFUSED", "NO_SUCH_INTENT"):
        return rec
    if rec["result"] == "CAPTURE_CONFIRMED":                      # e.g. an earlier capture timed out but succeeded
        return dict(rec, ok=True, result="ALREADY_CAPTURED")
    if rec.get("paypal_status") != "APPROVED":
        return dict(rec, ok=False, result=f"NOT_CAPTURED: paypal order is {rec.get('paypal_status')}"
                    + (" (buyer must approve it first)" if rec.get("paypal_status") in ("CREATED", "PAYER_ACTION_REQUIRED") else ""))
    try:
        resp = capture_order(it["order_id"], capture_request_id(intent_id))
    except (httpx.TimeoutException, httpx.TransportError) as e:
        store.transition(intent_id, "UNKNOWN", detail=f"{type(e).__name__} during capture")
        return {"ok": False, "result": "UNKNOWN", "state": "UNKNOWN",
                "detail": "capture may have happened; run reconcile before anything else"}
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500:
            store.transition(intent_id, "UNKNOWN", detail=f"PayPal {e.response.status_code} during capture")
            return {"ok": False, "result": "UNKNOWN", "state": "UNKNOWN"}
        return {"ok": False, "result": f"CAPTURE_REJECTED_HTTP_{e.response.status_code}", "state": it["state"]}
    probs = order_problems(it, resp)
    if probs:
        store.flag(intent_id, "; ".join(probs))
        return {"ok": False, "result": "RECONCILIATION_MISMATCH", "problems": probs}
    state, cap_id, detail = state_from_order(resp)
    store.transition(intent_id, state, cap_id, detail)
    return {"ok": state == "CAPTURE_CONFIRMED", "result": state, "capture_id": cap_id,
            "state": store.get(intent_id)["state"], "detail": detail}


# ---------- webhooks ----------

CAPTURE_EVENTS = {"PAYMENT.CAPTURE.COMPLETED": "CAPTURE_CONFIRMED",
                  "PAYMENT.CAPTURE.DENIED": "FAILED",
                  "PAYMENT.CAPTURE.DECLINED": "FAILED",
                  "PAYMENT.CAPTURE.PENDING": "UNKNOWN"}

def handle_webhook(store: IntentStore, headers, raw_body: bytes, verify, webhook_id) -> tuple:
    """Returns (http_status, result dict). Order: parse -> verify signature WITH PAYPAL -> dedupe by
    event id -> reconcile against the approved intent -> legal state transition.
    `verify(headers, event, webhook_id) -> bool` is paypal.verify_webhook_signature in the server."""
    if not webhook_id:
        return 503, {"result": "NOT_CONFIGURED", "detail": "PAYPAL_WEBHOOK_ID not set; refusing unverified events"}
    try:
        event = json.loads(raw_body)
        event_id, etype = event["id"], event["event_type"]
    except (ValueError, KeyError, TypeError):
        return 400, {"result": "BAD_EVENT"}
    try:
        verified = verify(headers, event, webhook_id)
    except Exception as e:                       # cannot verify now -> PayPal will redeliver
        return 503, {"result": "VERIFY_UNAVAILABLE", "detail": type(e).__name__}
    if not verified:
        return 401, {"result": "SIGNATURE_INVALID", "event_id": str(event_id)[:60]}
    if not store.claim_event(event_id, etype):
        return 200, {"result": "DUPLICATE", "event_id": event_id}
    try:
        res = _apply_event(store, event, etype)
    except Exception as e:
        store.release_event(event_id)            # let PayPal's redelivery try again
        return 500, {"result": "ERROR", "detail": type(e).__name__}
    store.finish_event(event_id, res["result"])
    return 200, dict(res, event_id=event_id, event_type=etype)

def _apply_event(store, event, etype) -> dict:
    if etype not in CAPTURE_EVENTS:
        return {"result": "IGNORED_EVENT_TYPE"}
    cap = event.get("resource") or {}
    intent_id = cap.get("custom_id")
    it = store.get(intent_id) if intent_id else None
    if not it:
        return {"result": "UNMATCHED", "detail": "no approved intent for this custom_id"}
    probs = []
    order_id = ((cap.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
    if order_id != it["order_id"]:
        probs.append(f"event order {order_id} != intent order {it['order_id']}")
    probs += _amount_problems(it, cap.get("amount") or {}, "event")
    probs += _payee_problem(cap.get("payee"), "event")
    if probs:
        store.flag(intent_id, "webhook: " + "; ".join(probs))
        return {"result": "RECONCILIATION_MISMATCH", "intent_id": intent_id, "problems": probs, "state": it["state"]}
    new = CAPTURE_EVENTS[etype]
    moved = store.transition(intent_id, new, cap.get("id"), f"webhook {etype}")
    return {"result": new if moved else "OUT_OF_ORDER_IGNORED", "intent_id": intent_id,
            "state": store.get(intent_id)["state"]}

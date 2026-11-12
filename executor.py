# executor.py -- the only place money moves in protected mode.
import uuid
from dataclasses import dataclass
from decimal import InvalidOperation
from typing import Optional
from approval import money, fingerprint
from policy import extract_host

MAX_QTY = 1000

class PaymentUnknown(Exception):
    """Timeout / network / 5xx during payment: money may have moved. Never blind-retry."""

@dataclass
class PayOutcome:
    order_id: str
    state: str = "ORDER_CREATED"       # ORDER_CREATED | CAPTURE_SIMULATED | CAPTURE_CONFIRMED
    detail: str = ""

@dataclass
class Result:
    # `status` is the legacy code (kept so existing tests pass). Use `state` for anything
    # user-facing: "PAID" below means only "order created/capture simulated", see state.
    status: str                    # PAID | PENDING_APPROVAL | BLOCKED | REJECT_* | PAYMENT_FAILED | PAYMENT_UNKNOWN
    detail: str = ""
    approval_id: Optional[str] = None
    order_id: Optional[str] = None
    state: str = "UNKNOWN"         # explicit payment state
    rule: str = ""

def _state_for(status: str) -> str:
    return {"PENDING_APPROVAL": "NEEDS_APPROVAL", "BLOCKED": "BLOCKED",
            "REJECT_EXPIRED": "EXPIRED", "PAYMENT_FAILED": "FAILED",
            "PAYMENT_UNKNOWN": "UNKNOWN"}.get(status, "REJECTED")

def _r(status, detail="", **kw) -> Result:
    return Result(status, detail, state=_state_for(status), **kw)

def execute_purchase(*, ledger, store, policy, url, amount, currency, product_id,
                     price_lookup, pay, approval_id=None, quantity=1,
                     intent_id=None) -> Result:
    host = extract_host(url)
    if (not isinstance(quantity, int) or isinstance(quantity, bool)
            or quantity < 1 or quantity > MAX_QTY):
        return _r("REJECT_BAD_QUANTITY", f"quantity={quantity!r}")
    try:
        amt = money(amount)
    except (InvalidOperation, ValueError, TypeError):
        return _r("REJECT_BAD_AMOUNT", f"amount={amount!r}")

    # 1. total must match the merchant's own catalog (unit price x quantity), not the agent's claim
    try:
        catalog = price_lookup(product_id)
    except Exception as e:
        return _r("REJECT_CATALOG_UNAVAILABLE", type(e).__name__)
    if catalog is None:
        return _r("REJECT_PRICE_MISMATCH", f"catalog=None claimed={amt}")
    unit = money(catalog)
    total = unit * quantity
    if total != amt:
        return _r("REJECT_PRICE_MISMATCH", f"catalog={unit} x{quantity}={total} claimed={amt}")

    fp = fingerprint(host, total, currency, product_id, quantity, unit)

    # 2. if an approval is presented, validate it without spending it yet
    if approval_id is not None:
        code = store.check(approval_id, fp)
        if code != "OK":
            return _r(code)

    # 3. policy check + budget reservation, atomically
    d, ok = ledger.reserve(policy, url, total, approved=approval_id is not None, item=product_id)
    if not ok:
        if d.verdict == "needs_approval":
            return _r("PENDING_APPROVAL", d.reason, approval_id=store.request(fp), rule=d.rule)
        return _r("BLOCKED", f"{d.rule}: {d.reason}", rule=d.rule)

    # 3b. re-validate the price immediately before execution
    try:
        again = price_lookup(product_id)
    except Exception:
        again = None
    if again is None or money(again) != unit:
        ledger.release(url, total, product_id)
        return _r("REJECT_PRICE_MISMATCH", "catalog changed before execution")

    # 4. spend the approval (single use). If a parallel replay beat us, undo the reservation.
    if approval_id is not None:
        code = store.consume(approval_id, fp)
        if code != "OK":
            ledger.release(url, total, product_id)
            return _r(code)

    # 5. pay. Request id is stable for this intent (approval id when present) -> PayPal idempotency.
    rid = str(uuid.uuid5(uuid.NAMESPACE_URL,
                         "agent-wallet:" + (intent_id or approval_id or uuid.uuid4().hex)))
    kw = {"request_id": rid} if getattr(pay, "wants_request_id", False) else {}
    try:
        out = pay(str(total), product_id, **kw)
    except PaymentUnknown as e:
        # keep the reservation: money may have moved. Reconcile via GET order before any retry.
        return _r("PAYMENT_UNKNOWN", str(e), rule="OK")
    except Exception as e:
        ledger.release(url, total, product_id)
        return _r("PAYMENT_FAILED", str(e))
    if isinstance(out, str):
        out = PayOutcome(out)
    return Result("PAID", out.detail, order_id=out.order_id, state=out.state, rule="OK")

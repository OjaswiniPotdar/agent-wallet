# approval.py
import os, hmac, hashlib, json, time, uuid, threading
from decimal import Decimal

SECRET = os.environ.get("APPROVAL_SECRET", "dev-only-change-me").encode()

def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"))

def fingerprint(host, amount, currency, product_id, quantity=1, unit_price=None) -> str:
    """Binds host, product, quantity, unit price, total (= amount), currency.
    Expiry is bound by the store entry. Any change -> different fingerprint."""
    total = money(amount)
    unit = money(unit_price) if unit_price is not None else money(total / int(quantity))
    canon = json.dumps({"host": host.lower(), "total": str(total), "unit": str(unit),
                        "qty": int(quantity), "currency": currency.upper(),
                        "product": product_id},
                       sort_keys=True, separators=(",", ":"))
    return hmac.new(SECRET, canon.encode(), hashlib.sha256).hexdigest()

class ApprovalStore:
    """In-memory, thread-safe, single process. Only a human path may call approve()/reject();
    the agent has no tool for either."""
    def __init__(self):
        self._lock = threading.Lock()
        self._a = {}

    def request(self, fp, ttl=900) -> str:
        aid = uuid.uuid4().hex
        with self._lock:
            self._a[aid] = {"fp": fp, "expires": time.time() + ttl, "status": "pending"}
        return aid

    def approve(self, aid) -> bool:
        with self._lock:
            a = self._a.get(aid)
            if a and a["status"] == "pending":
                a["status"] = "approved"
                return True
        return False

    def reject(self, aid) -> bool:
        with self._lock:
            a = self._a.get(aid)
            if a and a["status"] == "pending":
                a["status"] = "rejected"
                return True
        return False

    def _check(self, aid, fp) -> str:
        a = self._a.get(aid)
        if not a:                              return "REJECT_UNKNOWN"
        if a["status"] != "approved":          return "REJECT_NOT_APPROVED_OR_REPLAY"
        if time.time() > a["expires"]:         return "REJECT_EXPIRED"
        if not hmac.compare_digest(a["fp"], fp): return "REJECT_MISMATCH"
        return "OK"

    def check(self, aid, fp) -> str:           # read-only
        with self._lock:
            return self._check(aid, fp)

    def consume(self, aid, fp) -> str:         # atomic, single use
        with self._lock:
            code = self._check(aid, fp)
            if code == "OK":
                self._a[aid]["status"] = "consumed"
            return code

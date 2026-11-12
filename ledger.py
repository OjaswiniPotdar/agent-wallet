# ledger.py
import threading
from decimal import Decimal
from policy import decide, extract_host

class Ledger:
    def __init__(self):
        self._lock = threading.Lock()
        self.spent = Decimal("0")
        self.recent = []          # (host, Decimal amount, item) for R5 / R7

    def reserve(self, policy, url, amount: Decimal, approved: bool, item=None):
        with self._lock:
            d = decide(policy, self.spent, url, amount, list(self.recent), item=item)
            ok = d.verdict == "allow" or (d.verdict == "needs_approval" and approved)
            if ok:
                self.spent += amount
                self.recent.append((extract_host(url), amount, item))
            return d, ok

    def release(self, url, amount: Decimal, item=None):
        with self._lock:
            self.spent -= amount
            try:
                self.recent.remove((extract_host(url), amount, item))
            except ValueError:
                pass
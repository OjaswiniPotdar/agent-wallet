# ledger.py -- budget accounting. reserve() runs the policy check and the reservation as ONE atomic
# step, so two purchases can never both pass a budget check that only one of them fits in.
#
# Ledger        in memory, per process (the agent run and the evals use this one).
# SqliteLedger  persistent and atomic across threads AND processes: the check and the insert run in
#               one write-locked SQLite transaction (BEGIN IMMEDIATE). Reservations survive a restart,
#               so a crash after a possibly-successful payment cannot free that money for a second
#               purchase. Opt in with LEDGER_DB=<path> (agent.py).
import os, sqlite3, threading, time
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


class SqliteLedger:
    """Same interface as Ledger. `account` names one budget (e.g. one user or one month).
    `recent_seconds` bounds the window the split / duplicate rules (R5, R7) look at, since a
    persistent ledger outlives a single agent session."""

    SCHEMA = """CREATE TABLE IF NOT EXISTS ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, host TEXT NOT NULL,
        amount TEXT NOT NULL, item TEXT, state TEXT NOT NULL, ts REAL NOT NULL)"""

    def __init__(self, path, account="default", recent_seconds=3600):
        self.path, self.account, self.recent_seconds = path, account, recent_seconds
        with self._conn() as c:
            c.execute(self.SCHEMA)
            c.execute("CREATE INDEX IF NOT EXISTS ledger_acct ON ledger(account, state)")

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        return _Closing(c)

    @staticmethod
    def _spent(c, account):
        return sum((Decimal(r[0]) for r in c.execute(
            "SELECT amount FROM ledger WHERE account=? AND state='reserved'", (account,))), Decimal("0"))

    def _recent(self, c):
        rows = c.execute("SELECT host, amount, item FROM ledger WHERE account=? AND state='reserved' AND ts>? ORDER BY id",
                         (self.account, time.time() - self.recent_seconds))
        return [(h, Decimal(a), i) for h, a, i in rows]

    @property
    def spent(self):
        with self._conn() as c:
            return self._spent(c, self.account)

    @property
    def recent(self):
        with self._conn() as c:
            return self._recent(c)

    def reserve(self, policy, url, amount: Decimal, approved: bool, item=None):
        amount = Decimal(str(amount))
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")          # write lock: no other process can check-and-reserve now
            try:
                d = decide(policy, self._spent(c, self.account), url, amount, self._recent(c), item=item)
                ok = d.verdict == "allow" or (d.verdict == "needs_approval" and approved)
                if ok:
                    c.execute("INSERT INTO ledger (account, host, amount, item, state, ts) VALUES (?,?,?,?,'reserved',?)",
                              (self.account, extract_host(url), str(amount), item, time.time()))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return d, ok

    def release(self, url, amount: Decimal, item=None):
        """Undo ONE matching reservation (a payment that definitely did not happen)."""
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT id FROM ledger WHERE account=? AND host=? AND amount=? AND item IS ? AND state='reserved'"
                            " ORDER BY id DESC LIMIT 1", (self.account, extract_host(url), str(Decimal(str(amount))), item)).fetchone()
            if row:
                c.execute("UPDATE ledger SET state='released' WHERE id=?", (row[0],))
            c.execute("COMMIT")

    def reservations(self):
        with self._conn() as c:
            return [dict(zip(("id", "host", "amount", "item", "state", "ts"), r)) for r in c.execute(
                "SELECT id, host, amount, item, state, ts FROM ledger WHERE account=? ORDER BY id", (self.account,))]


class _Closing:
    def __init__(self, c): self.c = c
    def __enter__(self): return self.c
    def __exit__(self, *a): self.c.close()

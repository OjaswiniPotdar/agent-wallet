# audit.py -- SQLite audit log + human approval decisions (local demo).
# Stores NO secrets: only item, host, amount, state, rule, approval id, timestamps.
# The web page records only a human yes/no keyed by approval id. The binding of that
# approval to host/product/quantity/unit price/total/currency/expiry is still enforced
# by the HMAC fingerprint in approval.ApprovalStore when executor.execute_purchase runs.
import os, sqlite3, threading, time
from pathlib import Path

DB_PATH = os.environ.get("AUDIT_DB", str(Path(__file__).resolve().parent / "audit.db"))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, run_id TEXT, mode TEXT, phase TEXT,
  item TEXT, quantity TEXT, host TEXT, amount TEXT, state TEXT, rule TEXT, status TEXT,
  approval_id TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS decisions (
  approval_id TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL, run_id TEXT,
  host TEXT, item TEXT, quantity TEXT, amount TEXT, currency TEXT,
  status TEXT NOT NULL DEFAULT 'pending', decided REAL);
"""

def _conn(path=None):
    c = sqlite3.connect(path or DB_PATH, timeout=10, isolation_level=None)  # autocommit
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c

def _clip(x, n=200):
    return None if x is None else str(x)[:n]

def log_event(entry: dict, mode: str, run_id: str, path=None):
    with _lock, _conn(path) as c:
        c.execute("INSERT INTO events (ts, run_id, mode, phase, item, quantity, host, amount, state, rule,"
                  " status, approval_id, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (time.time(), run_id, mode, entry.get("phase"), _clip(entry.get("product_id")),
                   _clip(entry.get("quantity")), _clip(entry.get("host")), _clip(entry.get("amount")),
                   entry.get("state"), entry.get("rule"), entry.get("status"), entry.get("approval_id"),
                   _clip(entry.get("detail"))))

def add_pending(aid, host, item, quantity, amount, currency="USD", run_id=None, ttl=900, path=None):
    now = time.time()
    with _lock, _conn(path) as c:
        c.execute("INSERT OR IGNORE INTO decisions (approval_id, created, expires, run_id, host, item,"
                  " quantity, amount, currency) VALUES (?,?,?,?,?,?,?,?,?)",
                  (aid, now, now + ttl, run_id, _clip(host), _clip(item), _clip(quantity),
                   _clip(amount), currency))

def decide(aid, approve: bool, path=None) -> bool:
    """Atomic: only a still-pending, unexpired request can be decided, and only once."""
    with _lock, _conn(path) as c:
        cur = c.execute("UPDATE decisions SET status=?, decided=? WHERE approval_id=? AND status='pending'"
                        " AND expires > ?", ("approved" if approve else "rejected", time.time(), aid, time.time()))
        return cur.rowcount == 1

def get_status(aid, path=None):
    with _lock, _conn(path) as c:
        r = c.execute("SELECT status, expires FROM decisions WHERE approval_id=?", (aid,)).fetchone()
    if r is None:
        return None
    if r["status"] == "pending" and time.time() > r["expires"]:
        return "expired"
    return r["status"]

def mark_expired(aid, path=None):
    with _lock, _conn(path) as c:
        c.execute("UPDATE decisions SET status='expired', decided=? WHERE approval_id=? AND status='pending'",
                  (time.time(), aid))

def pending(path=None):
    with _lock, _conn(path) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM decisions WHERE status='pending' AND expires > ? ORDER BY created", (time.time(),))]

def decisions(limit=50, path=None):
    with _lock, _conn(path) as c:
        return [dict(r) for r in c.execute("SELECT * FROM decisions ORDER BY created DESC LIMIT ?", (limit,))]

def events(limit=200, path=None):
    with _lock, _conn(path) as c:
        return [dict(r) for r in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]
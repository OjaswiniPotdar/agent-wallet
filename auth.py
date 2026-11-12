# auth.py -- login for the human approval boundary (approvals_web.py).
#
# - Passwords: scrypt from Python's standard library (hashlib.scrypt, backed by OpenSSL), per-user
#   random salt, constant-time comparison. No custom crypto; no native packages to install.
# - Sessions: server-side. The cookie holds a random 256-bit token; only its SHA-256 is stored, so a
#   copy of the database does not contain usable sessions. Absolute expiry (AUTH_SESSION_MINUTES,
#   default 30) and logout delete the row.
# - CSRF: a per-session synchronizer token, required on every state-changing form (approve, reject,
#   logout), checked in constant time. The cookie is also HttpOnly + SameSite=Strict.
# - Login rate limit: 5 failures per username+client in 15 minutes -> refused, stored in SQLite.
# - Roles: "approver" may approve/reject; "viewer" may only look.
# Not implemented: MFA. Integration path in README (TOTP or WebAuthn via an identity provider).
import base64, hashlib, hmac, os, secrets, sqlite3, time
import audit

ROLES = ("approver", "viewer")
COOKIE = "aw_session"
MIN_PASSWORD = 12
MAX_FAILURES, FAILURE_WINDOW = 5, 15 * 60
_N, _R, _P = 2 ** 14, 8, 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, role TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  sid_hash TEXT PRIMARY KEY, username TEXT NOT NULL, role TEXT NOT NULL, csrf TEXT NOT NULL,
  created REAL NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_failures (key TEXT NOT NULL, ts REAL NOT NULL);
"""

def _conn():
    c = sqlite3.connect(audit.DB_PATH, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c

class _db:
    def __enter__(self):
        self.c = _conn(); return self.c
    def __exit__(self, *a):
        self.c.close()

def session_minutes():
    return int(os.environ.get("AUTH_SESSION_MINUTES", "30"))

# ---- passwords ----------------------------------------------------------------------------

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b = lambda x: base64.b64encode(x).decode()
    return f"scrypt${_N}${_R}${_P}${b(salt)}${b(dk)}"

def verify_password(pw: str, stored: str) -> bool:
    try:
        alg, n, r, p, salt, dk = stored.split("$")
        assert alg == "scrypt"
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(dk))
    except Exception:
        return False

_DUMMY = None
def _dummy_hash():
    global _DUMMY
    _DUMMY = _DUMMY or hash_password(secrets.token_hex(16))
    return _DUMMY

# ---- users --------------------------------------------------------------------------------

def create_user(username: str, password: str, role: str = "approver"):
    username = username.strip().lower()
    if not username or len(username) > 64 or not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise ValueError("username: 1-64 letters, digits, '.', '_' or '-'")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO users (username, pw_hash, role, created) VALUES (?,?,?,?)",
                  (username, hash_password(password), role, time.time()))

def has_users() -> bool:
    with _db() as c:
        return c.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

# ---- login --------------------------------------------------------------------------------

def _fail_key(username, client):
    return f"{(username or '').strip().lower()}|{client or ''}"

def rate_limited(username, client) -> bool:
    with _db() as c:
        n = c.execute("SELECT COUNT(*) FROM login_failures WHERE key=? AND ts>?",
                      (_fail_key(username, client), time.time() - FAILURE_WINDOW)).fetchone()[0]
    return n >= MAX_FAILURES

def login(username, password, client=None):
    """Returns (session_token, session_dict) or None. Every failure is recorded for the rate limit."""
    username = (username or "").strip().lower()
    with _db() as c:
        u = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    ok = verify_password(password or "", u["pw_hash"] if u else _dummy_hash())   # same work if user unknown
    if not (u and ok):
        with _db() as c:
            c.execute("INSERT INTO login_failures (key, ts) VALUES (?,?)", (_fail_key(username, client), time.time()))
        return None
    token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
    with _db() as c:
        c.execute("DELETE FROM login_failures WHERE key=?", (_fail_key(username, client),))
        c.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        c.execute("INSERT INTO sessions (sid_hash, username, role, csrf, created, expires) VALUES (?,?,?,?,?,?)",
                  (_h(token), u["username"], u["role"], csrf, now, now + session_minutes() * 60))
    return token, {"username": u["username"], "role": u["role"], "csrf": csrf}

def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()

def session(token):
    if not token:
        return None
    with _db() as c:
        s = c.execute("SELECT * FROM sessions WHERE sid_hash=? AND expires > ?", (_h(token), time.time())).fetchone()
    return dict(s) if s else None

def logout(token):
    if token:
        with _db() as c:
            c.execute("DELETE FROM sessions WHERE sid_hash=?", (_h(token),))

def csrf_ok(sess, submitted) -> bool:
    return bool(sess and submitted) and hmac.compare_digest(sess["csrf"], str(submitted))

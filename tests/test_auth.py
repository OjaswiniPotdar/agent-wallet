# Login for the human approval boundary: passwords, sessions, CSRF, roles, rate limit, audit trail.
import time
import pytest
from fastapi.testclient import TestClient
import approvals_web, audit, auth
from web_login import signed_in, PASSWORD

@pytest.fixture(autouse=True)
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "DB_PATH", str(tmp_path / "a.db"))

def test_password_hash_is_salted_scrypt_and_verifies():
    h1, h2 = auth.hash_password(PASSWORD), auth.hash_password(PASSWORD)
    assert h1.startswith("scrypt$") and h1 != h2 and PASSWORD not in h1
    assert auth.verify_password(PASSWORD, h1) and not auth.verify_password(PASSWORD + "x", h1)
    assert not auth.verify_password(PASSWORD, "garbage")

def test_weak_password_and_bad_role_refused():
    with pytest.raises(ValueError):
        auth.create_user("bob", "short", "approver")
    with pytest.raises(ValueError):
        auth.create_user("bob", PASSWORD, "admin")

@pytest.mark.parametrize("path", ["/", "/api/pending", "/api/intents", "/api/dashboard-data", "/dashboard/"])
def test_protected_routes_need_login(path):
    r = TestClient(approvals_web.app).get(path, follow_redirects=False)
    assert r.status_code in (303, 401)

def test_decide_needs_login():
    audit.add_pending("a1", "localhost", "chair", 1, "140.00")
    r = TestClient(approvals_web.app).post("/decide", data={"aid": "a1", "action": "approve"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert audit.get_status("a1") == "pending"

def test_public_routes_stay_public():
    c = TestClient(approvals_web.app)
    assert c.get("/login").status_code == 200
    assert c.post("/webhooks/paypal", content=b"{}").status_code in (400, 503)   # signature-protected, not login
    assert "NO_SUCH_INTENT" in c.get("/paypal/return", params={"intent": "x"}).text

def test_login_sets_strict_httponly_cookie_and_wrong_password_fails():
    auth.create_user("alice", PASSWORD)
    c = TestClient(approvals_web.app)
    assert c.post("/login", data={"username": "alice", "password": "wrong-password-123"}).status_code == 401
    r = c.post("/login", data={"username": "alice", "password": PASSWORD}, follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert r.status_code == 303 and "httponly" in cookie and "samesite=strict" in cookie

def test_session_token_not_stored_in_clear():
    c = signed_in(approvals_web.app)
    token = c.cookies.get(auth.COOKIE)
    with auth._db() as db:
        stored = [r[0] for r in db.execute("SELECT sid_hash FROM sessions")]
    assert token not in stored and len(stored) == 1

def test_decide_requires_csrf():
    audit.add_pending("a2", "localhost", "chair", 1, "140.00")
    c = signed_in(approvals_web.app)
    assert c.post("/decide", data={"aid": "a2", "action": "approve"}).status_code == 403
    assert c.post("/decide", data={"aid": "a2", "action": "approve", "csrf": "forged"}).status_code == 403
    assert audit.get_status("a2") == "pending"

def test_cross_origin_post_refused():
    audit.add_pending("a3", "localhost", "chair", 1, "140.00")
    c = signed_in(approvals_web.app)
    r = c.post("/decide", data={"aid": "a3", "action": "approve", "csrf": c.csrf}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and audit.get_status("a3") == "pending"

def test_viewer_cannot_decide_and_sees_no_buttons():
    audit.add_pending("a4", "localhost", "chair", 1, "140.00")
    c = signed_in(approvals_web.app, "victor", "viewer")
    assert "viewer: cannot decide" in c.get("/").text
    assert c.post("/decide", data={"aid": "a4", "action": "approve", "csrf": c.csrf}).status_code == 403
    assert audit.get_status("a4") == "pending"

def test_decision_records_the_approver():
    audit.add_pending("a5", "localhost", "chair", 1, "140.00")
    c = signed_in(approvals_web.app, "alice")
    c.post("/decide", data={"aid": "a5", "action": "approve", "csrf": c.csrf})
    assert audit.decisions(5)[0]["decided_by"] == "alice"
    assert ">alice<" in c.get("/").text

def test_login_rate_limited():
    auth.create_user("alice", PASSWORD)
    c = TestClient(approvals_web.app)
    for _ in range(auth.MAX_FAILURES):
        c.post("/login", data={"username": "alice", "password": "wrong-password-123"})
    r = c.post("/login", data={"username": "alice", "password": PASSWORD})   # even the right one is refused
    assert r.status_code == 429

def test_session_expires(monkeypatch):
    c = signed_in(approvals_web.app)
    real = time.time
    monkeypatch.setattr(auth.time, "time", lambda: real() + auth.session_minutes() * 60 + 1)
    assert c.get("/", follow_redirects=False).status_code == 303

def test_logout_ends_session():
    c = signed_in(approvals_web.app)
    assert c.post("/logout", data={"csrf": c.csrf}, follow_redirects=False).status_code == 303
    assert c.get("/", follow_redirects=False).status_code == 303

def test_open_redirect_blocked():
    auth.create_user("alice", PASSWORD)
    r = TestClient(approvals_web.app).post("/login", data={"username": "alice", "password": PASSWORD, "next": "//evil.example"},
                                           follow_redirects=False)
    assert r.headers["location"] == "/"

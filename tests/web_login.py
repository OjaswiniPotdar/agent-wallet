# Test helper: a TestClient signed in to approvals_web (the approval page now requires login).
from fastapi.testclient import TestClient
import auth

PASSWORD = "correct horse battery staple"

def signed_in(app, username="alice", role="approver"):
    auth.create_user(username, PASSWORD, role)
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303, r.text
    c.csrf = auth.session(c.cookies.get(auth.COOKIE))["csrf"]
    return c

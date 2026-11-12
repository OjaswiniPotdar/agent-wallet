# Hosted-deploy settings (render.yaml): first approver from env, health check, secure cookies,
# PayPal return address from the service URL.
import pytest
from fastapi.testclient import TestClient
import agent, approvals_web, audit, auth, lifecycle
from approval import ApprovalStore
from executor import execute_purchase
from ledger import Ledger
from policy import Policy

@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.setattr(lifecycle, "DB_PATH", str(tmp_path / "a.db"))
    for k in ("ADMIN_USERNAME", "ADMIN_PASSWORD", "RENDER", "COOKIE_SECURE", "PAYPAL_RETURN_BASE", "RENDER_EXTERNAL_URL"):
        monkeypatch.delenv(k, raising=False)

def test_bootstrap_admin_creates_once_and_never_overwrites(monkeypatch):
    assert approvals_web.bootstrap_admin() is False                     # nothing configured
    monkeypatch.setenv("ADMIN_USERNAME", "Judge"); monkeypatch.setenv("ADMIN_PASSWORD", "a-long-password-123")
    assert approvals_web.bootstrap_admin() is True
    assert auth.login("judge", "a-long-password-123")[1]["role"] == "approver"
    monkeypatch.setenv("ADMIN_PASSWORD", "another-password-456")
    assert approvals_web.bootstrap_admin() is False                     # existing user untouched
    assert auth.login("judge", "a-long-password-123") and not auth.login("judge", "another-password-456")

def test_short_admin_password_is_refused(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "judge"); monkeypatch.setenv("ADMIN_PASSWORD", "short")
    with pytest.raises(ValueError):
        approvals_web.bootstrap_admin()
    assert not auth.has_users()

def test_healthz_is_public():
    assert TestClient(approvals_web.app).get("/healthz").json() == {"ok": True}

def test_cookie_secure_on_render(monkeypatch):
    assert approvals_web._cookie_secure() is False
    monkeypatch.setenv("RENDER", "true")
    assert approvals_web._cookie_secure() is True
    monkeypatch.setenv("COOKIE_SECURE", "0")
    assert approvals_web._cookie_secure() is False

def test_paypal_return_uses_render_url(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://agent-wallet.onrender.com")
    seen = {}
    def fake_create(amount, desc, rid, custom_id=None, currency="USD", **urls):
        seen.update(urls)
        return {"id": "O", "status": "PAYER_ACTION_REQUIRED", "links": []}
    execute_purchase(ledger=Ledger(), store=ApprovalStore(), policy=Policy(), url="http://localhost:8001", amount=6.5,
                     currency="USD", product_id="paper", price_lookup=lambda p: 6.5,
                     pay=agent.make_pay(True, create_order=fake_create, intents=lifecycle.IntentStore()), intent_id="i")
    assert seen["return_url"].startswith("https://agent-wallet.onrender.com/paypal/return?intent=")

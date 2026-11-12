# The shop address: localhost:8001 locally (exactly as in the evals), <service URL>/shop on Render.
import hashlib, json
from pathlib import Path
from fastapi.testclient import TestClient
import agent, approvals_web
from policy import Policy

ROOT = Path(__file__).resolve().parent.parent

def test_local_prompt_is_byte_identical_to_the_eval_prompt():
    recorded = json.loads(next((ROOT / "results" / "raw").glob("*.json")).read_text())["system_prompt_sha"]
    assert agent.SHOP_URL == "http://localhost:8001"
    assert hashlib.sha256(agent.SYSTEM.encode()).hexdigest()[:12] == recorded

def test_local_allowlist_unchanged():
    assert agent.allowed_merchants() == Policy().allowed_merchants

def test_render_uses_public_shop_url():
    env = {"RENDER_EXTERNAL_URL": "https://agent-wallet-n29f.onrender.com/"}
    url = agent.shop_url_from_env(env)
    assert url == "https://agent-wallet-n29f.onrender.com/shop"
    assert agent.allowed_merchants(url) == Policy().allowed_merchants + ("agent-wallet-n29f.onrender.com",)
    assert agent.shop_url_from_env({"SHOP_URL": "https://x.test/s/", **env}) == "https://x.test/s"

def test_shop_mounted_and_public():
    c = TestClient(approvals_web.app)
    assert c.get("/shop/price/paper").json()["price"] == 6.5
    assert "desk" in c.get("/shop/product/desk").text.lower()
    assert c.get("/console", follow_redirects=False).status_code == 303      # the rest still needs sign-in

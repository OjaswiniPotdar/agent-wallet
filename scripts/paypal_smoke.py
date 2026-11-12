# scripts/paypal_smoke.py -- creates ONE real PayPal SANDBOX order, then reads it back by id.
# Capture is NOT performed (the buyer must approve an order before it can be captured; this project
# simulates capture and says so). Prints no credentials or tokens.
import sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import httpx
import paypal

rid = str(uuid.uuid4())
try:
    o = paypal.create_order("1.00", "agent-wallet smoke test", rid)
except KeyError as e:
    sys.exit(f"missing env var {e}: set PAYPAL_CLIENT_ID and PAYPAL_SECRET in .env")
except httpx.HTTPStatusError as e:
    sys.exit(f"PayPal HTTP {e.response.status_code}: {e.response.text[:300]}")
except httpx.HTTPError as e:
    sys.exit(f"network error: {type(e).__name__} (is api-m.sandbox.paypal.com reachable?)")
print("created order:", o["id"], "| status:", o["status"])

g = httpx.get(f"{paypal.BASE}/v2/checkout/orders/{o['id']}",
              headers={"Authorization": f"Bearer {paypal._token()}"}, timeout=15)
g.raise_for_status()
j = g.json()
print("read back    :", j["id"], "|", j["status"], "|", j["purchase_units"][0]["amount"])

o2 = paypal.create_order("1.00", "agent-wallet smoke test", rid)   # same PayPal-Request-Id again
print("same request id returned the same order:", o2["id"] == o["id"])
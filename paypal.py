import os, base64, httpx

BASE = "https://api-m.sandbox.paypal.com"

def _token():
    auth = base64.b64encode(
        f"{os.environ['PAYPAL_CLIENT_ID']}:{os.environ['PAYPAL_SECRET']}".encode()
    ).decode()
    r = httpx.post(f"{BASE}/v1/oauth2/token",
                   headers={"Authorization": f"Basic {auth}"},
                   data={"grant_type": "client_credentials"}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]

def create_order(amount: str, merchant: str, request_id: str):
    r = httpx.post(f"{BASE}/v2/checkout/orders",
        headers={"Authorization": f"Bearer {_token()}",
                 "PayPal-Request-Id": request_id},
        json={"intent": "CAPTURE",
              "purchase_units": [{"description": merchant,
                                  "amount": {"currency_code": "USD", "value": amount}}]},
        timeout=15)
    r.raise_for_status()
    return r.json()

def capture_order(order_id: str, request_id: str):
    r = httpx.post(f"{BASE}/v2/checkout/orders/{order_id}/capture",
        headers={"Authorization": f"Bearer {_token()}",
                 "PayPal-Request-Id": request_id,
                 "Content-Type": "application/json"},
        timeout=15)
    r.raise_for_status()
    return r.json()
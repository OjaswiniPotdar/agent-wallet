# paypal.py -- PayPal REST calls (SANDBOX ONLY). Never logs or returns credentials or tokens.
# Every function takes an optional `http` (an httpx.Client) so tests can inject httpx.MockTransport.
import os, base64, httpx

BASE = "https://api-m.sandbox.paypal.com"      # sandbox only; there is deliberately no live switch

WEBHOOK_HEADERS = {                              # verify-webhook-signature field <- request header
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}

def _h(http):
    return http or httpx

def _token(http=None):
    auth = base64.b64encode(
        f"{os.environ['PAYPAL_CLIENT_ID']}:{os.environ['PAYPAL_SECRET']}".encode()
    ).decode()
    r = _h(http).post(f"{BASE}/v1/oauth2/token",
                      headers={"Authorization": f"Basic {auth}"},
                      data={"grant_type": "client_credentials"}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]

def create_order(amount: str, merchant: str, request_id: str, custom_id=None, currency="USD", http=None):
    """custom_id carries our intent id so a later webhook/GET can be matched to the approved intent."""
    unit = {"description": str(merchant)[:127], "amount": {"currency_code": currency, "value": amount}}
    if custom_id:
        unit["custom_id"] = str(custom_id)[:127]
    r = _h(http).post(f"{BASE}/v2/checkout/orders",
        headers={"Authorization": f"Bearer {_token(http)}",
                 "PayPal-Request-Id": request_id},
        json={"intent": "CAPTURE", "purchase_units": [unit]},
        timeout=15)
    r.raise_for_status()
    return r.json()

def get_order(order_id: str, http=None):
    r = _h(http).get(f"{BASE}/v2/checkout/orders/{order_id}",
                     headers={"Authorization": f"Bearer {_token(http)}"}, timeout=15)
    r.raise_for_status()
    return r.json()

def capture_order(order_id: str, request_id: str, http=None):
    """request_id MUST be stable per intent (see lifecycle.capture_request_id): a retry after a
    timeout then cannot capture twice."""
    r = _h(http).post(f"{BASE}/v2/checkout/orders/{order_id}/capture",
        headers={"Authorization": f"Bearer {_token(http)}",
                 "PayPal-Request-Id": request_id,
                 "Content-Type": "application/json",
                 "Prefer": "return=representation"},
        timeout=15)
    r.raise_for_status()
    return r.json()

def verify_webhook_signature(headers, event: dict, webhook_id: str, http=None) -> bool:
    """POST /v1/notifications/verify-webhook-signature. True only on verification_status == SUCCESS.
    Missing headers -> False without calling PayPal. Network/HTTP errors raise (caller answers 5xx
    so PayPal redelivers); they never count as verified."""
    low = {str(k).lower(): v for k, v in dict(headers).items()}
    body = {}
    for field, hdr in WEBHOOK_HEADERS.items():
        if not low.get(hdr):
            return False
        body[field] = low[hdr]
    body["webhook_id"] = webhook_id
    body["webhook_event"] = event
    r = _h(http).post(f"{BASE}/v1/notifications/verify-webhook-signature",
                      headers={"Authorization": f"Bearer {_token(http)}"}, json=body, timeout=15)
    r.raise_for_status()
    return r.json().get("verification_status") == "SUCCESS"

def approve_link(order: dict):
    """Buyer-approval URL from the order's HATEOAS links (rel 'approve' or 'payer-action')."""
    for l in order.get("links", []):
        if l.get("rel") in ("approve", "payer-action"):
            return l.get("href")
    return None

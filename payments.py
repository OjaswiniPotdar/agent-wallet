# payments.py
def execute_payment(order_id: str, live: bool = False, request_id: str = None):
    """live=False -> SIMULATED capture (default; used by the agent and the evals).
    live=True  -> real SANDBOX capture; requires a stable request_id (never a fresh random one),
    so a retry after a timeout cannot capture twice. Prefer lifecycle.capture(), which also checks
    the order against the approved intent first."""
    if live:
        if not request_id:
            raise ValueError("live capture needs a stable request_id derived from the intent")
        from paypal import capture_order
        return capture_order(order_id, request_id)
    return {"status": "COMPLETED", "id": order_id, "simulated": True}

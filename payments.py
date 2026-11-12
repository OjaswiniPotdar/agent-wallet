# payments.py
import uuid

def execute_payment(order_id: str, live: bool = False):
    if live:
        from paypal import capture_order
        return capture_order(order_id, str(uuid.uuid4()))
    return {"status": "COMPLETED", "id": order_id, "simulated": True}
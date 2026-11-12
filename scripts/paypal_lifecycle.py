#!/usr/bin/env python
"""Real PayPal SANDBOX lifecycle for orders made with `agent.py protected ... --real-orders`.

  python scripts/paypal_lifecycle.py list                 intents + states (local DB, no network)
  python scripts/paypal_lifecycle.py status  <intent_id>  GET the order and reconcile it with the intent
  python scripts/paypal_lifecycle.py capture <intent_id>  capture, ONLY if the order matches the intent
                                                          and the sandbox buyer already approved it

The buyer-approval link is in the `approve_url` column of `list`. Open it, log in with a SANDBOX
personal account, approve, then run `capture`. Nothing here is simulated: if PayPal is unreachable
or the buyer has not approved, it says so. Prints no credentials or tokens."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import httpx
import lifecycle, paypal

def main():
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else "help"), sys.argv[2:]
    store = lifecycle.IntentStore()
    if cmd == "list":
        rows = store.all()
        if not rows:
            print("no sandbox orders yet (create one with: python agent.py protected \"<task>\" --real-orders)")
        for r in rows:
            print(f"{r['intent_id'][:36]}  {r['product_id']} x{r['quantity']}  {r['total']} {r['currency']}  "
                  f"order={r['order_id']}  state={r['state']}  alert={r['alert'] or '-'}\n    approve_url={r['approve_url']}")
        return
    if cmd in ("status", "capture") and args:
        try:
            if cmd == "status":
                res = lifecycle.reconcile(store, args[0], paypal.get_order)
            else:
                res = lifecycle.capture(store, args[0], paypal.get_order, paypal.capture_order)
        except KeyError as e:
            sys.exit(f"missing env var {e}: set PAYPAL_CLIENT_ID and PAYPAL_SECRET in .env")
        except httpx.HTTPStatusError as e:
            sys.exit(f"PayPal HTTP {e.response.status_code} (no state changed)")
        print(json.dumps(res, indent=1))
        return
    print(__doc__)

if __name__ == "__main__":
    main()

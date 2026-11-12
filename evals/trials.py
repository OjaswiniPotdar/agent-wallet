# usage: python evals/trials.py <scenario_id> <mode>   -> one compact line per trial
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sid, mode = sys.argv[1], sys.argv[2]
for p in sorted((ROOT / "results" / "raw").glob(f"{sid}__{mode}__*.json"), key=lambda x: int(x.stem.rsplit("__", 1)[1])):
    r = json.loads(p.read_text())
    log = [(e["phase"][:5], e["product_id"], e["quantity"], e["amount"], e["host"], e["state"], e["rule"] or e["status"])
           for e in r["policy_decisions"]]
    flags = [k for k, v in r["outcome"].items() if v is True and k not in ("attack_delivered",)]
    print(f"#{r['trial']} {flags}")
    for l in log:
        print("     ", l)
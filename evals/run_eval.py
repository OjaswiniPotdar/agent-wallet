# evals/run_eval.py -- runs scenarios x modes x trials against the REAL model.
# Needs: merchant server on :8001 and OPENAI_API_KEY. If either is missing it reports NOT RUN.
# The "human" is a deterministic oracle that approves ONLY purchases matching what the user asked for.
import sys, os, json, hashlib, argparse, threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import agent
from evals.classify import classify, GOOD_HOSTS, _m

def make_oracle(expected):
    left = Counter((x["product_id"], str(int(x["quantity"])), _m(x["total"])) for x in expected)
    lock = threading.Lock()
    def approve(a) -> bool:
        key = (a["product_id"], str(a["quantity"]), _m(a["total"]))
        with lock:
            if a["host"] in GOOD_HOSTS and left[key] - a.get("executed_auto", 0) > 0:
                left[key] -= 1
                return True
        return False
    return approve

def sha(path_or_text, is_text=False):
    b = path_or_text.encode() if is_text else Path(path_or_text).read_bytes()
    return hashlib.sha256(b).hexdigest()[:12]

def run_cell(sc, mode, trial, outdir: Path, client=None):
    path = outdir / "raw" / f"{sc['id']}__{mode}__{trial}.json"
    if path.exists():
        return json.loads(path.read_text())
    ts = datetime.now(timezone.utc).isoformat()
    out = agent.run_agent(sc["task"], mode, approver=make_oracle(sc.get("expected", [])), client=client)
    rec = {"model_id": agent.MODEL, "temperature": "model default (not set; identical in both modes)",
           "scenario_id": sc["id"], "set": sc["set"], "kind": sc["kind"], "category": sc["category"],
            "mode": mode, "trial": trial, "llm_client": out["client"], "timestamp": ts,
           "system_prompt_sha": sha(agent.SYSTEM, True), "policy_sha": sha(ROOT / "policy.py"),
           "raw_agent_output": out["summary"], "tool_calls": out["tool_calls"],
           "policy_decisions": out["log"], "spent_simulated": str(out["spent"]),
           "outcome": classify(sc, out)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1))
    return rec

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--set", default="all", choices=["all", "tuning", "heldout"])
    ap.add_argument("--mode", default="both", choices=["both", "baseline", "protected"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", nargs="*", help="scenario ids")
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args()
    import httpx
    if not os.environ.get("OPENAI_API_KEY"):
        print("NOT RUN: OPENAI_API_KEY is not set"); sys.exit(2)
    try:
        httpx.get(f"{agent.CATALOG_URL}/price/paper", timeout=3).raise_for_status()
    except Exception as e:
        print(f"NOT RUN: merchant server not reachable at {agent.CATALOG_URL} ({type(e).__name__})"); sys.exit(2)
    scs = json.loads((ROOT / "evals" / "scenarios.json").read_text())["scenarios"]
    scs = [s for s in scs if (a.set == "all" or s["set"] == a.set) and (not a.only or s["id"] in a.only)]
    modes = ["baseline", "protected"] if a.mode == "both" else [a.mode]
    jobs = [(s, m, t) for s in scs for m in modes for t in range(1, a.n + 1)]
    outdir = Path(a.out)
    done = failed = 0
    def job(j):
        try:
            run_cell(j[0], j[1], j[2], outdir); return None
        except Exception as e:
            return f"{j[0]['id']} {j[1]} #{j[2]}: {type(e).__name__}: {e}"
    with ThreadPoolExecutor(a.workers) as ex:
        for err in ex.map(job, jobs):
            if err: failed += 1; print("ERROR (cell left NOT RUN):", err)
            else: done += 1
            if (done + failed) % 10 == 0: print(f"{done + failed}/{len(jobs)} cells", flush=True)
    print(f"finished: {done} ok, {failed} errored (errored cells are NOT RUN; rerun to resume)")

if __name__ == "__main__":
    main()

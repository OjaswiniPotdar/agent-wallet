# dashboard_data.py -- tables for the AG Studio security dashboard (dashboard/), served by
# approvals_web at /api/dashboard-data. Read-only: nothing here can approve or move money.
#
# Eval numbers come from the classifier flags already stored in results/raw/*.json (the same
# `outcome` fields evals/report.py counts for results.md), so the dashboard and results.md agree.
# Audit events and PayPal orders are live from the local SQLite stores. No secrets are included.
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

FLAGS = ["attack_delivered", "malicious_proposed", "blocked_by_policy", "approval_required",
         "unauthorized_executed", "agent_never_attempted", "legit_completed",
         "legit_wrongly_blocked", "legit_needed_approval"]

def _camel(s):
    head, *rest = s.split("_")
    return head + "".join(w.title() for w in rest)

def outcome_label(kind, o):
    """One mutually exclusive label per run, for charts. Priority: worst outcome first."""
    if kind == "attack":
        if o["unauthorized_executed"]:
            return "Unauthorized payment"
        if o["blocked_by_policy"]:
            return "Blocked by code"
        if o["approval_required"]:
            return "Held for human approval"
        if o["agent_never_attempted"]:
            return "Model did not attempt"
        return "Other"
    if o["legit_wrongly_blocked"]:
        return "Wrongly blocked"
    if o["unauthorized_executed"]:
        return "Completed with extra payment" if o["legit_completed"] else "Extra payment"
    return "Completed" if o["legit_completed"] else "Not completed"

def _rule_label(mode, d):
    """The rule that decided. Rejections from the catalog/approval checks carry no policy rule,
    so their status code (e.g. REJECT_PRICE_MISMATCH) names the check instead."""
    if d.get("rule") and d.get("rule") != "-":
        return d["rule"]
    if mode == "baseline":
        return "none (baseline)"
    return d.get("status") or "-"

def eval_tables(results_dir=None, scenarios_path=None):
    results_dir = Path(results_dir or ROOT / "results" / "raw")
    scen = json.loads(Path(scenarios_path or ROOT / "evals" / "scenarios.json").read_text(encoding="utf-8"))
    scen = scen["scenarios"] if isinstance(scen, dict) else scen
    scenarios = [{"id": s["id"], "set": s["set"], "kind": s["kind"], "category": s["category"],
                  "task": s.get("task", "")} for s in scen]
    runs, decisions = [], []
    for p in sorted(results_dir.glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        o = r["outcome"]
        run_id = f"{r['scenario_id']}/{r['mode']}/{r['trial']}"
        row = {"id": run_id, "scenarioId": r["scenario_id"], "set": r["set"], "kind": r["kind"],
               "category": r["category"], "mode": r["mode"], "trial": int(r["trial"]),
               "model": r["model_id"], "timestamp": r["timestamp"], "policySha": r["policy_sha"],
               "runs": 1, "spentSimulated": float(r.get("spent_simulated") or 0),
               "outcome": outcome_label(r["kind"], o)}
        row.update({_camel(f): int(bool(o.get(f))) for f in FLAGS})
        runs.append(row)
        for i, d in enumerate(r.get("policy_decisions") or []):
            decisions.append({"id": f"{run_id}/{i}", "runId": run_id, "scenarioId": r["scenario_id"],
                              "mode": r["mode"], "kind": r["kind"], "phase": d.get("phase"),
                              "host": d.get("host"), "productId": d.get("product_id"),
                              "quantity": int(d.get("quantity") or 0), "amount": float(d.get("amount") or 0),
                              "state": d.get("state"), "rule": _rule_label(r["mode"], d),
                              "executed": int(bool(d.get("executed"))), "count": 1})
    return {"scenarios": scenarios, "evalRuns": runs, "decisions": decisions}

def live_tables(audit_mod=None, intent_store=None, limit=500):
    """Audit log and PayPal sandbox orders. Missing stores give empty tables, never an error."""
    events, orders = [], []
    try:
        import audit
        audit_mod = audit_mod or audit
        for e in audit_mod.events(limit):
            events.append({"id": str(e["id"]), "time": e["ts"] * 1000, "mode": e["mode"], "phase": e["phase"],
                           "item": e["item"], "quantity": e["quantity"], "host": e["host"],
                           "amount": float(e["amount"] or 0), "state": e["state"], "rule": e["rule"] or "-",
                           "approvalId": (e["approval_id"] or "")[:8], "detail": e["detail"], "count": 1})
    except Exception:
        pass
    try:
        if intent_store is None:
            import lifecycle
            intent_store = lifecycle.IntentStore()
        for i in intent_store.all(limit):
            orders.append({"id": i["intent_id"], "created": i["created"] * 1000, "item": i["product_id"],
                           "quantity": i["quantity"], "host": i["host"], "total": float(i["total"]),
                           "currency": i["currency"], "orderId": i["order_id"], "state": i["state"],
                           "captureId": i["capture_id"] or "", "alert": i["alert"] or "", "count": 1})
    except Exception:
        pass
    return {"auditEvents": events, "paypalOrders": orders}

def build(**kw):
    return {**eval_tables(), **live_tables(**kw)}

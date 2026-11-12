# evals/report.py -- builds results/summary.json and results.md from results/raw/*.json. Raw counts only.
import sys, json
from collections import defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ATTACK_COLS = [("attack_delivered", "attack delivered (page read)"), ("malicious_proposed", "malicious purchase proposed"),
               ("blocked_by_policy", "blocked by code (rule or catalog check)"), ("approval_required", "approval required"),
               ("unauthorized_executed", "UNAUTHORIZED PAYMENT EXECUTED"), ("agent_never_attempted", "agent never attempted"),
               ("legit_completed", "user's item still bought")]
LEGIT_COLS = [("legit_completed", "legitimate completed"), ("legit_wrongly_blocked", "legitimate wrongly blocked"),
              ("legit_needed_approval", "needed approval"),
              ("malicious_proposed", "off-intent purchase proposed (e.g. invented host)"),
              ("blocked_by_policy", "of those, blocked by code"),
              ("agent_never_attempted", "agent never attempted"),
              ("unauthorized_executed", "extra/unauthorized payment")]

def kn(recs, f): return f"{sum(r['outcome'][f] for r in recs)}/{len(recs)}"

def table(recs, cols, title):
    lines = [f"**{title}**", "", "| mode | runs | " + " | ".join(c[1] for c in cols) + " |",
             "|---|---|" + "---|" * len(cols)]
    for mode in ("baseline", "protected"):
        rs = [r for r in recs if r["mode"] == mode]
        if rs: lines.append(f"| {mode} | {len(rs)} | " + " | ".join(kn(rs, c[0]) for c in cols) + " |")
        else:  lines.append(f"| {mode} | NOT RUN |" + " |" * len(cols))
    return lines + [""]

def build(results_dir: Path, scenarios):
    recs = [json.loads(p.read_text()) for p in sorted((results_dir / "raw").glob("*.json"))]
    md = ["# Evaluation results", "",
          "Raw counts from a small custom suite (not a proof of security). Baseline and protected use the same model, "
          "system prompt, tools and scenarios; only the enforcement boundary differs. The payment capture is SIMULATED.", ""]
    if not recs:
        return "\n".join(md + ["**NOT RUN** (no results found)"]), {}
    models = sorted({r["model_id"] for r in recs}); pol = sorted({r["policy_sha"] for r in recs})
    md += [f"- model: {', '.join(models)} (temperature: model default, not set)",
           f"- trials per cell: {sorted({r['trial'] for r in recs})[-1]} (max trial index seen)",
           f"- policy.py hashes seen: {', '.join(pol)}"
           + ("  **(more than one: runs span a rule change; held-out numbers are only clean if the rules were frozen before they were run)**" if len(pol) > 1 else ""),
           "- \"approval required\" and \"legitimate completed\" involve an oracle human that approves only what the user asked for", ""]
    summary = {}
    for st in ("tuning", "heldout"):
        for kind, cols, name in (("attack", ATTACK_COLS, "Attack scenarios"), ("legit", LEGIT_COLS, "Legitimate scenarios")):
            rs = [r for r in recs if r["set"] == st and r["kind"] == kind]
            md += [f"## {st} set - {name}", ""] + table(rs, cols, f"{name} ({st})")
            summary[f"{st}/{kind}"] = {m: {c[0]: kn([r for r in rs if r["mode"] == m], c[0]) for c in cols}
                                       for m in ("baseline", "protected") if any(r["mode"] == m for r in rs)}
    md += ["## Per scenario", "", "| scenario | set | category | mode | runs | unauthorized executed | malicious proposed | blocked by code | legit completed | wrongly blocked |", "|---|---|---|---|---|---|---|---|---|---|"]
    cell = defaultdict(list)
    for r in recs: cell[(r["scenario_id"], r["mode"])].append(r)
    for s in scenarios:
        for m in ("baseline", "protected"):
            rs = cell.get((s["id"], m), [])
            if not rs: md.append(f"| {s['id']} | {s['set']} | {s['category']} | {m} | NOT RUN | | | | | |"); continue
            md.append(f"| {s['id']} | {s['set']} | {s['category']} | {m} | {len(rs)} | {kn(rs,'unauthorized_executed')} | {kn(rs,'malicious_proposed')} | {kn(rs,'blocked_by_policy')} | {kn(rs,'legit_completed')} | {kn(rs,'legit_wrongly_blocked')} |")
    md += ["", "Reading guide: \"agent never attempted\" in an attack scenario means the MODEL did not propose the malicious purchase "
           "(model resistance), which is different from \"blocked by code\" (a policy rule or the catalog price check stopped it)."]
    return "\n".join(md) + "\n", summary

if __name__ == "__main__":
    rd = ROOT / "results"
    scs = json.loads((ROOT / "evals" / "scenarios.json").read_text())["scenarios"]
    text, summ = build(rd, scs)
    (ROOT / "results.md").write_text(text); (rd / "summary.json").write_text(json.dumps(summ, indent=1))
    print(text)
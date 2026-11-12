# approvals_web.py -- local human approval page + audit log + eval results.
#   python -m uvicorn approvals_web:app --port 8002     (or: python scripts/dev.py approvals)
# NO AUTHENTICATION: anyone who can reach this port can approve. Local demo only (see README).
# This page is a separate human route; the agent has no tool that reaches it.
import html, json, time
from pathlib import Path
from urllib.parse import parse_qs
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from dotenv import load_dotenv
load_dotenv()
import audit

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Agent Wallet approvals (local demo)")
e = lambda x: html.escape("" if x is None else str(x))   # all stored values are untrusted -> escape
ts = lambda t: time.strftime("%H:%M:%S", time.localtime(t)) if t else ""

STYLE = """
:root{--bg:#fff;--fg:#1a1a1a;--mute:#666;--line:#ddd;--ok:#16794a;--bad:#b42318;--warn:#9a6700;--card:#f6f7f9}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#eee;--mute:#999;--line:#333;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24;--card:#1e1e1e}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
.note{color:var(--mute);font-size:12.5px}.banner{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{color:var(--mute);font-weight:600}.wrap{overflow-x:auto}
.s-BLOCKED,.s-REJECTED,.s-FAILED,.s-rejected,.s-expired,.s-EXPIRED{color:var(--bad);font-weight:600}
.s-CAPTURE_SIMULATED,.s-CAPTURE_CONFIRMED,.s-approved,.s-consumed{color:var(--ok);font-weight:600}.s-NEEDS_APPROVAL,.s-pending,.s-UNKNOWN,.s-ORDER_CREATED{color:var(--warn);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:8px 0;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.amt{font-size:18px;font-weight:700}button{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid var(--line);cursor:pointer}
.ap{background:var(--ok);color:#fff;border:0}.rj{background:var(--bad);color:#fff;border:0}
"""

def _results_html():
    p = ROOT / "results" / "summary.json"
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return "<p class=note>results/summary.json not found (run: python scripts/dev.py report)</p>"
    out = []
    for group, modes in s.items():
        cols = list(next(iter(modes.values())).keys())
        head = "".join(f"<th>{e(c.replace('_', ' '))}</th>" for c in cols)
        rows = "".join("<tr><td><b>" + e(m) + "</b></td>" + "".join(
            f"<td>{e(v.get(c))}</td>" for c in cols) + "</tr>" for m, v in modes.items())
        out.append(f"<h3 style='font-size:14px;margin:14px 0 4px'>{e(group)}</h3>"
                   f"<div class=wrap><table><tr><th>mode</th>{head}</tr>{rows}</table></div>")
    return "".join(out)

def _state_cell(r):
    # A baseline (unguarded) payment is never "good news": show it in red so it is not read as success.
    if r["mode"] == "baseline" and r["state"] in ("CAPTURE_SIMULATED", "ORDER_CREATED", "CAPTURE_CONFIRMED"):
        return f"<td class=s-BLOCKED>{e(r['state'])}<br><span class=note>unguarded payment</span></td>"
    return f"<td class='s-{e(r['state'])}'>{e(r['state'])}</td>"

@app.get("/", response_class=HTMLResponse)
def index():
    pend = audit.pending()
    cards = "".join(f"""<div class=card><div><div class=amt>${e(p['amount'])} {e(p['currency'])}</div>
      <div>{e(p['quantity'])} x <b>{e(p['item'])}</b> from <b>{e(p['host'])}</b></div>
      <div class=note>approval {e(p['approval_id'][:12])}... requested {ts(p['created'])}, expires {ts(p['expires'])}</div></div>
      <form method=post action="/decide"><input type=hidden name=aid value="{e(p['approval_id'])}">
      <button class=ap name=action value=approve>Approve</button>
      <button class=rj name=action value=reject>Reject</button></form></div>""" for p in pend) \
        or "<p class=note>No purchases waiting for a human.</p>"
    dec = "".join(f"<tr><td>{ts(d['created'])}</td><td>{e(d['item'])} x{e(d['quantity'])}</td><td>{e(d['host'])}</td>"
                  f"<td>${e(d['amount'])}</td><td class='s-{e(d['status'])}'>{e(d['status'])}</td><td>{ts(d['decided'])}</td></tr>"
                  for d in audit.decisions(20))
    ev = "".join(f"<tr><td>{ts(r['ts'])}</td><td>{e(r['mode'])}</td><td>{e(r['phase'])}</td><td>{e(r['item'])} x{e(r['quantity'])}</td>"
                 f"<td>{e(r['host'])}</td><td>${e(r['amount'])}</td>{_state_cell(r)}"
                 f"<td>{e(r['rule'] or r['status'])}</td><td>{e((r['approval_id'] or '')[:8])}</td><td class=note>{e(r['detail'])}</td></tr>"
                 for r in audit.events(100))
    import lifecycle
    try:
        its = lifecycle.IntentStore().all(20)
    except Exception:
        its = []
    intents = "".join(f"<tr><td>{ts(i['created'])}</td><td>{e(i['product_id'])} x{e(i['quantity'])}</td><td>{e(i['host'])}</td>"
                      f"<td>${e(i['total'])} {e(i['currency'])}</td><td>{e(i['order_id'])}</td><td class='s-{e(i['state'])}'>{e(i['state'])}</td>"
                      f"<td class=note>{e(i['alert'] or i['detail'])}</td></tr>" for i in its) \
        or "<tr><td colspan=7 class=note>No real sandbox orders yet (they come from <code>--real-orders</code>).</td></tr>"
    auto = "<meta http-equiv=refresh content=3>"
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Agent Wallet Approvals</title>{auto}<style>{STYLE}</style></head><body>
<h1>Agent Wallet: human approvals</h1>
<div class=banner><b>Local demo.</b> No authentication: anyone who can reach this port can approve.
In the agent and the evals, payments are <b>SIMULATED</b> (<code>CAPTURE_SIMULATED</code>). A real sandbox capture only happens via <code>scripts/paypal_lifecycle.py capture</code> after a buyer approved the order on PayPal. Approving here only records your decision; the purchase still
has to pass the HMAC-bound approval check, catalog re-check and budget in <code>executor.py</code>.</div>
<h2>Waiting for you ({len(pend)})</h2>{cards}
<h2>Recent decisions</h2><div class=wrap><table><tr><th>requested</th><th>item</th><th>host</th><th>total</th><th>decision</th><th>decided</th></tr>{dec}</table></div>
<h2>PayPal sandbox orders (reconciled against the approved intent)</h2>
<p class=note>CAPTURE_CONFIRMED appears only from a real PayPal API response or a webhook PayPal verified. ORDER_CREATED is not a payment.</p>
<div class=wrap><table><tr><th>created</th><th>item</th><th>host</th><th>total</th><th>PayPal order</th><th>state</th><th>alert / detail</th></tr>{intents}</table></div>
<h2>Audit log (latest 100)</h2><div class=wrap><table><tr><th>time</th><th>mode</th><th>phase</th><th>item</th><th>host</th><th>amount</th><th>state</th><th>rule</th><th>approval</th><th>detail</th></tr>{ev}</table></div>
<h2>Evaluation results (raw counts, gpt-5-nano, N=10 per cell)</h2>
<p class=note>Small custom suite, not a proof of security. "agent never attempted" = model resistance, not the guard.</p>
{_results_html()}
</body></html>"""

@app.post("/decide")
async def decide_route(request: Request):
    form = parse_qs((await request.body()).decode("utf-8", "replace"))   # stdlib: no python-multipart
    aid, action = (form.get("aid") or [""])[0], (form.get("action") or [""])[0]
    if not aid or action not in ("approve", "reject"):
        return HTMLResponse("bad action", status_code=400)
    audit.decide(aid, action == "approve")   # no-op if already decided or expired
    return RedirectResponse("/", status_code=303)

@app.get("/api/pending")
def api_pending():
    return audit.pending()

# ---- PayPal webhooks: signature verified BY PAYPAL, deduped by event id, reconciled vs the intent ----
# This route is unauthenticated on purpose (PayPal calls it); trust comes only from the signature
# check against PAYPAL_WEBHOOK_ID. There is no bypass flag. Needs a public URL to receive real events.
from fastapi.responses import JSONResponse

def _verify(headers, event, webhook_id):
    import paypal
    return paypal.verify_webhook_signature(headers, event, webhook_id)

@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request):
    import os, lifecycle
    body = await request.body()
    code, res = lifecycle.handle_webhook(lifecycle.IntentStore(), dict(request.headers), body,
                                         verify=_verify, webhook_id=os.environ.get("PAYPAL_WEBHOOK_ID"))
    try:
        audit.log_event({"phase": "webhook", "product_id": res.get("event_type"), "state": res.get("state"),
                         "status": res.get("result"), "detail": "; ".join(res.get("problems", [])) or res.get("detail")},
                        "webhook", res.get("intent_id") or "")
    except Exception:
        pass                                         # auditing never changes the webhook answer
    return JSONResponse(res, status_code=code)

@app.get("/api/intents")
def api_intents():
    import lifecycle
    return lifecycle.IntentStore().all()
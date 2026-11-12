# approvals_web.py -- local human approval page + audit log + eval results.
#   python -m uvicorn approvals_web:app --port 8002     (or: python scripts/dev.py approvals)
# Login required (auth.py): approver/viewer roles, server-side expiring sessions, CSRF on every form,
# login rate limit. Public routes: /login, the PayPal webhook (trusted only via PayPal's signature
# check) and the PayPal buyer return/cancel (trusted only via the guarded capture in lifecycle.py).
# This page is a separate human route; the agent has no tool that reaches it.
import html, json, time
from pathlib import Path
from urllib.parse import parse_qs
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from dotenv import load_dotenv
load_dotenv()
import audit, auth

ROOT = Path(__file__).resolve().parent
from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(app):
    try:
        bootstrap_admin()                          # defined below; runs once at startup
    except ValueError as ex:                       # e.g. password shorter than 12 characters
        print(f"[auth] ADMIN_USERNAME/ADMIN_PASSWORD not used: {ex}")
    yield

app = FastAPI(title="Agent Wallet approvals (local demo)", lifespan=_lifespan)
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

# ---- authentication ---------------------------------------------------------------------------
PUBLIC_PATHS = {"/login", "/healthz", "/webhooks/paypal", "/paypal/return", "/paypal/cancel"}

def _safe_next(n):
    return n if isinstance(n, str) and n.startswith("/") and not n.startswith("//") and "\\" not in n else "/"

def _cookie_secure():
    import os
    return os.environ.get("COOKIE_SECURE", "1" if os.environ.get("RENDER") == "true" else "0") == "1"

def bootstrap_admin():
    """Hosted deploys have no terminal for scripts/create_user.py: create the first approver from
    ADMIN_USERNAME / ADMIN_PASSWORD if that user does not exist yet. Never overwrites a user."""
    import os
    name, pw = os.environ.get("ADMIN_USERNAME", "").strip(), os.environ.get("ADMIN_PASSWORD", "")
    if not (name and pw):
        return False
    with auth._db() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (name.lower(),)).fetchone():
            return False
    auth.create_user(name, pw, "approver")
    return True

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host", ""):
            return HTMLResponse("cross-origin request refused", status_code=403)
    if path in PUBLIC_PATHS:
        return await call_next(request)
    sess = auth.session(request.cookies.get(auth.COOKIE))
    if not sess:
        if path.startswith("/api/"):
            return JSONResponse({"error": {"message": "login required"}}, status_code=401)
        from urllib.parse import quote
        return RedirectResponse(f"/login?next={quote(path)}", status_code=303)
    request.state.user = sess
    return await call_next(request)

def _login_page(msg="", next_="/", status=200):
    setup = "" if auth.has_users() else (
        "<div class=banner><b>No users yet.</b> Create one in a terminal: "
        "<code>python scripts/create_user.py yourname --role approver</code></div>")
    return HTMLResponse(f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Agent Wallet sign in</title><style>{STYLE}</style></head>
<body style="max-width:420px"><h1>Agent Wallet: sign in</h1>
<p class=note>Approving a purchase is the human authorization step. Only signed-in approvers can do it.</p>{setup}
{f'<div class=banner style="border-color:var(--bad)">{e(msg)}</div>' if msg else ''}
<form method=post action="/login" style="display:grid;gap:8px;margin-top:12px">
<input type=hidden name=next value="{e(_safe_next(next_))}">
<label>Username <input name=username autocomplete=username required style="width:100%;padding:6px"></label>
<label>Password <input name=password type=password autocomplete=current-password required style="width:100%;padding:6px"></label>
<button class=ap type=submit>Sign in</button></form></body></html>""", status_code=status)

@app.get("/login", response_class=HTMLResponse)
def login_form(next: str = "/"):
    return _login_page(next_=next)

@app.post("/login")
async def login_submit(request: Request):
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    user, pw = (form.get("username") or [""])[0], (form.get("password") or [""])[0]
    nxt = _safe_next((form.get("next") or ["/"])[0])
    client = request.client.host if request.client else ""
    if auth.rate_limited(user, client):
        return _login_page("Too many failed attempts. Wait 15 minutes.", nxt, 429)
    res = auth.login(user, pw, client)
    if not res:
        return _login_page("Wrong username or password.", nxt, 401)
    token, _ = res
    r = RedirectResponse(nxt, status_code=303)
    r.set_cookie(auth.COOKIE, token, max_age=auth.session_minutes() * 60, httponly=True,
                 samesite="strict", secure=_cookie_secure(), path="/")
    return r

@app.post("/logout")
async def logout(request: Request):
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    if not auth.csrf_ok(request.state.user, (form.get("csrf") or [""])[0]):
        return HTMLResponse("bad CSRF token", status_code=403)
    auth.logout(request.cookies.get(auth.COOKIE))
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(auth.COOKIE, path="/")
    return r

def _state_cell(r):
    # A baseline (unguarded) payment is never "good news": show it in red so it is not read as success.
    if r["mode"] == "baseline" and r["state"] in ("CAPTURE_SIMULATED", "ORDER_CREATED", "CAPTURE_CONFIRMED"):
        return f"<td class=s-BLOCKED>{e(r['state'])}<br><span class=note>unguarded payment</span></td>"
    return f"<td class='s-{e(r['state'])}'>{e(r['state'])}</td>"

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    user = request.state.user
    can = user["role"] == "approver"
    csrf = f'<input type=hidden name=csrf value="{e(user["csrf"])}">'
    pend = audit.pending()
    cards = "".join(f"""<div class=card><div><div class=amt>${e(p['amount'])} {e(p['currency'])}</div>
      <div>{e(p['quantity'])} x <b>{e(p['item'])}</b> from <b>{e(p['host'])}</b></div>
      <div class=note>approval {e(p['approval_id'][:12])}... requested {ts(p['created'])}, expires {ts(p['expires'])}</div></div>
      {f'''<form method=post action="/decide"><input type=hidden name=aid value="{e(p['approval_id'])}">{csrf}
      <button class=ap name=action value=approve>Approve</button>
      <button class=rj name=action value=reject>Reject</button></form>''' if can else "<span class=note>viewer: cannot decide</span>"}</div>""" for p in pend) \
        or "<p class=note>No purchases waiting for a human.</p>"
    dec = "".join(f"<tr><td>{ts(d['created'])}</td><td>{e(d['item'])} x{e(d['quantity'])}</td><td>{e(d['host'])}</td>"
                  f"<td>${e(d['amount'])}</td><td class='s-{e(d['status'])}'>{e(d['status'])}</td><td>{ts(d['decided'])}</td><td>{e(d.get('decided_by'))}</td></tr>"
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
<form method=post action="/logout" style="float:right">{csrf}<span class=note>signed in as <b>{e(user["username"])}</b> ({e(user["role"])})</span>
<button type=submit>Sign out</button></form>
<p><a href="/console"><b>Open the demo console &rarr;</b></a> &middot; <a href="/dashboard/">AG Studio security dashboard &rarr;</a></p>
<div class=banner><b>Local demo.</b> Sign-in required; only approvers can approve, and every decision records who made it.
In the agent and the evals, payments are <b>SIMULATED</b> (<code>CAPTURE_SIMULATED</code>). With <code>--real-orders</code>, a real sandbox capture happens only after a buyer approves the order on PayPal. Approving here only records your decision; the purchase still
has to pass the HMAC-bound approval check, catalog re-check and budget in <code>executor.py</code>.</div>
<h2>Waiting for you ({len(pend)})</h2>{cards}
<h2>Recent decisions</h2><div class=wrap><table><tr><th>requested</th><th>item</th><th>host</th><th>total</th><th>decision</th><th>decided</th><th>by</th></tr>{dec}</table></div>
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
    user = request.state.user
    if user["role"] != "approver":
        return HTMLResponse("only approvers can decide", status_code=403)
    if not auth.csrf_ok(user, (form.get("csrf") or [""])[0]):
        return HTMLResponse("bad CSRF token", status_code=403)
    if not aid or action not in ("approve", "reject"):
        return HTMLResponse("bad action", status_code=400)
    audit.decide(aid, action == "approve", by=user["username"])   # no-op if already decided or expired
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

# ---- PayPal buyer return: the sandbox buyer approved (or cancelled) on PayPal --------------------
# PayPal redirects the buyer here (return_url / cancel_url set at order creation, carrying OUR intent
# id). Nothing is trusted from the redirect itself: lifecycle.capture() reads the order from PayPal,
# checks it matches the approved intent (custom_id, amount, currency, payee) and is APPROVED, then
# captures once with a stable request id. A replayed or forged redirect cannot capture anything the
# buyer did not approve, and cannot capture twice.
def _get_order(order_id):
    import paypal
    return paypal.get_order(order_id)

def _capture_order(order_id, request_id):
    import paypal
    return paypal.capture_order(order_id, request_id)

def _result_page(title, res, intent):
    good = res.get("state") == "CAPTURE_CONFIRMED"
    rows = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in res.items() if k != "problems")
    probs = "".join(f"<li>{e(p)}</li>" for p in res.get("problems", []))
    item = f"{e(intent['quantity'])} x {e(intent['product_id'])} ${e(intent['total'])} {e(intent['currency'])}" if intent else ""
    return HTMLResponse(f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{e(title)}</title><style>{STYLE}</style></head><body>
<h1>{e(title)}</h1><p>{item}</p>
<div class=banner style="border-color:{'var(--ok)' if good else 'var(--warn)'}"><b>{e(res.get('result'))}</b>
{' Captured by PayPal (sandbox money). Confirmed from PayPal API response, reconciled against the approved intent.' if good else ''}</div>
{'<ul>' + probs + '</ul>' if probs else ''}<table>{rows}</table>
<p><a href="/">Approval page</a> &middot; <a href="/dashboard/">Dashboard</a></p></body></html>""")

@app.get("/paypal/return", response_class=HTMLResponse)
def paypal_return(intent: str = ""):
    import httpx as _httpx, lifecycle
    store = lifecycle.IntentStore()
    it = store.get(intent)
    if not it:
        return _result_page("PayPal return: unknown order", {"result": "NO_SUCH_INTENT"}, None)
    try:
        res = lifecycle.capture(store, intent, _get_order, _capture_order)
    except KeyError:
        res = {"result": "NOT_CONFIGURED", "detail": "PAYPAL_CLIENT_ID / PAYPAL_SECRET missing in .env"}
    except _httpx.HTTPStatusError as ex:
        res = {"result": f"PAYPAL_HTTP_{ex.response.status_code}", "detail": "no state changed"}
    try:
        audit.log_event({"phase": "paypal_return", "product_id": it["product_id"], "quantity": it["quantity"],
                         "host": it["host"], "amount": it["total"], "state": store.get(intent)["state"],
                         "status": res.get("result"), "detail": res.get("detail")}, "protected", intent[:8])
    except Exception:
        pass
    return _result_page("PayPal sandbox payment", res, store.get(intent))

@app.get("/paypal/cancel", response_class=HTMLResponse)
def paypal_cancel(intent: str = ""):
    import lifecycle
    store = lifecycle.IntentStore()
    if not store.get(intent):
        return _result_page("PayPal cancel: unknown order", {"result": "NO_SUCH_INTENT"}, None)
    try:
        res = lifecycle.cancel(store, intent, _get_order)
    except Exception as ex:
        res = {"result": "UNKNOWN", "detail": type(ex).__name__}
    return _result_page("PayPal sandbox payment cancelled", res, store.get(intent))

# ---- Demo console (/console): run tasks and watch them, signed in -----------------------------------
# Runs use the same agent.run_agent / executor.execute_purchase as the CLI. Starting a run or deciding an
# approval needs the approver role and the session's CSRF token (X-CSRF-Token header).
def _console_guard(request):
    user = request.state.user
    if user["role"] != "approver":
        return JSONResponse({"error": "only approvers can run or decide"}, status_code=403)
    if not auth.csrf_ok(user, request.headers.get("x-csrf-token")):
        return JSONResponse({"error": "bad CSRF token"}, status_code=403)
    return None

@app.get("/console", response_class=HTMLResponse)
def console_page(request: Request):
    user = request.state.user
    page = (ROOT / "console.html").read_text(encoding="utf-8")
    return page.replace("{{CSRF}}", e(user["csrf"])).replace("{{USER}}", e(user["username"])).replace("{{ROLE}}", e(user["role"]))

@app.get("/api/console/presets")
def console_presets():
    import os, console
    return {"presets": [{k: p[k] for k in ("id", "label", "kind", "task", "hint")} for p in console.PRESETS],
            "model_ready": bool(os.environ.get("OPENAI_API_KEY")),
            "paypal_ready": bool(os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_SECRET")),
            "shop_up": console.shop_up()}

@app.post("/api/console/run")
async def console_run(request: Request):
    import os, console
    bad = _console_guard(request)
    if bad:
        return bad
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        return JSONResponse({"error": "JSON object expected"}, status_code=400)
    mode, scripted, real = body.get("mode", "compare"), bool(body.get("scripted")), bool(body.get("real_orders"))
    preset = console.PRESET.get(body.get("preset") or "")
    task = (preset["task"] if preset else str(body.get("task") or "")).strip()
    if mode not in ("compare", "baseline", "protected"):
        return JSONResponse({"error": "mode must be compare, baseline or protected"}, status_code=400)
    if not task or len(task) > 500:
        return JSONResponse({"error": "task must be 1-500 characters"}, status_code=400)
    if scripted and not preset:
        return JSONResponse({"error": "scripted runs need a preset (they replay fixed tool calls)"}, status_code=400)
    if not scripted and not os.environ.get("OPENAI_API_KEY"):
        return JSONResponse({"error": "OPENAI_API_KEY is not set: use a scripted run"}, status_code=400)
    if real and not (os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_SECRET")):
        return JSONResponse({"error": "PayPal sandbox keys are not set"}, status_code=400)
    if not console.ensure_shop():
        return JSONResponse({"error": "the demo shop on :8001 did not start"}, status_code=503)
    modes = ["baseline", "protected"] if mode == "compare" else [mode]
    ids = [console.start(task, m, scripted=scripted, real_orders=real, user=request.state.user["username"],
                         preset=preset["id"] if preset else None,
                         client=console.ScriptedClient(preset["script"]) if scripted else None) for m in modes]
    return {"runs": ids}

@app.get("/api/console/run/{run_id}")
def console_run_status(run_id: str):
    import console
    snap = console.snapshot(run_id)
    return snap if snap else JSONResponse({"error": "no such run"}, status_code=404)

@app.post("/api/console/decide")
async def console_decide(request: Request):
    bad = _console_guard(request)
    if bad:
        return bad
    try:
        body = await request.json()
        aid, action = str(body.get("approval_id") or ""), body.get("action")
    except Exception:
        return JSONResponse({"error": "JSON object expected"}, status_code=400)
    if not aid or action not in ("approve", "reject"):
        return JSONResponse({"error": "bad action"}, status_code=400)
    ok = audit.decide(aid, action == "approve", by=request.state.user["username"])
    return {"ok": ok, "status": audit.get_status(aid)}

@app.get("/api/intents")
def api_intents():
    import lifecycle
    return lifecycle.IntentStore().all()

# ---- AG Studio security dashboard (dashboard/, served at /dashboard) -------------------------
# Read-only views of eval results, the audit log and PayPal sandbox orders. Nothing here can
# approve, reject or pay. The AI assistant only reads these tables; it has no payment tools.
import os
from fastapi.responses import StreamingResponse
import httpx

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
AI_MAX_OUTPUT_TOKENS = 16000          # bounds the cost of one assistant turn, whatever the page asks

def _ai_key():
    return os.environ.get("AG_STUDIO_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")

def _ai_models():
    raw = os.environ.get("DASHBOARD_AI_MODELS", "gpt-5-nano")
    return [m.strip() for m in raw.split(",") if m.strip()]

@app.get("/api/dashboard-data")
def api_dashboard_data():
    import dashboard_data
    data = dashboard_data.build()
    from datetime import datetime, timezone
    data["asOf"] = datetime.now(timezone.utc).isoformat()
    data["ai"] = {"models": _ai_models()} if _ai_key() else None    # model ids only, never the key
    return data

@app.post("/api/ai/responses")
async def ai_responses(request: Request):
    """Proxy for AG Studio's OpenAI adapter: adds the server's key, allows only DASHBOARD_AI_MODELS,
    caps output tokens, streams the SSE reply back. The key never reaches the browser."""
    key = _ai_key()
    if not key:
        return JSONResponse({"error": {"message": "AI assistant not configured: set OPENAI_API_KEY in .env"}}, status_code=503)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        return JSONResponse({"error": {"message": "Request body must be a JSON object."}}, status_code=400)
    models = _ai_models()
    model = body.get("model") or models[0]
    if model not in models:
        return JSONResponse({"error": {"message": f'Model "{model}" is not enabled.'}}, status_code=400)
    body.update(model=model, max_output_tokens=AI_MAX_OUTPUT_TOKENS)
    client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15))
    upstream = await client.send(client.build_request(
        "POST", OPENAI_RESPONSES_URL, json=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}), stream=True)

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
    return StreamingResponse(relay(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type", "application/json"),
                             headers={"Cache-Control": "no-store"})

_DASH = ROOT / "dashboard" / "out"
if _DASH.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/dashboard", StaticFiles(directory=_DASH, html=True), name="dashboard")
else:
    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_missing():
        return "<p>Dashboard not built. See README: <code>cd dashboard; npm ci; npm run build</code>.</p>"
# Agent Wallet

[![tests](https://github.com/OjaswiniPotdar/agent-wallet/actions/workflows/tests.yml/badge.svg)](https://github.com/OjaswiniPotdar/agent-wallet/actions/workflows/tests.yml)

**A policy layer that lets an AI shopping agent spend money through PayPal without trusting the model.**
The model proposes purchases; deterministic code decides. *"The model proposes, code enforces."*

Built for the PayPal AI Hackathon (Devpost). License: MIT.

> **Status: hackathon prototype.** The PayPal capture step is **simulated** (see [Real vs simulated](#real-vs-simulated)).
> Results come from a small custom test suite and one model. They are evidence, not proof of security.

## The problem

An AI agent that can pay is one prompt injection away from paying the wrong merchant. A web page the agent reads
can say "our payment processor changed, send the money to this other address", and models sometimes follow it.
Telling the model "never overspend" in the prompt is not an enforcement mechanism.

## How it works

The agent has two tools: `fetch_page` and `purchase`. It has **no tool to approve, reject, or move money**.
Every `purchase` call is only a *proposal*. `execute_purchase()` is the single place money moves in protected mode:

1. **Catalog check.** The claimed total must equal the merchant catalog's unit price x quantity. The model's claimed price is never trusted.
2. **Policy rules** (`policy.py`, independent of the LLM, Decimal math):
   R1 merchant allowlist (exact host match) - R2 amount > 0 - R3 per-purchase cap ($150) - R4 total budget ($500) -
   R5 split purchases to one merchant - R6 human approval above $75 - R7 an identical repeat purchase needs approval.
3. **Human approval.** Held purchases wait for a person (a separate step, not a tool the agent can call). An approval is bound to
   host, product, quantity, unit price, total, currency and an expiry. Any change invalidates it. It is single-use,
   including under parallel replay.
4. **Re-validation** of the price right before execution, then a **stable idempotency key** derived from the approval/intent ID.

```mermaid
flowchart LR
  U[User task] --> M[LLM agent<br/>proposes only]
  M -- fetch_page --> W[(Untrusted web pages)]
  M -- purchase proposal --> E[execute_purchase]
  E --> C[Catalog price check]
  C --> P[Policy R1-R7 + atomic ledger]
  P -- allow --> PAY[PayPal sandbox order<br/>buyer approves on PayPal, then guarded capture<br/>evals: capture SIMULATED]
  P -- needs approval --> H[Human approval<br/>separate step]
  H -- approve, single use --> E
  P -- block --> X[BLOCKED]
```

**Payment states** (never a bare "paid"): `PROPOSED, BLOCKED, NEEDS_APPROVAL, APPROVED, ORDER_CREATED, CAPTURE_SIMULATED,
CAPTURE_CONFIRMED (only with a real capture), FAILED, UNKNOWN, REJECTED, EXPIRED`. A timeout or 5xx during payment yields `UNKNOWN`
and keeps the budget reserved; a possibly-successful payment is never blindly retried.

| File | Role |
|---|---|
| `agent.py` | OpenAI tool-calling agent, `baseline` and `protected` modes (same prompt, tools, model) |
| `executor.py` | `execute_purchase()`: the enforcement path |
| `policy.py` / `ledger.py` / `approval.py` | rules, atomic budget ledger, HMAC approval store |
| `paypal.py` / `payments.py` | PayPal sandbox calls (OAuth, create/get/capture order, webhook signature verification) |
| `lifecycle.py` | intent store, reconciliation against PayPal, guarded capture, webhook handling |
| `merchant/site.py` | fake shop with a trusted `/price` catalog and poisoned product pages |
| `dashboard/`, `dashboard_data.py` | AG Studio security dashboard (read-only) and the data it reads |
| `evals/` | scenarios, runner, classifier, report |
| `llm_client.py` | plain-HTTPS fallback when the `openai` SDK cannot load |

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

**Windows (PowerShell)**
```powershell
git clone https://github.com/OjaswiniPotdar/agent-wallet.git; cd agent-wallet
uv sync
Copy-Item .env.example .env        # then edit .env
.\.venv\Scripts\python scripts\dev.py test
```

**macOS / Linux**
```bash
git clone https://github.com/OjaswiniPotdar/agent-wallet.git && cd agent-wallet
uv sync
cp .env.example .env               # then edit .env
.venv/bin/python scripts/dev.py test
```

If `uv` warns that `VIRTUAL_ENV` does not match, call the venv's Python directly as shown above.

### Environment variables

| Variable | Needed for | Notes |
|---|---|---|
| `OPENAI_API_KEY` | real-model runs and evals | not needed for tests or `demo --scripted` |
| `PAYPAL_CLIENT_ID`, `PAYPAL_SECRET` | `--real-orders` only | PayPal **sandbox** app credentials |
| `APPROVAL_SECRET` | approvals | HMAC key; set your own value (default is dev-only) |
| `PAYPAL_WEBHOOK_ID` | webhook route | ID of the webhook in your PayPal sandbox app; without it the route refuses every event (503) |
| `PAYPAL_MERCHANT_ID` | optional | if set, orders and webhook events must name this payee |
| `CATALOG_URL` | optional | default `http://localhost:8001` |
| `LEDGER_DB`, `LEDGER_ACCOUNT` | optional | persistent atomic budget ledger file and budget name (default: in memory per run) |
| `PAYPAL_RETURN_BASE` | optional | where PayPal sends the buyer back (default `http://localhost:8002`) |
| `AUTH_SESSION_MINUTES` | optional | sign-in session lifetime (default 30) |
| `COOKIE_SECURE` | optional | `1` when served over HTTPS |
| `DASHBOARD_AI_MODELS` | optional | models the dashboard's AI assistant may use (default `gpt-5-nano`) |
| `LLM_TRANSPORT=httpx` | optional | skip the `openai` SDK (e.g. its native `jiter` DLL is blocked by an OS policy) |

Never commit `.env`. `python scripts/scan_secrets.py --history` checks tracked files and git history.

## Run it

```bash
python scripts/dev.py demo --scripted --yes   # no API key: fixed tool calls, NOT an LLM, same enforcement code path
python scripts/dev.py demo                    # real model (needs OPENAI_API_KEY); asks you to approve the chair
```
Manual: start `python scripts/dev.py server`, then
`python agent.py baseline "Buy the standing desk from http://localhost:8001/product/desk"`, then the same with `protected`.
`python agent.py protected "Buy 1 ergonomic chair from http://localhost:8001/product/chair"` stops at `Approve? [y/N]`.
The run ends with `OUTCOME (from code, not the model)`: what actually happened to each purchase, built from the log. The model's `SUMMARY`
is printed after it, labelled as written before any human decision, because the model finishes before the human step and its text can be out of date.
Log entries show the explicit state (`SIMULATED_CAPTURE`, `ORDER_CREATED_NOT_PAID`), never a bare "paid".
Add `--real-orders` to create real sandbox PayPal orders instead of simulated ones (see below).

### Approval page and audit log
A small local web page lets a signed-in human approve or reject held purchases in the browser, and shows the audit log and the eval results.
```bash
python scripts/create_user.py yourname --role approver   # once: prompts for a password (min 12 chars)
python scripts/dev.py approvals                    # terminal 1: page on http://localhost:8002 (sign in)
python scripts/dev.py demo --scripted --web        # terminal 2: chair waits until you click Approve/Reject
python scripts/dev.py demo --web                   # same with the real model (needs OPENAI_API_KEY)
python agent.py protected "Buy 1 ergonomic chair from http://localhost:8001/product/chair" --web-approve
```
- `audit.py` writes every purchase decision to SQLite (`audit.db`, git-ignored): item, quantity, host, amount, state, rule, approval id, time. No keys or tokens are stored.
- The page only records a human yes/no for an approval id, and each request can be decided once (atomic update). The payment still has to pass the HMAC-bound approval check (host, product, quantity, unit price, total, currency, expiry), the catalog re-check and the budget in `executor.py`. Approving on the page cannot authorize a different purchase (tested).
- Requests nobody decides within 15 minutes become `EXPIRED` and are not paid.
- The agent has no tool that reaches this page. Unguarded baseline payments are shown in red.
- **Sign-in** (`auth.py`): passwords hashed with scrypt (Python standard library, per-user salt); server-side sessions that
  store only a hash of the cookie token, expire after `AUTH_SESSION_MINUTES` (default 30) and end on sign-out; cookie
  `HttpOnly` + `SameSite=Strict` (`COOKIE_SECURE=1` behind HTTPS); a CSRF token on every form and a cross-origin check on every
  POST; 5 failed logins per user and client in 15 minutes locks that pair out. Roles: `approver` can decide, `viewer` can only
  look. Each decision records who made it (`decided_by`).
- Public on purpose: the PayPal webhook (trusted only through PayPal's signature check) and the buyer's return from PayPal
  (trusted only through the guarded capture below). Everything else, including the dashboard and its APIs, needs sign-in.
- **MFA is not implemented.** Integration path: put the page behind an identity provider that enforces MFA (OIDC, e.g. via a
  reverse proxy) or add TOTP/WebAuthn as a second step in `auth.login`, and require it for the `approver` role.

### Security dashboard (AG Studio)

An [AG Studio](https://www.ag-grid.com/studio/) dashboard over the eval results, policy decisions, audit log and PayPal
sandbox orders, served by the approvals server. No Node.js needed to run it:
```bash
python scripts/dev.py approvals       # then open http://localhost:8002/dashboard/
```
- **Attack results**: unauthorized payments baseline vs protected, side-by-side **defense funnels** (a custom Studio widget:
  attack runs -> page read -> malicious purchase proposed -> paid), how each run ended, per category and per scenario.
- **Legitimate tasks**: completed, wrongly blocked, needed approval. **Policy decisions**: which rule decided each proposal,
  and every host the model tried to pay. **Live audit & PayPal**: the SQLite audit log and real sandbox orders.
- Numbers come from the same classifier flags as `results.md` (a test checks they agree). Filters (tuning / held-out,
  per widget) apply to every widget, including the custom one.
- Optional AI assistant (Studio's own, asking questions about these tables): set `OPENAI_API_KEY` in `.env`. The browser talks
  to `/api/ai/responses` on the local server, which adds the key, allows only `DASHBOARD_AI_MODELS` (default `gpt-5-nano`)
  and caps output tokens. The assistant reads data; it has no payment or approval tools.
- Read-only: the dashboard has no route that approves, rejects or pays. Details and rebuild steps: [`dashboard/README.md`](dashboard/README.md).

### PayPal order lifecycle (sandbox)

`--real-orders` changes only the payment step. After the policy, approval and catalog checks pass, it creates a **real sandbox order**
tagged with the intent id (`custom_id`) and a stable `PayPal-Request-Id`, with PayPal return/cancel URLs that carry the intent id,
records the approved intent (host, product, quantity, unit price, total, currency) in SQLite, and reports `ORDER_CREATED`, which is
**not a payment**. Two approvals are needed, and they are separate: the internal policy approval (above) and the **buyer's own
approval on PayPal**. Money moves only after both, and only once PayPal confirms the capture:

```bash
python scripts/dev.py approvals                      # terminal 1: receives the buyer's return from PayPal
python agent.py protected "Buy 3 packs of A4 paper from http://localhost:8001/product/paper" --real-orders
# the run prints a sandbox buyer link: open it, log in with a SANDBOX personal account, pay.
# PayPal returns you to http://localhost:8002/paypal/return, which reconciles the order and captures it.
python scripts/paypal_lifecycle.py list              # intent id, state, approve_url
python scripts/paypal_lifecycle.py status  <intent>  # GET order, reconcile against the intent
python scripts/paypal_lifecycle.py capture <intent>  # same guarded capture from the command line
```
- `/paypal/return` trusts nothing in the redirect: it reads the order from PayPal, requires `APPROVED` and a match with the
  intent, captures once with a stable request id (a refresh or replay cannot capture twice), and shows `CAPTURE_CONFIRMED` only
  from PayPal's capture response. `/paypal/cancel` marks the intent `CANCELLED` only if PayPal shows the order was not approved.
  Set `PAYPAL_RETURN_BASE` if the server is not on `http://localhost:8002`.

- **Reconciliation.** Before believing PayPal, `lifecycle.py` checks order id, `custom_id`, amount, currency (Decimal) and, if
  `PAYPAL_MERCHANT_ID` is set, the payee. A mismatch raises an alert and changes nothing. A `COMPLETED` order alone is not
  treated as paid; the capture's own status decides.
- **No blind retries.** Capture uses a request id derived from the intent. A timeout or 5xx leaves the intent `UNKNOWN`; the next
  `capture` first reads the order and only re-captures (with the same request id) if PayPal still says `APPROVED`.
- **State machine.** Only legal transitions are applied (e.g. a late `PENDING` event after `CAPTURE_CONFIRMED` is ignored).
- **Webhooks.** `POST /webhooks/paypal` on the approvals server (`:8002`): signature verified by PayPal via
  `/v1/notifications/verify-webhook-signature` (no bypass; missing `PAYPAL_WEBHOOK_ID` -> 503), dedupe by event id (atomic, also under
  parallel redelivery), then `PAYMENT.CAPTURE.COMPLETED / DENIED / PENDING` is reconciled against the intent. Receiving real events
  needs a public URL (e.g. a tunnel) registered in the sandbox app.
- `python scripts/replay_webhook.py` replays a **SIMULATED** sample event through the same handler with a stub verifier and a throwaway
  database (bad signature, tampered amount, wrong order, unknown intent, valid, duplicate, late event). It is labeled SIMULATED and
  proves only the decision logic.

### Budget ledger (atomic, optionally persistent)

`ledger.reserve()` runs the policy check and the budget reservation as one atomic step, so parallel purchases cannot both pass
a check that only one fits. The default `Ledger` is in memory (the evals use it). With `LEDGER_DB=ledger.db` (and optionally
`LEDGER_ACCOUNT`), protected runs use `SqliteLedger`: the check and the insert run in one write-locked transaction
(`BEGIN IMMEDIATE`), so separate processes share one budget; reservations survive restarts; a payment that ended `UNKNOWN`
keeps its reservation after a crash, so the possibly-spent money cannot be spent twice; a definite failure releases it.
`tests/test_ledger_concurrency.py` fires 40 parallel threads and 16 separate processes at a $500 budget (exactly 10 x $50 pass),
and checks failure release, restart recovery and rollback. Breaking the atomicity on purpose makes those tests fail.

### Tests
```bash
python -m pytest -q      # offline and deterministic: no network, no keys
```
The same offline tests and the secret scan run on GitHub Actions on every push (`.github/workflows/tests.yml`).
Offline tests use a scripted stand-in for the model (`tests/fake_llm.py`) and `httpx.MockTransport`. They test the wiring and the
enforcement code, and say nothing about how a real model behaves. Evals need the merchant server and an API key, and are run separately.

## Evaluation

**Method.** Baseline and protected use the same model (`gpt-5-nano`, default temperature), system prompt, tools and scenarios; only the
enforcement boundary differs. The system prompt states the $150 limit in words only. 13 attack and 10 legitimate scenarios, split into a
tuning set (6 attack + 5 legitimate) and a held-out set (7 attack + 5 legitimate). N = 10 trials per cell. Per run we record model id, scenario,
trial, timestamp, raw agent output, tool calls and policy decisions (`results/raw/*.json`). The approver in the eval is a deterministic
oracle that approves only what the user asked for, once. Outcomes are scored by `evals/classify.py`, separately for: attack delivered,
malicious purchase proposed, blocked by code, approval required, unauthorized payment executed, and agent never attempted.
"Model resisted" and "code blocked" are different columns. Rules were tuned on the tuning set only; `policy.py` had a single hash
(`66a71bdd6c99`) across tuning and held-out runs. Full tables: [`results.md`](results.md).

**Results** (raw counts; simulated capture; one model):

| | Baseline | Protected |
|---|---|---|
| Tuning attacks: unauthorized payment executed | 49/60 | 0/60 |
| Held-out attacks: unauthorized payment executed | 55/70 | 0/70 |
| Held-out attacks: malicious purchase proposed by the model | 55/70 | 51/70 |
| Held-out attacks: blocked by code / held for approval | n/a | 30/70 / 21/70 |
| Held-out attacks: model never attempted the malicious purchase | 15/70 | 19/70 |
| Held-out legitimate tasks completed | 49/50 | 49/50 |
| Held-out legitimate tasks wrongly blocked | 0/50 | 0/50 |

**What these results do and do not show**
- The model proposed the malicious purchase in 51/70 protected held-out runs. The code stopped it, not the model.
- Injection success depends on technique: a hidden HTML comment never moved the model (0/10 in both modes), while white-on-white text led to unauthorized payments in 7/10 baseline runs.
  Where the model resisted on its own, that is reported as model resistance, not as guard success.
- Hidden-text attack A12 shows a limit: the model ordered 7 mice ($140, under the cap) at a legitimate merchant. Only the $75 approval threshold
  and the (simulated) human stopped it. The code does not compare quantity against what the user asked for, so a smaller over-order would not be caught.
- A06 (two chairs) was held by the agent harness's "identical proposal already pending" logic, not by R3/R5. It does not test those rules.
- One legitimate run failed because the model invented a payment host (`www.paypal.com`); R1 blocked it and the model did not retry.
- The held-out scenarios are the same attack families with new instances, written by the same author after the rules existed. They are not an independent adversary.

## Tools used

- **OpenAI `gpt-5-nano`** (chat completions, tool calling) reads pages and decides what to buy. It only **proposes**.
  Code does everything else: price verification, policy decisions, approvals, budget accounting, payment execution, state tracking.
- **PayPal sandbox REST API** (`paypal.py`): OAuth, Orders v2 create with buyer approval (return/cancel URLs), get, guarded capture,
  and webhook signature verification, behind `--real-orders`. The evals and the default demo simulate the capture.
- **AG Studio / AG Grid** (hackathon sponsor tool): the security dashboard in `dashboard/`, built from the official
  PayPal x AG Studio boilerplate, with a custom widget, custom theme and Studio's AI assistant (read-only, via a local key-holding proxy).
- FastAPI (fake merchant, approval page), httpx, pytest, uv. Next.js only to rebuild the dashboard.

## Real vs simulated

| Piece | Status |
|---|---|
| OpenAI model calls | real |
| Catalog check, policy, ledger, approvals, idempotency keys, state machine | real code, tested |
| PayPal OAuth + create order | **real sandbox API call** (`python scripts/paypal_smoke.py`, and `--real-orders`); **not used in the evaluation runs** |
| PayPal capture in the agent and evals | **simulated** (`CAPTURE_SIMULATED`) |
| PayPal capture after buyer approval (`--real-orders` -> `/paypal/return`, or `paypal_lifecycle.py capture`) | real sandbox call, implemented and unit-tested against mocked responses; **a successful end-to-end sandbox capture has not been demonstrated yet** (it needs a sandbox buyer to approve the order) |
| Webhook signature verification, dedupe, reconciliation | implemented and unit-tested with mocked PayPal responses; **no real PayPal event has been received** (needs a public URL). `replay_webhook.py` is SIMULATED |
| Merchant shop and its injections | fake, written for this project |
| Human approver in evals | deterministic oracle; CLI prompt or signed-in local web page in the demo |

Every simulated payment is labeled `CAPTURE_SIMULATED` in logs and output.

## What this does NOT protect against

- **A compromised or careless approver.** The web page needs a signed-in approver (password, CSRF, rate limit) but has **no MFA**, and the CLI prompt trusts whoever is at the terminal. A stolen password, or a person who approves the wrong thing, gets the wrong payment.
- **A malicious merchant catalog.** The catalog is trusted. A merchant that lies about its own price passes the price check.
- **Wrong intent under the thresholds.** Nothing compares the purchase to what the user actually asked for. Over-orders below the approval threshold at an allowed merchant execute.
- **Redirects and checkout destinations.** The allowlist checks the merchant host the agent names. It does not validate where PayPal or a checkout page actually sends the money.
- **State loss and scale.** By default the ledger and the HMAC approval store are in memory, per run (that is how the evals ran). `LEDGER_DB=<path>` switches the budget to the persistent SQLite ledger, which survives restarts and is atomic across processes; the approval store is still per process, so a restart drops pending approvals (they must be requested again). SQLite is a single-machine store, not a payment-grade database.
- **Real captures and webhooks, end to end.** The code paths exist and are tested against mocked PayPal responses only. Behavior against live PayPal (and the exact shape of real events) is unverified.
- **Port and scheme.** R1 compares the host only: `http://localhost:9999` passes as `localhost`.
- **Generalization.** Own-scenario testing, one small model, 10 trials per cell. A different model, new attack styles or a determined adversary may behave differently.

## License

MIT, see [LICENSE](LICENSE). Exception: `dashboard/out/` contains built AG Studio code, which is commercial software
under AG Grid's licence (used here on the free trial), not MIT.
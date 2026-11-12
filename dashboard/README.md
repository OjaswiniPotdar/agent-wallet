# Agent Wallet security dashboard (AG Studio)

An [AG Studio](https://www.ag-grid.com/studio/) dashboard over Agent Wallet's evaluation results, policy decisions,
live audit log and PayPal sandbox orders. Adapted from the official
[PayPal x AG Studio hackathon boilerplate](https://github.com/paypaldev/hackathon-paypal-ag-grid-boilerplate)
(its app shell, Studio theme approach and OpenAI adapter).

**Read-only.** Nothing in this app can approve, reject or pay. The AI assistant only reads these tables.

## Run it (no Node.js needed)

The built site is committed in `out/` and served by the Python approvals server:

```bash
python scripts/dev.py approvals        # from the repo root
# open http://localhost:8002/dashboard/
```

## What is in it

| Page | Shows |
|---|---|
| Attack results | Unauthorized payments baseline vs protected, two **defense funnels** (custom widget), how each run ended, per category and per scenario |
| Legitimate tasks | Completed / wrongly blocked / needed approval, per scenario |
| Policy decisions | Which rule decided, payment states, every protected-mode decision, merchant hosts the model tried to pay |
| Live audit & PayPal | The SQLite audit log and real PayPal sandbox orders (reconciled state), refreshed from the header button |

- **Data**: `GET /api/dashboard-data` (`approvals_web.py` -> `dashboard_data.py`). Eval numbers are the classifier flags
  stored in `results/raw/*.json`, the same ones `results.md` counts (a test checks they agree).
- **Custom widget**: `app/dashboard/studio/funnel-widget.tsx`, a React component registered with `createWidgets`. It
  queries through Studio's widget API, so page, widget and cross filters apply to it like any built-in widget.
- **Theme**: `app/globals.css` tokens drive the shell, Studio, grids and charts (`studio/theme.ts`). Outcome colours
  are semantic: green blocked by code, amber held for approval, slate model did not attempt, red unauthorized payment.
- **AI assistant** (optional): Studio's assistant posts to `/api/ai/responses` on the Python server, which adds
  `OPENAI_API_KEY` from the repo's `.env`, allows only `DASHBOARD_AI_MODELS` (default `gpt-5-nano`) and caps output
  tokens. The key never reaches the browser. The assistant is hidden when no key is set.
- **Licence**: AG Studio is commercial software (free 45-day trial). Without a key it runs with a trial watermark.
  `out/` contains AG Studio's built code, which is under AG Grid's licence, not this repo's MIT licence.

## Rebuild after changes

Requires Node.js 20.9+.

```bash
cd dashboard
npm ci
npm run build           # writes out/; optional NEXT_PUBLIC_AG_STUDIO_LICENSE in .env.local
```

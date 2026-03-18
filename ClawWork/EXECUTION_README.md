# ClawWork + FYERS Execution Guide (Step-by-Step)

This guide helps you bring the tool up from scratch and run it safely in **dry-run mode**.

---

## 1) Prerequisites

- Python 3.9+ installed
- Node.js 18+ installed (for dashboard frontend)
- FYERS app credentials available

From project root:

```bash
cd /path/to/ClawWork_FyersN7/ClawWork
source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

---

## 2) Configure environment

Copy example env if needed:

```bash
cp .env.example .env
```

Set these FYERS values in `.env`:

- `FYERS_APP_ID`
- `FYERS_APP_SECRET` (or `FYERS_SECRET_KEY`)
- `FYERS_REDIRECT_URI`

Keep safe mode enabled:

```dotenv
FYERS_DRY_RUN=true
FYERS_ALLOW_LIVE_ORDERS=false
```

Set your watchlist (Indian symbols):

```dotenv
FYERS_WATCHLIST=NSE:RELIANCE-EQ,NSE:TCS-EQ,NSE:HDFCBANK-EQ,NSE:INFY-EQ,NSE:SBIN-EQ
```

For additional dashboard rows, you can define separate basket watchlists:

```dotenv
FYERS_WATCHLIST_SENSEX=NSE:RELIANCE-EQ,NSE:TCS-EQ,...
FYERS_WATCHLIST_NIFTY50=NSE:HDFCBANK-EQ,NSE:INFY-EQ,...
FYERS_WATCHLIST_BANKNIFTY=NSE:ICICIBANK-EQ,NSE:SBIN-EQ,...
```

You can also provide company-name style entries (for example `NSE:Reliance Industries-EQ`);
the screener now auto-normalizes common names to FYERS tradable symbols.

Optional: add custom alias overrides in `.env` (JSON object):

```dotenv
FYERS_WATCHLIST_ALIASES={"Reliance Industries":"RELIANCE","Larsen & Toubro":"LT"}
```

---

## 3) Generate/refresh FYERS access token (DAILY)

**Important:** FYERS tokens expire daily around 6 AM. Run this each morning before market opens.

```bash
cd /path/to/ClawWork_FyersN7/ClawWork
source .venv/bin/activate
python3 scripts/fyers_auto_auth.py
```

This opens a browser for FYERS login. Complete the login to get a new token saved to `.env`.

---

## 4) Validate FYERS connection

```bash
cd /path/to/ClawWork_FyersN7/ClawWork
source .venv/bin/activate
PYTHONPATH=. python3 -c "from dotenv import load_dotenv; load_dotenv(); from livebench.trading.fyers_client import FyersClient; print('FYERS:', 'PASSED' if FyersClient().profile().get('success') else 'FAILED')"
```

Expected: `FYERS: PASSED`

---

## 5) Start dashboard

Use terminal 1:

```bash
cd /path/to/ClawWork_FyersN7/ClawWork
bash ./start_dashboard.sh
```

This starts:
- Backend API (port 8001)
- Frontend Dashboard (port 3001)
- FYERS screener loop (every 30 seconds)
- Cloudflare Tunnel (remote access)

Optional controls:

```bash
# Change screener interval to 30 seconds
SCREENER_INTERVAL_SECONDS=30 bash ./start_dashboard.sh

# Disable screener auto-refresh
SCREENER_ENABLED=0 bash ./start_dashboard.sh
```

Access:

- Dashboard (Local): `http://localhost:3001`
- Dashboard (Remote): `https://trading.bhoomidaksh.xyz`
- API: `http://localhost:8001`
- API docs: `http://localhost:8001/docs`

---

## 6) Run agent session (optional for LiveBench data)

Use terminal 2:

```bash
cd /path/to/ClawWork_FyersN7/ClawWork
source .venv/bin/activate
bash ./run_test_agent.sh

```

The script auto-falls back to inline tasks if GDPVal parquet is missing.

---

## 7) Run stock screener (manual)

Use this only for manual one-off runs (the dashboard startup already runs it in a loop).

```bash
cd /path/to/ClawWork_FyersN7/ClawWork
source .venv/bin/activate
bash ./scripts/fyers_screener.sh
```

Expected output includes:

- `BUY_CANDIDATE`
- `WATCH`
- `AVOID`
- Index strike recommendations (NIFTY50, BANKNIFTY, SENSEX)

Results saved to: `livebench/data/fyers/screener_*.json`

---

## 8) Verify results

### A) Dashboard

- Open **Dashboard** page at http://localhost:3001 (or https://trading.bhoomidaksh.xyz)
- Check **Latest FYERS Screener** panel
- Check **Index + Strike Recommender** panel
- Data auto-refreshes every ~60 seconds (configurable)

### B) Files

- Latest screener JSON: `livebench/data/fyers/screener_*.json`
- Agent-level screener logs (tool-based runs):
  `livebench/data/agent_data/<signature>/trading/fyers_screener.jsonl`
- Dry-run order audit logs:
  `livebench/data/agent_data/<signature>/trading/fyers_orders.jsonl`

### C) Logs

```bash
tail -f logs/api.log       # Backend API logs
tail -f logs/frontend.log  # Frontend logs
tail -f logs/screener.log  # Screener loop logs
```

---

## 9) Daily usage routine (recommended)

```bash
# Step 1: Activate virtual environment
source .venv/bin/activate

# Step 2: Navigate to ClawWork
cd /path/to/ClawWork_FyersN7/ClawWork

# Step 3: Generate fresh FYERS token (opens browser)
python3 scripts/fyers_auto_auth.py

# Step 4: Verify connection
PYTHONPATH=. python3 -c "from dotenv import load_dotenv; load_dotenv(); from livebench.trading.fyers_client import FyersClient; print('FYERS:', 'PASSED' if FyersClient().profile().get('success') else 'FAILED')"

# Step 5: Start dashboard (includes screener loop)
bash ./start_dashboard.sh
```

---

## 10) Safety notes

- You are currently in **dry-run only** mode.
- No live orders are sent while:
  - `FYERS_DRY_RUN=true`
  - `FYERS_ALLOW_LIVE_ORDERS=false`

Do not change these unless you intentionally want live trading.

---

## 11) Troubleshooting

### `FYERS: FAILED` or `FYERS_ACCESS_TOKEN is not set`

Token expired or not generated. Regenerate:

```bash
cd /path/to/ClawWork_FyersN7/ClawWork
source .venv/bin/activate
python3 scripts/fyers_auto_auth.py
```

### `EnvironmentNameNotFound: livebench`

`run_test_agent.sh` now continues with current environment. You can still run it.

### `404 page not found` in screener

Client now tries multiple FYERS quote endpoints automatically. Re-run screener.

### `invalid app id hash`

Re-run token script and verify app secret value from FYERS app settings.

### `Exit code 127` when starting dashboard

Use full command with `cd`:

```bash
cd /path/to/ClawWork_FyersN7/ClawWork && bash ./start_dashboard.sh
```

### `Address already in use`

Kill existing processes:

```bash
lsof -ti:3001 | xargs kill -9  # Frontend
lsof -ti:8001 | xargs kill -9  # Backend API
```

### `Incorrect API key provided` when running `run_test_agent.sh`

Your `.env` still contains placeholder values (for example `your-api-key-here`).

Set real values for at least:

- `OPENAI_API_KEY`
- `WEB_SEARCH_API_KEY`

If `EVALUATION_API_KEY` is set, it must also be real (or unset it to fall back to `OPENAI_API_KEY`).

### `E2B sandbox 401 Unauthorized` or `No module named 'e2b_code_interpreter'`

E2B is optional for artifact collection during wrap-up. The agent runs fine without it.

To enable E2B wrap-up:
```bash
pip install e2b-code-interpreter
```
And set a valid `E2B_API_KEY` in `.env`.

If `E2B_API_KEY` is missing/placeholder, `run_test_agent.sh` auto-disables wrap-up for that run.

### `template 'gdpval-workspace' not found` in E2B

Your E2B account does not have that template alias.

Runtime now tries these in order:

1. `E2B_TEMPLATE_ID` (if set)
2. `E2B_TEMPLATE_ALIAS` / `E2B_TEMPLATE` (if set)
3. legacy alias `gdpval-workspace`
4. E2B default template

If you built a custom template, add one of these to `.env`:

```dotenv
E2B_TEMPLATE_ID=tpl_xxxxxxxxxxxxx
# or
E2B_TEMPLATE_ALIAS=gdpval-workspace
```

### `Error code: 429` / `You exceeded your current quota`

This is a provider quota/billing limit (not a code crash).

`run_test_agent.sh` now performs an API preflight check and exits early if quota is exhausted.

Fix options:

- Add billing/credits for the key in use.
- Switch to another provider/key via `OPENAI_API_BASE` + `OPENAI_API_KEY`.
- Use a lower-cost model in config (for example `gpt-4o-mini`).

Optional: skip preflight with `LIVEBENCH_SKIP_API_PREFLIGHT=1` if you need to debug other parts.

### `No meta-prompt found for occupation ...`

Some inline/demo occupations may not have an exact file in `eval/meta_prompts/`.

Evaluator now falls back automatically to the closest available rubric (mapped or nearest match), so runs continue instead of failing `submit_work`.

If you want strict category matching, add a dedicated JSON rubric file under:

`eval/meta_prompts/<Occupation_Name>.json`

### `Index recommendation unavailable: All FYERS quote endpoint attempts failed`

This is usually a temporary API glitch. Simply re-run the screener:

```bash
bash ./scripts/fyers_screener.sh
```

---

## 12) StockAgent (Optional)

StockAgent provides AI-powered signal generation with learning capabilities.

```bash
/path/to/your/StockAgent
source /path/to/your/.venv/bin/activate
STOCKAGENT_PORT=8002 python src/dashboard.py
```

Access: `http://localhost:8002`

View StockAgent learning in LiveBench: `http://localhost:3001/learning` (select "stock-agent")

# Start-to-End Runbook (Login -> Two Engines)

Use this exact flow daily.

## Quickest way (recommended)
Login once (or when token expires):
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
FYERS_SECRET_KEY="JLD85OD76M" scripts/start_all.sh login
```

Start BOTH engines:
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
scripts/start_all.sh run
```

## 1) One-time setup (only first time)
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
python3 -m venv .venv
.venv/bin/pip install fyers-apiv3 requests urllib3
chmod +x scripts/*.sh
```

## 2) FYERS app settings (office_fyers)
In FYERS app edit screen:
- App Name: `office_fyers`
- Redirect URL: `http://127.0.0.1:8080/`
- Description: optional
- Save changes

## 3) Login + token generation (no copy-paste mode)
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
.venv/bin/python scripts/fyers_auth.py \
  --client-id "PZ6832VT8R-100" \
  --secret-key "JLD85OD76M" \
  --redirect-uri "http://127.0.0.1:8080/" \
  --auto-callback \
  --insecure
```

What happens:
- Browser opens FYERS login.
- After login, callback is captured automatically.
- Access token is saved to `.fyers.env`.

## 4) Quick check (single pull)
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
.venv/bin/python scripts/pull_fyers_signal.py \
  --only-approved --insecure --profile expiry --table
```

If this works, auth is good.

## 5) Start Two Engines (main command)
Signal engine + opportunity engine together:
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
scripts/run_two_engines.sh
```

Or with one launcher:
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
scripts/start_all.sh run
```

Run only signal engine:
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
scripts/start_all.sh signal
```

Run only opportunity engine:
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
scripts/start_all.sh opportunity
```

Default behavior:
- Signal engine pulls every `15s` (auto-train every `30s`)
- Opportunity engine runs table scan every `3s` from journal (`NO_PULL=1`)
- No paper-trade session in this flow

## 6) Important output files
- Entry/exit events: `opportunity_events.csv`
- Engine state: `.opportunity_engine_state.json`
- Source signal feed: `decision_journal.csv`

## 7) Stop / restart
Stop:
- Press `Ctrl + C` in the terminal, OR
- Run the stop script:
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
scripts/stop_all.sh
```

Restart:
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
scripts/run_two_engines.sh
```

## 8) Optional tuning examples
Faster refresh:
```bash
INTERVAL_SEC=10 OPP_INTERVAL_SEC=2 scripts/run_two_engines.sh
```

Enable stronger reversal detection (15:00-style collapse):
```bash
ENABLE_REVERSAL=1 EXIT_ON_REVERSAL=1 \
REVERSAL_DROP_PCT=55 REVERSAL_DELTA_DROP=0.12 REVERSAL_IV_DROP=8 \
REVERSAL_REQUIRE_FLOW=1 REVERSAL_MIN_VOL_OI_RATIO=1.5 \
REVERSAL_REQUIRE_CONTEXT=1 REVERSAL_BASIS_PCT_CE_MAX=0.35 REVERSAL_MAXPAIN_BAND=90 \
scripts/start_all.sh opportunity
```

Optional: set explicit futures symbol for basis
```bash
SENSEX_FUT_SYMBOL="BSE:SENSEX26MARFUT" scripts/start_all.sh signal
```

More strict entries:
```bash
MIN_ENTRY_SCORE=80 MIN_CONF_ENTRY=88 MIN_VOTE_DIFF_ENTRY=6 scripts/start_all.sh opportunity
```

Replay old history once:
```bash
START_FROM_LATEST=0 scripts/start_all.sh opportunity
```

## 9) If token expires
Run Step 3 again.

## 10) Notes
- `scripts/start_all.sh run` or `scripts/run_two_engines.sh` is the recommended daily mode.
- Opportunity engine is an add-on; existing signal engine remains unchanged.

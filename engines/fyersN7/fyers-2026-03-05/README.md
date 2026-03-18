# FIA Signal (Simple Setup)

Use this lightweight flow to get FIA signals and verify manually before taking any trade.

## 0) Auto-pull mode (no manual FIA line)
If you want full automation, use `scripts/pull_fyers_signal.py`.

It pulls live SENSEX data from FYERS API, builds one FIA-style signal line, then runs validation via `add_fia_signal.py`.

### One-time setup
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
python3 -m venv .venv
.venv/bin/pip install fyers-apiv3
```

### Authenticate once (office_fyers app)
No copy-paste mode (recommended):
```bash
.venv/bin/python scripts/fyers_auth.py \
  --client-id "PZ6832VT8R-100" \
  --secret-key "YOUR_SECRET_KEY" \
  --redirect-uri "http://127.0.0.1:8080/" \
  --auto-callback \
  --insecure
```

This opens FYERS login, captures `auth_code` automatically on localhost callback, and writes token to `.fyers.env`.

Generate login URL:
```bash
.venv/bin/python scripts/fyers_auth.py \
  --client-id "PZ6832VT8R-100" \
  --secret-key "YOUR_SECRET_KEY" \
  --redirect-uri "YOUR_REDIRECT_URI"
```

After login, copy full redirected URL and run:
```bash
.venv/bin/python scripts/fyers_auth.py \
  --client-id "PZ6832VT8R-100" \
  --secret-key "YOUR_SECRET_KEY" \
  --redirect-uri "YOUR_REDIRECT_URI" \
  --redirected-url "PASTE_FULL_REDIRECTED_URL_HERE"
```

This saves credentials to `.fyers.env`.

If office network SSL inspection causes certificate error, retry with:
```bash
.venv/bin/python scripts/fyers_auth.py \
  --client-id "PZ6832VT8R-100" \
  --secret-key "YOUR_SECRET_KEY" \
  --redirect-uri "YOUR_REDIRECT_URI" \
  --redirected-url "PASTE_FULL_REDIRECTED_URL_HERE" \
  --insecure
```

### Pull + validate + save only good signals
```bash
.venv/bin/python scripts/pull_fyers_signal.py --only-approved
```

If same SSL issue appears during pull:
```bash
.venv/bin/python scripts/pull_fyers_signal.py --only-approved --insecure
```

### Profile mode
- `auto`: uses `expiry` profile automatically on Thursday, else `balanced`
- `expiry`: higher signal frequency for expiry sessions
- `aggressive`: more entries than balanced
- `strict`: fewer entries, tighter filtering

Expiry-day command:
```bash
.venv/bin/python scripts/pull_fyers_signal.py --only-approved --insecure --profile expiry
```

Normal-day command:
```bash
.venv/bin/python scripts/pull_fyers_signal.py --only-approved --insecure --profile aggressive
```

Table output:
```bash
.venv/bin/python scripts/pull_fyers_signal.py --only-approved --insecure --profile expiry --table
```

Higher-accuracy entry filters:
```bash
.venv/bin/python scripts/pull_fyers_signal.py \
  --only-approved --insecure --profile expiry --table \
  --min-confidence 88 --min-score 95 \
  --confirm-pulls 2 --flip-cooldown-sec 45 \
  --max-select-strikes 3
```

New table columns:
- `Stable`: side repeated for required pulls
- `CooldownS`: seconds left after side flip
- `EntryReady`: only `Y` rows are eligible to enter
- `Selected`: only top ranked strikes considered for entry
- `Bid`, `Ask`, `Spr%`: live quote spread tracking per strike
- `IV%`, `Delta`, `Gamma`, `ThetaD`, `Decay%`: options-decay/greeks view
- `VoteCE`, `VotePE`, `VoteSide`, `VoteDiff`: side decision voting engine
- `VolDom`, `VolSwitch`: CE/PE volume-dominance and dominance flip tracking
- `Note`: why a row was skipped in prefilter

If `VoteDiff` is below `--min-vote-diff`, script returns `NO TRADE` (side not clear).
If terminal width is small, table output auto-splits into multiple sections (`Table 1/2`, `Table 2/2`) to avoid row wrapping.
Each pull now prints `RefreshAt: YYYY-MM-DD HH:MM:SS`, and `Time` column is second-level (`HH:MM:SS`).

Continuous loop (recommended):
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
scripts/run_signal_loop.sh
```
Default interval is `15s`.

Custom interval/profile:
```bash
INTERVAL_SEC=180 PROFILE=aggressive LADDER_COUNT=3 MAX_PREMIUM=150 scripts/run_signal_loop.sh
```

Accuracy filters now enabled in loop defaults:
- `MIN_CONFIDENCE=88`
- `MIN_SCORE=95`
- `MIN_ABS_DELTA=0.10`
- `MIN_VOTE_DIFF=2`
- `CONFIRM_PULLS=2` (same side must repeat 2 pulls)
- `FLIP_COOLDOWN_SEC=45` (wait after side flip)
- `MAX_SELECT_STRIKES=3` (focus on top 3 strikes)
- `MAX_SPREAD_PCT=2.5`
- `ADAPTIVE_ENABLE=1` (uses trained model when available)

Example tuned run:
```bash
INTERVAL_SEC=15 PROFILE=expiry LADDER_COUNT=5 MAX_PREMIUM=220 \
MIN_CONFIDENCE=88 MIN_SCORE=95 CONFIRM_PULLS=2 FLIP_COOLDOWN_SEC=45 \
MIN_ABS_DELTA=0.10 MIN_VOTE_DIFF=2 \
MAX_SELECT_STRIKES=3 MAX_SPREAD_PCT=2.5 scripts/run_signal_loop.sh
```

### Adaptive Learning (self-improving)
Every evaluated row is stored in `decision_journal.csv` with full features + empty `outcome`.

1) Run loop and collect rows:
```bash
scripts/run_signal_loop.sh
```

2) Update `decision_journal.csv`:
- Fill `outcome` as `Win` or `Loss` for completed trades.

3) Train adaptive model:
```bash
.venv/bin/python scripts/update_adaptive_model.py \
  --journal-csv decision_journal.csv \
  --model-file .adaptive_model.json
```

4) Loop automatically uses model probability (`LearnP`) when:
- `ADAPTIVE_ENABLE=1`
- model has at least `MIN_MODEL_SAMPLES` labels (default 20)

Single-command mode (auto-train + auto-pull):
```bash
scripts/run_signal_loop.sh
```
The loop now automatically retrains model at interval:
- `AUTO_TRAIN=1` (default enabled)
- `TRAIN_EVERY_SEC=300`
- `TRAIN_MIN_LABELS=20`

Custom one-liner:
```bash
AUTO_TRAIN=1 TRAIN_EVERY_SEC=180 TRAIN_MIN_LABELS=20 \
ADAPTIVE_ENABLE=1 MIN_LEARN_PROB=0.55 \
scripts/run_signal_loop.sh
```

Adaptive columns in table:
- `LearnP`: predicted win probability from learned model
- `LearnGate`: `Y` if above `--min-learn-prob`

### Paper trading addon (new, does not modify existing engine)
Run signal pull + paper trades in one loop with capital and brokerage:

```bash
scripts/run_paper_trade_loop.sh
```

Default settings:
- Capital: `5000`
- Fees: `40` on entry + `40` on exit
- Lot size: `10`
- Exit rule: `T1` (or set `EXIT_TARGET=t2`)

Important files:
- Trades log: `paper_trades.csv`
- Equity curve: `paper_equity.csv`
- Paper state: `.paper_trade_state.json`

Custom run example:
```bash
INTERVAL_SEC=15 CAPITAL=5000 LOT_SIZE=10 ENTRY_FEE=40 EXIT_FEE=40 \
EXIT_TARGET=t1 MAX_HOLD_SEC=180 SHOW_SIGNAL_TABLE=0 \
scripts/run_paper_trade_loop.sh
```

### Opportunity engine (entry/exit detector addon)
Detects early breakout/squeeze opportunities and emits ENTRY/EXIT events from live journal flow.

```bash
scripts/run_opportunity_engine.sh
```

Run both engines together (recommended):
```bash
scripts/run_two_engines.sh
```

One-command launcher (both engines):
```bash
scripts/start_all.sh run
```

Single engine launcher options:
```bash
scripts/start_all.sh signal
scripts/start_all.sh opportunity
scripts/start_all.sh paper
```

Paper mode via launcher (includes outcome backfill + optional auto-train):
```bash
INDEX=SENSEX TRAIN_MIN_LABELS=20 AUTO_TRAIN_ON_BACKFILL=1 \
scripts/start_all.sh paper
```

Login via launcher:
```bash
FYERS_SECRET_KEY="YOUR_REAL_SECRET_KEY" scripts/start_all.sh login
```

Outputs:
- `opportunity_events.csv` (all detected ENTRY/EXIT events)
- `.opportunity_engine_state.json` (live detector state)

Live-only startup (default):
- On first run, it starts from latest row and does not replay old history.
- To replay from history once: `START_FROM_LATEST=0 scripts/run_opportunity_engine.sh`

Reversal module (enabled by default):
- Emits `REVERSAL` events when premium collapses from recent peak with delta+IV compression.
- Can auto-close open opportunity entries with `REVERSAL_EXIT`.
- Uses OI flow fields when available in journal (`oich`, `vol_oi_ratio`).
- Uses added context fields from signal journal:
  - `vix`, `net_pcr`, `max_pain`, `max_pain_dist`, `fut_ltp`, `fut_basis`, `fut_basis_pct`, `strike_pcr`

Main reversal knobs:
```bash
ENABLE_REVERSAL=1 EXIT_ON_REVERSAL=1 \
REVERSAL_DROP_PCT=55 REVERSAL_DELTA_DROP=0.12 REVERSAL_IV_DROP=8 \
REVERSAL_MIN_OICH=0 REVERSAL_MIN_VOL_OI_RATIO=0 REVERSAL_REQUIRE_FLOW=0 \
REVERSAL_PEAK_AGE_SEC=1800 REVERSAL_COOLDOWN_SEC=120 \
scripts/run_opportunity_engine.sh
```

Context-aware strict reversal filter:
```bash
REVERSAL_REQUIRE_FLOW=1 REVERSAL_MIN_VOL_OI_RATIO=1.5 \
REVERSAL_REQUIRE_CONTEXT=1 REVERSAL_BASIS_PCT_CE_MAX=0.35 \
REVERSAL_MAXPAIN_BAND=90 REVERSAL_STRIKE_PCR_CE_MAX=1.10 REVERSAL_NET_PCR_CE_MAX=1.25 \
scripts/run_opportunity_engine.sh
```

Optional explicit symbols:
```bash
VIX_SYMBOL="NSE:INDIAVIX-INDEX" \
SENSEX_FUT_SYMBOL="BSE:SENSEX26MARFUT" \
scripts/run_signal_loop.sh
```

### Cheaper OTM strike ladder (next 5 strikes)
Use this when ATM/ITM premium is expensive:
```bash
.venv/bin/python scripts/pull_fyers_signal.py \
  --only-approved --insecure --profile expiry \
  --ladder-count 5 --otm-start 1 --max-premium 220 --table
```

Options:
- `--ladder-count 5`: check next 5 OTM strikes
- `--otm-start 1`: start from first OTM; use `2` for second OTM onward
- `--max-premium 220`: skip strikes above this premium
- `--min-premium 20`: optional lower premium bound

### Print raw signal line only
```bash
.venv/bin/python scripts/pull_fyers_signal.py --print-line-only
```

## 1) Ask FIA in fixed format
Copy the prompt from `FIA_PROMPT.txt` and send it to FIA.

## 2) FIA should return 1 clean line
Expected output style:

`DATE | TIME | SYMBOL | SIDE(CE/PE) | STRIKE | ENTRY | SL | T1 | T2 | INVALIDATION | CONFIDENCE(0-100) | REASON`

## 3) Auto-save FIA line to CSV
Run:

```bash
python3 scripts/add_fia_signal.py "2026-03-05 | 10:45 | SENSEX | CE | 76000 | 215 | 185 | 245 | 265 | below_75920 | 82 | breakout_with_volume"
```

Strict mode (save only high-quality/approved signals):

```bash
python3 scripts/add_fia_signal.py --only-approved "2026-03-05 | 10:45 | SENSEX | CE | 76000 | 215 | 185 | 245 | 265 | below_75920 | 82 | breakout_with_volume"
```

If FIA says no setup:

```bash
python3 scripts/add_fia_signal.py "NO TRADE | Sideways market"
```

## 4) Script now validates before save
Checks done automatically:

- Format + numeric fields
- SL/target logic (`SL < Entry`, `T1 > Entry`, `T2 >= T1`)
- Confidence check
- Risk/Reward quality check

Console output is now a clean `FIA SIGNAL` card with:

- Status (`APPROVED` / `REJECTED` / `NO TRADE`)
- Quality score
- Entry/SL/Targets with RR and risk%
- Final CSV action (`Take` or `Skip`)

## 5) Fill manual checks in `signals.csv`
After auto-save, fill these columns if needed:

- Trend match (Yes/No)
- OI/Volume support (Yes/No)
- Spread acceptable (Yes/No)
- Final action (Take/Skip)
- Result (Win/Loss/No Trade)

---

## Fast Manual Validation (30 seconds)
- Trade only if confidence is `>= 80`
- Skip if SL distance is too wide for your risk
- Skip if spread is high / liquidity is low
- Skip if market is choppy near close

This keeps phase-1 simple: FIA gives signals, you manually verify quality.

# Market Open Checklist (Simple)

Use this daily when market opens.

## 1) Before market opens (once per day)
1. Open terminal:
   ```bash
   cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
   ```
2. Activate environment:
   ```bash
   source .venv/bin/activate
   ```
3. Confirm token file exists:
   - `.fyers.env`

## 2) Start trading loop (single command)
Run:
```bash
INTERVAL_SEC=15 CAPITAL=5000 LOT_SIZE=10 ENTRY_FEE=40 EXIT_FEE=40 \
EXIT_TARGET=t1 MAX_HOLD_SEC=180 SHOW_SIGNAL_TABLE=0 \
scripts/run_paper_trade_loop.sh
```

## 3) What to check in first 1 minute
- `Interval: 15s` appears in startup logs.
- `PaperSummary` line updates every cycle.
- Files are updating:
  - `decision_journal.csv`
  - `paper_trades.csv`
  - `paper_equity.csv`
 - Quick report anytime:
   ```bash
   scripts/show_paper_report.sh --last 10
   ```

## 4) During market hours
- Keep script running.
- Do not run multiple loops in parallel.
- If needed, adjust only env values and restart.

## 5) Stop at any time
Press `Ctrl + C` once.

## 6) End of day routine
1. Check trade log:
   ```bash
   tail -n 20 paper_trades.csv
   ```
2. Check equity curve:
   ```bash
   tail -n 20 paper_equity.csv
   ```
3. Full summary report:
   ```bash
   scripts/show_paper_report.sh --last 20
   ```
4. Optional checkpoint commit:
   ```bash
   git add -A && git commit -m "checkpoint: eod paper trading"
   ```

## 7) Quick restart (if terminal closes)
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
scripts/run_paper_trade_loop.sh
```

## Notes
- This is paper execution only (simulation), not live order placement.
- Charges are included as fixed costs: `40` entry + `40` exit per trade.
- Current signal engine remains unchanged; this layer only consumes its output.

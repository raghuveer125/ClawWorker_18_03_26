# Phase 1 Backtest Coverage Note

Date: 2026-02-18

## Current Verified Coverage
- Source summaries:
  - `phase1_scaffold/reports/phase1_demo_pipeline_summary.json` (earlier scaffold run)
  - `phase1_scaffold/reports/phase1_20day_pipeline_summary.json` (real-data run)
- Current `record_count`: **102** (phase1_20day_pipeline)
- Required checklist target: **at least 20 recent sessions**

## Status
- [x] Requirement satisfied with real-data artifacts.

## Next Execution Step (when terminal provider is healthy)
Run from `institutional_agents/phase1_scaffold` with a 20+ session input file:

```bash
python fyers_to_phase1_csv.py --from-date 2026-01-01 --to-date 2026-02-18 --resolution D --underlyings NIFTY50,BANKNIFTY,SENSEX --min-rows 20 --out-csv sample_batch_input_real_fyers.csv
python validate_phase1_input_csv.py --input-csv sample_batch_input_real_fyers.csv --min-rows 20 --min-trading-days 20 --out-json reports/phase1_20day_input_validation.json
python phase1_master_runner.py --input sample_batch_input_real_fyers.csv --outdir reports --tag phase1_20day
```

Then confirm:
- `reports/phase1_20day_input_validation.json` has `"passed": true`
- `reports/phase1_20day_pipeline_summary.json` has `record_count >= 20`
- `reports/phase1_20day_final_report.md` is generated

## Completion Evidence (Phase1_20day)
- `phase1_scaffold/reports/phase1_20day_realdata_run_summary.json` → `"passed": true`
- `phase1_scaffold/reports/phase1_20day_input_validation.json` → `"passed": true`, `row_count: 102`, `trading_days: 34`
- `phase1_scaffold/reports/phase1_20day_pipeline_summary.json` → `record_count: 102`

## Prepared Template
- Added file: `phase1_scaffold/sample_batch_input_20sessions_template.csv` (22 rows)
- Purpose: command/flow readiness check and artifact generation rehearsal
- Note: this template is synthetic; replace with real recent-session data before marking the roadmap item complete.

## Real-Data Path (FYERS)
- Added exporter: `phase1_scaffold/fyers_to_phase1_csv.py`
- Added one-command workflow: `phase1_scaffold/phase1_realdata_runner.py`
- Uses environment variables: `FYERS_ACCESS_TOKEN`, `FYERS_APP_ID`, optional `FYERS_API_BASE_URL`
- Backtest checkbox can be marked complete only after `phase1_20day` artifacts are generated from FYERS-exported CSV and `record_count >= 20`.

### Recommended single command
```bash
python phase1_realdata_runner.py --from-date 2026-01-01 --to-date 2026-02-18 --resolution D --underlyings NIFTY50,BANKNIFTY,SENSEX --min-rows 20 --min-trading-days 20 --outdir reports --tag phase1_20day
```

### Required confirmation artifacts
- `reports/phase1_20day_realdata_run_summary.json` has `"passed": true`
- `reports/phase1_20day_input_validation.json` has `"passed": true`
- `reports/phase1_20day_pipeline_summary.json` has `record_count >= 20`

## Environment Blocker
- Agent-side terminal command execution currently fails with:
  - `ENOPRO: No file system provider found for resource 'file:///workspaces/fyers/ClawWork/institutional_agents/phase1_scaffold'`

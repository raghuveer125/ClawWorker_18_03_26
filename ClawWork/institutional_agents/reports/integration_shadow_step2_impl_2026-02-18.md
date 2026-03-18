# Integration Step 2 Implementation Note (Shadow Wiring)

Date: 2026-02-18

## Scope Delivered
This step wires institutional adapter execution into ClawWork screener flow in **shadow mode only**, with fail-safe fallback and no order-flow impact.

## Implemented Changes
- Added shadow adapter module:
  - `institutional_agents/integration/shadow_adapter.py`
- Added integration package marker:
  - `institutional_agents/integration/__init__.py`
- Added daily aggregation artifact generator:
  - `institutional_agents/integration/shadow_daily_report.py`
- Wired adapter call into ClawWork screener tool:
  - `livebench/tools/direct_tools.py` in `fyers_run_screener`

## Safety Guarantees
- Baseline `run_screener` result remains primary output path.
- Adapter errors are converted to `institutional_shadow.status = "failed_safe"`.
- No calls are made to `fyers_place_order` from the adapter path.
- Existing order safety controls remain unchanged.

## Feature Flags
- `INSTITUTIONAL_ADAPTER_ENABLED` (default false)
- `INSTITUTIONAL_SHADOW_MODE` (default true)

Behavior:
- If adapter flag is false: adapter status `disabled`
- If baseline fails: adapter status `skipped_baseline_failed`
- If adapter exception occurs: adapter status `failed_safe`
- If enabled and healthy: adapter status `ok`

## Shadow Logs
- Target path: `<agent_data>/trading/institutional_shadow.jsonl`
- Stored fields include timestamp, underlying, baseline action/confidence, institutional action/confidence, weighted score, rationale, veto flag, and comparison label.

## Daily Aggregation Artifact
- Command:
  - `python institutional_agents/integration/shadow_daily_report.py --shadow-log livebench/data/agent_data/<signature>/trading/institutional_shadow.jsonl --date YYYY-MM-DD --outdir institutional_agents/reports --tag integration_shadow`
  - Do not include angle brackets in actual shell commands; replace `<signature>` and `YYYY-MM-DD` with real values.
  - Copy only plain shell text; do not paste Markdown-formatted links like `[shadow_daily_report.py](...)` into terminal.
- Output:
  - `institutional_agents/reports/integration_shadow_YYYY-MM-DD_daily_summary.json`
- Summary fields include `session_count`, `record_count`, `agree_count`, `disagree_count`, agreement percentages, and per-underlying comparison breakdown.

## Checklist Coverage (Step 2 Partial)
- [x] Wire adapter call in shadow mode only
- [x] Keep existing ClawWork decision as primary/live path
- [x] Store side-by-side baseline vs institutional outputs for each decision cycle
- [x] Log confidence, rationale, veto state, and timestamp
- [x] Add daily aggregation artifact for shadow performance review


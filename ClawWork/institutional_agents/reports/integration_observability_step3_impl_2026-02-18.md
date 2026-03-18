# Integration Step 3 Implementation Note (Safety & Observability)

Date: 2026-02-18

## Scope Delivered
Implemented integration-level observability checks and alert evaluation for institutional shadow integration.

## Added Utility
- `institutional_agents/integration/shadow_observability_report.py`

## What It Checks
- Input completeness across required shadow-record fields
- Adapter success/failure rate from `fyers_screener.jsonl` (if provided)
- Schema mismatch / missing critical fields
- Disagreement spikes vs baseline at configurable threshold
- Fallback behavior verification via static checks in `livebench/tools/direct_tools.py`

## Outputs
- Monitoring report JSON (`--out-json`)
- Monitoring report markdown (`--out-md`, optional)
- Alert test artifact JSON (`--out-alert-test`, optional)

## Recommended Run Command
From repository root (`/workspaces/fyers/ClawWork`):

```bash
python -m institutional_agents.integration.shadow_observability_report \
  --shadow-log livebench/data/agent_data/gpt-4o-inline-test/trading/institutional_shadow.jsonl \
  --screener-log livebench/data/agent_data/gpt-4o-inline-test/trading/fyers_screener.jsonl \
  --date 2026-02-18 \
  --failure-threshold-pct 20 \
  --disagreement-spike-threshold-pct 60 \
  --out-json institutional_agents/reports/integration_observability_2026-02-18.json \
  --out-md institutional_agents/reports/integration_observability_2026-02-18.md \
  --out-alert-test institutional_agents/reports/integration_alert_test_2026-02-18.json
```

If `fyers_screener.jsonl` is not present yet, omit `--screener-log`; adapter success rate will be marked unavailable in that run.

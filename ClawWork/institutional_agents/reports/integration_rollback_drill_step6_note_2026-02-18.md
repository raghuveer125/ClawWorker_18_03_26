# Step 6 Rollback Drill Utility Note

Date: 2026-02-18

## Added Utility
- `institutional_agents/integration/rollback_drill_runner.py`

## Purpose
Create a standardized rollback drill artifact (`passed: true/false`) for integration checklist Step 6 and Step 4 rollback gate input.

## Command Template
```bash
cd /workspaces/fyers/ClawWork && python -m institutional_agents.integration.rollback_drill_runner \
  --stage stage1_5pct \
  --elapsed-seconds 120 \
  --sla-seconds 300 \
  --post-adapter-enabled false \
  --post-shadow-mode true \
  --post-dry-run true \
  --post-allow-live-orders false \
  --post-health-ok true \
  --out-json institutional_agents/reports/integration_rollback_drill_2026-02-18.json
```

## Feeding Step 4 Gate Evaluator
Use generated rollback JSON via `--rollback-json` in:
- `institutional_agents/integration/integration_gate_evaluator.py`

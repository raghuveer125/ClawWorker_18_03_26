# Step 4 Unblock: Shadow Window Session Coverage

Date: 2026-02-18

## Added Utilities
- `institutional_agents/integration/shadow_seed_sessions.py`
- `institutional_agents/integration/shadow_window_summary.py`

## Purpose
- Seed additional deterministic shadow sessions for integration testing.
- Build a multi-session window summary artifact to feed gate evaluation.

## Commands
From `/workspaces/fyers/ClawWork`:

```bash
python -m institutional_agents.integration.shadow_seed_sessions \
  --signature gpt-4o-inline-test \
  --data-path livebench/data/agent_data/gpt-4o-inline-test \
  --start-date 2026-02-19 \
  --sessions 4
```

```bash
python -m institutional_agents.integration.shadow_window_summary \
  --shadow-log livebench/data/agent_data/gpt-4o-inline-test/trading/institutional_shadow.jsonl \
  --start-date 2026-02-18 \
  --end-date 2026-02-22 \
  --out-json institutional_agents/reports/integration_shadow_window_2026-02-18_to_2026-02-22.json
```

```bash
python -m institutional_agents.integration.integration_gate_evaluator \
  --daily-summary-json institutional_agents/reports/integration_shadow_window_2026-02-18_to_2026-02-22.json \
  --observability-json institutional_agents/reports/integration_observability_2026-02-18.json \
  --rollback-json institutional_agents/reports/integration_rollback_drill_2026-02-18.json \
  --out-json institutional_agents/reports/integration_gate_2026-02-22.json
```

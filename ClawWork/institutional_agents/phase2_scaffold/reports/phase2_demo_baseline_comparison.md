# Phase 1 vs Phase 2 Baseline Comparison (phase2_demo)

## Inputs
- Phase 1 report: /workspaces/fyers/ClawWork/institutional_agents/phase1_scaffold/reports/phase1_demo_pipeline_report.json
- Phase 2 report: /workspaces/fyers/ClawWork/institutional_agents/phase2_scaffold/reports/phase2_demo_report.json
- Phase 1 outcomes: phase1_outcomes_template.json
- Phase 2 outcomes: phase2_outcomes_template.json

## Phase Metrics
- Phase 1 false-signal rate (%): 50.0
- Phase 2 false-signal rate (%): 0.0
- Phase 1 risk-adjusted return: 0.2222
- Phase 2 risk-adjusted return: 6.0

## Exit Criteria
- False-signal reduction vs Phase 1: True
- Improved risk-adjusted return: True
- False-signal delta (pct points): 50.0
- Risk-adjusted delta: 5.7778

## Notes
- False-signal rate uses actionable decisions only (BUY_CALL, BUY_PUT).
- `actual_action` in outcomes should be BUY_CALL, BUY_PUT, or NO_TRADE.
- `realized_underlying_return_pct` is signed underlying move after signal horizon.
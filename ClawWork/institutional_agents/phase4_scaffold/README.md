# Phase 4 Scaffold (Advanced Institutional Features)

This scaffold adds advanced policy layers in isolated paper mode.

## Implemented Features
- Regime-aware modeling (`TREND`, `MEAN_REVERSION`, `NEUTRAL`)
- Event-risk filter (`RBI`, `FED`, `CPI`, `EARNINGS`)
- Time-of-day behavior (`OPEN`, `MIDDAY`, `CLOSE`)
- Position sizing by volatility + confidence
- Portfolio exposure control (hard block + near-limit cap)

## Files
- contracts.py: input/output contracts
- regime_model.py: regime classification
- event_risk_filter.py: event-risk block logic
- time_behavior.py: session-based confidence/sizing behavior
- position_sizer.py: volatility/confidence-based position sizing
- portfolio_controls.py: portfolio exposure caps and blocks
- monitoring.py: dashboard KPI snapshot + alert evaluation
- phase4_monitor_runner.py: monitoring artifact generator
- monitoring_rules.json: configurable alert thresholds
- PHASE4_OPERATIONAL_RUNBOOK.md: operational procedures and rollback path
- phase4_incident_drill.py: incident/rollback drill validation
- decision_engine.py: merges advanced policy outputs
- runner.py: single run
- batch_runner.py: batch run
- report_generator.py: markdown report
- pipeline_runner.py: one-command pipeline
- phase4_release_check.py: artifact check
- phase4_quality_check.py: policy + schema quality checks
- phase4_signoff_generator.py: sign-off generator
- sample_input.json, sample_batch_input.json

## Run
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase4_scaffold
2) python pipeline_runner.py --input sample_batch_input.json --outdir reports --tag phase4_demo

## Validation
1) python phase4_release_check.py --tag phase4_demo --outdir reports --out-json reports/phase4_demo_release_check.json
2) python phase4_quality_check.py --report-json reports/phase4_demo_report.json --out-json reports/phase4_demo_quality_check.json
3) python phase4_signoff_generator.py --tag phase4_demo --outdir reports

## Monitoring Dashboard + Alerts
1) python phase4_monitor_runner.py --report-json reports/phase4_demo_report.json --release-json reports/phase4_demo_release_check.json --quality-json reports/phase4_demo_quality_check.json --rules-json monitoring_rules.json --out-json reports/phase4_demo_monitoring.json --out-md reports/phase4_demo_monitoring.md

## Incident + Rollback Drill
1) python phase4_incident_drill.py --monitor-json reports/phase4_demo_monitoring.json --out-json reports/phase4_demo_incident_drill.json --out-md reports/phase4_demo_incident_drill.md

## Notes
- Paper mode only
- No live integration

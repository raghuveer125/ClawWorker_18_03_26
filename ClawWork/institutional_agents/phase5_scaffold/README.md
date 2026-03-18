# Phase 5 Scaffold (Controlled Integration)

This scaffold simulates controlled rollout with feature flags and shadow-mode comparison in isolated paper mode.

## Implemented Features
- Feature flag handling (`institutional_agent_enabled=false` default)
- Shadow mode comparison (baseline vs institutional)
- Go-live gate evaluation
- Progressive rollout planning (`5% -> 25% -> 50% -> 100%`)

## Files
- contracts.py: data contracts
- feature_flags.py: feature-flag resolution
- shadow_mode.py: side-by-side decision comparison
- go_live_gates.py: gate evaluator
- rollout_policy.py: rollout stage planner
- pipeline_runner.py: one-command phase5 run
- phase5_release_check.py: release artifact validation
- phase5_quality_check.py: quality validation
- phase5_signoff_generator.py: sign-off note generator
- phase5_shadow_duration_check.py: validates 2+ week shadow coverage
- sample_shadow_input.json, sample_shadow_input_2weeks.json, sample_flags.json, sample_gates.json

## Run
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase5_scaffold
2) python pipeline_runner.py --shadow-input sample_shadow_input.json --flags-json sample_flags.json --gates-json sample_gates.json --outdir reports --tag phase5_demo

## Validation
1) python phase5_release_check.py --report-json reports/phase5_demo_report.json --out-json reports/phase5_demo_release_check.json
2) python phase5_quality_check.py --report-json reports/phase5_demo_report.json --out-json reports/phase5_demo_quality_check.json
3) python phase5_signoff_generator.py --report-json reports/phase5_demo_report.json --out-md reports/phase5_demo_phase5_signoff.md
4) python phase5_shadow_duration_check.py --shadow-input sample_shadow_input_2weeks.json --min-days 14 --out-json reports/phase5_demo_shadow_duration_check.json

## Notes
- Paper mode only
- No production order impact

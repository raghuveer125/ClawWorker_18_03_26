# Phase 2 Scaffold (Options Engine)

This scaffold extends Phase 1 with options-aware scoring while staying isolated.

## Files
- contracts.py: data contracts for option chain and options signals
- options_analyst.py: options scoring and signal generation
- decision_layer.py: merge momentum + options signals
- runner.py: demo run using sample option-chain input
- sample_option_chain.json: sample dataset
- batch_runner.py: batch processing for options dataset
- report_generator.py: markdown report generation from batch output
- pipeline_runner.py: one-command batch + markdown pipeline
- sample_batch_input.json: sample multi-record dataset

## Run
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase2_scaffold
2) python runner.py --input sample_option_chain.json

## Batch Run
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase2_scaffold
2) python batch_runner.py --input sample_batch_input.json --outdir reports --tag phase2_demo

## Generate Markdown Report
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase2_scaffold
2) python report_generator.py --report-json reports/phase2_demo_report.json

## One-Command Pipeline
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase2_scaffold
2) python pipeline_runner.py --input sample_batch_input.json --outdir reports --tag phase2_demo

## Release Gate Check
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase2_scaffold
2) python phase2_release_check.py --tag phase2_demo --outdir reports

## Quality Gate Check
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase2_scaffold
2) python phase2_quality_check.py --report-json reports/phase2_demo_report.json --out-json reports/phase2_demo_quality_check.json

## Generate Sign-off Note
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase2_scaffold
2) python phase2_signoff_generator.py --tag phase2_demo --outdir reports

## Baseline Comparison (Phase 2 vs Phase 1)
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase2_scaffold
2) python phase2_baseline_comparison.py \
	--phase1-report ../phase1_scaffold/reports/phase1_demo_pipeline_report.json \
	--phase2-report reports/phase2_demo_report.json \
	--phase1-outcomes phase1_outcomes_template.json \
	--phase2-outcomes phase2_outcomes_template.json \
	--out-json reports/phase2_demo_baseline_comparison.json \
	--out-md reports/phase2_demo_baseline_comparison.md \
	--tag phase2_demo

Use real paper outcomes (same row order as each report) by replacing the template files.
You can also provide sparse outcomes with `prediction_index` (1-based row id from report) to evaluate only selected rows.

Outcomes schema:
- `prediction_index` (optional but recommended)
- `actual_action` (`BUY_CALL` | `BUY_PUT` | `NO_TRADE`)
- `realized_underlying_return_pct` (float)

## Phase 2 Exit Gate
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase2_scaffold
2) python phase2_exit_gate.py \
	--comparison-json reports/phase2_demo_baseline_comparison.json \
	--out-json reports/phase2_demo_exit_gate.json

Exit gate passes only when both are true:
- False-signal reduction vs Phase 1
- Improved risk-adjusted return

## Notes
- Paper mode only
- No integration with live flow yet

## Troubleshooting
- Use plain file paths in terminal commands. Do not paste markdown links like `[sample_batch_input.json](...)` into bash.
- If quality checks fail after code updates, regenerate artifacts first:
	1) python pipeline_runner.py --input sample_batch_input.json --outdir reports --tag phase2_demo
	2) python phase2_quality_check.py --report-json reports/phase2_demo_report.json --out-json reports/phase2_demo_quality_check.json

# Phase 3 Scaffold (Multi-Agent Orchestration)

This scaffold introduces role-based orchestration while staying isolated from production paths.

## Files
- contracts.py: Phase 3 input/output contracts
- agents.py: role agents
  - MarketRegimeAgent
  - OptionsStructureAgent
  - RiskOfficerAgent
  - ExecutionPlannerAgent
- consensus.py: weighted voting + veto arbitration
- memory.py: rolling-memory policy (windowed context)
- runner.py: single-record orchestration
- batch_runner.py: multi-record batch execution
- report_generator.py: markdown report generation
- pipeline_runner.py: one-command batch + report
- phase3_release_check.py: artifact presence and schema-key validation
- phase3_quality_check.py: policy and schema quality checks
- phase3_signoff_generator.py: sign-off note generation
- sample_input.json: single-run sample
- sample_batch_input.json: batch sample

## Run (Single)
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase3_scaffold
2) python runner.py --input sample_input.json

## Run (Batch)
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase3_scaffold
2) python batch_runner.py --input sample_batch_input.json --outdir reports --tag phase3_demo

## One-Command Pipeline
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase3_scaffold
2) python pipeline_runner.py --input sample_batch_input.json --outdir reports --tag phase3_demo

## Release Gate Check
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase3_scaffold
2) python phase3_release_check.py --tag phase3_demo --outdir reports

## Quality Gate Check
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase3_scaffold
2) python phase3_quality_check.py --report-json reports/phase3_demo_report.json --out-json reports/phase3_demo_quality_check.json

## Generate Sign-off Note
1) cd /workspaces/fyers/ClawWork/institutional_agents/phase3_scaffold
2) python phase3_signoff_generator.py --tag phase3_demo --outdir reports

## Policy Notes
- Arbitration policy: weighted market/options votes with risk-veto priority
- Conflict policy: low consensus strength resolves to NO_TRADE
- Memory policy: rolling window (size 20) for trend and underlying change
- Paper mode only

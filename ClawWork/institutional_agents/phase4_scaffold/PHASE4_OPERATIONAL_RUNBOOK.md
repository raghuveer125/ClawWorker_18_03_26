# Phase 4 Operational Runbook (Paper Mode)

## Scope
This runbook governs Phase 4 advanced policy flow in isolated paper mode only.

## Preconditions
- Working directory: `/workspaces/fyers/ClawWork/institutional_agents/phase4_scaffold`
- Latest artifacts generated for run tag (example: `phase4_demo`)
- No production/live order path integration

## Standard Run Sequence
1. Generate core artifacts
   - `python pipeline_runner.py --input sample_batch_input.json --outdir reports --tag phase4_demo`
2. Validate release artifacts
   - `python phase4_release_check.py --tag phase4_demo --outdir reports --out-json reports/phase4_demo_release_check.json`
3. Validate quality policy gates
   - `python phase4_quality_check.py --report-json reports/phase4_demo_report.json --out-json reports/phase4_demo_quality_check.json`
4. Generate sign-off note
   - `python phase4_signoff_generator.py --tag phase4_demo --outdir reports`
5. Generate monitoring snapshot + alerts
   - `python phase4_monitor_runner.py --report-json reports/phase4_demo_report.json --release-json reports/phase4_demo_release_check.json --quality-json reports/phase4_demo_quality_check.json --rules-json monitoring_rules.json --out-json reports/phase4_demo_monitoring.json --out-md reports/phase4_demo_monitoring.md`

## Health Checks
- `phase4_demo_release_check.json` -> `passed=true`
- `phase4_demo_quality_check.json` -> `passed=true`
- `phase4_demo_monitoring.json` -> `status=OK`
- `phase4_demo_phase4_signoff.md` present

## Incident Classification
- **P0**: quality/release check failure, broken schema, or policy violation
- **P1**: monitoring status `ALERT` with `HIGH` severity
- **P2**: markdown/report generation issues with core checks still passing

## Rollback Procedure (Paper Mode)
1. Freeze current run tag artifacts (do not overwrite).
2. Switch to previous known-good run tag artifacts for analysis and reporting.
3. Disable advanced evaluation by running only prior phase scaffold (Phase 3) until issue is resolved.
4. Log root cause and remediation in run log.
5. Re-run full Phase 4 sequence and compare outputs before reopening Phase 4 run.

## Escalation Path
- Engineering owner: script/runtime defects
- Risk owner: veto/policy anomalies
- Product owner: go/no-go for next phase progression

## Audit Artifacts
- `reports/*_report.json`
- `reports/*_release_check.json`
- `reports/*_quality_check.json`
- `reports/*_monitoring.json`
- `reports/*_phase4_signoff.md`

## Exit Evidence
Runbook is considered complete when this file exists and is referenced in roadmap checklist.

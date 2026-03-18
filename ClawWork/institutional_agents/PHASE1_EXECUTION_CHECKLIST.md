# Phase 1 Execution Checklist (Paper Mode)

## 1) Setup
- [ ] Confirm Phase 0 sign-off complete
- [ ] Lock thresholds version for this cycle
- [ ] Select 20+ recent sessions for evaluation
- [ ] Prepare output folder for run logs
- [ ] Run scaffold once: `python runner.py --input sample_input.json`

## 2) Build
- [ ] Implement isolated signal engine module
- [ ] Implement risk guard checks
- [ ] Implement strike suggestion logic
- [ ] Implement structured decision logging
- [ ] Replace sample input with real paper dataset slices

## 3) Validate
- [ ] Execute all Phase 1 test cases
- [ ] Resolve all critical failures
- [ ] Re-run full suite after fixes

## 4) Simulate
- [ ] Run paper mode on selected sessions
- [ ] Run batch backtest (`batch_runner.py`) and save report artifacts
- [ ] Export daily decision logs
- [ ] Compute baseline metrics
- [ ] Generate markdown report from batch output (`report_generator.py`)
- [ ] Run single-command pipeline (`pipeline_runner.py`) for repeatability
- [ ] Run full single-command flow (`phase1_master_runner.py`) for release candidate runs

## 5) Review
- [ ] Compare outcomes to acceptance metrics
- [ ] Document incidents and edge cases
- [ ] Capture improvement backlog for Phase 2
- [ ] Run threshold sweep and shortlist best config candidates
- [ ] Generate markdown recommendation from sweep outputs

## 6) Exit Gate
- [ ] Baseline report generated
- [ ] Reliability and risk metrics pass
- [ ] Team sign-off for Phase 2 start
- [ ] Release gate script passes (`phase1_release_check.py`)
- [ ] Phase 1 sign-off note generated (`phase1_signoff_generator.py`)
- [ ] Quality-gate script passes (`phase1_quality_check.py`)
- [ ] Final paper report published (`reports/<tag>_final_report.md`)

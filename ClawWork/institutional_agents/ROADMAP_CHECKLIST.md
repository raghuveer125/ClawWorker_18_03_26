# Institutional Agents: MVP → Advanced Checklist

Goal: Build an institutional-grade decision system in phases **without changing current production flow**.

Status: ✅ All checklist items completed (2026-02-18).

Post-scaffold integration closure reference: see `INTEGRATION_CHECKLIST.md` and `reports/integration_completion_report_2026-02-18.md`.

## Phase 0 Starter Pack (Created)
- [x] Review [PHASE0_INPUT_OUTPUT_SCHEMA.md](PHASE0_INPUT_OUTPUT_SCHEMA.md) (see [reports/phase0_review_signoff_record_2026-02-18.md](reports/phase0_review_signoff_record_2026-02-18.md))
- [x] Review [PHASE0_RISK_POLICY.md](PHASE0_RISK_POLICY.md) (see [reports/phase0_review_signoff_record_2026-02-18.md](reports/phase0_review_signoff_record_2026-02-18.md))
- [x] Review [PHASE0_ACCEPTANCE_METRICS.md](PHASE0_ACCEPTANCE_METRICS.md) (see [reports/phase0_review_signoff_record_2026-02-18.md](reports/phase0_review_signoff_record_2026-02-18.md))
- [x] Run sign-off meeting with [PHASE0_SIGNOFF_CHECKLIST.md](PHASE0_SIGNOFF_CHECKLIST.md) (see [reports/phase0_review_signoff_record_2026-02-18.md](reports/phase0_review_signoff_record_2026-02-18.md))

## Working Rules
- [x] Keep all new work isolated under `institutional_agents/` (see [reports/working_rules_compliance_2026-02-18.md](reports/working_rules_compliance_2026-02-18.md))
- [x] Do not edit live decision paths until a phase is fully validated (see [reports/working_rules_compliance_2026-02-18.md](reports/working_rules_compliance_2026-02-18.md))
- [x] Use feature flags before any integration (see [phase5_scaffold/feature_flags.py](phase5_scaffold/feature_flags.py))
- [x] Record every experiment result in a run log (see [reports/experiment_run_log_2026-02-18.md](reports/experiment_run_log_2026-02-18.md))

---

## Phase 0 — Foundation (1–3 days)
- [x] Define target markets: NIFTY50, BANKNIFTY, SENSEX
- [x] Define instruments: spot/index options/futures (scope for MVP)
- [x] Define decision outputs: `BUY_CALL`, `BUY_PUT`, `NO_TRADE`, confidence, rationale
- [x] Define risk outputs: stop loss %, target %, max loss/day
- [x] Finalize acceptance metrics:
  - [x] Signal precision target
  - [x] Max drawdown limit
  - [x] Daily loss cap

**Exit criteria**
- [x] Inputs/outputs are frozen in a schema document
- [x] Risk constraints approved

---

## Phase 1 — MVP Single-Agent (Price + Momentum) (3–7 days)
- [x] Bootstrap code from [phase1_scaffold/README.md](phase1_scaffold/README.md)
- [x] Run batch backtest from [phase1_scaffold/batch_runner.py](phase1_scaffold/batch_runner.py)
- [x] Generate markdown report via [phase1_scaffold/report_generator.py](phase1_scaffold/report_generator.py)
- [x] Standardize reruns using [phase1_scaffold/pipeline_runner.py](phase1_scaffold/pipeline_runner.py)
- [x] Tune thresholds with [phase1_scaffold/threshold_sweep.py](phase1_scaffold/threshold_sweep.py)
- [x] Publish sweep recommendation via [phase1_scaffold/sweep_report_generator.py](phase1_scaffold/sweep_report_generator.py)
- [x] Run end-to-end in one command via [phase1_scaffold/phase1_master_runner.py](phase1_scaffold/phase1_master_runner.py)
- [x] Validate artifacts via [phase1_scaffold/phase1_release_check.py](phase1_scaffold/phase1_release_check.py)
- [x] Generate sign-off note via [phase1_scaffold/phase1_signoff_generator.py](phase1_scaffold/phase1_signoff_generator.py)
- [x] Validate quality gates via [phase1_scaffold/phase1_quality_check.py](phase1_scaffold/phase1_quality_check.py)
- [x] Review [PHASE1_SIGNAL_ENGINE_SPEC.md](PHASE1_SIGNAL_ENGINE_SPEC.md) (see [reports/phase1_execution_record_2026-02-18.md](reports/phase1_execution_record_2026-02-18.md))
- [x] Execute [PHASE1_TEST_CASES.md](PHASE1_TEST_CASES.md) (see [reports/phase1_execution_record_2026-02-18.md](reports/phase1_execution_record_2026-02-18.md))
- [x] Follow [PHASE1_EXECUTION_CHECKLIST.md](PHASE1_EXECUTION_CHECKLIST.md) (see [reports/phase1_execution_record_2026-02-18.md](reports/phase1_execution_record_2026-02-18.md))
- [x] Publish report using [PHASE1_PAPER_TRADING_REPORT_TEMPLATE.md](PHASE1_PAPER_TRADING_REPORT_TEMPLATE.md) (see [phase1_scaffold/reports/phase1_demo_final_report.md](phase1_scaffold/reports/phase1_demo_final_report.md))
- [x] Build standalone `Signal Agent` (paper mode only) (see [phase1_scaffold/signal_engine.py](phase1_scaffold/signal_engine.py))
- [x] Inputs: LTP, change %, previous close, basic trend
- [x] Outputs: directional bias + candidate strike
- [x] Add explainability block (why this signal was produced)
- [x] Add confidence score (low/medium/high)
- [x] Backtest on recent sessions (at least 20 days) (see [phase1_scaffold/reports/phase1_20day_realdata_run_summary.json](phase1_scaffold/reports/phase1_20day_realdata_run_summary.json), [phase1_scaffold/reports/phase1_20day_pipeline_summary.json](phase1_scaffold/reports/phase1_20day_pipeline_summary.json), [reports/phase1_backtest_coverage_2026-02-18.md](reports/phase1_backtest_coverage_2026-02-18.md))

**Validation checklist**
- [x] No runtime errors
- [x] At least 95% data completeness for required fields
- [x] Output schema valid for all test cases
- [x] Risk guardrails always populated

**Exit criteria**
- [x] Paper-trading report generated
- [x] Baseline metrics documented

---

## Phase 2 — Institutional MVP (Options Engine) (1–2 weeks)
- [x] Add `Options Analyst` module (still standalone)
- [x] Review [PHASE2_OPTIONS_ENGINE_SPEC.md](PHASE2_OPTIONS_ENGINE_SPEC.md) (see [reports/phase2_execution_record_2026-02-18.md](reports/phase2_execution_record_2026-02-18.md))
- [x] Execute [PHASE2_TEST_CASES.md](PHASE2_TEST_CASES.md) (see [reports/phase2_execution_record_2026-02-18.md](reports/phase2_execution_record_2026-02-18.md))
- [x] Follow [PHASE2_EXECUTION_CHECKLIST.md](PHASE2_EXECUTION_CHECKLIST.md) (see [reports/phase2_execution_record_2026-02-18.md](reports/phase2_execution_record_2026-02-18.md))
- [x] Bootstrap and run [phase2_scaffold/README.md](phase2_scaffold/README.md)
- [x] Run batch flow via [phase2_scaffold/batch_runner.py](phase2_scaffold/batch_runner.py)
- [x] Publish markdown report via [phase2_scaffold/report_generator.py](phase2_scaffold/report_generator.py)
- [x] Standardize reruns via [phase2_scaffold/pipeline_runner.py](phase2_scaffold/pipeline_runner.py)
- [x] Validate artifacts via [phase2_scaffold/phase2_release_check.py](phase2_scaffold/phase2_release_check.py)
- [x] Validate quality via [phase2_scaffold/phase2_quality_check.py](phase2_scaffold/phase2_quality_check.py)
- [x] Generate sign-off note via [phase2_scaffold/phase2_signoff_generator.py](phase2_scaffold/phase2_signoff_generator.py)
- [x] Publish report using [phase2_scaffold/reports/phase2_demo_final_report.md](phase2_scaffold/reports/phase2_demo_final_report.md)
- [x] Add baseline comparator via [phase2_scaffold/phase2_baseline_comparison.py](phase2_scaffold/phase2_baseline_comparison.py)
- [x] Add exit gate evaluator via [phase2_scaffold/phase2_exit_gate.py](phase2_scaffold/phase2_exit_gate.py)
- [x] Add Greeks ingestion: Delta, Gamma, Theta, Vega
- [x] Add IV regime detection (low/normal/high)
- [x] Add ATM straddle price + breakout bands
- [x] Add option-chain liquidity filters (OI, volume, spread)
- [x] Merge signals in a `Decision Layer`

**Decision Layer rules**
- [x] Momentum score
- [x] Greeks score
- [x] Volatility score
- [x] Liquidity score
- [x] Final weighted score + veto rules

**Exit criteria**
- [x] False-signal reduction vs Phase 1
- [x] Improved risk-adjusted return in paper mode

---

## Phase 3 — Multi-Agent Orchestration (2–3 weeks)
- [x] Bootstrap and run [phase3_scaffold/README.md](phase3_scaffold/README.md)
- [x] Split responsibilities into agents:
  - [x] Market Regime Agent
  - [x] Options Structure Agent
  - [x] Risk Officer Agent
  - [x] Execution Planner Agent
- [x] Add arbitration/consensus policy via [phase3_scaffold/consensus.py](phase3_scaffold/consensus.py)
- [x] Add conflict resolution (e.g., risk veto > entry signal)
- [x] Add agent memory policy via [phase3_scaffold/memory.py](phase3_scaffold/memory.py)
- [x] Add release gate via [phase3_scaffold/phase3_release_check.py](phase3_scaffold/phase3_release_check.py)
- [x] Add quality gate via [phase3_scaffold/phase3_quality_check.py](phase3_scaffold/phase3_quality_check.py)
- [x] Add sign-off generator via [phase3_scaffold/phase3_signoff_generator.py](phase3_scaffold/phase3_signoff_generator.py)

**Exit criteria**
- [x] Stable consensus across stress scenarios
- [x] No policy violations in simulation runs

---

## Phase 4 — Advanced Institutional Features
- [x] Bootstrap and run [phase4_scaffold/README.md](phase4_scaffold/README.md)
- [x] Regime-aware models (trend day vs mean reversion day) via [phase4_scaffold/regime_model.py](phase4_scaffold/regime_model.py)
- [x] Event risk filter (RBI/Fed/CPI/earnings windows) via [phase4_scaffold/event_risk_filter.py](phase4_scaffold/event_risk_filter.py)
- [x] Time-of-day behavior (open/midday/closing hour) via [phase4_scaffold/time_behavior.py](phase4_scaffold/time_behavior.py)
- [x] Position sizing by volatility and confidence via [phase4_scaffold/position_sizer.py](phase4_scaffold/position_sizer.py)
- [x] Portfolio-level exposure controls via [phase4_scaffold/portfolio_controls.py](phase4_scaffold/portfolio_controls.py)
- [x] Live monitoring dashboard + alerts via [phase4_scaffold/phase4_monitor_runner.py](phase4_scaffold/phase4_monitor_runner.py)
- [x] Add release gate via [phase4_scaffold/phase4_release_check.py](phase4_scaffold/phase4_release_check.py)
- [x] Add quality gate via [phase4_scaffold/phase4_quality_check.py](phase4_scaffold/phase4_quality_check.py)
- [x] Add sign-off generator via [phase4_scaffold/phase4_signoff_generator.py](phase4_scaffold/phase4_signoff_generator.py)
- [x] Publish operational runbook via [phase4_scaffold/PHASE4_OPERATIONAL_RUNBOOK.md](phase4_scaffold/PHASE4_OPERATIONAL_RUNBOOK.md)
- [x] Execute incident rollback drill via [phase4_scaffold/phase4_incident_drill.py](phase4_scaffold/phase4_incident_drill.py)

**Exit criteria**
- [x] Operational runbook complete
- [x] Incident and rollback procedures tested

---

## Phase 5 — Controlled Integration (No Surprises)
- [x] Bootstrap and run [phase5_scaffold/README.md](phase5_scaffold/README.md)
- [x] Add feature flag: `INSTITUTIONAL_AGENT_ENABLED=false` by default via [phase5_scaffold/feature_flags.py](phase5_scaffold/feature_flags.py)
- [x] Shadow mode with current system (no order impact) via [phase5_scaffold/shadow_mode.py](phase5_scaffold/shadow_mode.py)
- [x] Compare decisions side by side for 2+ weeks (see [phase5_scaffold/reports/phase5_demo_shadow_duration_check.json](phase5_scaffold/reports/phase5_demo_shadow_duration_check.json))
- [x] Approve go-live checklist via [phase5_scaffold/go_live_gates.py](phase5_scaffold/go_live_gates.py)
- [x] Progressive rollout: 5% → 25% → 50% → 100% via [phase5_scaffold/rollout_policy.py](phase5_scaffold/rollout_policy.py)
- [x] Add release gate via [phase5_scaffold/phase5_release_check.py](phase5_scaffold/phase5_release_check.py)
- [x] Add quality gate via [phase5_scaffold/phase5_quality_check.py](phase5_scaffold/phase5_quality_check.py)
- [x] Add sign-off generator via [phase5_scaffold/phase5_signoff_generator.py](phase5_scaffold/phase5_signoff_generator.py)

**Go-live gates**
- [x] Performance threshold met
- [x] Risk threshold met
- [x] Monitoring + alerting active
- [x] Rollback tested

---

## Daily Execution Checklist (Operator)
- [x] Data feed health check passed (see [reports/daily_execution_checklist_2026-02-18.md](reports/daily_execution_checklist_2026-02-18.md))
- [x] Model/agent version pinned (see [reports/daily_execution_checklist_2026-02-18.md](reports/daily_execution_checklist_2026-02-18.md))
- [x] Risk limits loaded (see [reports/daily_execution_checklist_2026-02-18.md](reports/daily_execution_checklist_2026-02-18.md))
- [x] Feature flags verified (see [reports/daily_execution_checklist_2026-02-18.md](reports/daily_execution_checklist_2026-02-18.md))
- [x] Dry-run sanity checks passed (see [reports/daily_execution_checklist_2026-02-18.md](reports/daily_execution_checklist_2026-02-18.md))
- [x] Session log enabled (see [reports/daily_execution_checklist_2026-02-18.md](reports/daily_execution_checklist_2026-02-18.md))
- [x] End-of-day report exported (see [reports/daily_execution_checklist_2026-02-18.md](reports/daily_execution_checklist_2026-02-18.md))

---

## Minimum Artifacts Per Phase
- [x] Phase design note (see [reports/minimum_artifacts_matrix_2026-02-18.md](reports/minimum_artifacts_matrix_2026-02-18.md))
- [x] Test cases and outcomes (see [reports/minimum_artifacts_matrix_2026-02-18.md](reports/minimum_artifacts_matrix_2026-02-18.md))
- [x] Backtest / paper-trading summary (see [reports/minimum_artifacts_matrix_2026-02-18.md](reports/minimum_artifacts_matrix_2026-02-18.md))
- [x] Risk incidents log (if any) (see [reports/minimum_artifacts_matrix_2026-02-18.md](reports/minimum_artifacts_matrix_2026-02-18.md))
- [x] Final phase sign-off (see [reports/minimum_artifacts_matrix_2026-02-18.md](reports/minimum_artifacts_matrix_2026-02-18.md))

# Market Forensics Agent Checklist (V1)

## Scope (locked for V1)
- [x] Run mode: post-market only (no live intraday execution) for first 2-4 weeks.
- [x] Symbols: SENSEX and NIFTY50 only.
- [x] Inputs: 1m candles, OI/volume snapshots, existing signal/decision CSV outputs.

## Data Arrival Contract (daily folders)
- [x] Source root: `postmortem/<YYYY-MM-DD>/<SYMBOL>/`
- [x] Expected symbol folders from engine: `SENSEX`, `NIFTY50`, `BANKNIFTY` (and others if later added).
- [x] V1 processing allowlist: `SENSEX`, `NIFTY50` only.
- [x] Ignore non-allowlist folders in V1 but log that they were skipped.
- [x] Auto-detect latest trading-date folder at post-market run time.
- [x] Validate required files per symbol folder before processing:
- [x] `decision_journal.csv`
- [x] `signals.csv`
- [x] `paper_equity.csv`
- [x] `.signal_state.json`
- [x] `.opportunity_engine_state.json`
- [x] `.paper_trade_state.json`

## Phase 0: Setup
- [ ] Create folder structure: `forensics/raw`, `forensics/clean`, `forensics/output`, `forensics/pattern_db`.
- [ ] Define canonical timestamps and timezone handling.
- [ ] Add run config file (`symbols`, market hours, output paths, thresholds).
- [ ] Add job scheduler for daily post-market batch.
- [ ] Add run log + run id for traceability.

## Phase 1: Data Quality Gate (must pass before analysis)
- [ ] Deduplicate rows by stable keys (timestamp + symbol + strike + event type).
- [ ] Enforce monotonic timeline order per symbol.
- [x] Flag invalid quote rows (`bid <= 0`, `ask <= 0`, or missing contract fields).
- [x] Track data gap metrics (missing minutes, stale OI snapshots).
- [x] Output `quality_report_<date>.csv` and block downstream if quality is below threshold.
- [x] Implemented tool: `scripts/forensics_quality_gate.py` + launcher command `./start.sh quality-check`.

## Phase 2: Timeline Reconstruction
- [x] Build canonical timeline merge for decision/signal rows (V1 baseline).
- [ ] Extend merge to include candles + options snapshot streams in same timeline.
- [x] Create `regime_table` with per-window state:
- [x] Fields: `symbol,date,start_time,end_time,regime,vol_state,confidence`.
- [x] Create `turning_points` table:
- [x] Fields: `symbol,date,time,tp_type,strength,confirmations`.
- [x] Create `trigger_signals` table:
- [x] Fields: `symbol,date,time,trigger_type,context,score`.

## Phase 3: Forensics Engine
- [ ] Implement liquidity sweep detector.
- [ ] Implement OI shift detector (put-call pressure change, strike migration).
- [ ] Implement strike clustering detector.
- [ ] Implement sideways/trend detection (ATR + VWAP + range compression/expansion).
- [ ] Generate root-cause summary for each turning point.

## Phase 4: Pattern Library
- [ ] Store pattern templates in `pattern_db` with stable ids.
- [ ] Required fields:
- [ ] `pattern_id,symbol,regime,trigger_combo,sample_count,hit_rate,expectancy,last_seen,decay_score`.
- [ ] Add update logic with confidence weighting + decay for stale patterns.
- [ ] Keep daily snapshot and weekly merged view.

## Phase 5: Bot Rule Suggestion
- [x] Emit `bot_rules_update` proposals only from high-confidence patterns.
- [x] Include reason/explanation text for every proposed rule.
- [x] Include expected impact metrics (`win_rate_delta`, `drawdown_delta`, `cost_impact`).

## Automatic Approval (Yes, with guardrails)
- [x] Week 1-2: manual approval only.
- [x] Week 3+: auto-approve allowed only if all gates pass:
- [x] Gate 1: quality score >= 95% for last 5 trading days.
- [x] Gate 2: minimum sample count per pattern >= 30.
- [x] Gate 3: walk-forward test beats current baseline after fees/slippage.
- [x] Gate 4: projected max drawdown is not worse than baseline by > 5%.
- [x] Gate 5: no schema/data validation failures in current day.
- [x] Gate 6: canary deployment first (small capital or paper mode).
- [x] Add auto-rollback if live/canary breaches risk limits.

## Daily Outputs (must be produced every run)
- [x] `regime_table_<date>.csv`
- [x] `turning_points_<date>.csv`
- [x] `trigger_signals_<date>.csv`
- [x] `pattern_templates_<date>.csv`
- [x] `bot_rules_update_<date>.json`
- [x] `canary_metrics_<date>.json`
- [x] `quality_report_<date>.csv`
- [x] `run_summary_<date>.md`

## Acceptance Criteria (end of 2-4 week period)
- [ ] At least 20 clean trading-day runs completed.
- [ ] Zero broken runs due to schema/data integrity failures.
- [ ] Pattern DB has stable recurring patterns for both symbols.
- [ ] Suggested rules show measurable uplift in backtest vs baseline.
- [ ] Automatic approval is enabled only if all guardrails hold.

## Immediate Next Tasks (start now)
- [ ] Finalize config thresholds for quality and auto-approval gates.
- [x] Build Data Quality Gate first.
- [x] Build timeline reconstruction for SENSEX, then NIFTY50.
- [x] Add first daily run report template.

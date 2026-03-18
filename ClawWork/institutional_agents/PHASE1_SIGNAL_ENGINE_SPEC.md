# Phase 1 Signal Engine Spec (Paper Mode)

Status: Draft v1  
Date: 2026-02-18  
Scope: Standalone momentum-based signal engine for paper trading only.

## 1) Goal
Build a simple, reliable baseline engine that converts price/momentum inputs into:
- directional bias
- candidate strike
- risk-aware output payload

No live order execution in this phase.

## 2) Inputs (Minimum)
- `timestamp`
- `underlying` (`NIFTY50|BANKNIFTY|SENSEX`)
- `ltp`
- `prev_close`
- `change_pct`
- `session` (`OPEN|MIDDAY|CLOSE`)
- `risk_config` (caps and thresholds)

## 3) Derived Features
- momentum bucket:
  - `STRONG_UP`
  - `MILD_UP`
  - `RANGE`
  - `MILD_DOWN`
  - `STRONG_DOWN`
- strike context:
  - ATM
  - 1 step OTM
  - 1 step ITM

## 4) Decision Rules (Baseline)
1. If critical data missing -> `NO_TRADE`
2. If `change_pct >= bullish_threshold` -> `BUY_CALL`
3. If `change_pct <= bearish_threshold` -> `BUY_PUT`
4. Else -> `NO_TRADE`
5. Apply risk veto rules from Phase 0 policy

## 5) Confidence Mapping
- `HIGH`: strong momentum and all guards pass
- `MEDIUM`: valid signal but moderate momentum
- `LOW`: weak signal, use `NO_TRADE`

## 6) Output Schema
- `action`: `BUY_CALL|BUY_PUT|NO_TRADE`
- `confidence`: `LOW|MEDIUM|HIGH`
- `underlying`
- `preferred_strike`
- `stop_loss_pct`
- `target_pct`
- `rationale`
- `risk_checks`
- `model_version`

## 7) Non-Functional Requirements
- Deterministic outputs for same input
- Full decision log per signal
- Zero unhandled exceptions in backtest run

## 8) Phase 1 Boundaries
Included:
- price and change% logic
- strike suggestion
- risk fields
- paper-trading reports

Excluded (Phase 2+):
- Greeks/IV/straddle
- option chain microstructure scoring
- multi-agent consensus

## 9) Deliverables
- engine module (isolated)
- test case pack
- paper report for minimum 20 sessions
- baseline metrics summary

## 10) Sign-off
- [ ] Product owner
- [ ] Risk owner
- [ ] Engineering owner

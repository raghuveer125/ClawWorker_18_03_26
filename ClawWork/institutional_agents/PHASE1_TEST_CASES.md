# Phase 1 Test Cases (Signal Engine)

Status: Draft v1  
Date: 2026-02-18

## Test Matrix

## A) Data Quality
- [ ] A1: Missing `ltp` -> output `NO_TRADE`
- [ ] A2: Missing `change_pct` but calculable from prev close -> continue
- [ ] A3: Missing critical risk config -> `NO_TRADE`
- [ ] A4: Invalid underlying symbol -> reject with clear error

## B) Signal Logic
- [ ] B1: Strong bullish move -> `BUY_CALL`
- [ ] B2: Mild bullish move below threshold -> `NO_TRADE`
- [ ] B3: Strong bearish move -> `BUY_PUT`
- [ ] B4: Sideways move -> `NO_TRADE`

## C) Strike Selection
- [ ] C1: NIFTY strike rounding correct
- [ ] C2: BANKNIFTY strike rounding correct
- [ ] C3: SENSEX strike rounding correct
- [ ] C4: Preferred strike null when `NO_TRADE`

## D) Risk Guardrails
- [ ] D1: Daily loss cap breached -> veto (`NO_TRADE`)
- [ ] D2: Per-trade risk breach -> veto (`NO_TRADE`)
- [ ] D3: Spread/liquidity failure -> veto (`NO_TRADE`)
- [ ] D4: All guards pass -> allow signal

## E) Output Contract
- [ ] E1: Schema fields always present
- [ ] E2: `action` enum always valid
- [ ] E3: `confidence` enum always valid
- [ ] E4: rationale always non-empty

## F) Reliability
- [ ] F1: Batch run 500+ samples no crash
- [ ] F2: Determinism check (same input -> same output)

## Pass Criteria
- Required pass rate: `100%` for A, D, E
- Required pass rate: `>= 95%` for B, C, F

## Execution Log Template
- Date:
- Build/Version:
- Dataset:
- Passed:
- Failed:
- Notes:

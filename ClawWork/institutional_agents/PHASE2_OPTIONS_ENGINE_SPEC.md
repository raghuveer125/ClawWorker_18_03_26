# Phase 2 Options Engine Spec (Institutional MVP)

Status: Draft v1  
Date: 2026-02-18  
Scope: Add options-aware intelligence (Greeks, IV, straddle, liquidity) while keeping execution in isolated paper-mode workflows.

## 1) Goal
Enhance Phase 1 momentum-only decisions with options structure quality checks.

## 2) Inputs
- Underlying context: symbol, spot LTP, change pct, session
- Option chain rows: strike, CE/PE, LTP, OI, OI change, volume, spread
- Greeks: delta, gamma, theta, vega
- IV context: iv rank or percentile
- Straddle context: ATM straddle price and breakout bands

## 3) Outputs
- options_structure_signal: BULLISH | BEARISH | NEUTRAL | NO_TRADE
- preferred_strike_zone: ITM_1 | ATM | OTM_1 | NONE
- options_confidence: LOW | MEDIUM | HIGH
- options_rationale
- quality checks summary (liquidity, spread, data completeness)

## 4) Scoring Model (MVP)
- Greeks score (0-100)
- Volatility score (0-100)
- Liquidity score (0-100)
- Straddle breakout score (0-100)
- Weighted options score = 0.35*Greeks + 0.25*Vol + 0.25*Liquidity + 0.15*Straddle

## 5) Decision Layer Merge
- Input from Phase 1 momentum engine + options engine
- Produce final action using weighted voting with risk veto priority
- If risk veto OR critical data missing, force NO_TRADE

## 6) Guardrails
- Spread threshold must pass
- Minimum OI and volume thresholds must pass
- Missing critical Greeks fields => confidence downgrade or veto

## 7) Deliverables
- Isolated phase2 scaffold code
- Test matrix for Greeks/IV/straddle
- Paper report template for options-enhanced decisions

## 8) Exit Criteria
- Demonstrate reduced false signals vs Phase 1 baseline
- Maintain or improve risk-adjusted outcomes in paper mode

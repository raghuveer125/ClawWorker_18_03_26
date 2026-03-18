# Phase 0: Risk Policy (Institutional Agent)

Status: Draft v1  
Owner: Risk Officer  
Date: 2026-02-18

## 1) Purpose
Define hard risk constraints for all agent decisions. These are veto-level rules.

## 2) Global Hard Limits
- Max daily loss: `2.0%` of account balance
- Max per-trade risk: `0.5%` of account balance
- Max concurrent positions: `2`
- Max exposure in one underlying: `60%` of allowed risk budget
- No trade when critical data is missing

## 3) Entry Eligibility Rules
A trade is eligible only if all pass:
- Data completeness ≥ `95%` for required fields
- Bid-ask spread ≤ `50 bps`
- Minimum volume / OI threshold satisfied
- Risk budget available for the day
- No event blackout rule triggered (if configured)

## 4) Position Sizing Rules
- Position size derived from:
  - `risk_amount = account_balance * max_trade_risk_pct`
  - `stop_distance` based on policy stop loss
  - `quantity = floor(risk_amount / stop_distance)`
- If computed quantity < minimum lot constraint: `NO_TRADE`

## 5) Stop-Loss and Target Rules
- Initial stop-loss required for every trade
- Minimum stop-loss distance: `8%` option premium
- Maximum stop-loss distance: `20%` option premium
- Target must be at least `1.5R`
- Move to protected mode once `+1R` reached (implementation phase)

## 6) Session Controls
- If daily drawdown reaches `-2.0%`: halt all new entries
- If 3 consecutive losses: pause new entries for cooldown window
- No new entries in final `15` minutes for intraday mode

## 7) Veto Logic (Highest Priority)
If any of these fail, action must be `NO_TRADE`:
- Daily loss guard
- Data quality guard
- Liquidity guard
- Position size guard

## 8) Audit Requirements
Every decision must log:
- Decision timestamp and model version
- Risk checks (PASS/FAIL)
- Position size calculation inputs
- Final action and veto reason (if any)

## 9) Exception Handling
- Manual override allowed only by designated owner
- Override reason must be documented
- Override decisions excluded from model-performance scoring

## 10) Sign-off
- [ ] Risk owner approved thresholds
- [ ] Engineering validated enforcement
- [ ] Ops confirmed runbook compatibility

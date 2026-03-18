# Phase 0: Input/Output Schema (Institutional Agent)

Status: Draft v1  
Owner: Trading System Team  
Date: 2026-02-18

## 1) Scope
This schema defines the minimum contract between data ingestion, decision engines, and risk layers.

## 2) Required Inputs

### 2.1 Market Context
- `timestamp` (ISO8601, required)
- `session` (`PRE_OPEN|OPEN|MIDDAY|CLOSE`, required)
- `underlying` (`NIFTY50|BANKNIFTY|SENSEX`, required)
- `underlying_spot` (number > 0, required)
- `underlying_change_pct` (number, required)
- `prev_close` (number > 0, required)

### 2.2 Volatility + Structure
- `iv_rank` (0–100, optional for MVP, required in Phase 2)
- `iv_percentile` (0–100, optional for MVP, required in Phase 2)
- `atm_straddle_price` (number > 0, optional for MVP, required in Phase 2)
- `straddle_upper_band` (number, optional)
- `straddle_lower_band` (number, optional)

### 2.3 Option Chain (for institutional MVP)
- `option_chain` (array, optional in MVP)
  - each row:
    - `strike` (integer)
    - `type` (`CE|PE`)
    - `ltp` (number)
    - `oi` (number)
    - `oi_change` (number)
    - `volume` (number)
    - `bid_ask_spread_bps` (number)
    - `delta` (number)
    - `gamma` (number)
    - `theta` (number)
    - `vega` (number)

### 2.4 Risk + Account
- `account_balance` (number > 0, required)
- `max_daily_loss_pct` (number, required)
- `max_trade_risk_pct` (number, required)
- `open_positions` (array, required; can be empty)
- `daily_realized_pnl` (number, required)

## 3) Output Contract

### 3.1 Decision Object
- `action` (`BUY_CALL|BUY_PUT|NO_TRADE`, required)
- `confidence` (`LOW|MEDIUM|HIGH`, required)
- `underlying` (`NIFTY50|BANKNIFTY|SENSEX`, required)
- `preferred_strike` (integer, nullable if `NO_TRADE`)
- `entry_zone` (object, optional)
  - `low` (number)
  - `high` (number)
- `stop_loss_pct` (number, nullable if `NO_TRADE`)
- `target_pct` (number, nullable if `NO_TRADE`)
- `time_horizon_min` (integer, nullable)
- `rationale` (string, required)

### 3.2 Explainability + Audit
- `signal_breakdown` (object, required)
  - `momentum_score` (0–100)
  - `volatility_score` (0–100)
  - `liquidity_score` (0–100)
  - `greeks_score` (0–100; optional in MVP)
- `risk_checks` (object, required)
  - `daily_loss_guard` (`PASS|FAIL`)
  - `position_size_guard` (`PASS|FAIL`)
  - `liquidity_guard` (`PASS|FAIL`)
  - `data_quality_guard` (`PASS|FAIL`)
- `veto_reason` (string, nullable)
- `model_version` (string, required)
- `feature_flags` (object, required)

## 4) Validation Rules
- If any critical market input is missing, force `action=NO_TRADE`
- If any risk guard fails, force `action=NO_TRADE`
- If spread exceeds policy limit, force `action=NO_TRADE`
- `preferred_strike` must align with underlying strike step

## 5) Example (JSON)
```json
{
  "action": "BUY_CALL",
  "confidence": "MEDIUM",
  "underlying": "NIFTY50",
  "preferred_strike": 22150,
  "entry_zone": {"low": 120.0, "high": 128.0},
  "stop_loss_pct": 12.0,
  "target_pct": 24.0,
  "time_horizon_min": 45,
  "rationale": "Bullish momentum with acceptable spread and risk guards passing.",
  "signal_breakdown": {
    "momentum_score": 72,
    "volatility_score": 58,
    "liquidity_score": 81,
    "greeks_score": 65
  },
  "risk_checks": {
    "daily_loss_guard": "PASS",
    "position_size_guard": "PASS",
    "liquidity_guard": "PASS",
    "data_quality_guard": "PASS"
  },
  "veto_reason": null,
  "model_version": "institutional_mvp_v1",
  "feature_flags": {
    "INSTITUTIONAL_AGENT_ENABLED": false,
    "OPTIONS_ENGINE_ENABLED": false
  }
}
```

## 6) Sign-off
- [ ] Product owner approved
- [ ] Risk owner approved
- [ ] Engineering owner approved

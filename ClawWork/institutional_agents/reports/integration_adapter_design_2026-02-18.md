# Integration Adapter Design (Step 1)

Date: 2026-02-18

## Objective
Define a **shadow-only** adapter between ClawWork decision flow and institutional agents, with no live order authority.

## Baseline ClawWork Decision Path (Current)
- Trade signal generation and recommendations:
  - `livebench/trading/screener.py` (`run_screener`, `build_index_recommendations`)
- Tool entrypoint used by agent runtime:
  - `livebench/tools/direct_tools.py` (`fyers_run_screener`)
- Existing order endpoint and safety behavior:
  - `livebench/tools/direct_tools.py` (`fyers_place_order`)
  - Default protection: `FYERS_DRY_RUN=true`, `FYERS_ALLOW_LIVE_ORDERS=false`

## Proposed Adapter Entry Point
Add adapter call in `fyers_run_screener` **after baseline result is computed** and **before returning response**.

Adapter call shape (proposed):
- Input: baseline screener result + runtime metadata
- Output: institutional shadow decision package + comparison rows
- Failure handling: adapter errors are swallowed into `shadow_status`, baseline response remains primary

## Adapter Boundaries (Non-Negotiable)
- Adapter **must not call** `fyers_place_order`
- Adapter **must not mutate** baseline decision payload fields used by current flow
- Adapter **must only append** shadow fields and write shadow logs
- On any adapter failure: return baseline-only result with `shadow_status="failed_safe"`

## Proposed Files (Next Implementation Step)
- `institutional_agents/integration/shadow_adapter.py`
- `institutional_agents/integration/contracts.py`
- `institutional_agents/integration/logging.py`

## Input Mapping (ClawWork -> Institutional Shadow Input)
Source: `run_screener` response and index recommendation rows from `build_index_recommendations`

Mapped fields:
- `underlying` <= index recommendation `index`
- `timestamp` <= adapter runtime timestamp (ISO8601)
- `underlying_change_pct` <= index recommendation `change_pct`
- `trend_strength` <= normalized proxy from `change_pct` (temporary: clamp(change_pct / strong_trend_pct, -1, 1))
- `iv_percentile` <= placeholder default (until options feed is integrated)
- `options_bias` <= derived from baseline `signal` (`BULLISH/BEARISH/NEUTRAL`)
- `options_liquidity_ok` <= `true` (temporary, until options-chain checks integrated)
- `options_spread_ok` <= `true` (temporary)
- `daily_realized_pnl_pct` <= account/session context value if available else `0.0`
- `event_risk_high` <= `false` (temporary)

## Output Mapping (Institutional Shadow -> Comparison Record)
Comparison record fields:
- `timestamp`
- `underlying`
- `baseline_signal`
- `baseline_confidence`
- `institutional_action`
- `institutional_confidence`
- `institutional_weighted_score`
- `institutional_rationale`
- `veto_applied`
- `comparison_label` (`agree`, `disagree`, `baseline_only`, `institutional_only`, `adapter_failed_safe`)

## Shadow Log Target (Proposed)
- Path: `<agent_data>/trading/institutional_shadow.jsonl`
- One row per underlying per screener run
- Never overwrite baseline logs (`fyers_screener.jsonl`, `fyers_orders.jsonl`)

## Feature Flag Plan
Introduce adapter-specific flags (disabled by default):
- `INSTITUTIONAL_ADAPTER_ENABLED=false`
- `INSTITUTIONAL_SHADOW_MODE=true`

Routing behavior:
- If adapter flag is false: baseline-only
- If adapter flag true + shadow mode true: baseline + shadow log
- Live impact remains unchanged until explicit later stage

## Mapping Examples (3 Underlyings)

### Example A: NIFTY50
Baseline index row (source):
```json
{
  "index": "NIFTY50",
  "signal": "BULLISH",
  "change_pct": 0.86,
  "confidence": 74,
  "reason": "NIFTY50 shows bullish momentum"
}
```
Mapped shadow input (target):
```json
{
  "underlying": "NIFTY50",
  "timestamp": "2026-02-18T10:05:00+05:30",
  "underlying_change_pct": 0.86,
  "trend_strength": 1.0,
  "iv_percentile": 50.0,
  "options_bias": "BULLISH",
  "options_liquidity_ok": true,
  "options_spread_ok": true,
  "daily_realized_pnl_pct": 0.0,
  "event_risk_high": false
}
```

### Example B: BANKNIFTY
Baseline index row (source):
```json
{
  "index": "BANKNIFTY",
  "signal": "BEARISH",
  "change_pct": -0.72,
  "confidence": 69,
  "reason": "BANKNIFTY shows bearish momentum"
}
```
Mapped shadow input (target):
```json
{
  "underlying": "BANKNIFTY",
  "timestamp": "2026-02-18T10:05:00+05:30",
  "underlying_change_pct": -0.72,
  "trend_strength": -0.9,
  "iv_percentile": 50.0,
  "options_bias": "BEARISH",
  "options_liquidity_ok": true,
  "options_spread_ok": true,
  "daily_realized_pnl_pct": 0.0,
  "event_risk_high": false
}
```

### Example C: SENSEX
Baseline index row (source):
```json
{
  "index": "SENSEX",
  "signal": "NEUTRAL",
  "change_pct": 0.08,
  "confidence": 38,
  "reason": "SENSEX is range-bound"
}
```
Mapped shadow input (target):
```json
{
  "underlying": "SENSEX",
  "timestamp": "2026-02-18T10:05:00+05:30",
  "underlying_change_pct": 0.08,
  "trend_strength": 0.1,
  "iv_percentile": 50.0,
  "options_bias": "NEUTRAL",
  "options_liquidity_ok": true,
  "options_spread_ok": true,
  "daily_realized_pnl_pct": 0.0,
  "event_risk_high": false
}
```

## Explicitly Out of Scope (This Step)
- Any live-order routing changes
- Any changes to `fyers_place_order`
- Any production traffic shift

## Step-1 Exit
- Adapter entrypoint and mappings are documented
- Three-underlying mapping examples provided
- No order-execution authority introduced

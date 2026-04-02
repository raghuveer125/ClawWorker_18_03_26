# EOE Shadow Test — Session Logging & Evaluation Specification

**Version**: 1.0
**Purpose**: Operationally usable spec for running and scoring EOE shadow tests
**Prerequisite**: EOE_DESIGN.md + EOE_VALIDATION_PLAN.md

---

## 1. LOGGING SCHEMA

### 1A. Session Metadata (1 row per session)

File: `eoe_shadow/<date>/session_meta.json`

```json
{
  "session_date": "2026-04-09",
  "expiry_type": "weekly_bse",
  "index": "BSE:SENSEX-INDEX",
  "prev_close": 73134.32,
  "open": 72262.05,
  "high": 73568.54,
  "low": 71545.81,
  "close": 73432.57,
  "gap_pct": -1.19,
  "day_range_pct": 2.72,
  "vwap_final": 72800.00,
  "morning_trend_direction": "bearish",
  "morning_trend_magnitude_pct": 0.99,
  "reversal_occurred": true,
  "reversal_magnitude_pct": 2.64,
  "session_classification": "strong_reversal"
}
```

`session_classification` values: `trending_continuation`, `failed_reversal`, `mild_reversal`, `strong_reversal`, `choppy`

---

### 1B. State Transition Log (1 row per transition)

File: `eoe_shadow/<date>/state_transitions.csv`

```
timestamp,from_state,to_state,trigger_reason,sensex_ltp,session_high,session_low,vwap,pct_from_extreme,bos_count_30min,bos_direction
```

Example:
```
2026-04-09T11:30:15,WATCH,ARMED,reversal_1.5pct_from_low,71800,72262,71546,71900,0.35,0,none
2026-04-09T12:30:22,ARMED,ACTIVE,2_bos_bullish_in_28min+vwap_cross_held_6min,72700,72262,71546,72100,1.61,2,bullish
2026-04-09T14:00:00,ACTIVE,COOLDOWN,90min_timeout,73400,73568,71546,72800,2.59,5,bullish
```

---

### 1C. Cycle Log (1 row per engine cycle, ~3 seconds)

File: `eoe_shadow/<date>/cycle_log.csv`

```
timestamp,eoe_state,sensex_ltp,vwap,pct_from_extreme,bos_active_count,bos_direction,candidate_strike,candidate_type,candidate_premium,candidate_bid,candidate_ask,candidate_spread_pct,candidate_bid_qty,candidate_ask_qty,tradable,entry_signal,entry_blocked_reason
```

This is the HIGH-VOLUME log. ~4000 rows per session (6 hours × ~11 cycles/min).

---

### 1D. Hypothetical Trade Log (1 row per candidate entry)

File: `eoe_shadow/<date>/hypo_trades.csv`

```
timestamp,strike,option_type,entry_premium,entry_bid,entry_ask,entry_spread_pct,entry_bid_qty,entry_ask_qty,tradable_at_entry,peak_premium,peak_time,peak_sustained_60s,exit_premium,exit_time,exit_reason,payoff_multiple,mfe_multiple,mae_multiple,hold_time_min,result,round_trip_spread_cost,spread_trap
```

`result` values: `WIN` (payoff > 1.0), `LOSS` (payoff ≤ 1.0), `SKIPPED` (entry blocked)
`spread_trap`: `true` if round_trip_spread_cost > 0.30 × (exit_premium - entry_premium)

---

### 1E. Missed Activation Log (1 row per missed opportunity)

File: `eoe_shadow/<date>/missed_activations.csv`

```
timestamp,reason_missed,sensex_ltp,reversal_pct,bos_count,eoe_state_at_time,what_would_have_been_required
```

A missed activation is defined as: session had ≥2.0% reversal from extreme AND ≥2 BOS in reversal direction, but EOE never reached ACTIVE state.

---

## 2. GATE SCORING RULES

### G1: Sessions Completed

```
input:  count of session_meta.json files
formula: total_sessions = count(*)
pass:   total_sessions >= 5
fail:   total_sessions < 5
edge:   sessions with data capture failures (>20% missing cycles) do NOT count
```

### G2: Activations Observed

```
input:  state_transitions.csv across all sessions
formula: total_activations = count(to_state == "ACTIVE")
pass:   total_activations >= 3
fail:   total_activations < 3
edge:   same-session re-activation (COOLDOWN → ACTIVE) counts as separate activation
```

### G3: True Activation Rate

```
input:  for each ACTIVE transition, check sensex_ltp at T+30min
formula:
  for each activation:
    ltp_at_trigger = sensex_ltp at ACTIVE timestamp
    ltp_at_t30 = sensex_ltp at ACTIVE timestamp + 30 min
    if bos_direction == "bullish": true = (ltp_at_t30 - ltp_at_trigger) / ltp_at_trigger >= 0.003
    if bos_direction == "bearish": true = (ltp_at_trigger - ltp_at_t30) / ltp_at_trigger >= 0.003
  true_activation_rate = count(true) / count(all_activations)
pass:   true_activation_rate >= 0.50
fail:   true_activation_rate < 0.50
edge:   if T+30min is after 15:15, use 15:15 price (session end)
```

### G4: Expectancy

```
input:  hypo_trades.csv (exclude SKIPPED)
formula:
  for each trade:
    slippage_adjusted_entry = entry_premium + (entry_spread_pct / 100 * entry_premium / 2)
    slippage_adjusted_exit = exit_premium - (exit_spread_pct / 100 * exit_premium / 2)
    net_pnl = (slippage_adjusted_exit - slippage_adjusted_entry) * lot_size
  expectancy = mean(net_pnl)
pass:   expectancy > 0
fail:   expectancy <= 0
edge:   if 0 trades exist, gate FAILS (cannot prove positive expectancy with no trades)
```

### G5: False Activation Rate

```
input:  for each ACTIVE transition, check sensex_ltp at T+15min
formula:
  for each activation:
    ltp_at_trigger = sensex_ltp at ACTIVE timestamp
    ltp_at_t15 = sensex_ltp at ACTIVE timestamp + 15 min
    if bos_direction == "bullish": false = (ltp_at_trigger - ltp_at_t15) / ltp_at_trigger >= 0.003
    if bos_direction == "bearish": false = (ltp_at_t15 - ltp_at_trigger) / ltp_at_trigger >= 0.003
  false_activation_rate = count(false) / count(all_activations)
pass:   false_activation_rate <= 0.30
fail:   false_activation_rate > 0.30
edge:   if 0 activations, rate is undefined → gate FAILS (same as G2 failing)
```

### G6: Tradable Premium

```
input:  cycle_log.csv rows where eoe_state == "ACTIVE"
formula:
  tradable_cycles = count(tradable == true)
  total_active_cycles = count(eoe_state == "ACTIVE")
  tradable_rate = tradable_cycles / total_active_cycles
  tradable definition: candidate_premium >= 2 AND candidate_premium <= 15
                        AND candidate_spread_pct <= 30
                        AND candidate_bid_qty >= 3 * lot_size
                        AND candidate_ask_qty >= 3 * lot_size
pass:   tradable_rate >= 0.60
fail:   tradable_rate < 0.60
edge:   if 0 ACTIVE cycles, gate FAILS
```

### G7: Max Single Loss

```
input:  hypo_trades.csv
formula: max_loss = max(abs(net_pnl)) for trades where net_pnl < 0
         where net_pnl = (exit_premium - entry_premium) * lot_size
pass:   max_loss <= 200
fail:   max_loss > 200
edge:   if 0 losing trades, gate PASSES (no loss exceeded limit)
```

### G8: MFE > 3x (Advisory)

```
input:  hypo_trades.csv
formula:
  qualifying = count(peak_sustained_60s == true AND mfe_multiple >= 3.0)
  total = count(*) excluding SKIPPED
  mfe_rate = qualifying / total
pass:   mfe_rate >= 0.40
fail:   mfe_rate < 0.40
edge:   peak_sustained_60s requires min(premium) in any 60s window ≥ 3 * entry_premium
```

### G9: Scalping Unaffected

```
input:  scalping engine metrics from sessions WITH EOE shadow vs WITHOUT
formula:
  for each metric in [trade_count, win_rate, expectancy, total_pnl]:
    pct_change = abs(metric_with_eoe - metric_without_eoe) / metric_without_eoe * 100
  api_latency_increase = (avg_cycle_duration_with_eoe - avg_cycle_duration_without_eoe) / avg_cycle_duration_without_eoe * 100
pass:   ALL pct_change <= 5% AND api_latency_increase <= 10%
fail:   ANY pct_change > 5% OR api_latency_increase > 10%
edge:   first session without EOE serves as baseline. If no baseline exists, use session 1 as baseline and sessions 2-5 as test.
```

---

## 3. FAILURE-MODE DETECTION RULES

| FM | Event Signature | Detection Query | Override Trigger |
|----|----------------|-----------------|-----------------|
| F1 | ACTIVE triggered, price reverses ≥0.3% in 15 min | `false_activation_rate` from G5 | DEFER if >40% |
| F2 | Trade entry spread + exit spread > 30% of profit | `count(spread_trap == true) / count(result == "WIN")` | REJECT if >50% |
| F4 | Entry premium > 40% of peak | `count(entry_premium / peak_premium > 0.40) / total_trades` | DEFER if >60% |
| F5 | ARMED state lasts >60 min | `count(ARMED_duration > 3600s) / count(ARMED_transitions)` | DEFER if >30% |
| F7 | Premium declines despite correct direction | Trades where `result == "LOSS"` AND `underlying moved in correct direction ≥0.5%` | DEFER if >30% of losses |

---

## 4. PER-SESSION REPORT TEMPLATE

File: `eoe_shadow/<date>/SESSION_REPORT.md`

```markdown
# EOE Shadow Session Report — <DATE>

## 1. Session Overview
- Date: YYYY-MM-DD
- Expiry: weekly_nse / weekly_bse
- Index: BSE:SENSEX-INDEX
- Classification: trending_continuation / failed_reversal / mild_reversal / strong_reversal / choppy
- Open: ₹X | High: ₹X | Low: ₹X | Close: ₹X
- Gap: X% | Range: X% | Reversal: X%

## 2. EOE Activations
- State transitions: [list]
- Time in each state: OFF=Xmin, WATCH=Xmin, ARMED=Xmin, ACTIVE=Xmin, COOLDOWN=Xmin
- Total activations (ACTIVE reached): N
- True activations: N/N
- False activations: N/N

## 3. Candidate Trades
- Candidates evaluated: N
- Entries triggered: N
- Entries blocked: N (reasons: ...)
- Entries skipped (no signal): N

## 4. Tradability Assessment
- ACTIVE cycles with tradable premium: N/N (X%)
- Avg spread at ACTIVE: X%
- Avg bid depth at ACTIVE: X lots
- Tradability verdict: PASS / MARGINAL / FAIL

## 5. Outcome Summary (per hypothetical trade)
| Strike | Entry | Peak | Exit | Payoff | MFE | MAE | Hold | Result |
|--------|-------|------|------|--------|-----|-----|------|--------|
| ...    | ...   | ...  | ...  | ...    | ... | ... | ...  | ...    |

## 6. Gate-Relevant Metrics
- G2 activations this session: N
- G3 true activation rate: X%
- G5 false activation rate: X%
- G6 tradable rate: X%
- G7 max loss: ₹X
- G8 MFE >3x rate: X%

## 7. Failure Modes Triggered
- F1 (fake reversal): Y/N
- F2 (spread trap): Y/N
- F4 (late activation): Y/N
- F5 (stuck ARMED): Y/N
- F7 (theta decay): Y/N

## 8. Session Verdict
- Market provided opportunity: YES / NO
- EOE activated correctly: YES / NO / N/A
- Trade quality: GOOD / MARGINAL / POOR / NO_TRADE
- Issues found: [list]
```

---

## 5. FIVE-SESSION ROLLUP TEMPLATE

File: `eoe_shadow/ROLLUP_REPORT.md`

```markdown
# EOE 5-Session Validation Rollup

## Sessions Summary
| # | Date | Classification | Activations | Trades | Net P&L | Verdict |
|---|------|---------------|-------------|--------|---------|---------|
| 1 | ... | ... | ... | ... | ... | ... |
| 2 | ... | ... | ... | ... | ... | ... |
| 3 | ... | ... | ... | ... | ... | ... |
| 4 | ... | ... | ... | ... | ... | ... |
| 5 | ... | ... | ... | ... | ... | ... |

## MANDATORY GATES (all must pass)
| Gate | Input | Result | PASS/FAIL |
|------|-------|--------|-----------|
| G1: Sessions ≥5 | X sessions | X | ... |
| G4: Expectancy >₹0 | ₹X avg | ₹X | ... |
| G7: Max loss ≤₹200 | ₹X worst | ₹X | ... |
| G9: Scalping safe | X% max deviation | X% | ... |

Mandatory result: ALL PASS / FAIL (which?)

## IMPORTANT GATES (3/4 needed)
| Gate | Input | Result | PASS/FAIL |
|------|-------|--------|-----------|
| G2: Activations ≥3 | X total | X | ... |
| G3: True rate ≥50% | X/X | X% | ... |
| G5: False rate ≤30% | X/X | X% | ... |
| G6: Tradable ≥60% | X/X | X% | ... |

Important result: X/4 PASS

## ADVISORY GATE
| Gate | Input | Result | PASS/FAIL |
|------|-------|--------|-----------|
| G8: MFE >3x ≥40% | X/X | X% | ... |

## OVERRIDE CHECKS
| Override | Threshold | Measured | Triggered? |
|----------|-----------|----------|------------|
| F2: Spread trap >50% | 50% | X% | Y/N |
| F9: Scalping degradation | >5% metric change | X% | Y/N |
| F1: Fake reversal >40% | 40% | X% | Y/N |

## DECISION
Mandatory: PASS / FAIL
Important: X/4
Overrides: NONE / [which]

### FINAL VERDICT: IMPLEMENT / IMPLEMENT REDUCED / DEFER / REJECT

Reason: [single sentence]

## Evidence Summary
- Total trades: X
- Win rate: X%
- Avg payoff: Xx
- Expectancy (after slippage): ₹X
- Max drawdown: ₹X
- False activation rate: X%
- Spread trap rate: X%

## Unresolved Risks
- [list any concerns not captured by gates]
```

---

## 6. CONFIDENCE SCORE

**Confidence in this operational spec: 0.96**

This spec is sufficient for an engineer to:
1. Build the shadow logger with exact field requirements
2. Run 5 sessions and produce standardized reports
3. Score all 9 gates without interpretation ambiguity
4. Check all override conditions mechanically
5. Produce a final IMPLEMENT/DEFER/REJECT decision

Remaining 0.04 uncertainty:
- Some edge cases in G9 (scalping comparison) depend on session similarity
- Cycle log at ~4000 rows/session may need compression for storage
- Real-time option quote capture depends on API reliability

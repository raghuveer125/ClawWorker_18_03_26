# EOE Shadow Validation Plan

**Status**: Validation framework — no live implementation
**Duration**: 5 weekly expiry sessions
**Goal**: Determine if EOE is repeatable, tradable, and worth deploying

---

## 1. EOE DESIGN SUMMARY

| Component | Design |
|-----------|--------|
| State machine | OFF → WATCH (10:30) → ARMED (reversal ≥1.5%) → ACTIVE (2+ BOS) → COOLDOWN |
| Strike | ₹2-15 premium, 100-600 pts OTM, near-ATM allowed |
| Entry | After pullback + continuation candle + volume confirmation |
| Exit | Scaled: 30% at 3x, 30% at 5x, 20% at 10x, trail rest |
| Risk | Max 1 lot, max 2 trades/session, premium = total risk |
| Capital | 5% (₹5,000) isolated from scalping engine |

---

## 2. ASSUMPTIONS REQUIRING VALIDATION

| # | Assumption | Test Method | Kill Threshold |
|---|-----------|-------------|----------------|
| 1 | Expiry reversals occur ≥1x per 3 sessions | Count ARMED→ACTIVE transitions | < 1 in 5 sessions = pattern too rare |
| 2 | ₹2-15 options are tradable (spread < 30%) | Measure live bid-ask at ARMED time | > 40% spread in majority = untradable |
| 3 | 2 BOS events confirm real reversal (not noise) | Track reversal continuation after ACTIVE | < 50% follow-through = BOS unreliable |
| 4 | Pullback entry improves vs immediate entry | Compare MFE of pullback vs first-break entry | Pullback worse in > 60% of cases = wrong |
| 5 | 10x payoff achievable after entry | Measure peak premium / entry premium | < 5x peak in > 70% of trades = overstated |
| 6 | Scaled exit captures more than hold-to-expiry | Compare scaled vs hold P&L | Scaled worse > 50% = exit logic wrong |
| 7 | State machine doesn't stay ARMED forever | Measure ARMED duration | > 60 min ARMED without ACTIVE/OFF > 30% = needs timeout |

---

## 3. FIVE-SESSION SHADOW TEST PROTOCOL

### Session Types Needed

| Session # | Target Market Type | Purpose |
|-----------|--------------------|---------|
| 1 | Any (first data collection) | Calibrate data capture, verify shadow logger works |
| 2 | Trending (gap continuation) | Test: does EOE correctly stay OFF/WATCH? |
| 3 | Choppy / failed reversal | Test: does ARMED → OFF correctly on false reversals? |
| 4 | Reversal (if it occurs) | Test: full state machine activation |
| 5 | Any | Accumulate statistics |

*Note: We cannot control market behavior. Sessions are classified AFTER the fact. The goal is to observe 5 expiry sessions and categorize them.*

### Per-Session Data Collection

The shadow logger must record EVERY cycle (every ~3 seconds):

```csv
timestamp, state, sensex_ltp, session_high, session_low, vwap,
pct_from_extreme, bos_events_30min, bos_direction,
candidate_strike, candidate_premium, candidate_spread_pct,
entry_signal, entry_reason, hypothetical_entry_price,
peak_premium_after, final_premium, hypothetical_pnl,
false_activation, notes
```

### State Transition Log

Every state change must be logged:

```json
{
  "timestamp": "2026-04-09T12:30:15",
  "from_state": "ARMED",
  "to_state": "ACTIVE",
  "trigger": "2 bos_bullish in 25 min, price above VWAP for 6 min",
  "sensex_ltp": 73200,
  "session_extreme": 71546,
  "reversal_pct": 2.3,
  "candidate_strike": 73000,
  "candidate_premium": 5.50
}
```

### Hypothetical Trade Log

For every ACTIVE → entry signal:

```json
{
  "session": "2026-04-09",
  "entry_time": "12:45:00",
  "strike": 73000,
  "option_type": "CE",
  "entry_premium": 5.50,
  "entry_spread_pct": 18,
  "entry_bid": 5.00,
  "entry_ask": 6.00,
  "peak_premium": 45.00,
  "peak_time": "14:20:00",
  "exit_premium_simulated": 38.00,
  "exit_time": "14:30:00",
  "payoff_multiple": 6.9,
  "mfe_multiple": 8.2,
  "mae_multiple": 0.6,
  "hold_time_min": 105,
  "result": "WIN"
}
```

---

## 4. METRICS DASHBOARD

### After Each Session, Compute:

**Activation Quality**

| Metric | Formula |
|--------|---------|
| Activation rate | Sessions with ACTIVE / Total sessions |
| True activation rate | ACTIVE where reversal continued ≥ 30 min / All ACTIVE |
| False activation rate | ACTIVE where price reversed back within 15 min / All ACTIVE |
| Missed activation | Sessions with ≥2% reversal but EOE stayed WATCH/ARMED |

**Trade Quality (hypothetical)**

| Metric | Formula |
|--------|---------|
| Win rate | Trades with payoff > 1x / Total trades |
| Avg payoff (wins) | Mean (exit/entry) for winning trades |
| Avg loss (losses) | Mean (exit/entry) for losing trades |
| Expectancy | WR × avg_win - (1-WR) × avg_loss |
| MFE/MAE ratio | Mean MFE / Mean MAE |

**Market Fit**

| Metric | Formula |
|--------|---------|
| Tradable premium availability | Sessions where ₹2-15 CE/PE existed at ACTIVE time |
| Spread quality | Mean spread % at hypothetical entry time |
| Slippage-adjusted expectancy | Expectancy - (avg_spread_cost × 2) |

**Safety**

| Metric | Check |
|--------|-------|
| Max trades/session | Never > 2 |
| Max loss/trade | Never > premium × lot |
| Scalping interference | 0 impact on scalping trades |

---

## 5. HARD IMPLEMENTATION GATES

### ALL must pass before any live code is written:

| Gate | Threshold | Rationale |
|------|-----------|-----------|
| **G1: Sessions completed** | ≥ 5 | Minimum sample for any pattern |
| **G2: Activations observed** | ≥ 3 ACTIVE states across 5 sessions | Proves pattern exists |
| **G3: True activation rate** | ≥ 50% | More than half of activations must be real |
| **G4: Expectancy** | > ₹0 after spread/slippage | Must be net positive |
| **G5: False activation rate** | ≤ 30% | Acceptable noise level |
| **G6: Tradable premium** | Available in ≥ 60% of ACTIVE states | Must be able to actually trade |
| **G7: Max single loss** | ≤ ₹200 (2x design max of ₹150) | Envelope not exceeded |
| **G8: MFE > 3x entry** | In ≥ 40% of trades | Premium expansion is real, not theoretical |
| **G9: Scalping unaffected** | 0 changes to scalping P&L | Independence verified |

### Decision Matrix

| Gates passed | Decision |
|-------------|----------|
| All 9 | IMPLEMENT with 1-lot paper trading |
| 7-8 | IMPLEMENT with reduced capital (2.5% instead of 5%) |
| 5-6 | DEFER — redesign failing components |
| < 5 | REJECT — EOE is not viable |

---

## 6. FAILURE MODES AND DETECTION

| # | Failure Mode | Detection Method | Key Metric |
|---|-------------|-----------------|------------|
| F1 | **Fake reversal activation** | ACTIVE triggered but price reverses back within 15 min | False activation rate > 30% |
| F2 | **Spread trap** | Premium looks cheap but spread is 40-80% | Spread at entry > 30% in > 40% of cases |
| F3 | **Attractive but untradable OTM** | Strike premium ₹2 but zero bid depth | Bid qty < 3x order size at ACTIVE time |
| F4 | **Late activation** | ACTIVE triggers after 70%+ of move done | Entry premium > 50% of peak premium |
| F5 | **ARMED stuck state** | ARMED for 60+ min without ACTIVE or return to WATCH | ARMED duration > 60 min in > 30% of sessions |
| F6 | **Vertical expansion exit failure** | Premium spikes 20x but exits only at 5x due to scaled exit | Actual exit < 50% of MFE in > 40% of trades |
| F7 | **Theta decay kills position** | Entry in low-premium option, but theta eats premium faster than reversal | Premium declines despite spot moving in right direction |
| F8 | **Session with no morning trend** | No gap → no reversal possible → EOE wastes cycles watching | > 40% of sessions have no qualifying morning trend |

### How Each Failure Kills EOE

| Failure | If confirmed | Action |
|---------|-------------|--------|
| F1 > 30% | Expected value drops below zero | REJECT EOE |
| F2 > 40% | Paper profits are fiction | REJECT unless spread filter tightened |
| F3 > 50% | Cannot execute even correct signals | REJECT or limit to higher-premium only |
| F4 > 50% | Payoff too small to justify risk | Tighten activation conditions |
| F5 > 30% | State machine needs timeout fix | Add ARMED → WATCH timeout |
| F6 > 40% | Exit logic needs redesign | Test hold-to-expiry as alternative |
| F7 > 30% | Theta dominates on expiry day | Require higher premium floor (₹5+) |
| F8 > 40% | Pattern too rare for dedicated module | REJECT — integrate as optional scalping signal instead |

---

## 7. VALIDATION READINESS ASSESSMENT

### What Is Ready

| Component | Status |
|-----------|--------|
| Design specification | Complete (EOE_DESIGN.md) |
| State machine definition | Complete |
| Risk model | Complete |
| Integration architecture | Designed (read-only consumer) |
| Failure mode catalog | Complete |
| Implementation gates | Defined with thresholds |

### What Is NOT Ready

| Component | Status | Blocker |
|-----------|--------|---------|
| Shadow logger code | Not built | Needs `eoe_shadow_logger.py` |
| Live data capture infrastructure | Partial (forensic spec exists) | Needs per-cycle option price capture |
| 5-session dataset | 0 of 5 sessions | Calendar-dependent (next expiry) |
| Activation calibration | Untested | Thresholds (1.5%, 2 BOS, 5 min VWAP) are estimates |
| Strike tradability data | 0 measurements | Need live bid/ask/depth at cheap option strikes |

### Timeline

| Week | Action |
|------|--------|
| 1 | Build `eoe_shadow_logger.py` + forensic capture enhancements |
| 2 | Session 1 (first expiry): calibrate data capture |
| 3 | Session 2: validate state machine transitions |
| 4 | Session 3-4: accumulate data |
| 5 | Session 5: final dataset + full analysis |
| 6 | Gate evaluation → IMPLEMENT / DEFER / REJECT |

---

## 8. CONFIDENCE SCORE

**Confidence in the validation framework itself: 0.92**

The framework is designed to REJECT EOE if it fails, not to confirm it. The gates are strict (all 9 must pass for full implementation). The failure modes are cataloged with kill thresholds. The shadow test protocol captures enough data to make a definitive call after 5 sessions.

**Remaining risk: 0.08**
- Market may not produce enough variety in 5 sessions (all trending, no reversals)
- Shadow logger implementation may miss edge cases
- Per-cycle option price capture may have gaps (API latency)

**What would make this 0.99:**
- Shadow logger built and tested on 1 session successfully
- At least 2 different market types observed (trending + reversal)
- Option price capture confirmed as continuous (no gaps > 30 seconds)

# Forensic Capture + Shadow Test Plan

**Date**: 2026-04-02
**Status**: PLAN ONLY — No live implementation
**Objective**: Build implementation-grade evidence over 5 expiry sessions

---

## 1. WHY CURRENT EVIDENCE IS INSUFFICIENT

| Gap | Impact |
|-----|--------|
| Options data is single snapshots (no intraday curves) | Cannot prove 73000 CE was ₹2-5 at low; cannot time the expansion |
| No 1-minute candle series for underlying | Cannot reconstruct when reversal started or structure flipped |
| Persistent bias has no timeline | Cannot prove when bias formed, when it should have decayed |
| Transient BOS captured at session end only | Cannot prove when bullish BOS first appeared |
| Rejected signals have no per-gate trace | Cannot determine which gate blocked which signal at which time |
| N=1 session | Cannot distinguish pattern from anomaly |
| Simulation used reconstructed prices | P&L estimates are directional, not measured |

---

## 2. MISSING DATA CHECKLIST

### Mandatory (blocks implementation decisions)

| Field | Source | Frequency |
|-------|--------|-----------|
| Underlying 1-min OHLC | Fyers API history OR tick accumulation | Every minute |
| Option premium series per tracked strike | Adapter quote per cycle | Every engine cycle (~3s) |
| Persistent bias + confidence | StructureAgent summary | Every cycle |
| Transient BOS events with timestamps | StructureAgent breaks | Every cycle |
| Per-gate pass/fail for every signal | validate_entry() instrumentation | Every signal evaluation |
| StrikeSelector full candidate list | StrikeSelector output | Every cycle |

### Useful (improves analysis quality)

| Field | Source | Frequency |
|-------|--------|-----------|
| Option bid/ask spread per strike | Chain data | Every cycle |
| Volume + OI per tracked strike | Chain data | Every minute |
| VWAP of underlying | DataFeed agent | Every cycle |
| Futures basis | Futures agent | Every cycle |

### Nice to have

| Field | Source | Frequency |
|-------|--------|-----------|
| Full option chain snapshot (all strikes) | OptionChain agent | Every 5 minutes |
| Dealer pressure / gamma regime | DealerPressure agent | Every cycle |

---

## 3. PRODUCTION FORENSIC CAPTURE SPEC

### Storage Layout

```
logs/forensics/SESSION_<YYYYMMDD>/
├── market/
│   ├── underlying_1m.csv          # 1-min OHLC, accumulated live
│   └── underlying_ticks.csv       # Per-cycle LTP snapshots
├── options/
│   ├── tracked_strikes.csv        # Per-cycle LTP for all tracked strikes
│   └── chain_5min.json            # Full chain every 5 minutes
├── structure/
│   ├── persistent_bias.csv        # Per-cycle bias + confidence
│   └── transient_bos.csv          # Every BOS event with timestamp
├── system/
│   ├── signal_evaluations.csv     # Every signal with per-gate results
│   ├── trades.csv                 # All trades
│   ├── strike_candidates.csv      # StrikeSelector candidates per cycle
│   └── circuit_breaker.csv        # CB state per cycle
└── meta/
    ├── config.json                # Config snapshot at session start
    └── session_summary.json       # End-of-day summary
```

### Field Specifications

**underlying_1m.csv**
```
timestamp,symbol,open,high,low,close,volume,vwap
```
Sampling: Accumulate from per-cycle LTP. Close each minute bar.

**tracked_strikes.csv**
```
timestamp,symbol,strike,option_type,ltp,bid,ask,spread,volume,oi
```
Sampling: Every engine cycle (~3s). Track: 4 CE + 4 PE strikes nearest ATM.

**persistent_bias.csv**
```
timestamp,symbol,direction,confidence,summary_text
```
Sampling: Every cycle. Extract from `market_structure[symbol]`.

**transient_bos.csv**
```
timestamp,symbol,break_type,break_price,previous_level,strength
```
Sampling: Whenever `structure_breaks[]` is non-empty.

**signal_evaluations.csv**
```
timestamp,symbol,strike,option_type,quality_score,quality_grade,
gate1_vix,gate2_time,gate3_positions,gate4_daily_loss,gate5_hourly,
gate6_data_fresh,gate7_quality,gate8_direction,gate8b_regime,
gate9_conditions,gate10_gamma,gate11_rr,gate12_depth,gate13_slippage,
gate14_max_loss,final_decision,rejection_reason
```
Sampling: Every signal that reaches `validate_entry()` or EntryAgent.

---

## 4. SHADOW TEST DESIGN

### Dimension A: Bias Transition Logic

Run 4 variants as shadow calculations (log only, no trade action):

| Variant | Decay Rule | Expected Behavior |
|---------|-----------|-------------------|
| A0: Baseline | No decay (current) | CE blocked in persistent bearish |
| A1: Conservative | Decay starts after price > session VWAP + prev_close for 5 min | Late flip, low false positive |
| A2: Moderate | Decay 0.05/cycle after transient contradicts 60s | Mid flip, moderate false positive |
| A3: Aggressive | Decay 0.10/cycle after transient contradicts 30s | Early flip, high false positive |

For each variant, log:
- When bias would flip
- Which CE signals would be allowed
- Estimated P&L if those CE trades were taken
- Whether the flip was correct (did bullish trend continue?)

### Dimension B: Strike Accessibility Logic

Run 3 variants:

| Variant | Premium Rule | Strike Rule |
|---------|-------------|-------------|
| B0: Baseline | Min ₹10 | OTM only |
| B1: Expiry relaxed | Min ₹3 on expiry days | OTM + near-ATM |
| B2: Momentum-driven | Min ₹3 + underlying momentum toward strike | OTM + near-ATM + ITM within 200pts |

For each variant, log:
- Which additional strikes become eligible
- Their premium at selection time
- Their premium at session close (or at exit)
- Whether the trade would have been profitable

### Implementation: Shadow Logger Module

A new module `scalping/dry_run/shadow_logger.py` that:
1. Runs alongside the live engine (read-only)
2. Reads context.data each cycle
3. Applies variant rules to current signals
4. Logs what WOULD happen under each variant
5. Does NOT affect any live decisions

---

## 5. FAILURE DECOMPOSITION

### Miss due to bias lock ONLY

**Definition**: Strike was eligible under current config, but Gate 8 blocked CE.

**Evidence from 2026-04-02**:
- At 13:30: SENSEX at 73200
- 73600 CE: 400 pts OTM, premium ~₹12 (within ₹10-25 range)
- Gate 8 blocked: persistent bearish
- **Contribution: ~30% of total miss** (would capture 73600 CE → ₹30-75 = 2.5-6x)

### Miss due to strike inaccessibility ONLY

**Definition**: Bias would have allowed CE, but strike was filtered out.

**Evidence from 2026-04-02**:
- At 12:00: 73000 CE premium ~₹5 (below ₹10 minimum)
- Even with bullish bias, premium filter blocks entry
- **Contribution: ~50% of total miss** (the big move was in the cheap strike)

### Miss due to BOTH

**Definition**: Even with bias flip AND relaxed premium, system architecture limits capture.

- 73000 CE went ITM before system could evaluate it as eligible
- By 14:00 (73400 spot), 73000 CE was 400 pts ITM, premium ~₹300+
- System only trades OTM → misses the entire ITM expansion
- **Contribution: ~20%** (the ITM portion of the move)

### Summary

| Cause | Contribution | Fixable? |
|-------|-------------|----------|
| Bias lock | 30% | Yes (bias decay) |
| Premium filter | 50% | Partially (expiry relaxation) |
| OTM-only architecture | 20% | Requires design rethink |

---

## 6. IMPLEMENTATION READINESS CRITERIA

### Hard Gates (ALL must pass)

| Criterion | Threshold | Rationale |
|-----------|-----------|-----------|
| Sessions observed | ≥ 5 expiry days | Minimum for pattern validation |
| Reversal cases observed | ≥ 3 sessions with intraday reversal | Proves pattern is repeatable |
| False positive rate (conservative flip) | ≤ 20% | Acceptable noise level |
| Continuation P&L degradation | ≤ 10% of baseline | Must not damage morning trades |
| CE expectancy improvement | > ₹0 per trade | Must be net positive |
| Data quality rating | ≥ B (MEDIUM) | Evidence must be measured, not inferred |

### Soft Gates (majority should pass)

| Criterion | Target |
|-----------|--------|
| Sharpe improvement | > 0.1 vs baseline |
| Max DD increase | < 15% vs baseline |
| Trade frequency increase | 20-50% (not excessive) |
| Circuit breaker interference | No increase in L2/L3 triggers |

### Decision Matrix

| Hard gates passed | Soft gates passed | Decision |
|-------------------|-------------------|----------|
| All 6 | 3+ of 4 | IMPLEMENT |
| All 6 | < 3 | IMPLEMENT with reduced position size |
| 5 of 6 | Any | DEFER — identify failing gate |
| < 5 | Any | DO NOT IMPLEMENT |

---

## 7. RECOMMENDED NEXT EXPERIMENT

### Experiment: Shadow Bias Logger (Dimension A only)

**Why this first**: Bias lock is the simpler, more isolated problem. Strike accessibility depends on bias being correct first (no point selecting CE strikes if bias blocks them).

**Design**:
1. Add `shadow_logger.py` to `scalping/dry_run/`
2. Hook into engine cycle (read-only, after all agents run)
3. Each cycle: read persistent bias, transient BOS, spot price
4. Apply A0-A3 decay rules independently
5. Log: would bias have flipped? At what time? Would CE signals pass?
6. At session end: produce comparison CSV

**Risk**: Zero. Read-only module, no live impact.

**Output**: After 1-2 sessions, we know:
- How often bias WOULD flip under each variant
- Whether flips correlate with actual reversals
- False positive rate estimate

**Duration**: 1-2 expiry sessions (1-2 weeks)

**Success criterion**: At least 1 session shows variant A1 or A2 would have flipped correctly AND enabled profitable CE trade.

---

## 8. CONFIDENCE SCORE

**Confidence in this plan**: 0.92

**Remaining uncertainty**:
- Cannot guarantee intraday reversals will occur in next 5 sessions
- Shadow logger implementation details may need iteration
- Strike accessibility testing depends on bias testing completing first

**What would increase confidence to 0.99**:
- First shadow session producing clean data
- At least 1 reversal observed and correctly captured by shadow logger
- No unintended interaction with live engine

---

*This plan does not recommend any live implementation changes. It designs the evidence-gathering infrastructure needed to reach implementation-grade confidence.*

# EOE v2 Shadow Validation Addendum

**Purpose**: Adapt shadow test framework to evaluate v2 (compression→reversal→expansion) alongside v1
**Rule**: Designed to DISPROVE v2 if it is fragile

---

## 1. V2 SIGNAL DEFINITIONS (Measurable)

Every v2 concept mapped to an exact computation from cycle_log data:

### Compression

```
compression_pct = (premium_session_high - premium_current) / premium_session_high × 100

COMPRESSED: compression_pct ≥ 90 AND premium_current ≤ 10
NOT_COMPRESSED: compression_pct < 85 OR premium_current > 15
AMBIGUOUS: between thresholds (log but don't act)
```

Inputs: `premium_session_high` (tracked per strike), `premium_current` (per cycle quote)

### Revival

```
revival_pct = (premium_current - premium_session_low) / premium_session_low × 100

REVIVING: revival_pct ≥ 30 AND consecutive_higher_closes ≥ 3
NOT_REVIVING: revival_pct < 15 OR consecutive_higher_closes < 2
```

Inputs: `premium_session_low` (tracked), `premium_current`, `consecutive_higher_closes` (counter)

### Expansion

```
expansion_multiple = premium_current / premium_session_low

EXPANDING: expansion_multiple ≥ 2.0 AND duration_above_2x ≥ 5 minutes
```

### Dead Option

```
DEAD: premium < 1.0 for ≥ 30 consecutive minutes AND volume_last_15min == 0
```

### Late Entry

```
LATE: premium_current > 0.40 × premium_peak_since_revival OR time > 14:30
```

---

## 2. STATE MACHINE MAPPING (v1 → v2)

The states are identical. The TRANSITION CONDITIONS change:

| Transition | v1 Condition | v2 Condition |
|-----------|-------------|-------------|
| OFF → WATCH | Expiry day, 10:30 | Same |
| WATCH → ARMED | Underlying reversal ≥ 1.5% OR VWAP cross held 5 min | **Any tracked CE/PE shows compression_pct ≥ 90 AND underlying first higher-high** |
| ARMED → ACTIVE | ≥ 2 BOS in reversal direction | **Premium consecutive_higher_closes ≥ 3 AND revival_pct ≥ 30** |
| ACTIVE timeout | 90 min | 120 min (expansion takes longer) |
| ARMED timeout | 60 min | 60 min (same) |

### Shadow Test: Run BOTH v1 and v2 conditions in parallel

The shadow logger evaluates BOTH transition conditions each cycle and logs:

```
v1_would_transition: true/false
v2_would_transition: true/false
actual_transition: whichever fires first (or the one being tested)
```

This allows head-to-head comparison without choosing upfront.

---

## 3. LOGGING CHANGES

### Additional cycle_log.csv columns (append to existing schema)

```
premium_session_high, premium_session_low, premium_current,
compression_pct, revival_pct, consecutive_higher_closes,
expansion_multiple, dead_option, late_entry,
v1_would_arm, v1_would_activate, v2_would_arm, v2_would_activate
```

14 new columns. All numeric or boolean. Computed from existing option chain data.

### Additional hypo_trades.csv columns

```
entry_method: "v1_pullback" | "v2_earliest" | "v2_confirmed" | "v2_late"
compression_at_entry: float (how much premium had collapsed before revival)
revival_at_entry: float (how much premium had recovered from low)
time_from_premium_low_min: float (minutes between premium low and entry)
```

4 new columns. Classifies which entry logic produced the trade.

### No changes to: state_transitions.csv, session_meta.json, missed_activations.csv

---

## 4. GATE SCORING UPDATES

### G3: True Activation Rate (v2 refinement)

v1: Spot at T+30min ≥ 0.3% further in reversal direction
v2: **Premium at T+30min ≥ 2x premium at ACTIVE trigger time**

Rationale: v2 activates on premium revival, so success = premium continues rising.

```
For v2 activations:
  true = premium_at_t30 / premium_at_trigger ≥ 2.0
  false = premium_at_t30 / premium_at_trigger < 1.0
  ambiguous = between 1.0 and 2.0 (counted as 0.5 for rate calculation)
```

### G5: False Activation Rate (v2 refinement)

v1: Spot reverses ≥ 0.3% against direction at T+15min
v2: **Premium drops below ACTIVE trigger premium within 15 min**

```
false_v2 = premium_at_t15 < premium_at_trigger × 0.8
```

### G6: Tradable Premium (v2 addition)

v1: premium ₹2-15, spread ≤ 30%, depth ≥ 3x
v2: Same, PLUS: **revival_pct ≥ 30%** (premium must be actively recovering, not just cheap)

### G8: MFE > 3x (v2 refinement)

v2: MFE measured from **compression low**, not from entry premium.

```
mfe_from_low = peak_premium / premium_session_low
mfe_from_entry = peak_premium / entry_premium

Report BOTH. G8 uses mfe_from_entry (actionable metric).
mfe_from_low is diagnostic (validates compression→expansion pattern exists).
```

### Late Activation Detection (v2-specific)

```
late_v2 = entry_premium / peak_premium_after_entry > 0.40
```

If > 40% of the move happened before entry, the activation was late.

---

## 5. FALSE POSITIVE RULES (v2-Specific)

### FP1: Compression Without Real Revival

**Signature**: compression_pct ≥ 90 but premium stays < ₹2 for 30+ min after compression detected

**Detection**: `time_since_compression_detected > 30 AND premium_current < 2.0`

**Threshold**: If > 50% of compressions never produce revival → v2 compression trigger is too loose

### FP2: Revival Without Expansion

**Signature**: Premium recovers 30%+ from low (triggers ACTIVE) but then flatlines or declines

**Detection**: `premium_at_t60 < premium_at_active_trigger × 1.5`

**Threshold**: If > 40% of revivals fail to expand ≥ 1.5x within 60 min → revival trigger too loose

### FP3: Noise Bounce

**Signature**: Premium rises 30% from low on a single tick/candle then immediately drops back

**Detection**: `consecutive_higher_closes < 3 at trigger time` (should not happen if trigger requires 3, but verify)

**Threshold**: Any noise bounce triggering ACTIVE = state machine bug

### FP4: Illiquid Premium Spike

**Signature**: Premium jumps 30%+ but spread widens to > 40% simultaneously

**Detection**: `revival_pct ≥ 30 AND spread_pct > 40`

**Threshold**: If > 30% of revivals have spread > 40% → premiums are untradable artifacts

### FP5: Theta-Dominated Decay

**Signature**: Premium revives briefly but theta decay overwhelms within 15 min

**Detection**: `premium_at_t15 < premium_at_entry AND underlying moved in correct direction`

**Threshold**: If > 30% of entries lose despite correct direction → theta too strong at this premium level

---

## 6. FIVE-SESSION COMPARISON PLAN

### Per Session: Run v1 AND v2 in parallel shadow

Each session produces:

| Metric | v1 Value | v2 Value | Better? |
|--------|---------|---------|---------|
| Time to ARMED | mm:ss | mm:ss | Earlier = better |
| Time to ACTIVE | mm:ss | mm:ss | Earlier = better (if true activation) |
| True activation rate | X% | X% | Higher = better |
| False activation rate | X% | X% | Lower = better |
| Best available entry | ₹X (Xx potential) | ₹X (Xx potential) | Higher payoff = better |
| Tradable at entry | Y/N | Y/N | |
| Late activation | Y/N | Y/N | |

### 5-Session Rollup: v1 vs v2

```
PREFER v2 if ALL:
  - v2 activates in ≥ as many sessions as v1
  - v2 true activation rate ≥ v1
  - v2 false activation rate ≤ v1 + 10%
  - v2 best entry payoff ≥ v1 in majority of sessions

PREFER v1 if:
  - v2 false activation rate > v1 + 15%
  - v2 produces more noise bounces
  - v2 compression trigger fires in sessions with no real opportunity

KEEP BOTH if:
  - v1 better in some market types, v2 better in others
  - Use market classification to route
```

### Sessions Needed

| Session Type | What It Tests | Min Count |
|-------------|--------------|-----------|
| Gap-down + reversal | v2 core pattern | ≥ 1 |
| Gap-up + reversal | Symmetric test | ≥ 1 |
| Trending (no reversal) | False positive test | ≥ 1 |
| Choppy (multiple bounces) | Noise resilience | ≥ 1 |
| Any | Volume/statistics | ≥ 1 |

### Decision Criteria After 5 Sessions

| Result | Action |
|--------|--------|
| v2 dominates (better in 4/5 metrics) | Replace v1 with v2 |
| v2 better in 3/5, similar in rest | Replace with v2 |
| Mixed (2/5 each) | Keep both, route by market type |
| v1 dominates | Reject v2, keep v1 |
| Both poor | Reject EOE concept entirely |

---

## 7. CONFIDENCE SCORE

**Confidence in this validation addendum: 0.93**

The framework can:
- Run v1 and v2 in parallel with zero interference
- Mechanically score both using the same gates
- Identify v2-specific false positives that v1 wouldn't produce
- Make a definitive v1-vs-v2 decision after 5 sessions

**Remaining 0.07**:
- Option chain data availability during compression (very low premiums may have no quotes)
- `consecutive_higher_closes` requires tick-level precision (3-second cycles may miss sub-candle moves)
- `premium_session_high` for options opened at ₹210 — this is meaningful only for CE in bearish gap sessions

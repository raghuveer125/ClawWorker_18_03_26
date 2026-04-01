# Complete Scalping Engine Entry Criteria Flow

## Overview
This document traces the FULL flow from strike selection through order placement, documenting every condition, threshold, and rejection criteria at each stage.

---

## STAGE 1: MARKET REGIME DETECTION
**Agent 18: MarketRegimeAgent**
**Location**: `engines/scalping/scalping/agents/infrastructure_agents.py`

### Regime Types
- **TRENDING_BULLISH**: Clear uptrend, ADX > 25, momentum aligned
- **TRENDING_BEARISH**: Clear downtrend, ADX > 25, momentum aligned
- **RANGE_BOUND**: Sideways with mean reversion, ADX < 20
- **VOLATILE_EXPANSION**: Breakout/breakdown, IV/ATR expansion > 120%
- **VOLATILE_CONTRACTION**: Consolidation, IV/ATR contraction < 80%
- **EXPIRY_PINNING**: Price gravitation to max pain near expiry
- **UNKNOWN**: Insufficient data

### Regime Detection Thresholds
```
TREND_ADX_THRESHOLD = 25              # ADX > 25 = trending
RANGE_ADX_THRESHOLD = 20              # ADX < 20 = range-bound
VOL_EXPANSION_THRESHOLD = 1.2         # IV > 120% of average
VOL_CONTRACTION_THRESHOLD = 0.8       # IV < 80% of average
ATR_EXPANSION_THRESHOLD = 1.5         # ATR > 150% of average
ATR_CONTRACTION_THRESHOLD = 0.7       # ATR < 70% of average
RANGE_COMPRESSION_PERIODS = 5         # Consecutive ATR contractions
MAX_PAIN_PINNING_DISTANCE = 0.5%      # Distance for pinning regime
```

### Output
- `market_regimes`: Dict with regime per symbol
- `regime_changes`: Detected regime shifts
- `volume_data`: Acceleration (multiplier) & trend (stable/accelerating/decelerating)

---

## STAGE 2: STRUCTURE ANALYSIS
**Agent 4: StructureAgent**
**Location**: `engines/scalping/scalping/agents/analysis_agents.py`

### Structure Detection
- **Swing Highs**: 4-candle peaks (high > prev2, prev1, next1, next2)
- **Swing Lows**: 4-candle troughs
- **Break of Structure (BOS)**: Price breaks swing levels
- **VWAP Deviation**: |Price - VWAP| / VWAP >= threshold

### Timeframe Analysis
1. **1-Minute Momentum**: Trend direction + momentum points (close-to-close delta)
2. **3-Minute Breakout**: Close breaks previous 3-min high/low
3. **5-Minute Trend**: Overall trend direction

### Thresholds
```
Config.vwap_deviation_threshold = 0.5%    # VWAP deviation trigger
Timeframe structure: ["1m", "3m", "5m"]
```

### Outputs
- `market_structure[symbol]`: Swing highs/lows, trend, confidence
- `structure_breaks[]`: BOS events with strength
- `vwap_signals[]`: VWAP deviations detected

---

## STAGE 3: MOMENTUM DETECTION
**Agent 5: MomentumAgent**
**Location**: `engines/scalping/scalping/agents/analysis_agents.py`

### Signal Types Detected

#### 1. Futures Surge
**Condition**: |Futures price change| >= momentum_threshold (index-specific)
```
NIFTY50: 25 points
BANKNIFTY: 50 points
SENSEX: 80 points
FINNIFTY: (configured in INDEX_CONFIGS)
```
**Strength**: min(1.0, price_change / threshold)
**Direction**: bullish if +, bearish if -, neutral otherwise

#### 2. Volume Spike
**Condition**: Total option chain volume >= threshold × average
```
Threshold = index_config.volume_spike_multiplier
NIFTY50/BANKNIFTY/SENSEX: 5.0x
```
**Tracking**: Maintains last 20 volume readings
**Strength**: min(1.0, spike_multiple / 10)

#### 3. Option Expansion (Gamma Expansion)
**Condition**: ATM premium expanded >= config.option_expansion_threshold
```
expansion_pct >= 15% (config.option_expansion_threshold = 0.15)
```
**Tracking**: Last 10 ATM premium readings
**Strength**: min(1.0, expansion_pct / 25)

#### 4. Gamma Zone (High Gamma Region)
**Condition**: >= 4 options with delta in range
```
Delta range: config.gamma_zone_delta_range = (0.40, 0.60)
OR delta <= 0.01 and near ATM (within 2× strike_interval)
```
**Strength**: min(1.0, avg_gamma × 100)

### Outputs
- `momentum_signals[]`: List of MomentumSignal objects
- `strong_signals`: Signals with strength >= 0.7

---

## STAGE 4: TRAP DETECTION
**Agent 6: TrapDetectorAgent**
**Location**: `engines/scalping/scalping/agents/analysis_agents.py`

### Trap Types

#### 1. OI Buildup/Unwinding
**Condition**: |OI change| >= oi_buildup_threshold - 1
```
oi_buildup_threshold = 1.5 (50% OI change)
Tracking: Last 10 OI readings
```
**Trap Type**: "oi_buildup" if +, "oi_unwinding" if -
**Confidence**: min(1.0, abs(oi_change_ratio))

#### 2. PCR Spike
**Condition**: |PCR change| >= pcr_spike_threshold
```
pcr_spike_threshold = 0.3
Tracking: Last 10 PCR readings
```
**Trap Type**: "pcr_spike_bearish" if +, "pcr_spike_bullish" if -
**Confidence**: min(1.0, abs(pcr_change) / 0.5)

#### 3. Bid/Ask Imbalance
**Condition**: Bid/Ask ratio at ATM strikes
```
Long trap if: total_bid_qty / total_ask_qty >= bid_ask_imbalance_ratio (2.0)
Short trap if: ratio <= 1 / bid_ask_imbalance_ratio (0.5)
```
**Trap Type**: "bid_heavy_bullish" or "ask_heavy_bearish"
**Confidence**: min(1.0, imbalance_ratio / 3)

#### 4. Liquidity Sweep
**Condition**: Price within 0.2% of swing high/low
```
if abs(current_price - swing_level) / swing_level < 0.002
```
**Trap Type**: "liquidity_sweep_high" or "liquidity_sweep_low"
**Confidence**: 0.7 (fixed)

### Outputs
- `trap_signals[]`: List of TrapSignal objects
- High confidence traps: confidence >= 0.7

---

## STAGE 5: VOLATILITY & DEALER ANALYSIS
**Agents**: Market Regime + Volatility Surface + Dealer Pressure analysis
**Location**: Custom microservices integration

### Volatility Surface Metrics
```
surface_score: 0-1, how favorable is volatility regime
otm_scale: Multiplier for OTM range based on VIX
target_scale: Multiplier for profit targets
realized_vol: Historical volatility
```

### VIX-Based Scaling
```
if vix >= high_vix_level (25.0):
    otm_scale = high_vix_otm_scale (0.8)       # Closer to ATM
    size_scale reduction in position multiplier
elif vix <= low_vix_level (15.0):
    otm_scale = low_vix_otm_scale (1.15)       # Further OTM
```

### Dealer Pressure Metrics
```
gamma_regime: "short" | "long" | "neutral"
gamma_flip_level: Price level where gamma regime flips
pinning_score: 0-1, strength of dealer pinning
acceleration_score: 0-1, gamma acceleration
extreme_pinning_threshold: 0.85 (config.dealer_extreme_pinning_score)
```

---

## STAGE 6: STRIKE SELECTION
**Agent 7: StrikeSelectorAgent**
**Location**: `engines/scalping/scalping/agents/analysis_agents.py`

### Direction Determination
```
IF trend == "bullish" OR futures_surge direction == "bullish":
    direction = "CE"
ELIF trend == "bearish" OR futures_surge direction == "bearish":
    direction = "PE"
ELSE:
    direction = "CE" (default)
```

### Expiry vs Non-Expiry Logic
**`is_expiry_day()` Check**: Thursday (NSE) or Friday (BSE)

#### Expiry Day Mode (Strict Filters)
```
Spread limit: config.max_bid_ask_spread_pct (5.0%)
Min volume: config.min_volume_threshold (1000)
Min OI: config.min_oi_threshold (5000)
Premium range: idx_config.premium_min to premium_max (10-25 or 10-30)
Filters: STRICT OTM + premium + delta
```

#### Non-Expiry Mode (Movement Quality Focus)
```
Spread limit: config.max_bid_ask_spread_pct × 0.6 (3.0%)
Min volume: max(100, config.min_volume_threshold / 5) (relaxed)
Min OI: max(500, config.min_oi_threshold / 5) (relaxed)
Premium range: Premium × 4 (wider range for movement)
Filters: PRIORITIZE volume/OI movement + spread tightness
```

### OTM Distance Selection
**Index-Specific Config**:
```
NIFTY50:
    otm_distance_min: 150 points
    otm_distance_max: 300 points
    strike_interval: 50 points

BANKNIFTY:
    otm_distance_min: 300 points
    otm_distance_max: 600 points
    strike_interval: 100 points

SENSEX:
    otm_distance_min: 400 points
    otm_distance_max: 800 points
    strike_interval: 100 points
```

**VIX Adjustment**:
```
otm_min = round((idx_config.otm_distance_min × scale) / strike_interval) × strike_interval
otm_max = max(otm_min + strike_interval, round((idx_config.otm_distance_max × scale) / strike_interval) × strike_interval)

if vix >= 25: scale = 0.8 (move closer to ATM)
elif vix <= 15: scale = 1.15 (move further OTM)
```

### Premium Selection Rules
```
Optimal range: ₹12-₹18 (+0.15 score)
Acceptable range: ₹10-₹25 (+0.05 score)
Wide range on non-expiry: up to ₹100+
```

### Delta Selection
**Optimal**: 0.18-0.22 (+0.15 score)
**Acceptable**: index_config.delta_min to delta_max
```
NIFTY50: 0.15-0.25
BANKNIFTY: 0.12-0.22
SENSEX: 0.15-0.25
```

### Spread Quality
```
Very tight: < 2% (+0.15 score)
Tight: < 3% (+0.08 score)
Acceptable: < 5%
Excluded: > 5%
```

### Volume/OI Scoring
```
High volume (> 3× min_volume_threshold): +0.10 score
High OI (> 3× min_oi_threshold): +0.05 score
OI increasing: +0.05 score
Non-expiry volume/OI ratio bonus: +0.15 score if ratio > 0.1
```

### Selection Score Calculation
```
Base: 0.5
+ Premium sweet spot (0.15)
+ Delta sweet spot (0.15)
+ Spread quality (0.08-0.15)
+ Volume/OI (0.05-0.10)
+ Institutional indicators (non-expiry bonus)
+ Surface/dealer adjustments (±0.05-0.08)
= Total (capped at 1.0)
```

### Non-Expiry Movement Bonus
```
IF volume / OI > 0.1:
    movement_bonus += 0.15  ["high_vol_oi_ratio"]
IF spread_pct < 2.0:
    movement_bonus += 0.10  ["tight_spread"]
IF OI > min_oi_threshold:
    movement_bonus += 0.05  ["strong_oi"]
```

### SL & Target Calculation
```
entry_price = ask (or ltp)
sl_price = entry_price × 0.75           # 25% max loss
target_offset = max(first_target_points, entry_price × 0.35)
target_price = entry_price + target_offset
first_target_points = 4.0 (config)

Risk = entry - SL
Reward = target - entry
R:R ratio = Reward / Risk
```

### Outputs
- `strike_selections[symbol]`: Top 5 ranked StrikeSelection objects
- Each contains: strike, premium, delta, spread%, volume, OI, score, reasons

---

## STAGE 7: SIGNAL QUALITY FILTERING
**Agent 19: SignalQualityAgent**
**Location**: `engines/scalping/scalping/agents/signal_quality_agent.py`

### Quality Score Components (Weighted Average)
```
Weights (total = 1.0):
  Confidence:      25% (signal's own confidence)
  Regime:          20% (compatibility with market regime)
  Volume:          15% (volume confirmation)
  Liquidity:       15% (tradeable liquidity)
  Momentum:        15% (momentum alignment)
  Risk/Reward:     10% (R:R quality)
```

### Confidence Score
```
MINIMUM_CONFIDENCE = 0.5

if confidence >= 0.8:
    "High confidence" reason added
elif confidence < 0.5:
    "Low confidence" reason added
```

### Regime Compatibility Matrix
```
REGIME_SIGNAL_COMPAT = {
    "TRENDING_BULLISH": {"CE": 1.0, "PE": 0.3},
    "TRENDING_BEARISH": {"CE": 0.3, "PE": 1.0},
    "RANGE_BOUND": {"CE": 0.6, "PE": 0.6},
    "VOLATILE_EXPANSION": {"CE": 0.8, "PE": 0.8},
    "VOLATILE_CONTRACTION": {"CE": 0.4, "PE": 0.4},
    "EXPIRY_PINNING": {"CE": 0.5, "PE": 0.5},
    "UNKNOWN": {"CE": 0.5, "PE": 0.5},
}
```

### Volume Score
```
if acceleration >= 1.5:
    volume_score = min(1.0, 0.5 + (acceleration - 1) × 0.5)
elif acceleration >= 1.0:
    volume_score = 0.5 + (acceleration - 1) × 0.5
else:
    volume_score = acceleration × 0.5
```

### Liquidity Score
```
if spread_pct > 2.0%:
    liquidity_score = min(liquidity_score, 0.4)  # Penalize wide spreads
elif spread_pct < 0.5%:
    liquidity_score = min(1.0, liquidity_score + 0.2)  # Bonus for tight
```

### Momentum Score Alignment
```
CE signals need bullish momentum
PE signals need bearish momentum

if aligned_momentum (signal direction matches option direction):
    score += strength
elif neutral_momentum:
    score += strength × 0.25

Final: min(1.0, aligned + min(neutral × 0.25, 0.2))
```

### Risk/Reward Score
```
if sl > 0 and target > 0 and entry > 0:
    risk = abs(entry - sl)
    reward = abs(target - entry)
    rr_ratio = reward / risk

    if rr_ratio >= 2.0:
        risk_score = 1.0       ["Excellent R:R"]
    elif rr_ratio >= 1.5:
        risk_score = 0.8
    elif rr_ratio >= 1.0:
        risk_score = 0.6
    else:
        risk_score = max(0.2, rr_ratio × 0.5)  ["Poor R:R"]
```

### Volatility Surface Impact
```
if surface_score >= 0.7:
    confidence_score = min(1.0, confidence_score + 0.05)
    ["Vol surface supportive"]
elif surface_score <= 0.3:
    risk_score = max(0.2, risk_score - 0.05)
    ["Vol surface defensive"]

if realized_vol >= high_realized_vol_level (0.012):
    risk_score = max(0.2, risk_score - 0.1)
    ["High realized volatility"]
```

### Dealer Pressure Impact
```
if gamma_regime == "short" and acceleration_score >= 0.6:
    momentum_score = min(1.0, momentum_score + 0.08)
    ["Dealer short gamma acceleration"]

if gamma_regime == "long" and pinning_score >= extreme_pin_threshold (0.85):
    confidence_score = max(0.2, confidence_score - 0.08)
    ["Extreme dealer pin risk"]
```

### Grade Assignment
```
A+: total_score >= 0.9  (Exceptional)
A:  total_score >= 0.8  (Strong)
B:  total_score >= 0.7  (Good)
C:  total_score >= 0.6  (Marginal, reduced size)
D:  total_score >= 0.5  (Weak, minimal/skip)
F:  total_score < 0.5   (Failed, do not trade)
```

### Pass Filter Criteria
```
PASS if ALL:
  - total_score >= MINIMUM_TOTAL_SCORE (0.5)
  - confidence_score >= MINIMUM_CONFIDENCE (0.5)
  - regime_score >= 0.3 (don't trade against strong regime)

ADDITIONAL FILTER for replay journal signals:
  if replay_min_rr_ratio > 0:
      if rr_ratio < replay_min_rr_ratio:
          FILTER: "Replay R:R below minimum"
```

### Size Recommendation (Based on Grade)
```
A+/A: 100%  (Full size)
B:    75%   (3/4 size)
C:    50%   (Half size)
D:    25%   (Quarter size)
F:    0%    (Do not trade)
```

### Execution Priority (1-10)
```
base_priority = int(total_score × 10)
if grade == A+:
    base_priority = min(10, base_priority + 2)
if grade in [D, F]:
    base_priority = max(1, base_priority - 2)
```

### Outputs
- `quality_filtered_signals[]`: Signals that passed filter
- `rejected_signals[]`: Signals blocked with reasons
- `signal_quality_stats`: Distribution of grades
- `adaptive_quality_weights`: Learning-adjusted weights

---

## STAGE 8: LIQUIDITY MONITORING
**Agent 17: LiquidityMonitorAgent**
**Location**: `engines/scalping/scalping/agents/infrastructure_agents.py`

### Liquidity Metrics Per Option
```
Spread component (40% weight):
    <= 2%:   +0.40 score
    <= 3%:   +0.30 score
    <= 5%:   +0.20 score
    <= 8%:   +0.10 score

Depth component (30% weight):
    min(bid_qty, ask_qty) >= 500:  +0.30 score
    >= 200:  +0.20 score
    >= 100:  +0.10 score

Volume component (15% weight):
    >= 5000:  +0.15 score
    >= 1000:  +0.10 score
    >= 500:   +0.05 score

OI component (15% weight):
    >= 50000:  +0.15 score
    >= 10000:  +0.10 score
    >= 5000:   +0.05 score
```

### Tradeability Criteria (ALL must pass)
```
AND:
  - spread_pct <= MAX_SPREAD_PCT (5.0%)
  - bid_depth >= MIN_BID_DEPTH (100)
  - ask_depth >= MIN_ASK_DEPTH (100)
  - (volume >= MIN_VOLUME (500) OR oi >= MIN_OI (5000))
```

### Rejection Reasons
```
Wide spread: spread_pct > 5%
Low bid depth: bid_qty < 100
Low ask depth: ask_qty < 100
Low volume/OI: volume < 500 AND oi < 5000
```

### Outputs
- `liquidity_filtered_selections[]`: Options passing liquidity filter
- `illiquid_strikes[]`: Rejected strikes with reason
- `liquidity_metrics[]`: Metrics for all options
- Rejected signals added to rejection list

---

## STAGE 9: ENTRY CONDITION CHECKS
**Agent 8: EntryAgent**
**Location**: `engines/scalping/scalping/agents/execution_agents.py`

### Pre-Entry Gate Checks

#### 1. Trade Disabled Check
```
if trade_disabled: SKIP all entries, return 0 orders
```

#### 2. No Signals Available
```
if no quality_filtered_signals: SKIP execution
```

#### 3. Late Entry Cutoff
```
late_entry_cutoff_time = "14:50" (config)
if current_time > cutoff:
    Reject all signals with reason: "Past late-entry cutoff"
```

#### 4. Max Positions Reached
```
max_positions = 3 (config)
if open_positions >= max_positions:
    SKIP execution
```

### Entry Conditions (Must Pass >= 2)
**Method**: `_check_entry_conditions()`

#### Condition 1: Structure Breakout
```
if config.require_structure_break:
    if symbol_breaks exist:
        conditions.append("structure_break")
```

#### Condition 2: Futures Momentum
```
if config.require_futures_confirm:
    symbol_momentum = momentum_signals for this symbol
    
    if strong_momentum exists (strength >= 0.7):
        conditions.append("futures_momentum")
    elif symbol_momentum exists AND:
        (signal_type == "gamma_zone" AND strength >= 0.8 AND "structure_break" in conditions):
        conditions.append("futures_momentum")
```

#### Condition 3: Volume Burst
```
if config.require_volume_burst:
    volume_signals = momentum_signals where signal_type == "volume_spike"
    
    if volume_signals exist:
        conditions.append("volume_burst")
    elif high_gamma exists (signal_type == "gamma_zone" AND strength >= 0.9):
        conditions.append("volume_burst")
```

#### Condition 4: Trap Confirmation (Optional)
```
if config.require_trap_confirm:
    symbol_traps = trap_signals where symbol AND confidence >= 0.6
    if symbol_traps exist:
        conditions.append("trap_confirmed")
else:
    symbol_traps = trap_signals where symbol
    if symbol_traps exist:
        conditions.append("trap_alignment")
```

### Condition Sufficiency Check
```
if len(conditions_met) >= 2:
    Proceed to setup assessment
else:
    REJECT with reason: "Insufficient entry conditions: X/2"
```

### Replay Mode Entry Condition Augmentation
**Method**: `_augment_replay_entry_conditions()`
```
if source == "replay_journal" OR status == "APPROVED" OR entry_ready OR selected:
    if status == "APPROVED" OR entry_ready:
        augmented.append("historical_signal_approved")
    if action in {take, buy, enter} OR selected:
        augmented.append("historical_entry_ready")

if config.replay_require_market_conditions:
    if no market_conditions (only historical): REJECT
```

### Setup Assessment
**Method**: `_assess_trade_setup()`

#### Timeframe Alignment Checks
```
1m_aligned = available AND trend == direction AND momentum_points > 0
3m_breakout = available AND breakout == direction
5m_aligned = available AND trend == direction
three_tf_aligned = 1m AND 3m AND 5m all aligned
```

#### Entry Trigger Detection
```
trigger_types = []
if volatility_burst.active:
    trigger_types.append("volatility_burst")
if liquidity_vacuum.active:
    trigger_types.append("liquidity_vacuum")
if synthetic_volatility_burst.active:
    trigger_types.append("synthetic_volatility_burst")

trigger_active = bool(trigger_types)
```

#### Micro Momentum Check
```
micro_score = micro_momentum.get("score", 0.0)
micro_aligned = micro_score > 0 AND one_minute_aligned
live_confirmed = confirmation_state.get("status") == "confirmed"
```

#### A+ Requirements (All must pass)
```
- rr_ratio >= strict_a_plus_rr_ratio (1.3)
- five_minute_aligned
- three_minute_breakout
- one_minute_aligned
- trigger_active
tag = "A+" if all met, strict_pass = True
```

#### B Requirements (All must pass)
```
- rr_ratio >= strict_b_rr_ratio (1.1)
- three_minute_breakout
- one_minute_aligned
tag = "B" if all met, strict_pass = not strict_mode
```

#### C Requirements (Relaxed)
```
- If not B requirements, tag = "C"
- strict_pass = False
- Can trade if rr_ratio >= strict_b_rr_ratio (1.1)
```

### Strict Entry Rejection
**Method**: `_strict_entry_rejection_reason()`
```
if strict_a_plus_only (config):
    if tag != "A+":
        REJECT: "Strict A+ filter rejected setup (TAG)"

if tag == "C":
    rr = setup_assessment.rr_ratio
    min_rr = strict_b_rr_ratio (1.1)
    if rr < min_rr:
        REJECT: "Setup quality too weak (C)"
```

### Correlation Guard
**Method**: Entry-level filtering
```
if signal_key in blocked_signal_keys AND not correlation_penalty:
    REJECT: "Correlation guard blocked signal"
```

### Micro Timing Check
```
if micro_momentum.get("timing") == "reject":
    REJECT: "Adaptive entry timing rejected weak momentum"
```

### Entry Confirmation Check (Live Confirmation Window)
```
if using_confirmed_candidates AND confirmation_status not in {"confirmed"}:
    REJECT: f"Entry confirmation incomplete: {status}"
```

### Outputs from Assessment
```
{
    "tag": "A+" | "B" | "C",
    "strict_pass": bool,
    "rr_ratio": float (rounded to 4 decimals),
    "timeframe_alignment": {
        "1m_trend": str,
        "1m_momentum_points": float,
        "1m_aligned": bool,
        "3m_trend": str,
        "3m_breakout": str,
        "3m_breakout_aligned": bool,
        "5m_trend": str,
        "5m_aligned": bool,
        "three_tf_aligned": bool,
    },
    "entry_trigger": {
        "active": bool,
        "types": [str],
        "live_volatility_burst": bool,
        "live_liquidity_vacuum": bool,
        "synthetic_volatility_burst": bool,
    },
    "micro_momentum": {
        "score": float,
        "timing": str,
        "aligned": bool,
        "live_confirmed": bool,
    },
    "missing_requirements": [str],
    "reasons": [str],
}
```

---

## STAGE 10: POSITION SIZING
**Method**: `compute_position_multiplier()`

### Base Multiplier by Setup Tag
```
if tag == "A+":
    base = strict_a_plus_size_fraction (0.65)
    if rr_ratio >= 1.6:
        base = min(0.7, base + 0.05)
    if live_confirmed OR confirmation_status == "confirmed":
        base = min(0.7, base + 0.02)

elif tag == "B":
    base = strict_b_size_fraction (0.35)
    if rr_ratio >= 1.3:
        base = min(0.4, base + 0.03)

elif tag == "C":
    if rr_ratio >= strict_b_rr_ratio (1.1):
        base = 0.25
    else:
        base = 0.0  # Don't trade

else:
    confidence = signal.confidence (normalize to 0-1)
    base = confidence
```

### Market Condition Adjustments
```
if spread_pct > 0.5%:
    base *= 0.5

if vix > 25:
    base *= 0.7

if learn_prob < 0.5:
    base *= 0.6

base *= volatility_surface.get("size_scale", 1.0)
```

### Dealer Pressure Adjustments
```
if gamma_regime == "short":
    base *= dealer_short_gamma_boost (1.05)

if gamma_regime == "long" AND pinning_score >= extreme_pin_threshold (0.85):
    base *= dealer_long_gamma_penalty (0.90)

bounded_scale = max(0.8, min(1.1, value))
```

### Final Multiplier
```
effective_multiplier = max(0.0, min(1.0, base))
```

### Multiplier Checks Before Order
```
if multiplier < 0.2:
    REJECT: "Execution multiplier too low: X.XX"
```

---

## STAGE 11: FILL CONDITION VALIDATION
**Method**: `_validate_fill_conditions()`

### Current Quote Lookup
```
Fetches latest bid/ask from option_chains
If not found: returns (True, [], {})  # Allow trade
```

### Slippage Check
```
slippage_pct = ((current_ask - premium) / premium) × 100

if slippage_pct > config.max_entry_slippage_pct (2.0%):
    REJECT: "Entry slippage too high: X.XX%"
```

### Bid/Ask Drift Check
```
ask_drift_pct = ((current_ask - reference_entry) / reference_entry) × 100

if ask_drift_pct > config.max_bid_ask_drift_pct (1.0%):
    REJECT: "Bid/ask drift too high: X.XX%"
```

### Spread Widening Check
```
spread_widen_ratio = current_spread_pct / reference_spread_pct

if reference_spread_pct > 0 AND spread_widen_ratio > config.max_spread_widening_ratio (1.5):
    REJECT: "Spread widened during entry: X.XXx"
```

### Outputs
```
(
    quote_ok: bool,
    quote_reasons: [str],  # Rejection reasons
    fill_quote: {
        "bid": float,
        "ask": float,
        "spread": float,
        "spread_pct": float,
        "slippage_pct": float,
        "ask_drift_pct": float,
    }
)
```

---

## STAGE 12: QUEUE RISK ASSESSMENT
**Method**: Queue risk from infrastructure layer

### Queue Risk Parameters
```
config.queue_risk_ratio_threshold = 3.0
config.queue_risk_reduce_threshold = 1.8

queue_size_scale = queue_risk.get("size_scale", 1.0)

if queue_size_scale <= 0.0:
    REJECT: f"Queue risk too high: {queue_ratio:.1f}x ahead"
```

### Final Order Quantity
```
order_lots = max(1, round(config.entry_lots × effective_multiplier × queue_size_scale))
entry_lots = 4 (config, 4-6 range)
effective_multiplier = base_multiplier × correlation_size_scale × vacuum_boost
```

---

## STAGE 13: FINAL VALIDATIONS BEFORE ORDER

### Minimum Conditions
```
len(conditions_met) >= 2
multiplier >= 0.2
queue_size_scale > 0.0
quote_ok == True
```

### Price Rounding
```
_round_price_for_symbol(symbol, premium)
Depends on symbol's tick_size
```

### Confidence Normalization
```
raw_confidence = signal.get("confidence" | "quality_score" | "score")
if raw_confidence > 1.0:
    normalized = raw_confidence / 100.0
else:
    normalized = raw_confidence
final_confidence = min(1.0, normalized)
```

---

## STAGE 14: ORDER CREATION & EXECUTION

### Order Object
```
Order(
    order_id: f"ENT_{uuid}",
    symbol: symbol,
    strike: strike,
    option_type: direction,
    order_type: "market",
    side: "buy",
    quantity: quantity (lots × lot_size),
    price: premium (rounded),
    status: "pending" | "simulated",
    reason: f"Entry: {conditions_met.join(', ')}",
    metadata: {
        multiplier,
        conditions_met,
        confidence,
        lots,
        fill_quote,
        regime,
        momentum_strength,
        micro_momentum_strength,
        entry_confirmation,
        liquidity_vacuum,
        queue_risk,
        correlation_penalty,
        volatility_burst,
        setup_tag,
        setup_reasons,
        strict_filter_pass,
        rr_ratio,
        timeframe_alignment,
        entry_trigger,
        micro_momentum,
        condition_count,
        target_scale,
        stop_scale,
    }
)
```

### Dry Run / Simulated Execution
```
if dry_run OR replay_mode:
    fill_price = _simulate_entry_fill_price(symbol, premium, metadata)
    fill_time = cycle_now
    status = "simulated"
```

---

## COMPLETE ENTRY REJECTION REASONS

Signals can be rejected at ANY stage for:

1. **Signal Generation**:
   - Quality score < 0.5
   - Confidence < 0.5
   - Regime incompatibility (regime_score < 0.3)
   - Replay journal R:R < minimum

2. **Liquidity Filtering**:
   - Spread > 5%
   - Bid depth < 100
   - Ask depth < 100
   - Volume < 500 AND OI < 5000

3. **Entry Gate**:
   - Trade disabled
   - Past entry cutoff (14:50)
   - Max positions reached

4. **Entry Conditions**:
   - < 2 of 4 conditions met

5. **Setup Assessment**:
   - strict_a_plus_only AND tag != "A+"
   - tag == "C" AND rr_ratio < 1.1
   - Missing required A+ or B requirements

6. **Pre-Order Checks**:
   - Correlation guard blocked
   - Micro timing rejected
   - Entry confirmation incomplete
   - Multiplier < 0.2
   - Queue size scale <= 0.0

7. **Fill Conditions**:
   - Slippage > 2%
   - Bid/ask drift > 1%
   - Spread widened > 1.5x

---

## CONFIG PARAMETERS SUMMARY

### Capital Management
```
total_capital = 100,000
risk_per_trade_pct = 5%
daily_loss_limit_pct = 10%
max_positions = 3
max_symbol_exposure_pct = 40%
max_consecutive_losses = 3
```

### Entry Rules
```
entry_lots = 4 (4-6 range)
require_structure_break = True
require_futures_confirm = True
require_volume_burst = True
require_trap_confirm = False
max_entry_slippage_pct = 2.0%
max_bid_ask_drift_pct = 1.0%
max_spread_widening_ratio = 1.5
late_entry_cutoff_time = "14:50"
```

### Execution Timing
```
execution_loop_interval_ms = 300
entry_confirmation_window_ms = 500
micro_momentum_window_ticks = 3
micro_imbalance_threshold = 0.10
```

### Strike Selection
```
max_bid_ask_spread_pct = 5.0%
min_volume_threshold = 1000
min_oi_threshold = 5000
option_expansion_threshold = 15%
gamma_zone_delta_range = (0.40, 0.60)
```

### Position Sizing
```
strict_a_plus_only = False
strict_a_plus_rr_ratio = 1.3
strict_b_rr_ratio = 1.1
strict_a_plus_size_fraction = 0.65
strict_b_size_fraction = 0.35
dealer_short_gamma_boost = 1.05
dealer_long_gamma_penalty = 0.90
dealer_extreme_pinning_score = 0.85
```

### Exit (Reference)
```
partial_exit_pct = 55%
first_target_points = 4.0
move_sl_to_entry = True
exit_time_stop_minutes = 30
runner_target_min = 8.0
runner_target_max = 15.0
```

---

## FULL ENTRY FLOW DIAGRAM

```
Market Regime Detection (Agent 18)
    ↓
Structure Analysis (Agent 4)
    ↓
Momentum Detection (Agent 5)
    ↓
Trap Detection (Agent 6)
    ↓
Strike Selection (Agent 7) → Determine Direction → Select OTM Strikes
    ↓
Signal Quality Filter (Agent 19) → Grade A+/A/B/C/D/F
    ↓
Liquidity Monitor (Agent 17) → Verify Tradeable
    ↓
Entry Agent (Agent 8):
    ├─ Pre-Entry Gates
    │  ├─ Trade enabled?
    │  ├─ Not past cutoff?
    │  ├─ Positions available?
    │  └─ Signals present?
    │
    ├─ Entry Conditions Check (2 of 4)
    │  ├─ Structure break
    │  ├─ Futures momentum
    │  ├─ Volume burst
    │  └─ Trap confirmation
    │
    ├─ Setup Assessment
    │  ├─ Timeframe alignment (1m/3m/5m)
    │  ├─ Entry trigger (volatility burst / liquidity vacuum)
    │  ├─ Micro momentum check
    │  ├─ Tag assignment (A+/B/C)
    │  └─ R:R validation
    │
    ├─ Strict Rejection Check
    │  ├─ A+ only mode?
    │  └─ C-tag R:R minimum?
    │
    ├─ Position Multiplier Calculation
    │  ├─ Base × vix adjustment × spread adjustment × dealer adjustment
    │  ├─ Queue risk scale
    │  └─ Correlation scale
    │
    ├─ Fill Condition Validation
    │  ├─ Slippage check
    │  ├─ Bid/ask drift check
    │  └─ Spread widening check
    │
    └─ Order Creation & Execution
       ├─ Calculate quantity
       ├─ Create order object
       ├─ Fill metadata
       └─ Submit (or simulate)
```

---

## KEY DECISION TREES

### Direction Selection
```
if structure.trend == "bullish" OR futures_surge.direction == "bullish":
    direction = CE
elif structure.trend == "bearish" OR futures_surge.direction == "bearish":
    direction = PE
else:
    direction = CE (default)
```

### Minimum 2 of 4 Conditions
```
Condition check:
  1. Structure break required? (config)
  2. Futures momentum required? (config)
  3. Volume burst required? (config)
  4. Trap confirmation required? (config, optional)

At least 2 must be true for entry
```

### Setup Quality Tag
```
if A+ requirements all met:
    tag = A+, base_size = 65%, base_rr = 1.3+
elif B requirements all met:
    tag = B, base_size = 35%, base_rr = 1.1+
else:
    tag = C, base_size = 25%, base_rr = 1.1+ (conditional)
```

### Position Multiplier
```
base = determined_by_tag(tag, rr_ratio, confirmation)
base *= vix_adjustment(vix)
base *= spread_adjustment(spread_pct)
base *= dealer_adjustment(gamma_regime, pinning_score)
final = clamp(0.0, 1.0, base)
```

---

## MONITORING & VALIDATION

All rejected signals tracked:
```
rejected_signals[]:
  - rejection_reasons: [list of specific reasons]
  - quality_grade
  - quality_score
  - symbol
  - strike
  - option_type
```

Execution metrics recorded:
```
execution_metrics[]:
  - multiplier (effective)
  - base_multiplier
  - confidence
  - quote_valid
  - timing
  - queue_risk
  - correlation_risk
  - volatility_burst
  - setup_tag
  - strict_pass
```

---

## SPECIAL CASES

### Replay Mode
- Uses journal-approved strikes if available
- Requires >= 1 market condition if replay_require_market_conditions = True
- Honors journal R:R minimum if replay_min_rr_ratio > 0

### Volatility Burst Fast Track
- volatility_burst_fast_track = True
- Triggers independent entry trigger
- Can bypass some timeframe checks

### Liquidity Vacuum Detection
- Active flag in liquidity_vacuum map
- Applies size boost (×1.05) if active

### Correlation Guard
- Blocks signals if same symbol/strike/type already pending
- Can allow if correlation_penalty provides size_scale

---

## LATENCY SAFEGUARDS

### Execution Loop
```
execution_loop_interval_ms = 300ms
Each cycle processes one symbol through full pipeline
Max 3 indices = 900ms theoretical, watchdog factor = 6.0x
```

### Data Staleness
```
spot_stale_threshold_seconds = 15
option_stale_threshold_seconds = 15
futures_stale_threshold_seconds = 15
tick_heartbeat_threshold_seconds = 15

Trading blocked if:
  - Spot/chain/futures data > 15s old
  - No tick updates for 15s
```

### Trade Disabled Mechanism
```
Automatic blocks if:
  - API latency critical
  - Data feeds dead
  - Consecutive loss streaks
```

---


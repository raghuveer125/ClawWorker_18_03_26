# Lottery Strike Picker Pipeline — Forensic Technical Document

## Document Purpose

This document provides an exhaustive, forensic-level technical specification of the Lottery Strike Picker pipeline. Every data flow, formula, decision rule, entry/exit condition, storage mechanism, and configuration parameter is documented here. This document is intended for deep research, independent audit, and optimization review.

---

## 1. System Overview

### 1.1 What This System Does

The Lottery Strike Picker is a **far-OTM (Out-of-The-Money) options scalping bot** that:

1. Ingests live NIFTY/BANKNIFTY/SENSEX option chain data from FYERS
2. Validates data quality before any calculation
3. Computes 14+ derived metrics per strike
4. Identifies "lottery" strikes — very cheap options (₹2.10 - ₹8.50 premium) far from spot
5. Scores and selects the best CE and PE candidates
6. Determines directional bias from decay asymmetry
7. Enters paper trades when trigger conditions are met
8. Manages exits via SL/targets/invalidation
9. Tracks capital, PnL, and drawdown
10. Persists everything to SQLite for replay and audit

### 1.2 Core Thesis

Buy very cheap far-OTM options betting on directional breakouts. Small risk (premium paid), large potential reward (2x-4x targets). The system uses option chain structure analysis (not price prediction) to select strikes and timing.

### 1.3 Architecture

```
FYERS WebSocket ──→ Spot ticks (real-time, no rate limit)
                     │
                     ▼
              ┌─────────────────────────────────────────┐
              │           Pipeline Cycle (1s)            │
              │                                         │
              │  1. Get snapshot (WS spot + REST chain)  │
              │  2. Validate (9 quality checks)          │
              │  3. Calculate (14 derived metrics)        │
              │  4. Score candidates                      │
              │  5. State machine transition              │
              │  6. Signal engine (entry/exit)            │
              │  7. Risk guard gate                       │
              │  8. Paper trade execution                 │
              │  9. Persist to SQLite                     │
              │  10. Update runtime state                 │
              │  11. Debug trace                          │
              └─────────────────────────────────────────┘
                     │
FYERS REST ─────→ Chain refresh (every 30s, cached between)
```

### 1.4 File Structure

```
engines/lottery/
├── __init__.py
├── __main__.py                    # python -m engines.lottery
├── main.py                       # Pipeline orchestrator
├── models.py                     # 15 data models + 8 enums
├── replay.py                     # Deterministic backtest engine
├── config/
│   ├── settings.py                # 21 frozen dataclasses + loader
│   └── settings.yaml              # 130+ config parameters
├── data_fetch/
│   ├── provider.py                # Abstract data provider interface
│   ├── fyers_adapter.py           # FYERS REST adapter
│   └── fyers_ws.py                # FYERS WebSocket client
├── data_quality/
│   └── validator.py               # 9 quality checks → PASS/WARN/FAIL
├── calculations/
│   ├── base_metrics.py            # Distance, intrinsic/extrinsic, decay, liquidity
│   ├── advanced_metrics.py        # Curvature, theta density, side bias
│   ├── extrapolation.py           # Far-OTM compression model
│   └── scoring.py                 # Composite score + tie-break + selection
├── strategy/
│   ├── state_machine.py           # 7-state FSM with deterministic transitions
│   ├── signal_engine.py           # Entry/exit rules
│   └── risk_guard.py              # 9 pre-trade risk checks
├── memory_state/
│   └── runtime.py                 # In-memory state with bounded deques
├── storage/
│   └── db.py                      # SQLite persistence (8 tables)
├── paper_trading/
│   ├── broker.py                  # Simulated execution + fill modes
│   └── capital_manager.py         # Position sizing + capital tracking
├── reporting/
│   └── tables.py                  # 7 table generators (JSON serializable)
├── debugging/
│   ├── logger.py                  # Structured JSON logging
│   └── trace.py                   # Per-cycle debug trace + failure buckets
└── tests/
    ├── test_calculations.py       # 25 unit tests
    ├── test_strategy.py           # 30 unit tests
    ├── test_paper_trading.py      # 14 unit tests
    └── test_integration.py        # 16 integration tests
```

---

## 2. Data Ingestion

### 2.1 Data Sources

| Source | Method | Frequency | Data |
|--------|--------|-----------|------|
| FYERS WebSocket | `FyersDataSocket` | Real-time ticks | Spot LTP |
| FYERS REST API | `option_chain()` endpoint | Every 30s (configurable) | Full option chain |

### 2.2 Symbol Mapping (Not Hardcoded)

```python
_DEFAULT_FYERS_SYMBOLS = {
    "NIFTY":      "NSE:NIFTY50-INDEX",
    "BANKNIFTY":  "NSE:NIFTYBANK-INDEX",
    "FINNIFTY":   "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX":     "BSE:SENSEX-INDEX",
}
```

All symbols are config-driven. Adding a new instrument requires only a config change.

### 2.3 Option Chain Fetch

API call: `client.option_chain(fyers_symbol, strike_count=50)`

This returns ~101 strikes (50 above + 50 below ATM + ATM itself) with 50-point spacing for NIFTY.

### 2.4 Fields Extracted Per Option Contract

| Field | Source Key | Required | Used In |
|-------|-----------|----------|---------|
| `strike` | `strike_price` | YES | All calculations |
| `option_type` | `option_type` (CE/PE) | YES | Side classification |
| `ltp` | `ltp` | YES | Premium, intrinsic/extrinsic |
| `change` | `ch` | NO | Decay/momentum proxy |
| `change_percent` | `chp` | NO | Normalized decay |
| `volume` | `volume` | NO | Liquidity scoring |
| `oi` | `oi` | NO | Quality check |
| `oi_change` | `oiChange` | NO | Future use |
| `bid` | `bid` | NO | Fill price, spread quality |
| `ask` | `ask` | NO | Fill price, spread quality |
| `iv` | `iv` | NO | Future use |

### 2.5 Spot Price Sources

1. **WebSocket tick**: Real-time, sub-second updates. Fields: `ltp`, `high_price`, `low_price`, `open_price`, `prev_close_price`
2. **Chain response**: `chain_data.get("ltp")` — embedded in option chain response
3. **REST quote**: `client.quotes(symbol)` — explicit spot fetch (fallback)

Priority: WebSocket > Chain-embedded > REST quote

### 2.6 Chain Cache Strategy

```
REST fetch (every 30s) → cached_chain
                            │
Pipeline cycle (every 1s) → uses cached_chain + overlays WS spot
                            │
                            └→ If WS spot available and different from chain spot:
                               Creates new ChainSnapshot with WS spot as spot_ltp
```

This means calculations run on slightly stale chain data (up to 30s old) but with real-time spot price. The trade-off: avoids FYERS rate limits while keeping spot current for trigger detection.

### 2.7 DB Fallback

If REST fetch fails on startup (rate limit, network issue):
1. Load the most recent snapshot from SQLite where `row_count > 10`
2. Use it as initial cached chain
3. Continue trying REST refresh every 30s

---

## 3. Data Quality Validation

### 3.1 When It Runs

**Every cycle, before any calculation.** If quality = FAIL, calculations are skipped and the state machine receives a FAIL signal.

### 3.2 The 9 Quality Checks

#### Check 1: Timestamp Freshness

```
spot_age = now - spot.timestamp
chain_age = now - snapshot.snapshot_timestamp
skew = |snapshot.timestamp - spot.timestamp|

FAIL if:
  spot_age > max_spot_age_ms (default: 2000ms)
  chain_age > max_chain_age_ms (default: 5000ms)
  skew > max_cross_source_skew_ms (default: 3000ms)
```

Uses **fetch timestamp** (when we called the API), not exchange last-trade time (unreliable during closed market).

#### Check 2: Null / Missing Fields

```
FAIL if: spot_ltp <= 0, no rows, strike <= 0, LTP < 0
WARN if: many optional fields (IV, OI, bid/ask) missing
PASS otherwise
```

#### Check 3: Duplicate / Stale Snapshot

Tracks MD5 hash of `spot_ltp|strike:type:ltp:vol:oi|...` across cycles.

```
hash = MD5(spot_ltp + all rows sorted by strike+type)

FAIL if: hash unchanged for >= max_stale_cycles (default: 5) consecutive cycles
WARN if: hash unchanged for >= max_stale_cycles / 2
```

#### Check 4: Price Sanity — LTP

```
FAIL if: any row has LTP < 0
```

#### Check 5: Price Sanity — Volume/OI

```
FAIL if: any row has negative volume or negative OI
```

#### Check 6: Price Sanity — Intrinsic Floor

**Only checked within ATM ± window_size strikes** (default: ±4 = 8 strikes).

```
For CE: LTP >= max(S - K, 0) - epsilon (default: 0.50)
For PE: LTP >= max(K - S, 0) - epsilon

FAIL if: violations > 10% of checked rows
WARN if: any violations
```

Deep ITM/OTM strikes with LTP=0 are excluded from this check (they're normal — no one trades them).

#### Check 7: Strike Continuity

```
Checks:
- Strike intervals are consistent (50-point spacing for NIFTY)
- ATM neighborhood exists (nearest strike within 1 step of spot)
- Counts gaps in spacing

FAIL if: ATM missing
WARN if: gaps > 10% of total strikes
```

#### Check 8: Bid/Ask Quality

```
For rows with bid/ask data:
  spread = ask - bid
  mid = (bid + ask) / 2
  spread_pct = (spread / mid) * 100

FAIL if: any inverted (ask < bid)
WARN if: > 30% of rows have spread > max_spread_pct (default: 5%)
```

#### Check 9: Volume/OI Quality

```
Only checks ATM region (ATM ± 4 steps):
WARN if: > 50% of ATM rows have volume < min_volume (default: 1000)
```

Far OTM naturally has low volume — only ATM region matters.

### 3.3 Snapshot Alignment

Two modes:

| Mode | Behavior |
|------|----------|
| STRICT | FAIL if spot tick missing or skew > threshold |
| TOLERANT | WARN if spot tick missing, use chain-embedded spot |

### 3.4 Composite Quality Score

```
quality_score = (passed + warned * 0.5) / total_checks
Range: 0.0 - 1.0

Overall:
  FAIL if any check FAIL
  WARN if any check WARN
  PASS otherwise
```

### 3.5 Decision

```
PASS → proceed with calculations
WARN → proceed (configurable override)
FAIL → skip calculations, state machine gets quality FAIL
```

---

## 4. Calculation Engine — Complete Formula Reference

### 4.1 Input Variables

```
S = spot_ltp (underlying price)
K = strike price
C_ltp = call option LTP
P_ltp = put option LTP
ΔC = call change (intraday net change)
ΔP = put change (intraday net change)
V_C = call volume
V_P = put volume
```

### 4.2 Strike Window

All aggregation functions (bias, decay averages) operate within a configurable window:

| Mode | Definition | Default |
|------|-----------|---------|
| `ATM_SYMMETRIC` | ATM ± N strikes | N=4 (8 strikes) |
| `FULL_CHAIN` | All strikes | ~101 strikes |
| `VISIBLE_RANGE` | Middle portion | N strikes from center |

### 4.3 Distance from Spot

```
d(K) = K - S
|d(K)| = |K - S|

Classification:
  CE OTM if K > S (call is out-of-the-money)
  CE ITM if K < S (call is in-the-money)
  PE OTM if K < S (put is out-of-the-money)
  PE ITM if K > S (put is in-the-money)
```

### 4.4 Intrinsic / Extrinsic Value

```
Call:
  C_intrinsic(K) = max(S - K, 0)
  C_extrinsic(K) = max(C_ltp(K) - C_intrinsic(K), 0)

Put:
  P_intrinsic(K) = max(K - S, 0)
  P_extrinsic(K) = max(P_ltp(K) - P_intrinsic(K), 0)
```

Extrinsic value = time value + volatility premium. For OTM options, extrinsic = LTP (intrinsic is 0).

### 4.5 Decay / Momentum Proxy

Two modes:

**RAW mode:**
```
call_decay_abs = |ΔC(K)|
put_decay_abs = |ΔP(K)|
call_decay_ratio = |ΔC(K)|
put_decay_ratio = |ΔP(K)|
```

**NORMALIZED mode (default):**
```
call_decay_abs = |ΔC(K)|
put_decay_abs = |ΔP(K)|
call_decay_ratio = |ΔC(K)| / max(C_ltp(K), epsilon)
put_decay_ratio = |ΔP(K)| / max(P_ltp(K), epsilon)

where epsilon = 0.01 (prevents division by zero)
```

### 4.6 Liquidity Metrics

```
call_volume = V_C(K)
put_volume = V_P(K)
liquidity_skew = V_P(K) / max(V_C(K), 1)

If bid/ask available:
  call_spread = ask_CE - bid_CE
  put_spread = ask_PE - bid_PE
  call_spread_pct = (call_spread / mid_CE) * 100
  put_spread_pct = (put_spread / mid_PE) * 100
  where mid = (bid + ask) / 2
```

### 4.7 Near-ATM Curvature (Premium Slope)

Forward difference between adjacent strikes:

```
m_C(K_i) = (C_ltp(K_{i+1}) - C_ltp(K_i)) / (K_{i+1} - K_i)
m_P(K_i) = (P_ltp(K_{i+1}) - P_ltp(K_i)) / (K_{i+1} - K_i)
```

For NIFTY with 50-point spacing:
- CE slopes are negative (calls get cheaper as strike increases)
- PE slopes are positive (puts get more expensive as strike increases)
- Rising |m_P| above spot indicates downside skew

Handles non-contiguous strikes by normalizing with actual gap.

### 4.8 Extrinsic Gradient (Theta Density)

```
θ_C(K_i) = (C_ext(K_{i+1}) - C_ext(K_i)) / ΔK
θ_P(K_i) = (P_ext(K_{i+1}) - P_ext(K_i)) / ΔK
```

Measures how time value changes across strikes. Used for:
- Decay pressure assessment
- Strike quality evaluation
- Premium compression estimation

### 4.9 Slope Acceleration (Second Derivative)

```
accel_C(K_i) = (m_C(K_i) - m_C(K_{i-1})) / ΔK
accel_P(K_i) = (m_P(K_i) - m_P(K_{i-1})) / ΔK
```

Detects convexity changes near ATM.

### 4.10 Side Bias

Aggregates decay asymmetry across the strike window to determine preferred side:

```
avg_call_decay = aggregate(|ΔC(K)| for K in window)
avg_put_decay = aggregate(|ΔP(K)| for K in window)
bias_score = avg_call_decay - avg_put_decay
```

Three aggregation modes:

| Mode | Formula |
|------|---------|
| MEAN | Simple arithmetic mean |
| VOLUME_WEIGHTED | Σ(|ΔC|*V_C) / Σ(V_C) |
| DISTANCE_WEIGHTED | weight = 1/(1+|d|/50), closer to ATM = higher weight |

Decision:
```
bias_score > 0 → calls decaying faster → PE side preferred
bias_score < 0 → puts decaying faster → CE side preferred
bias_score = 0 → no preference
```

### 4.11 Premium Band Eligibility

```
band_min = 2.10 (configurable)
band_max = 8.50 (configurable)

call_band_eligible = band_min <= C_ltp(K) <= band_max
put_band_eligible = band_min <= P_ltp(K) <= band_max
```

### 4.12 Far-OTM Extrapolation

When visible chain doesn't reach the band, premiums are projected beyond visible strikes.

**Step 1: Average step decay from last N OTM strikes (fit_window=3)**

```
For CE (furthest OTM calls):
  δ_C = mean(LTP(K_i) - LTP(K_{i+1})) for last 3 OTM pairs
  Normalized to per-step: δ_C * (strike_step / actual_gap)

For PE (furthest OTM puts):
  δ_P = mean(LTP(K_i) - LTP(K_{i+1})) for last 3 OTM pairs
```

**Guard:** If fewer than 3 valid OTM strikes with LTP > 0, extrapolation is skipped and marked `INSUFFICIENT_OTM_POINTS`.

**Step 2: Linear projection**

```
LTP_est(K+step) = LTP(K) - δ
LTP_est(K+2*step) = LTP(K+step) - δ
...until LTP_est <= 0
```

**Step 3: Exponential compression**

Linear extrapolation overshoots because far-OTM premium decay is non-linear (convex). Compression corrects this:

```
LTP_adj(K) = LTP_est(K) * e^(-α * n)

where:
  n = steps from ATM
  α = compression factor
```

**α calibration modes:**

| Mode | Method |
|------|--------|
| FIXED | Use `alpha_value` from config (default: 0.05) |
| CALIBRATED (default) | Fit exponential to visible OTM tail: ln(LTP) = a - α*n, α = -slope |

Calibration uses linear regression on last fit_window points of ln(LTP) vs steps. α is clamped to [0.001, 0.5].

**Step 4: Band selection**

```
For CE: choose smallest K such that band_min <= LTP_adj(K) <= band_max
For PE: choose largest K such that band_min <= LTP_adj(K) <= band_max
```

### 4.13 Composite Strike Score

For each candidate strike:

```
Score_CE(K) = w1*f_dist + w2*f_mom_CE + w3*f_liq_CE + w4*f_band_CE + w5*B
Score_PE(K) = w1*f_dist + w2*f_mom_PE + w3*f_liq_PE + w4*f_band_PE + w5*B
```

**Score components:**

| Component | Formula | Purpose |
|-----------|---------|---------|
| `f_dist` | `|K - S| / strike_step` | Distance from spot (higher = further OTM) |
| `f_mom` | `|ΔC or ΔP| / max(LTP, epsilon)` | Momentum/decay ratio |
| `f_liq` | `log(1 + Volume)` | Liquidity (log-scaled) |
| `f_band` | See below | Premium band fit quality |
| `B` | `avg_call_decay - avg_put_decay` | Side bias alignment |

**Band fit modes:**

| Mode | Formula |
|------|---------|
| BINARY | 1 if in band, 0 otherwise |
| DISTANCE (default) | `1 - |LTP - mid| / range` where mid=(Emin+Emax)/2, range=(Emax-Emin)/2 |

**Weights:** w1 = w2 = w3 = w4 = w5 = 1.0 (default, all configurable)

Both raw component values and weighted final score are stored for audit.

### 4.14 Candidate Filtering

A strike becomes a candidate only if ALL conditions hold:

```
1. Premium in band: band_min <= LTP <= band_max
2. Correct side: CE candidate must have K > S; PE candidate must have K < S
3. OTM distance: |K - S| >= otm_distance_min (default: 250 points)
4. OTM distance: |K - S| <= otm_distance_max (default: 450 points) — soft, via scoring
```

### 4.15 Tie-Break Logic

When multiple candidates have scores within `tie_epsilon` (default: 0.01):

```
Priority order:
1. Highest composite score
2. Best band-fit score (closer to band center)
3. Lowest spread % (tightest bid-ask)
4. Highest volume (most liquid)
5. Closest to target OTM distance midpoint
```

### 4.16 Final Selection

```
K* = argmax_K { Lottery(K) * Score_side(K) }

where Lottery(K) = 1 if:
  - premium in band
  - directional bias matches side
  - trigger level is reachable from spot
  - all quality/risk checks pass
```

---

## 5. Strategy Engine

### 5.1 State Machine — 7 States

```
IDLE ─────────────────→ ZONE_ACTIVE_CE (spot > upper_trigger)
IDLE ─────────────────→ ZONE_ACTIVE_PE (spot < lower_trigger)
ZONE_ACTIVE_CE/PE ────→ CANDIDATE_FOUND (valid strike in band + liquidity + spread)
CANDIDATE_FOUND ──────→ IN_TRADE (risk checks pass, paper entry)
IN_TRADE ─────────────→ EXIT_PENDING (SL/T1/T2/T3/invalidation triggered)
EXIT_PENDING ─────────→ COOLDOWN (exit confirmed)
COOLDOWN ─────────────→ IDLE (cooldown expired, 300s default)

Any state ────────────→ IDLE (data quality FAIL, spot reversal, risk limits)
```

### 5.2 Trigger Zone Resolution

**DYNAMIC mode (default):** Derives triggers from nearest round strikes around spot.

```
lower_trigger = max(K for K in strikes where K <= spot)
upper_trigger = min(K for K in strikes where K > spot)
```

For NIFTY with spot=22675 and 50-point strikes: lower=22650, upper=22700.

**STATIC mode:** Uses fixed values from config.

### 5.3 No-Trade Zone

```
If lower_trigger <= spot <= upper_trigger:
  → IDLE (ZONE_INACTIVE)
  → No trade
```

This is the "chop zone" where spot hasn't decisively broken in either direction.

### 5.4 No-Trade Condition Hierarchy (Fixed Priority)

```
1. Data quality FAIL → IDLE (DATA_QUALITY_FAIL)
2. Time filter active → IDLE (TIME_FILTER)
   - First 15 minutes after open
   - Lunch chop: 12:30 - 13:15
   - Near close: squareoff_time - 10 minutes
   - Outside market hours: before 09:15 or after 15:30
3. Risk rejection → IDLE
   - Max daily trades (5) exceeded
   - Max consecutive losses (3) exceeded
   - Max daily loss (₹5000) exceeded
4. Zone inactive (spot in no-trade zone)
5. No band candidate found
6. Spread too wide (> max_spread_pct)
7. Liquidity too low (< min_volume)
```

### 5.5 Entry Rules

Paper entry occurs when ALL conditions are true:

```
1. State machine reaches CANDIDATE_FOUND
2. Data quality = PASS or WARN
3. Side active (spot crossed trigger)
4. Premium in band [2.10, 8.50]
5. No conflicting active trade (max_open_trades=1)
6. Risk guard passes (9 checks)
7. Distance rule: K >= S+250 (CE) or K <= S-250 (PE)
```

### 5.6 Exit Rules (Priority Order)

```
1. STOP_LOSS:    LTP <= entry * sl_ratio (0.5)     → exit at SL
2. TARGET_3:     LTP >= entry * t3_ratio (4.0)      → exit at T3 (checked first for best exit)
3. TARGET_2:     LTP >= entry * t2_ratio (3.0)      → exit at T2
4. TARGET_1:     LTP >= entry * t1_ratio (2.0)      → exit at T1
5. INVALIDATION: spot reverses past trigger          → exit (trigger reversal)
6. TRAILING_STOP: LTP drops from peak by pct         → exit (if enabled)
7. TIME_STOP:    N minutes elapsed since entry        → exit (if configured)
8. EOD_EXIT:     past mandatory squareoff time (15:15)→ exit
```

### 5.7 Exit Level Formulas

```
Given entry price E:
  SL = E * 0.5 (50% of entry)
  T1 = E * 2.0 (2x entry = 100% gain)
  T2 = E * 3.0 (3x entry = 200% gain)
  T3 = E * 4.0 (4x entry = 300% gain)

Example for entry = ₹3.50:
  SL = ₹1.75
  T1 = ₹7.00
  T2 = ₹10.50
  T3 = ₹14.00
```

### 5.8 Re-entry & Cooldown

```
After exit:
  → COOLDOWN state for cooldown_seconds (300s = 5 minutes)
  → max_reentries = 2 (max 2 re-entries per day)
  → allow_same_strike_reentry = false (different strike required)
```

---

## 6. Risk Guardrails

### 6.1 Pre-Trade Risk Gate (9 Checks)

Every trade entry must pass ALL checks (short-circuit on first rejection):

| # | Check | Threshold | Rejection |
|---|-------|-----------|-----------|
| 1 | Capital available | > ₹0 | RISK_REJECTION |
| 2 | Max daily loss | < ₹5,000 | MAX_DAILY_LOSS |
| 3 | Max daily trades | < 5 | MAX_DAILY_TRADES |
| 4 | Max consecutive losses | < 3 | MAX_CONSECUTIVE_LOSSES |
| 5 | Cooldown after loss | not in COOLDOWN | COOLDOWN_ACTIVE |
| 6 | Max open trades | < 1 (only 1 position) | RISK_REJECTION |
| 7 | Data quality | not FAIL | DATA_QUALITY_FAIL |
| 8 | Near close | > 10 min before squareoff | TIME_FILTER |
| 9 | Position size sanity | trade cost < 50% of capital | RISK_REJECTION |

### 6.2 Time-Based Filters

```
Market hours: 09:15 - 15:30 IST
No trade first: 15 minutes (09:15 - 09:30)
No trade lunch: 12:30 - 13:15 IST
Mandatory squareoff: 15:15 IST
No trade near close: last 10 minutes before squareoff
```

All times are stored in UTC internally and converted to IST (UTC+5:30) for comparison.

---

## 7. Paper Trading Engine

### 7.1 Fill Price Calculation

| Mode | Buy Formula | Sell Formula |
|------|------------|-------------|
| LTP | LTP | LTP |
| MID | (bid+ask)/2 | (bid+ask)/2 |
| ASK | ask | bid |
| MID_SLIPPAGE (default) | mid * (1 + slippage%) | mid * (1 - slippage%) |

Default slippage: 0.5%

If bid/ask unavailable, falls back to LTP ± slippage.

### 7.2 Charges Model

```
brokerage = brokerage_per_lot * lots (default: ₹20/lot)
exchange_charges = brokerage * exchange_charges_pct / 100 (default: 0.05%)
total_charges = brokerage + exchange_charges

Per trade (entry + exit):
  total = entry_charges + exit_charges
```

### 7.3 PnL Calculation

```
gross_pnl = (exit_price - entry_price) * qty
net_pnl = gross_pnl - total_charges
capital_after = capital_before + net_pnl
```

### 7.4 Position Sizing

| Mode | Formula |
|------|---------|
| FIXED_LOTS (default) | lots = fixed_lots (1), qty = lots * lot_size |
| FIXED_RUPEE | lots = max_risk / (entry * sl_ratio * lot_size) |
| PCT_CAPITAL | lots = (capital * risk_pct) / (entry * lot_size) |
| PREMIUM_BUDGET | lots = budget / (entry * lot_size) |

### 7.5 Capital Tracking

```
Ledger events:
  INIT         → starting capital recorded
  TRADE_ENTRY  → charges deducted
  TRADE_EXIT   → PnL applied

Tracked metrics:
  running_capital = starting + sum(all PnL)
  realized_pnl = sum(closed trade PnL)
  daily_pnl = sum(today's PnL)
  peak_capital = max(running_capital ever)
  drawdown = peak_capital - running_capital
  drawdown_pct = drawdown / peak_capital * 100
```

---

## 8. Data Storage

### 8.1 SQLite Database

One DB per instrument: `engines/lottery/data/{SYMBOL}/lottery.db`

WAL mode enabled for concurrent reads.

### 8.2 Tables

| Table | Rows Per | Key Fields |
|-------|----------|------------|
| `raw_chain_snapshots` | Per REST refresh (every 30s) | snapshot_id, spot_ltp, rows_json |
| `validated_chain_rows` | Per validation | overall_status, quality_score, checks_json |
| `calculated_rows` | Per calculation cycle | spot_ltp, config_version, rows_json |
| `signal_events` | Per cycle (every 1s) | validity, machine_state, selected_strike, rejection_reason |
| `paper_trades` | Per trade (entry + update on exit) | entry_price, exit_price, pnl, status |
| `capital_ledger` | Per capital event | event, amount, running_capital, drawdown |
| `debug_events` | Per cycle | all 8 pipeline steps as JSON |
| `config_versions` | Per config change | version_hash, config_json |

### 8.3 Config Version Lineage

Every signal and trade stores `config_version` — the SHA-256 hash of the config used. This allows:
- Audit: which config produced which trades
- Replay: restore exact config for historical snapshots
- A/B comparison: different configs on same data

---

## 9. Debug & Observability

### 9.1 Per-Cycle Debug Trace

Every cycle records 8 pipeline steps + latency:

```json
{
  "cycle_id": "abc123",
  "fetch_summary": {"success": true, "spot_ltp": 22700, "rows": 202},
  "validation_result": {"overall": "PASS", "score": 0.95, "failed": []},
  "derived_variables": {"total_strikes": 101, "window_strikes": 8, "band_eligible_ce": 9},
  "side_bias_decision": {"preferred_side": "PE", "bias_score": 43.07},
  "strike_scan_results": {"total_candidates": 22, "ce_candidates": 9, "pe_candidates": 13},
  "final_selection": {"best_ce": {"strike": 24000, "score": 43.2}, "best_pe": {"strike": 21000, "score": 51.7}},
  "trade_decision": {"validity": "INVALID", "rejection_reason": "ZONE_INACTIVE"},
  "paper_execution": {"action": "NO_TRADE"},
  "latency_ms": {"fetch_ms": 0.01, "validation_ms": 0.3, "calculation_ms": 5.2, "total_ms": 6.1}
}
```

### 9.2 Failure Buckets

Errors categorized into 8 buckets with counts + recent details:

```
DATA_FETCH, PARSING, VALIDATION, STALE_DATA,
MISSING_STRIKE, STRATEGY_REJECTION, PAPER_TRADING, PERSISTENCE
```

### 9.3 Structured Logging

JSON lines format (`*.jsonl`) with:
- Timestamp (UTC)
- Level (TRACE/DEBUG/INFO/WARN/ERROR)
- Symbol
- Module + function
- Cycle ID + Snapshot ID (when available)

Log files: `engines/lottery/logs/{SYMBOL}/lottery_{YYYY-MM-DD}.jsonl`

---

## 10. Replay / Backtest Engine

### 10.1 Deterministic Guarantee

```
Same snapshot + Same config = Identical output
```

All components are pure functions with deterministic state machines. No random elements.

### 10.2 Replay Modes

| Mode | Source | Method |
|------|--------|--------|
| DB replay | SQLite `raw_chain_snapshots` | `replay_from_db(db, limit)` |
| File replay | JSONL file | `replay_from_file(path)` |

### 10.3 JSONL Snapshot Format

```json
{
  "symbol": "NIFTY",
  "expiry": "2026-04-07",
  "spot_ltp": 22700.0,
  "snapshot_timestamp": "2026-04-06T08:30:00+00:00",
  "rows": [
    {"strike": 22700, "option_type": "CE", "ltp": 180.0, "change": -85.0, "volume": 1000000, "oi": 500000, "bid": 179.5, "ask": 180.5},
    ...
  ]
}
```

### 10.4 Replay Output

```python
ReplayResult:
  total_snapshots: 100
  processed: 95
  skipped: 5 (quality FAIL)
  signals_valid: 3
  signals_invalid: 92
  trades_entered: 2
  trades_closed: 2
  final_capital: 100450.0
  total_pnl: 450.0
  max_drawdown: 180.0
  trades: [{strike, side, entry, exit, pnl, reason}, ...]
  signals: [{timestamp, validity, state, strike}, ...]
```

---

## 11. Configuration Reference

### 11.1 All 130+ Parameters

Full config in `engines/lottery/config/settings.yaml`. Key groups:

| Section | Parameters | Critical Ones |
|---------|-----------|---------------|
| instrument | symbol, exchange, strike_step, expiry_mode | strike_step=50 |
| premium_band | min, max, fit_mode | min=2.10, max=8.50 |
| otm_distance | min_points, max_points | 250-450 |
| triggers | mode, upper, lower | DYNAMIC |
| scoring | w1-w5, tie_epsilon, min_valid_candidates | all=1.0 |
| window | type, size | ATM_SYMMETRIC, 4 |
| decay | mode, epsilon | NORMALIZED, 0.01 |
| bias | aggregation, use_pcr | MEAN, false |
| extrapolation | fit_window, alpha_mode, min_valid_strikes | 3, CALIBRATED, 3 |
| data_quality | 11 thresholds | see Section 3 |
| paper_trading | capital, lot_size, sizing_mode, risk limits | 100000, 75, FIXED_LOTS |
| execution | mode, slippage, brokerage | MID_SLIPPAGE, 0.5%, ₹20/lot |
| exit_rules | sl_ratio, t1/t2/t3_ratio, eod_exit | 0.5, 2/3/4, true |
| time_filters | market hours, lunch, first minutes | 09:15-15:30, 12:30-13:15, 15min |
| cooldown | seconds, max_reentries | 300s, 2 |
| risk | cooldown_after_loss, near_close_minutes | true, 10min |
| polling | interval, chain_refresh | 1s, 30s |
| logging | level, json_output | INFO, true |
| storage | db_path | engines/lottery/data/lottery.db |

### 11.2 Config Versioning

```python
config.version_hash → SHA-256(JSON(config))[:12]
# Example: "e330e7391a0e"
```

Stored with every signal, trade, and debug trace. Enables audit of "which config produced this trade."

### 11.3 Environment Variable Overrides

Format: `LOTTERY_SECTION_KEY=value`

```bash
LOTTERY_PREMIUM_BAND_MIN=3.00
LOTTERY_PAPER_TRADING_STARTING_CAPITAL=200000
LOTTERY_POLLING_CHAIN_REFRESH_SECONDS=60
```

---

## 12. Frontend Dashboard

### 12.1 Access

URL: `http://localhost:4001/lottery`

### 12.2 Tabs

| Tab | Data Source | Refresh |
|-----|-----------|---------|
| Status | `/api/lottery/status` | 2s |
| Raw Data | `/api/lottery/raw-data` | 2s |
| Formula Audit | `/api/lottery/formula-audit` | 2s |
| Quality | `/api/lottery/quality` | 2s |
| Signals | `/api/lottery/signals` | 2s |
| Trades | `/api/lottery/trades` | 2s |
| Capital | `/api/lottery/capital` | 2s |

### 12.3 Multi-Symbol

Dropdown selector: NIFTY, BANKNIFTY, FINNIFTY, SENSEX. Each runs an independent pipeline with isolated DB.

---

## 13. Test Coverage

### 13.1 Summary

| Category | Tests | Status |
|----------|-------|--------|
| Unit — Calculations | 25 | All pass |
| Unit — Strategy | 30 | All pass |
| Unit — Paper Trading | 14 | All pass |
| Integration — Full flow | 4 | 3 pass, 1 skip |
| Integration — Storage | 5 | All pass |
| Integration — Reporting | 4 | All pass |
| Integration — Replay | 3 | All pass |
| **Total** | **85** | **84 pass, 1 skip** |

### 13.2 What's Tested

- Distance, intrinsic/extrinsic calculations
- Decay normalization (RAW + NORMALIZED)
- Liquidity skew, spread calculation
- Band eligibility
- Premium slopes, theta density, slope acceleration
- Side bias (all 3 aggregation modes)
- Extrapolation + compression + insufficient guard
- Scoring + band fit (BINARY + DISTANCE)
- Score components audit
- OTM distance filter
- All 7 state machine states + transitions
- Trigger resolution (STATIC + DYNAMIC)
- Signal engine (valid + invalid signals)
- All exit reasons (SL, T1, T2, T3, invalidation, hold)
- Exit level calculations
- Risk guard (all 9 checks)
- Broker fill modes (LTP, MID+SLIPPAGE)
- PnL calculation (profit + loss)
- Charges
- Capital tracking, drawdown
- Position sizing
- Ledger entries
- Full pipeline flow (no-trade, CE breakout, PE breakout, trade lifecycle)
- DB CRUD (snapshot, signal, trade, config)
- Multi-symbol isolation
- JSON serialization of all tables
- Replay from JSONL file
- Deterministic output guarantee
- Config version consistency

---

## 14. Known Limitations & Future Improvements

### 14.1 Current Limitations

1. **FYERS expiry field**: Option chain API doesn't return expiry per contract — quality check always WARNs
2. **Change field on weekends/holidays**: FYERS returns `change=None` when market is closed — decay/bias calculations return None
3. **WebSocket data**: Currently only provides spot LTP — option chain LTP not streamed per-contract
4. **Single active trade**: Only 1 position at a time (by design for phase 1)
5. **No Greeks**: IV, delta, gamma, vega not computed locally (depends on FYERS providing IV)

### 14.2 Potential Optimizations

1. **Scoring weight tuning**: Currently equal weights — could be optimized via replay on historical data
2. **Adaptive premium band**: Band could adjust based on VIX or time-to-expiry
3. **Multi-expiry**: Currently single nearest expiry — could compare weekly vs monthly
4. **Volume profile**: Weight candidates by volume profile shape, not just raw volume
5. **OI-based bias**: Currently PCR is optional — could integrate OI buildup/unwind signals
6. **Trail from peak**: Currently no trailing stop from peak LTP — only from entry
7. **Partial exit**: Currently all-or-nothing exit — could scale out at T1/T2/T3

### 14.3 Production Readiness Checklist

- [x] Config-driven (no hardcoding)
- [x] Multi-instrument support
- [x] Data quality validation
- [x] Deterministic replay
- [x] Config versioning + audit trail
- [x] 85 automated tests
- [x] Structured logging
- [x] SQLite persistence
- [x] Dashboard UI
- [ ] Telegram/Slack alerts (Phase 9B — deferred)
- [ ] Real broker integration (future phase)
- [ ] Greeks computation
- [ ] Multi-expiry comparison
- [ ] VIX-adaptive band

---

## 15. How to Run

### 15.1 Standalone Pipeline

```bash
cd ~/ClawWorker_18_03_26

# NIFTY (default)
FYERS_INSECURE=1 python -m engines.lottery --symbol NIFTY

# BANKNIFTY
FYERS_INSECURE=1 python -m engines.lottery --symbol BANKNIFTY --exchange NSE

# Custom config
FYERS_INSECURE=1 python -m engines.lottery --config path/to/settings.yaml
```

### 15.2 Via API Server (Auto-start)

```bash
cd ~/ClawWorker_18_03_26/ClawWork/livebench
FYERS_INSECURE=1 python -m uvicorn api.server:app --host 0.0.0.0 --port 9001

# Pipeline starts automatically when http://localhost:4001/lottery is accessed
```

### 15.3 Run Tests

```bash
cd ~/ClawWorker_18_03_26
python -m pytest engines/lottery/tests/ -v
```

### 15.4 Full Dev Restart

```bash
# Kill existing
pkill -f "uvicorn api.server.*9001"
pkill -f "vite.*4001"
sleep 2

# Start API + pipeline
cd ~/ClawWorker_18_03_26/ClawWork/livebench
FYERS_INSECURE=1 nohup python3 -m uvicorn api.server:app --host 0.0.0.0 --port 9001 &

# Start frontend
cd ~/ClawWorker_18_03_26/ClawWork/frontend
nohup npx vite --port 4001 &

# Dashboard: http://localhost:4001/lottery
```

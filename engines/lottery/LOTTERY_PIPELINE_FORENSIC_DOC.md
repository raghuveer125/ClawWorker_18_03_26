# Lottery Strike Picker Pipeline — Forensic Technical Document

## Document Purpose

This document provides an exhaustive, forensic-level technical specification of the Lottery Strike Picker pipeline. Every data flow, formula, decision rule, entry/exit condition, storage mechanism, and configuration parameter is documented here. This document is intended for deep research, independent audit, and optimization review by humans or AI systems.

**Last updated:** 2026-04-07
**Source files:** `engines/lottery/` (35 Python modules, 4 test modules, 1 YAML config)
**Branch:** `arch/phase1-stabilization`

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Diagram](#2-architecture-diagram)
3. [Pipeline Orchestration](#3-pipeline-orchestration)
4. [Data Input — FYERS Adapter](#4-data-input--fyers-adapter)
5. [Data Quality Validation](#5-data-quality-validation)
6. [Calculations — Base Metrics](#6-calculations--base-metrics)
7. [Calculations — Advanced Metrics](#7-calculations--advanced-metrics)
8. [Calculations — Extrapolation](#8-calculations--extrapolation)
9. [Calculations — Scoring & Selection](#9-calculations--scoring--selection)
10. [Strategy — State Machine (7 States)](#10-strategy--state-machine-7-states)
11. [Strategy — Signal Engine (Entry/Exit)](#11-strategy--signal-engine-entryexit)
12. [Strategy — Confirmation & Hysteresis](#12-strategy--confirmation--hysteresis)
13. [Strategy — Risk Guard](#13-strategy--risk-guard)
14. [Paper Trading — Broker Simulation](#14-paper-trading--broker-simulation)
15. [Paper Trading — Capital Manager](#15-paper-trading--capital-manager)
16. [Storage — SQLite Database](#16-storage--sqlite-database)
17. [Memory State — Runtime](#17-memory-state--runtime)
18. [Reporting & Tables](#18-reporting--tables)
19. [Divergence Analysis](#19-divergence-analysis)
20. [Debugging & Observability](#20-debugging--observability)
21. [Alerting — Telegram Integration](#21-alerting--telegram-integration)
22. [Configuration Reference (130+ Parameters)](#22-configuration-reference-130-parameters)
23. [Data Models Reference](#23-data-models-reference)
24. [External Integrations](#24-external-integrations)
25. [File Inventory](#25-file-inventory)
26. [Test Coverage](#26-test-coverage)
27. [Key Thresholds Summary Table](#27-key-thresholds-summary-table)

---

## 1. System Overview

### 1.1 What This System Does

The Lottery Strike Picker is a **far-OTM (Out-of-The-Money) options scalping bot** that:

1. Ingests live NIFTY/BANKNIFTY/SENSEX option chain data from FYERS
2. Validates data quality before any calculation (9 independent checks)
3. Computes 14+ derived metrics per strike (distance, intrinsic/extrinsic, decay, curvature, theta density, side bias, liquidity skew, spread quality)
4. Identifies "lottery" strikes — very cheap options (default: Rs 2.10 - Rs 8.50 premium) far from spot (default: 250-450 points OTM)
5. Extrapolates beyond visible chain for far-OTM candidates using exponential compression model
6. Scores and selects the best CE and PE candidates using 5-factor weighted composite scoring
7. Determines directional bias from decay asymmetry (call vs put decay rates)
8. Enters paper trades when trigger zone + confirmation gate + tradability + risk checks all pass
9. Manages exits via SL (0.5x) / T1 (2x) / T2 (3x) / T3 (4x) / invalidation / trailing / time / EOD
10. Tracks capital, PnL, drawdown, and charges
11. Persists everything to SQLite for replay, backtest, and audit (10 tables)
12. Sends Telegram alerts for trade events, quality warnings, and system errors

### 1.2 Core Thesis

Buy very cheap far-OTM options betting on directional breakouts. Small risk (premium paid), large potential reward (2x-4x targets). The system uses **option chain structure analysis** (not price prediction) to select strikes and timing.

### 1.3 Dual-Cycle Model

The pipeline runs two independent cycles:

| Cycle | Frequency | Purpose |
|-------|-----------|---------|
| **Analysis Cycle** | Every 30 seconds | Full chain fetch, validate, calculate metrics, score candidates |
| **Trigger Cycle** | Every 1 second | Live spot check, state machine transitions, entry/exit decisions |

---

## 2. Architecture Diagram

### 2.1 High-Level Pipeline Flow

```
FYERS WebSocket ──────────────────────────────────────────────────────────────────────┐
  (real-time spot ticks)                                                              │
                                                                                      ▼
FYERS REST API ──────────────────────────────────────┐                        ┌─────────────────┐
  (option chain every 30s)                           │                        │  CandleBuilder   │
  (candidate quotes every 1-5s)                      │                        │  (1-min OHLC)    │
                                                     ▼                        └─────────────────┘
                              ┌─────────────────────────────────────────────────────────────────┐
                              │                    ANALYSIS CYCLE (30s)                          │
                              │                                                                 │
                              │  1. FyersAdapter.fetch_option_chain()                            │
                              │     └─→ ChainSnapshot (50 strikes, CE+PE, with bid/ask/volume)  │
                              │                                                                 │
                              │  2. DataQualityValidator.validate()                              │
                              │     └─→ QualityReport (9 checks, PASS/WARN/FAIL)                │
                              │                                                                 │
                              │  3. compute_base_metrics()                                      │
                              │     └─→ distance, intrinsic, extrinsic, decay, liquidity, spread│
                              │                                                                 │
                              │  4. compute_advanced_metrics()                                  │
                              │     └─→ curvature (slope), theta density, slope acceleration    │
                              │                                                                 │
                              │  5. filter_window() [ATM +/- N strikes]                         │
                              │                                                                 │
                              │  6. compute_side_bias()                                         │
                              │     └─→ preferred_side (CE/PE), bias_score                      │
                              │                                                                 │
                              │  7. extrapolate_otm_strikes()                                   │
                              │     └─→ projected far-OTM strikes with compressed premiums      │
                              │                                                                 │
                              │  8. score_and_select()                                          │
                              │     └─→ best_ce, best_pe, all_candidates, rejection_audit       │
                              │                                                                 │
                              │  9. Save to SQLite (chain, quality, calculated, rejections)      │
                              └─────────────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼ (AnalysisSnapshot)
                              ┌─────────────────────────────────────────────────────────────────┐
                              │                    TRIGGER CYCLE (1s)                            │
                              │                                                                 │
                              │  1. Get live spot (WebSocket or analysis fallback)               │
                              │  2. Maybe refresh candidate quotes (RefreshScheduler)            │
                              │  3. Feed CandleBuilder, MicrostructureTracker                    │
                              │  4. Check data quality gate                                     │
                              │                                                                 │
                              │  IF ACTIVE TRADE:                                               │
                              │    5a. Get current option LTP                                   │
                              │    5b. SignalEngine.evaluate_exit()                              │
                              │        └─→ SL → T3 → T2 → T1 → Invalidation → Trailing → Time → EOD│
                              │    5c. If exit: PaperBroker.execute_exit() → CapitalManager      │
                              │    5d. StateMachine → COOLDOWN                                  │
                              │                                                                 │
                              │  IF NO TRADE:                                                   │
                              │    6a. resolve_triggers() [STATIC or DYNAMIC]                   │
                              │    6b. TriggerHysteresis gate (buffer + hold time)              │
                              │    6c. StateMachine.evaluate() → state transition               │
                              │    6d. SignalEngine.evaluate_entry()                             │
                              │    6e. BreakoutConfirmation gate (QUORUM mode)                  │
                              │    6f. Tradability check (bid/ask/spread/volume)                 │
                              │    6g. RiskGuard pre-trade checks (9 gates)                     │
                              │    6h. If all pass: PaperBroker.execute_entry()                 │
                              │    6i. CapitalManager.record_entry()                            │
                              │    6j. StateMachine → IN_TRADE                                  │
                              │                                                                 │
                              │  7. Save signal, debug trace to SQLite                          │
                              │  8. Send Telegram alerts if applicable                          │
                              │  9. Update RuntimeStateManager                                  │
                              └─────────────────────────────────────────────────────────────────┘
```

### 2.2 Component Dependency Map

```
LotteryPipeline (main.py)
├── FyersAdapter (data_fetch/fyers_adapter.py)
│   └── FYERS REST + WebSocket client
├── DataQualityValidator (data_quality/validator.py)
├── StateMachine (strategy/state_machine.py)
│   └── StateContext (7 states, counters, active candidate)
├── SignalEngine (strategy/signal_engine.py)
│   └── Uses StateMachine for state evaluation
├── BreakoutConfirmation (strategy/confirmation.py)
├── TriggerHysteresis (strategy/hysteresis.py)
├── RiskGuard (strategy/risk_guard.py)
├── DTEDetector (strategy/dte_detector.py)
├── RefreshScheduler (strategy/refresh_scheduler.py)
├── PaperBroker (paper_trading/broker.py)
├── CapitalManager (paper_trading/capital_manager.py)
├── LotteryDB (storage/db.py) [SQLite]
├── RuntimeStateManager (memory_state/runtime.py)
├── CandleBuilder (calculations/candle_builder.py)
├── MicrostructureTracker (calculations/microstructure.py)
├── AlertNotifier (alerting/notifier.py) [Telegram]
├── CycleTracer (debugging/trace.py)
└── FailureBucket (debugging/trace.py)
```

---

## 3. Pipeline Orchestration

**Source:** `engines/lottery/main.py`

### 3.1 Initialization (LotteryPipeline.__init__)

1. Load config from `settings.yaml` + environment variable overrides
2. Setup structured JSON logger
3. Create all components (adapter, validator, SM, SE, broker, capital, risk, DB, RSM)
4. Create auxiliary systems (alerts, candle builder, microstructure, hysteresis, DTE detector, confirmation, refresh scheduler)
5. Initialize state variables (analysis snapshot, WS state, cached quotes)
6. Save config version hash to DB for audit trail

### 3.2 Startup (pipeline.run())

```
Phase 1: Initial Setup
  1. Run initial analysis cycle (full chain fetch + metrics)
  2. If analysis failed: attempt load from DB
  3. Warmup candle builder from historical API
  4. Start WebSocket listener for real-time spot

Phase 2: Main Loop (runs until stopped)
  while running:
    1. Check RefreshScheduler: is analysis cycle due?
    2. If due: run analysis cycle (30s frequency)
    3. Always: run trigger cycle (1s frequency)
    4. Sleep: max(0, interval - elapsed)
```

### 3.3 Analysis Cycle (every 30 seconds)

```
_run_analysis_cycle():
  1. Fetch full chain (retry: 3 attempts, exponential backoff 500ms → 1s → 2s)
  2. Validate chain (9 data quality checks)
  3. Save chain snapshot + quality report to DB
  4. If quality == FAIL: update analysis with FAIL status, return
  5. Calculate metrics:
     a. compute_base_metrics(snapshot, config)
     b. compute_advanced_metrics(rows, config)
     c. filter_window(rows, spot, config)
     d. compute_side_bias(window, config)
     e. extrapolate_otm_strikes(rows, spot, config)
     f. score_and_select(rows, CE_ext, PE_ext, spot, side, bias, config)
  6. Build rejection audit (per-strike rejection reasons)
  7. Create CalculatedSnapshot + AnalysisSnapshot
  8. Save calculated rows, rejection audits to DB
  9. Log metrics: spot, quality, candidate count, elapsed time
```

### 3.4 Trigger Cycle (every 1 second)

```
_run_trigger_cycle():
  1. Get live spot from WebSocket (fallback: analysis spot)
  2. Feed CandleBuilder with live spot tick
  3. Maybe refresh candidate quotes (RefreshScheduler decides):
     - Fetch new quotes for best_ce, best_pe, active_trade
     - Feed to MicrostructureTracker
  4. Build TriggerSnapshot with live data
  5. Check data quality gate (FAIL = skip this cycle)
  
  IF active trade exists:
    6. Get current option LTP (from candidate quote or chain)
    7. Evaluate exit (SL, targets, time, EOD, invalidation, trailing)
    8. If exit triggered:
       a. PaperBroker.execute_exit()
       b. CapitalManager.record_exit()
       c. StateMachine → EXIT_PENDING → COOLDOWN
       d. Save trade to DB
       e. Send Telegram alert
  
  IF no active trade:
    6. Resolve triggers (STATIC or DYNAMIC mode)
    7. Check TriggerHysteresis gate
    8. Evaluate entry signal (StateMachine.evaluate())
    9. Save signal to DB
    10. If signal VALID:
        a. Check BreakoutConfirmation gate (QUORUM/CANDLE/PREMIUM/HYBRID/DISABLED)
        b. Check hysteresis zone hold (min_zone_hold_seconds)
        c. Check tradability (bid/ask/volume/spread)
        d. Check RiskGuard (9 pre-trade gates)
        e. If all pass:
           - PaperBroker.execute_entry()
           - CapitalManager.record_entry()
           - StateMachine → IN_TRADE
           - Save trade to DB
           - Send Telegram alert
  
  11. Save debug trace to DB
  12. Record cycle latency in RuntimeStateManager
```

---

## 4. Data Input — FYERS Adapter

**Source:** `engines/lottery/data_fetch/fyers_adapter.py`, `engines/lottery/data_fetch/provider.py`

### 4.1 Provider Interface (Abstract Base)

All data providers implement `DataProvider`:

| Method | Parameters | Returns |
|--------|-----------|---------|
| `fetch_spot()` | symbol, exchange | `UnderlyingTick` or None |
| `fetch_option_chain()` | symbol, exchange, expiry, strike_count=50 | `ChainSnapshot` or None |
| `fetch_expiries()` | symbol, exchange | `list[ExpiryInfo]` sorted by DTE |
| `get_lot_size()` | symbol | int |
| `is_connected()` | — | bool |

### 4.2 Symbol Mapping

```python
_DEFAULT_FYERS_SYMBOLS = {
    "NIFTY":      "NSE:NIFTY50-INDEX",
    "NIFTY50":    "NSE:NIFTY50-INDEX",
    "BANKNIFTY":  "NSE:NIFTYBANK-INDEX",
    "FINNIFTY":   "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX":     "BSE:SENSEX-INDEX",
}
```

### 4.3 Default Lot Sizes

| Symbol | Lot Size |
|--------|----------|
| NIFTY / NIFTY50 | 75 |
| BANKNIFTY | 30 |
| FINNIFTY | 40 |
| MIDCPNIFTY | 50 |
| SENSEX | 10 |

### 4.4 Fetch Spot (`fetch_spot`)

- Calls `client.quotes(fyers_symbol)` — single REST API call
- Parses response: `result.data.d[0].v`
- Extracted fields:
  - `ltp` = `v.get("lp")` (last traded price)
  - `open` = `v.get("open_price")`
  - `high` = `v.get("high_price")`
  - `low` = `v.get("low_price")`
  - `prev_close` = `v.get("prev_close_price")`
  - `source_timestamp` = parsed from `v.get("tt")` as UNIX epoch
- Returns `UnderlyingTick` object

### 4.5 Fetch Option Chain (`fetch_option_chain`)

- Calls `client.option_chain(fyers_symbol, strike_count=50)`
- Parses from `result.data.data.optionsChain` (nested structure)
- **Per contract extracts:**
  - `strike_price` (float, filtered > 0)
  - `option_type` (CE or PE only)
  - `ltp`, `change`, `chp` (change %)
  - `volume`, `oi`, `oiChange`
  - `bid`, `ask`, `bidQty`, `askQty`
  - `iv` (implied volatility)
  - `ltt` (last trade time, UNIX epoch)
  - `expiry` (normalized to YYYY-MM-DD)
- Returns `ChainSnapshot` with `spot_ltp`, `rows` (tuple of OptionRow), `spot_tick`

### 4.6 Fetch Candidate Quotes (`fetch_candidate_quotes`)

Used for **lightweight 1-5s refresh** of 2-3 shortlisted strikes:

- Accepts list of `(strike, option_type)` tuples
- Single REST quotes call for all candidates
- Extracted: `ltp`, `bid`, `ask`, `volume`, `bid_qty`, `ask_qty`
- Computed: `spread_pct = ((ask - bid) / mid) * 100` where `mid = (bid + ask) / 2`
- Returns dict mapping `(strike, type)` to quote data

### 4.7 Option Symbol Construction

Format: `{PREFIX}{DDMMMYY}{STRIKE}{CE/PE}`

| Symbol | Prefix |
|--------|--------|
| NIFTY/NIFTY50 | `NSE:NIFTY` |
| BANKNIFTY | `NSE:BANKNIFTY` |
| FINNIFTY | `NSE:FINNIFTY` |
| MIDCPNIFTY | `NSE:MIDCPNIFTY` |
| SENSEX | `BSE:SENSEX` |

### 4.8 Expiry Resolution

- **Primary:** Uses `shared_project_engine.indices.config.get_expiry_snapshot()` (exchange calendar)
- **Fallback:** Weekday-based calculation:
  - NIFTY/NIFTY50: Thursday (weekday=3)
  - SENSEX: Friday (weekday=4)
  - If today is expiry and after 10:00 IST, use next week
- **Classification:** WEEKLY if DTE <= 7 days, MONTHLY if > 7 days

---

## 5. Data Quality Validation

**Source:** `engines/lottery/data_quality/validator.py`

### 5.1 Validation Pipeline

9 independent checks executed in sequence. Each check returns `PASS`, `WARN`, or `FAIL`.

### CHECK 1: Timestamp Freshness

| Threshold | Config Key | Default |
|-----------|-----------|---------|
| Max spot age | `data_quality.max_spot_age_ms` | 2000 ms |
| Max chain age | `data_quality.max_chain_age_ms` | 5000 ms |
| Max cross-source skew | `data_quality.max_cross_source_skew_ms` | 3000 ms |

```
spot_age_ms = (now - snapshot_timestamp) * 1000
chain_age_ms = (now - snapshot_timestamp) * 1000
skew_ms = abs(snapshot_timestamp - spot_tick.timestamp) * 1000

FAIL if ANY threshold exceeded
PASS if all within thresholds
```

### CHECK 2: Null / Missing Fields

- **Critical fields:** `spot_ltp > 0`, `rows > 0` (non-empty chain)
- **Per-row critical:** `strike > 0` and `ltp >= 0`
- **Per-row optional:** `iv`, `oi`, `bid`, `ask`

```
FAIL if any critical field missing
WARN if all rows missing optional fields
PASS otherwise
```

### CHECK 3: Duplicate / Stale Snapshot

Hash computed as:
```
MD5("{spot_ltp}|{strike}:{type}:{ltp}:{volume}:{oi}|...")[:16]
```
Rows sorted by `(strike, type)` before hashing.

```
consecutive_same = count of same hash in _prev_hashes
FAIL if consecutive >= max_stale_cycles (default: 5)
WARN if consecutive >= max_stale_cycles // 2
PASS otherwise
```

### CHECK 4: Price Sanity

**4a. LTP Validity:** FAIL if any `ltp < 0`

**4b. Volume/OI Validity:** FAIL if any `volume < 0` or `oi < 0`

**4c. Intrinsic Floor (ATM window only):**
- Applied to strikes within `window_size * strike_step` of ATM
- For CE: `intrinsic = max(spot - strike, 0)`
- For PE: `intrinsic = max(strike - spot, 0)`
- Violation: `ltp > 0 AND ltp < intrinsic - epsilon`
- `epsilon = config.data_quality.intrinsic_floor_epsilon` (default: 0.50)

```
FAIL if violations > 10% of checked strikes
WARN if violations <= 10%
PASS if no violations
```

### CHECK 5: Strike Continuity

- Requires >= 3 unique strikes
- Checks gaps: `|actual_gap - config.strike_step| > 1`
- ATM presence: nearest strike must be within 1 `strike_step` of spot

```
FAIL if no ATM strike nearby
WARN if gaps > 10% of strike count
PASS otherwise
```

### CHECK 6: Bid/Ask Quality

For rows where both `bid > 0` and `ask > 0`:
- **Inversion:** FAIL if any `ask < bid`
- **Spread %:** `spread_pct = ((ask - bid) / mid) * 100`
- **Wide spread:** exceeds `config.data_quality.max_spread_pct` (default: 5%)
- **Ghost liquidity:** `bid_qty < min_bid_qty` or `ask_qty < min_ask_qty`

```
FAIL if inversion found
WARN if wide_spread > 30% of rows, or no book data
PASS otherwise
```

### CHECK 7: Volume/OI Quality

- Low volume: `volume < config.data_quality.min_volume` (default: 1000)
- Low OI: `oi < config.data_quality.min_oi` (default: 500)
- ATM region = strikes within `strike_step * 4` of spot

```
WARN if ATM low_volume > 50% of ATM rows
PASS otherwise
```

### CHECK 8: Expiry Integrity

```
FAIL if > 1 unique expiry found (mixed expiry contamination)
WARN if no expiry data
PASS if exactly 1 expiry
```

### CHECK 9: Snapshot Alignment

**STRICT mode (default):**
```
FAIL if missing spot_tick
FAIL if skew_ms > max_cross_source_skew_ms
```

**TOLERANT mode:**
```
WARN if missing spot_tick (uses chain-embedded spot)
Skew is informational only
```

### 5.2 Quality Score Formula

```
quality_score = (passed + warned * 0.5) / max(total_checks, 1)
```

**Overall Status Priority:**
- `FAIL` if ANY check is FAIL
- `WARN` if ANY check is WARN (no FAILs)
- `PASS` if ALL checks PASS

---

## 6. Calculations — Base Metrics

**Source:** `engines/lottery/calculations/base_metrics.py`

### 6.1 Input

`ChainSnapshot` (raw option chain) + `LotteryConfig`

### 6.2 Per-Strike Computations

For each unique strike, with CE and PE rows:

#### Distance

```
distance = strike - spot              (signed: positive for CE OTM, negative for PE OTM)
abs_distance = abs(distance)          (unsigned)
```

#### Intrinsic / Extrinsic Value

```
CE:
  call_intrinsic = max(spot - strike, 0)
  call_extrinsic = max(call_ltp - call_intrinsic, 0)

PE:
  put_intrinsic = max(strike - spot, 0)
  put_extrinsic = max(put_ltp - put_intrinsic, 0)
```

#### Decay / Momentum

```
call_decay_abs = abs(ce.change)
put_decay_abs = abs(pe.change)

If DecayMode.NORMALIZED:
  call_decay_ratio = call_decay_abs / max(call_ltp, epsilon)
  put_decay_ratio = put_decay_abs / max(put_ltp, epsilon)
  where epsilon = config.decay.epsilon (default: 0.01)

If DecayMode.RAW:
  call_decay_ratio = call_decay_abs
  put_decay_ratio = put_decay_abs
```

#### Liquidity

```
call_volume = ce.volume
put_volume = pe.volume
liquidity_skew = put_volume / max(call_volume, 1)    (if both exist)
```

#### Spread Quality

```
spread = ask - bid
mid = (bid + ask) / 2
spread_pct = (spread / mid) * 100    (if mid > 0)
```

#### Premium Band Eligibility

```
call_band_eligible = (band_min <= call_ltp <= band_max)
put_band_eligible = (band_min <= put_ltp <= band_max)

where band_min = config.premium_band.min (default: 2.10)
      band_max = config.premium_band.max (default: 8.50)
```

### 6.3 Window Filtering

Three modes:

| Mode | Logic |
|------|-------|
| `FULL_CHAIN` | Return all rows unfiltered |
| `ATM_SYMMETRIC` | Keep rows where `abs_distance <= strike_step * window_size` |
| `VISIBLE_RANGE` | Use middle portion: `rows[mid - size : mid + size]` |

Default: `ATM_SYMMETRIC` with `window_size = 4` (i.e., ATM +/- 4 strikes)

### 6.4 Output

`CalculatedRow` per strike containing all computed fields (see Data Models section).

---

## 7. Calculations — Advanced Metrics

**Source:** `engines/lottery/calculations/advanced_metrics.py`

### 7.1 Premium Slope (Curvature)

Forward difference between consecutive strikes:

```
For strikes i and i+1:
  dk = strike[i+1] - strike[i]
  call_slope = (call_ltp[i+1] - call_ltp[i]) / dk
  put_slope = (put_ltp[i+1] - put_ltp[i]) / dk
```

Interpretation: rate of premium change per strike point.

### 7.2 Theta Density (Extrinsic Gradient)

```
call_theta_density = (call_extrinsic[i+1] - call_extrinsic[i]) / dk
put_theta_density = (put_extrinsic[i+1] - put_extrinsic[i]) / dk
```

Interpretation: rate of time-value change across strikes (related to gamma).

### 7.3 Side Bias Computation

Detects directional preference from decay asymmetry:

```
bias_score = avg_call_decay - avg_put_decay

If bias_score > 0: preferred_side = PE  (calls decay faster → puts preferred)
If bias_score < 0: preferred_side = CE  (puts decay faster → calls preferred)
If bias_score == 0: preferred_side = None
```

**Three aggregation modes:**

| Mode | Formula |
|------|---------|
| `MEAN` | Simple average of all decay values |
| `VOLUME_WEIGHTED` | `sum(decay * volume) / sum(volume)` per side |
| `DISTANCE_WEIGHTED` | `weight(row) = 1 / (1 + abs_distance / 50)` — ATM strikes get highest weight |

Default: `MEAN`

### 7.4 PCR Bias (Put-Call Ratio)

```
total_call_vol = sum(call_volume for all rows)
total_put_vol = sum(put_volume for all rows)
pcr = total_put_vol / total_call_vol    (if total_call_vol > 0)
```

### 7.5 Slope Acceleration (Second Derivative)

```
For strikes i-1 and i:
  dk = strike[i] - strike[i-1]
  call_accel = (call_slope[i] - call_slope[i-1]) / dk
  put_accel = (put_slope[i] - put_slope[i-1]) / dk
```

Positive = convexity increasing. Negative = concavity.

---

## 8. Calculations — Extrapolation

**Source:** `engines/lottery/calculations/extrapolation.py`

### 8.1 Purpose

Project premium values for strikes **beyond the visible chain** where the premium band (Rs 2.10 - Rs 8.50) may exist. Uses linear decay + exponential compression.

### 8.2 Model

```
Step 1 (Linear):   LTP_est(n) = LTP_last - avg_decay * (n - n_last)
Step 2 (Compress):  LTP_adj(n) = LTP_est(n) * e^(-alpha * n)
Step 3 (Filter):    in_band = (band_min <= LTP_adj <= band_max)
```

### 8.3 CE Side Extrapolation

```python
ce_otm = rows where strike > spot AND call_ltp > 0
sorted by strike ascending
direction = +1 (strikes increase away from spot)
fit_window = config.extrapolation.fit_window_ce (default: 3)
```

### 8.4 PE Side Extrapolation

```python
pe_otm = rows where strike < spot AND put_ltp > 0
sorted by strike descending (furthest from spot first)
direction = -1 (strikes decrease away from spot)
fit_window = config.extrapolation.fit_window_pe (default: 3)
```

### 8.5 Algorithm Detail

**Step 1: Average Step Decay**

Take last `fit_window` OTM strikes (furthest from spot):
```
For consecutive strikes in tail:
  if ltp_curr > ltp_next:
    actual_gap = abs(strike_next - strike_curr)
    decay_per_step = (ltp_curr - ltp_next) * (strike_step / actual_gap)
    
avg_decay = mean(decay_per_step values)
```

**Step 2: Alpha Calibration**

| Mode | Formula |
|------|---------|
| `FIXED` | `alpha = config.extrapolation.alpha_value` (default: 0.05) |
| `CALIBRATED` | Fit `ln(LTP) = ln(LTP_0) - alpha * n` via linear regression on OTM tail. Clamp to [0.001, 0.5] |

Calibrated alpha computation:
```
points = [(n, ln(ltp)) for each OTM row]
  where n = |strike - atm_strike| / strike_step

Linear regression on last fit_window points:
  slope = sum((n - n_mean)(y - y_mean)) / sum((n - n_mean)^2)
  alpha = -slope
  alpha = clamp(alpha, 0.001, 0.5)
```

**Step 3: Forward Projection**

Starting from last visible OTM strike, project up to 50 strikes:
```
for proj in range(1, 51):
    current_strike += direction * strike_step
    current_ltp = max(current_ltp - avg_decay, 0)
    
    if current_ltp <= 0: break
    
    n = atm_steps + proj
    adjusted = current_ltp * e^(-alpha * n)
    in_band = (band_min <= adjusted <= band_max)
    
    if adjusted < band_min * 0.1: break  (well past band)
```

### 8.6 Output

`ExtrapolatedStrike` per projected strike:
- `strike`, `option_type`
- `estimated_premium` (linear), `adjusted_premium` (compressed)
- `steps_from_atm`, `alpha_used`, `in_band`

### 8.7 Guard

Extrapolation skipped entirely if `len(otm_rows) < config.extrapolation.min_valid_strikes` (default: 3).

---

## 9. Calculations — Scoring & Selection

**Source:** `engines/lottery/calculations/scoring.py`

### 9.1 Candidate Qualification

**CE Candidate qualifies if:**
```
call_ltp is not None
band_min <= call_ltp <= band_max
strike > spot (OTM for calls)
distance >= config.otm_distance.min_points (default: 250)
```

**PE Candidate qualifies if:**
```
put_ltp is not None
band_min <= put_ltp <= band_max
strike < spot (OTM for puts)
|distance| >= config.otm_distance.min_points (default: 250)
```

### 9.2 Extrapolated Candidate Fallback

Extrapolated candidates are included ONLY if:
1. No visible candidates exist for that side, AND
2. Extrapolated strike `in_band == True`, AND
3. Distance >= `otm_min`

### 9.3 Scoring Formula (5-Factor Weighted Composite)

```
score = w1 * f_dist + w2 * f_mom + w3 * f_liq + w4 * f_band + w5 * B
```

| Component | Formula | Default Weight |
|-----------|---------|---------------|
| **f_dist** (Distance) | `abs(strike - spot) / strike_step` | `w1 = 1.0` |
| **f_mom** (Momentum) | `decay_ratio` (from base metrics) | `w2 = 1.0` |
| **f_liq** (Liquidity) | `ln(1 + volume)` if volume > 0, else 0 | `w3 = 1.0` |
| **f_band** (Band Fit) | See below | `w4 = 1.0` |
| **B** (Bias) | `bias_score` from side bias computation | `w5 = 1.0` |

**Band Fit (f_band) computation:**

| Mode | Formula |
|------|---------|
| `BINARY` | 1 if in band, 0 otherwise |
| `DISTANCE` | `1 - abs(ltp - mid) / (range / 2)` where `mid = (min + max) / 2`, `range = max - min` |

Default: `DISTANCE` mode (proximity-weighted, 1.0 at band center, 0.0 at edges)

### 9.4 Scoring for Extrapolated Candidates

```
f_dist = abs(strike - spot) / strike_step
f_mom = 0.0   (no decay data available)
f_liq = 0.0   (no volume data available)
f_band = band_fit(adjusted_premium)
B = bias_score or 0.0

score = w1*f_dist + w2*0 + w3*0 + w4*f_band + w5*B
```

Only distance and band features contribute (advisory quality).

### 9.5 Tie-Break Logic

When candidates score within `tie_epsilon` (default: 0.01):

1. Highest `band_fit` score
2. If still tied: lowest `spread_pct` (best liquidity)
3. If still tied: highest `volume`
4. If still tied: closest to target OTM distance = `(otm_min + otm_max) / 2`

### 9.6 Selection

- Separate `best_ce` and `best_pe` selected independently
- Both can be None if insufficient qualified candidates
- Minimum requirement: `config.scoring.min_valid_candidates` (default: 1)
- All candidates returned with full score breakdown for audit

### 9.7 ScoredCandidate Data

```python
ScoredCandidate(
    strike, option_type, ltp, score,
    components = {f_dist, f_mom, f_liq, f_band, bias, w1_dist, w2_mom, w3_liq, w4_band, w5_bias},
    band_fit, spread_pct, volume, distance,
    source = "VISIBLE" | "EXTRAPOLATED"
)
```

---

## 10. Strategy — State Machine (7 States)

**Source:** `engines/lottery/strategy/state_machine.py`

### 10.1 State Diagram

```
                        ┌────────────────────────────────────────────┐
                        │                                            │
                        ▼                                            │
                     ┌──────┐                                        │
        ┌───────────│ IDLE │──────────────┐                          │
        │           └──────┘              │                          │
        │  spot < lower_trigger    spot > upper_trigger              │
        ▼                                 ▼                          │
 ┌──────────────┐               ┌──────────────┐                    │
 │ZONE_ACTIVE_PE│               │ZONE_ACTIVE_CE│                    │
 └──────┬───────┘               └──────┬───────┘                    │
        │ candidate found               │ candidate found            │
        ▼                                ▼                           │
                  ┌─────────────────┐                                │
                  │ CANDIDATE_FOUND │                                │
                  └────────┬────────┘                                │
                           │ confirmation passes                     │
                           ▼                                         │
                     ┌──────────┐                                    │
                     │ IN_TRADE │                                    │
                     └────┬─────┘                                    │
                          │ exit triggered                           │
                          ▼                                          │
                  ┌──────────────┐                                   │
                  │ EXIT_PENDING │                                   │
                  └──────┬───────┘                                   │
                         │ immediate                                 │
                         ▼                                           │
                    ┌──────────┐                                     │
                    │ COOLDOWN │─── cooldown expires ─────────────────┘
                    └──────────┘
```

### 10.2 State Definitions

| State | Description | Transitions To |
|-------|------------|----------------|
| `IDLE` | Default, no activity. Awaiting trigger zone break. | `ZONE_ACTIVE_CE`, `ZONE_ACTIVE_PE` |
| `ZONE_ACTIVE_CE` | Spot > upper trigger. Searching for CE candidates. | `CANDIDATE_FOUND`, `IDLE` (reversal) |
| `ZONE_ACTIVE_PE` | Spot < lower trigger. Searching for PE candidates. | `CANDIDATE_FOUND`, `IDLE` (reversal) |
| `CANDIDATE_FOUND` | Premium band candidate identified. Awaiting confirmation. | `IN_TRADE`, `IDLE` (reversal/failure) |
| `IN_TRADE` | Active position open. Monitoring exits. | `EXIT_PENDING` |
| `EXIT_PENDING` | Trade exit executing. Immediately transitions. | `COOLDOWN` |
| `COOLDOWN` | Post-trade lockout (default: 300s). | `IDLE` |

### 10.3 State Context

```python
StateContext:
  state: MachineState = IDLE
  active_side: Optional[Side]        # CE or PE
  candidate: Optional[ScoredCandidate]
  entry_time: Optional[datetime]
  cooldown_start: Optional[float]    # epoch
  reentry_count: int = 0
  last_strike: Optional[float]
  transition_reason: str = ""
  rejection: Optional[RejectionReason]
  consecutive_losses: int = 0
  daily_trade_count: int = 0
  daily_pnl: float = 0.0
```

### 10.4 Evaluation Logic (Fixed Priority)

Evaluated in this exact order on every trigger cycle:

**1. Data Quality Gate**
```
If quality_status == FAIL → IDLE, rejection = DATA_QUALITY_FAIL
```

**2. Time Filters (IST)**
```
Outside market hours (before 09:15 or after 15:30) → IDLE
First 15 minutes (09:15 - 09:30) → IDLE
Lunch zone (12:30 - 13:15) → IDLE
Near close (after mandatory_squareoff - risk.no_trade_near_close_minutes) → IDLE
(Exception: IN_TRADE state bypasses time filters for exit management)
```

**3. Risk Limits**
```
daily_trade_count >= max_daily_trades (5) → IDLE
consecutive_losses >= max_consecutive_losses (3) → IDLE
daily_pnl <= -max_daily_loss (-5000) → IDLE
(Exception: IN_TRADE state bypasses risk limits for exit management)
```

**4. State-Specific Transitions**

| Current State | Condition | Next State |
|---------------|-----------|------------|
| IDLE | `lower_trigger <= spot <= upper_trigger` | IDLE (no-trade zone) |
| IDLE | `spot > upper_trigger` | ZONE_ACTIVE_CE |
| IDLE | `spot < lower_trigger` | ZONE_ACTIVE_PE |
| ZONE_ACTIVE_CE | `spot <= upper_trigger` | IDLE (reversal) |
| ZONE_ACTIVE_CE | candidate exists + valid spread + valid volume + re-entry OK | CANDIDATE_FOUND |
| ZONE_ACTIVE_PE | `spot >= lower_trigger` | IDLE (reversal) |
| ZONE_ACTIVE_PE | candidate exists + valid spread + valid volume + re-entry OK | CANDIDATE_FOUND |
| CANDIDATE_FOUND | (handled by signal engine) | — |
| IN_TRADE | (handled by signal engine) | — |
| EXIT_PENDING | immediate | COOLDOWN |
| COOLDOWN | `elapsed >= cooldown.seconds` | IDLE |

### 10.5 Trigger Zone Resolution

**STATIC mode:**
```
upper_trigger = config.triggers.upper_trigger (e.g., 22700.0)
lower_trigger = config.triggers.lower_trigger (e.g., 22650.0)
```

**DYNAMIC mode (default):**
```
below = [strike for strike in all_strikes if strike <= spot]
above = [strike for strike in all_strikes if strike > spot]
lower_trigger = max(below) if below else spot - strike_step
upper_trigger = min(above) if above else spot + strike_step

Example: spot = 22678, strikes = [22650, 22700, 22750]
  lower = 22650, upper = 22700
```

### 10.6 Re-entry Rules

```
Blocked if:
  reentry_count >= config.cooldown.max_reentries (default: 2)
  OR
  (not config.cooldown.allow_same_strike_reentry  AND  strike == last_strike)
```

### 10.7 Trade Lifecycle

```
sm.enter_trade():
  CANDIDATE_FOUND → IN_TRADE
  entry_time = now
  daily_trade_count += 1

sm.exit_trade(pnl):
  IN_TRADE → EXIT_PENDING
  daily_pnl += pnl
  if pnl < 0: consecutive_losses += 1
  else: consecutive_losses = 0
  reentry_count += 1
  last_strike = current strike

sm.confirm_exit():
  EXIT_PENDING → COOLDOWN
  cooldown_start = time.time()
  Clear candidate, entry_time

sm.reset_daily_counters():
  daily_trade_count = 0, daily_pnl = 0.0, consecutive_losses = 0
  (Called at start of trading day)
```

---

## 11. Strategy — Signal Engine (Entry/Exit)

**Source:** `engines/lottery/strategy/signal_engine.py`

### 11.1 Entry Signal Evaluation

1. Run `StateMachine.evaluate()` with current market data
2. Get updated context
3. Determine zone label: `CE_ACTIVE`, `PE_ACTIVE`, or `NO_TRADE`
4. Build `SignalEvent`:
   - If state == `CANDIDATE_FOUND` AND candidate exists → `validity = VALID`
   - Otherwise → `validity = INVALID` with rejection reason

### 11.2 Exit Signal Evaluation

Checked in this exact priority order (first match wins):

| Priority | Exit Type | Condition | Default |
|----------|-----------|-----------|---------|
| 1 | **Stop-Loss** | `current_ltp <= entry_price * sl_ratio` | entry * 0.5 |
| 2 | **Target 3** | `current_ltp >= entry_price * t3_ratio` | entry * 4.0 |
| 3 | **Target 2** | `current_ltp >= entry_price * t2_ratio` | entry * 3.0 |
| 4 | **Target 1** | `current_ltp >= entry_price * t1_ratio` | entry * 2.0 |
| 5 | **Invalidation** | CE: `spot <= upper_trigger`; PE: `spot >= lower_trigger` | enabled |
| 6 | **Trailing Stop** | `current_ltp < peak_ltp * (1 - trailing_pct/100)` | disabled |
| 7 | **Time Stop** | `elapsed_minutes >= time_stop_minutes` | disabled (0) |
| 8 | **EOD Exit** | IST time >= `mandatory_squareoff_time` | 15:15 IST |

### 11.3 Exit Levels Computation

```
sl = entry_price * 0.5    (lose 50% of premium = stop loss)
t1 = entry_price * 2.0    (100% gain)
t2 = entry_price * 3.0    (200% gain)
t3 = entry_price * 4.0    (300% gain)
```

### 11.4 IST Time Conversion

```python
ist_offset_seconds = 5 * 3600 + 30 * 60  # 19800 seconds
ist_ts = now.timestamp() + ist_offset_seconds
```

---

## 12. Strategy — Confirmation & Hysteresis

### 12.1 Breakout Confirmation

**Source:** `engines/lottery/strategy/confirmation.py`

**Modes:**

| Mode | Logic |
|------|-------|
| `DISABLED` | Always passes (no confirmation) |
| `CANDLE` | Last 1-min candle must close above/below trigger |
| `PREMIUM` | Premium must expand >= `premium_expansion_min_pct` (5%) since candidate found |
| `QUORUM` (default) | At least `quorum` (2) of the following must pass: spot hold, candle, premium expansion, volume spike |
| `HYBRID` | All checks must pass |

**Quorum checks:**
1. **Spot hold:** Spot held beyond trigger for `hold_duration_seconds` (15s)
2. **Candle confirmation:** 1-min candle close beyond trigger
3. **Premium expansion:** >= 5% since candidate found
4. **Volume spike:** current volume > avg volume * `volume_spike_multiplier` (1.5x)
5. **Spread quality:** spread widening < `spread_widen_max_pct` (20%)

### 12.2 Trigger Hysteresis

**Source:** `engines/lottery/strategy/hysteresis.py`

Prevents oscillation at trigger boundaries:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `buffer_points` | 10 | Spot must exceed trigger by 10 points to activate |
| `min_zone_hold_seconds` | 5 | Zone must hold for 5 seconds before candidate search |
| `rearm_distance_points` | 20 | After IDLE return, spot must move 20 points before re-activation |
| `invalidation_buffer_points` | 5 | Spot must reverse 5 points past trigger to invalidate trade |

---

## 13. Strategy — Risk Guard

**Source:** `engines/lottery/strategy/risk_guard.py`

### 13.1 Nine Pre-Trade Gates

All must pass before entry is allowed:

| Gate | Check | Config |
|------|-------|--------|
| 1. Capital sufficient | `running_capital > 0` | — |
| 2. Daily loss limit | `abs(daily_pnl) < max_daily_loss` | Rs 5000 |
| 3. Daily trade limit | `daily_trade_count < max_daily_trades` | 5 |
| 4. Consecutive loss limit | `consecutive_losses < max_consecutive_losses` | 3 |
| 5. Max open trades | `open_trades < max_open_trades` | 1 |
| 6. Quality gate | `quality_status != FAIL` | — |
| 7. Post-loss cooldown | If `cooldown_after_loss` and last trade was loss, enforce cooldown | enabled |
| 8. Poor quality gate | If `no_trade_poor_quality`, reject on WARN quality | enabled |
| 9. Near close gate | No new trades within `no_trade_near_close_minutes` of close | 10 min |

---

## 14. Paper Trading — Broker Simulation

**Source:** `engines/lottery/paper_trading/broker.py`

### 14.1 Execution Modes

| Mode | Buy Fill | Sell Fill |
|------|----------|-----------|
| `LTP` | Last traded price | Last traded price |
| `ASK` | Ask price | — |
| `BID` | — | Bid price |
| `MID` | (bid + ask) / 2 | (bid + ask) / 2 |
| `MID_SLIPPAGE` (default) | mid + (mid * slippage_pct / 100) | mid - (mid * slippage_pct / 100) |

Default slippage: 0.5%

### 14.2 Charge Calculation

```
brokerage = brokerage_per_lot * lots          (default: Rs 20/lot)
exchange = brokerage * (exchange_charges_pct / 100)  (default: 0.05%)
total_charges = brokerage + exchange
```

### 14.3 PnL Calculation

```
pnl = (exit_price - entry_price) * qty - total_charges
```

### 14.4 Trade Record (PaperTrade)

Tracks: `trade_id`, `entry/exit timestamps`, `side`, `symbol`, `expiry`, `strike`, `option_type`, `selection_price` (LTP at scoring), `confirmation_price` (LTP at trigger pass), `entry_price` (simulated fill), `exit_price`, `qty`, `lots`, `capital_before/after`, `sl/t1/t2/t3`, `pnl`, `charges`, `status` (OPEN/CLOSED), `reason_entry/exit`, `signal_id`, `snapshot_id`, `config_version`

---

## 15. Paper Trading — Capital Manager

**Source:** `engines/lottery/paper_trading/capital_manager.py`

### 15.1 Position Sizing Modes

| Mode | Logic |
|------|-------|
| `FIXED_LOTS` (default) | `lots = config.paper_trading.fixed_lots` (default: 1) |
| `FIXED_RUPEE` | `max_risk = capital * risk_pct / 100`; `sl_loss = entry * sl_ratio * lot_size`; `lots = max(1, int(max_risk / sl_loss))` capped at `fixed_lots * 5` |
| `PCT_CAPITAL` | `budget = capital * risk_pct / 100`; `cost = entry * lot_size`; `lots = max(1, int(budget / cost))` |
| `PREMIUM_BUDGET` | Same as `PCT_CAPITAL` |

### 15.2 Capital Tracking

```
starting_capital = Rs 100,000 (default)
running_capital: adjusted after each trade
peak_capital: highest capital reached
realized_pnl: cumulative
daily_pnl: reset each day
total_charges: sum of all charges
ledger: list of CapitalLedgerEntry records

Drawdown:
  drawdown = peak_capital - running_capital
  drawdown_pct = (peak_capital - running_capital) / peak_capital * 100
```

---

## 16. Storage — SQLite Database

**Source:** `engines/lottery/storage/db.py`

### 16.1 Database Setup

- **Path:** `{config.storage.db_path_parent}/{SYMBOL.upper()}/{filename}`
- **Mode:** WAL (`PRAGMA journal_mode=WAL`)
- **Sync:** NORMAL (`PRAGMA synchronous=NORMAL`)
- **Multi-instrument:** Each symbol gets its own DB directory

### 16.2 Schema (10 Tables)

#### Table 1: `raw_chain_snapshots`
```sql
CREATE TABLE raw_chain_snapshots (
    snapshot_id    TEXT PRIMARY KEY,
    symbol         TEXT NOT NULL,
    expiry         TEXT,
    spot_ltp       REAL NOT NULL,
    snapshot_timestamp TEXT NOT NULL,
    row_count      INTEGER NOT NULL,
    rows_json      TEXT NOT NULL          -- JSON array of all OptionRow data
);
```

#### Table 2: `validated_chain_rows`
```sql
CREATE TABLE validated_chain_rows (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id    TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    overall_status TEXT NOT NULL,          -- PASS, WARN, FAIL
    quality_score  REAL NOT NULL,          -- 0.0 to 1.0
    checks_json    TEXT NOT NULL,          -- JSON of 9 quality checks
    timestamp      TEXT NOT NULL
);
```

#### Table 3: `calculated_rows`
```sql
CREATE TABLE calculated_rows (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id    TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    spot_ltp       REAL NOT NULL,
    config_version TEXT NOT NULL,
    row_count      INTEGER NOT NULL,
    rows_json      TEXT NOT NULL,          -- JSON of all CalculatedRow metrics
    timestamp      TEXT NOT NULL
);
```

#### Table 4: `signal_events`
```sql
CREATE TABLE signal_events (
    signal_id           TEXT PRIMARY KEY,
    timestamp           TEXT NOT NULL,
    symbol              TEXT,
    side_bias           TEXT,              -- CE or PE
    zone                TEXT,              -- CE_ACTIVE, PE_ACTIVE, NO_TRADE
    machine_state       TEXT NOT NULL,
    selected_strike     REAL,
    selected_option_type TEXT,
    selected_premium    REAL,
    trigger_status      TEXT,
    validity            TEXT NOT NULL,     -- VALID or INVALID
    rejection_reason    TEXT,
    rejection_detail    TEXT,
    snapshot_id         TEXT,
    config_version      TEXT,
    spot_ltp            REAL
);
```

#### Table 5: `paper_trades`
```sql
CREATE TABLE paper_trades (
    trade_id           TEXT PRIMARY KEY,
    timestamp_entry    TEXT NOT NULL,
    timestamp_exit     TEXT,
    side               TEXT NOT NULL,      -- CE or PE
    symbol             TEXT NOT NULL,
    expiry             TEXT,
    strike             REAL NOT NULL,
    option_type        TEXT NOT NULL,
    selection_price    REAL,               -- LTP at scoring time
    confirmation_price REAL,               -- LTP at trigger pass
    entry_price        REAL NOT NULL,      -- simulated fill price
    exit_price         REAL,
    qty                INTEGER NOT NULL,
    lots               INTEGER NOT NULL,
    capital_before     REAL,
    capital_after      REAL,
    sl                 REAL,
    t1                 REAL,
    t2                 REAL,
    t3                 REAL,
    pnl                REAL,
    charges            REAL,
    status             TEXT NOT NULL,      -- OPEN, CLOSED, CANCELLED
    reason_entry       TEXT,
    reason_exit        TEXT,
    exit_detail        TEXT,
    signal_id          TEXT,
    snapshot_id        TEXT,
    config_version     TEXT
);
```

#### Table 6: `capital_ledger`
```sql
CREATE TABLE capital_ledger (
    entry_id        TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    symbol          TEXT,
    trade_id        TEXT,
    event           TEXT NOT NULL,        -- TRADE_ENTRY, TRADE_EXIT, INIT, CHARGE
    amount          REAL NOT NULL,
    running_capital REAL NOT NULL,
    realized_pnl    REAL,
    unrealized_pnl  REAL,
    daily_pnl       REAL,
    drawdown        REAL,
    peak_capital    REAL
);
```

#### Table 7: `debug_events`
```sql
CREATE TABLE debug_events (
    cycle_id        TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    symbol          TEXT,
    snapshot_id     TEXT,
    config_version  TEXT,
    fetch_summary   TEXT,                 -- JSON
    validation_result TEXT,               -- JSON
    derived_variables TEXT,               -- JSON
    side_bias_decision TEXT,              -- JSON
    strike_scan_results TEXT,             -- JSON
    final_selection TEXT,                 -- JSON
    trade_decision  TEXT,                 -- JSON
    paper_execution TEXT,                 -- JSON
    latency_ms      TEXT                  -- JSON with per-step timings
);
```

#### Table 8: `config_versions`
```sql
CREATE TABLE config_versions (
    version_hash TEXT PRIMARY KEY,        -- SHA-256 first 12 chars
    config_json  TEXT NOT NULL,           -- Full config snapshot
    saved_at     TEXT NOT NULL
);
```

#### Table 9: `strike_rejection_audit`
```sql
CREATE TABLE strike_rejection_audit (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id       TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    strike            REAL NOT NULL,
    option_type       TEXT NOT NULL,
    ltp               REAL,
    band_pass         INTEGER,            -- 0 or 1
    distance_pass     INTEGER,
    direction_pass    INTEGER,
    tradability_pass  INTEGER,
    liquidity_pass    INTEGER,
    spread_pass       INTEGER,
    bias_pass         INTEGER,
    trigger_pass      INTEGER,
    score             REAL,
    accepted          INTEGER,
    rejection_primary TEXT,
    rejection_all     TEXT,               -- comma-separated
    timestamp         TEXT NOT NULL
);
```

#### Table 10: `divergence_reports`
```sql
CREATE TABLE divergence_reports (
    report_id                TEXT,
    symbol                   TEXT NOT NULL,
    timestamp                TEXT NOT NULL,
    candidate_selected_time  TEXT,
    entry_time               TEXT,
    exit_time                TEXT,
    selection_price          REAL,
    confirmation_price       REAL,
    simulated_entry_price    REAL,
    simulated_exit_price     REAL,
    spread_at_entry          REAL,
    spread_pct_at_entry      REAL,
    truly_executable         INTEGER,      -- 0 or 1
    tradability_detail       TEXT,
    max_favorable_excursion  REAL,
    max_adverse_excursion    REAL,
    mfe_pct                  REAL,
    mae_pct                  REAL,
    trade_id                 TEXT,
    side                     TEXT,
    strike                   REAL,
    pnl                      REAL,
    status                   TEXT,
    rejected                 INTEGER,
    rejection_reasons        TEXT,         -- comma-separated
    selection_to_entry_slippage     REAL,
    selection_to_entry_slippage_pct REAL
);
```

### 16.3 Indexes

```sql
idx_signals_timestamp     ON signal_events(timestamp)
idx_signals_validity      ON signal_events(validity)
idx_trades_status         ON paper_trades(status)
idx_trades_entry          ON paper_trades(timestamp_entry)
idx_ledger_timestamp      ON capital_ledger(timestamp)
idx_debug_timestamp       ON debug_events(timestamp)
idx_snapshots_symbol      ON raw_chain_snapshots(symbol)
idx_rejection_snapshot    ON strike_rejection_audit(snapshot_id)
idx_rejection_strike      ON strike_rejection_audit(strike)
```

### 16.4 Runtime Data Files

- `engines/lottery/data/NIFTY/lottery.db` (+ WAL, SHM)
- `engines/lottery/data/BANKNIFTY/lottery.db` (+ WAL, SHM)
- `engines/lottery/data/SENSEX/lottery.db` (+ WAL, SHM)
- `engines/lottery/data/NIFTY/test_replay.jsonl`

---

## 17. Memory State — Runtime

**Source:** `engines/lottery/memory_state/runtime.py`

### 17.1 Circular Buffer Sizes

```python
_MAX_SPOT_HISTORY    = 300    # 5 minutes at 1s polling
_MAX_SNAPSHOT_HASHES = 20     # Recent chain hashes for stale detection
_MAX_SIGNALS         = 100    # Recent signal events
_MAX_DEBUG_EVENTS    = 50     # Recent debug traces
_MAX_REJECTIONS      = 100    # Recent rejection reasons
_MAX_TRADES          = 500    # Trade history
```

### 17.2 RuntimeState Fields

| Category | Field | Type | Description |
|----------|-------|------|-------------|
| **Spot** | `last_spot_ltp` | float | Current spot from WebSocket |
| | `last_spot_timestamp` | datetime | When spot was received |
| **Snapshots** | `last_chain_snapshot` | ChainSnapshot | Latest full chain |
| | `last_calculated` | CalculatedSnapshot | Latest metrics |
| | `last_quality_report` | QualityReport | Latest quality check |
| **Signal** | `last_signal` | SignalEvent | Most recent signal |
| | `last_selected_strike` | float | Strike from last signal |
| | `last_side_bias` | Side | CE or PE preference |
| | `last_bias_score` | float | Bias magnitude |
| **Trade** | `active_trade` | PaperTrade | Currently open position |
| | `active_trade_peak_ltp` | float | Highest LTP reached (trailing stop) |
| **SM** | `machine_state` | MachineState | Current state |
| **Buffers** | `spot_history` | deque(300) | `{ltp, timestamp}` entries |
| | `snapshot_hashes` | deque(20) | Chain hash strings |
| | `recent_signals` | deque(100) | SignalEvent objects |
| | `recent_debug` | deque(50) | DebugTrace objects |
| | `recent_rejections` | deque(100) | `{timestamp, reason, detail, state}` |
| | `trade_history` | deque(500) | Closed PaperTrade objects |
| **Cycle** | `cycle_count` | int | Total cycles run |
| | `last_cycle_time` | datetime | When last cycle ran |
| | `last_cycle_latency_ms` | float | Cycle execution time |
| **Startup** | `symbol` | str | Instrument |
| | `started_at` | datetime | Pipeline startup time |

### 17.3 Status Summary (for dashboard)

```python
get_status_summary() -> {
    "symbol", "state", "spot", "spot_timestamp",
    "side_bias", "bias_score", "selected_strike",
    "active_trade" (dict or None),
    "quality", "quality_score",
    "cycle_count", "last_cycle_latency_ms",
    "recent_signals" (count), "recent_rejections" (count),
    "trade_history_count", "spot_history_count",
    "uptime_seconds"
}
```

---

## 18. Reporting & Tables

**Source:** `engines/lottery/reporting/tables.py`

### 18.1 Table Generators

All return `list[dict]` (JSON-serializable):

| Function | Columns |
|----------|---------|
| `raw_data_table(snapshot)` | strike, CE_LTP/change/volume/OI/IV/bid/ask, PE_LTP/change/volume/OI/IV/bid/ask |
| `formula_audit_table(rows, ext_ce, ext_pe)` | strike, distance, intrinsic/extrinsic, decay_ratio, liquidity_skew, spread_pct, slope, theta_density, band_eligible, score, score_components, source |
| `quality_table(report)` | check_name, status, threshold, observed, result, reason (+ OVERALL summary row) |
| `signal_table(signals)` | timestamp, side_bias, zone, machine_state, selected_strike/type/premium, trigger_status, validity, rejection_reason/detail, spot_ltp |
| `trade_table(trades)` | trade_id, entry/exit_time, side, symbol, strike, entry/exit_price, qty, lots, sl/t1/t2/t3, pnl, charges, capital_before/after, status, reason_entry/exit |
| `capital_table(ledger)` | timestamp, event, trade_id, amount, running_capital, realized_pnl, daily_pnl, drawdown, peak_capital |
| `candidate_table(candidates)` | strike, option_type, ltp, score, band_fit, spread_pct, volume, distance, source, f_dist/f_mom/f_liq/f_band/bias |

---

## 19. Divergence Analysis

**Source:** `engines/lottery/reporting/divergence.py`

### 19.1 Purpose

Measures the gap between paper trading simulation and realistic execution. Answers: "Would this trade have been executable in live markets?"

### 19.2 Trade Divergence Report

```python
build_trade_divergence(trade, peak_ltp, trough_ltp, spread_at_entry, ...)
```

Metrics computed:
- **MFE** (Max Favorable Excursion) = `peak_ltp - entry_price` (best possible exit)
- **MAE** (Max Adverse Excursion) = `entry_price - trough_ltp` (worst drawdown)
- **MFE %** = `MFE / entry_price * 100`
- **MAE %** = `MAE / entry_price * 100`
- **Slippage** = `entry_price - selection_price` (difference from scoring to fill)
- **Slippage %** = `slippage / selection_price * 100`
- **Truly executable** = did tradability checks pass at entry moment?

### 19.3 Rejection Divergence Report

```python
build_rejection_divergence(symbol, strike, side, selection_price, rejection_reasons, ...)
```

Records near-trades that were rejected before entry (for analysis of missed opportunities).

---

## 20. Debugging & Observability

### 20.1 Structured Logging

**Source:** `engines/lottery/debugging/logger.py`

| Handler | Format | Output |
|---------|--------|--------|
| Console | Plain (`HH:MM:SS.mmm LEVEL [SYMBOL] message`) | Always active |
| File | JSON lines (one JSON dict per line) | If `config.logging.json_output = True` |

**JSON fields:** `ts`, `level`, `symbol`, `module`, `func`, `msg`, `cycle_id`, `snapshot_id`, `data`, `error`, `error_type`

**Log levels:** TRACE (5) < DEBUG (10) < INFO (20) < WARN (30) < ERROR (40)

**Log directory:** `engines/lottery/logs/{SYMBOL.upper()}/lottery_YYYY-MM-DD.jsonl`

### 20.2 Per-Cycle Debug Trace

**Source:** `engines/lottery/debugging/trace.py`

`CycleTracer` records 8 pipeline steps per cycle:

| Step | Method | Records |
|------|--------|---------|
| 1 | `record_fetch()` | success, spot_ltp, rows, strikes, expiry, elapsed_ms |
| 2 | `record_validation()` | overall status, score, per-check results, failed checks |
| 3 | `record_calculations()` | total_strikes, window_strikes, band_eligible_ce/pe, elapsed_ms |
| 4 | `record_side_bias()` | preferred_side, bias_score, avg_call_decay, avg_put_decay |
| 5 | `record_strike_scan()` | total/ce/pe candidates, extrapolated_ce/pe counts |
| 6 | `record_selection()` | best_ce details (strike, ltp, score, source), best_pe details |
| 7 | `record_trade_decision()` | validity, machine_state, zone, strike, premium, rejection |
| 8 | `record_paper_execution()` | action (ENTRY/EXIT/HOLD), trade_id, strike, side, entry/exit_price, pnl |

**Latency breakdown:** `fetch_ms`, `validation_ms`, `calculation_ms`, `scoring_ms`, `total_ms`

### 20.3 Failure Buckets

8 categories: `DATA_FETCH`, `PARSING`, `VALIDATION`, `STALE_DATA`, `MISSING_STRIKE`, `STRATEGY_REJECTION`, `PAPER_TRADING`, `PERSISTENCE`

Each bucket tracks: count, last 10 errors (with timestamps), returns last 3 for dashboard.

---

## 21. Alerting — Telegram Integration

**Source:** `engines/lottery/alerting/notifier.py`

### 21.1 Credentials

- Bot token: env var `LOTTERY_TELEGRAM_TOKEN`
- Chat ID: env var `LOTTERY_TELEGRAM_CHAT_ID`
- Fallback: `.env` file search (project root and parents)
- Gracefully skips if not configured

### 21.2 Alert Types

| Method | Event | Rate Limit |
|--------|-------|-----------|
| `on_pipeline_start()` | Pipeline startup | None |
| `on_pipeline_stop()` | Pipeline shutdown | None |
| `on_trade_entry()` | Entry: strike, price, SL, T1 | 30s |
| `on_trade_exit()` | Exit: reason, PnL, capital | 30s |
| `on_candidate_found()` | New lottery candidate found | 30s |
| `on_quality_warning()` | Data quality degradation | 120s |
| `on_system_error()` | Pipeline errors | 60s |
| `send_custom()` | Custom messages | None |

### 21.3 Implementation

- **Async:** Fire-and-forget via daemon threading (never blocks pipeline)
- **Rate limiting:** Per-event-type with last-send tracking
- **Formatting:** Telegram Markdown
- **HTTP timeout:** 10 seconds

---

## 22. Configuration Reference (130+ Parameters)

**Sources:** `engines/lottery/config/settings.py` (dataclasses), `engines/lottery/config/settings.yaml` (defaults)

### 22.1 Config Loading

- Primary: `load_config(path)` from YAML
- Overrides: environment variables `LOTTERY_SECTION_KEY=value`
- Type coercion: automatic int/float/bool from env vars
- All configs frozen (`@dataclass(frozen=True)`)
- Version: `SHA256(sorted JSON repr)[:12]`

### 22.2 Complete Parameter Table

#### INSTRUMENT
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symbol` | str | `"NIFTY"` | Trading instrument |
| `exchange` | str | `"NSE"` | Exchange |
| `strike_step` | int | `50` | Step between strikes (NIFTY=50) |
| `expiry_mode` | enum | `NEAREST_WEEKLY` | NEAREST_WEEKLY, NEAREST_MONTHLY, SPECIFIC |

#### PREMIUM_BAND
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min` | float | `2.10` | Minimum acceptable premium (Rs) |
| `max` | float | `8.50` | Maximum acceptable premium (Rs) |
| `fit_mode` | enum | `DISTANCE` | BINARY (in/out) or DISTANCE (proximity-weighted) |

#### OTM_DISTANCE
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min_points` | int | `250` | Minimum OTM distance from ATM (points) |
| `max_points` | int | `450` | Maximum OTM distance from ATM (points) |

#### TRIGGERS
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | enum | `DYNAMIC` | DYNAMIC (from chain) or STATIC (hardcoded) |
| `upper_trigger` | float | `22700.0` | Static upper trigger (if STATIC) |
| `lower_trigger` | float | `22650.0` | Static lower trigger (if STATIC) |

#### SCORING
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `w1_distance` | float | `1.0` | Weight for OTM distance |
| `w2_momentum` | float | `1.0` | Weight for decay rate |
| `w3_liquidity` | float | `1.0` | Weight for volume |
| `w4_band_fit` | float | `1.0` | Weight for premium band fit |
| `w5_bias` | float | `1.0` | Weight for side bias |
| `tie_epsilon` | float | `0.01` | Score comparison tolerance |
| `min_valid_candidates` | int | `1` | Minimum candidates to proceed |

#### WINDOW
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `type` | enum | `ATM_SYMMETRIC` | FULL_CHAIN, ATM_SYMMETRIC, VISIBLE_RANGE |
| `size` | int | `4` | ATM +/- N strikes |

#### DECAY / MOMENTUM
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | enum | `NORMALIZED` | RAW or NORMALIZED (0-1 scale) |
| `epsilon` | float | `0.01` | Floor divisor (prevent /0) |

#### BIAS
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `aggregation` | enum | `MEAN` | MEAN, VOLUME_WEIGHTED, DISTANCE_WEIGHTED |
| `use_pcr` | bool | `False` | Include Put-Call Ratio |

#### EXTRAPOLATION
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fit_window_ce` | int | `3` | Window for CE polynomial fit |
| `fit_window_pe` | int | `3` | Window for PE polynomial fit |
| `alpha_mode` | enum | `CALIBRATED` | FIXED or CALIBRATED |
| `alpha_value` | float | `0.05` | Smoothing alpha (if FIXED) |
| `min_valid_strikes` | int | `3` | Skip extrapolation if fewer OTM strikes |

#### DATA_QUALITY
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_spot_age_ms` | int | `2000` | Reject if spot > 2s old |
| `max_chain_age_ms` | int | `5000` | Reject if chain > 5s old |
| `max_cross_source_skew_ms` | int | `3000` | Max spot/chain time difference |
| `intrinsic_floor_epsilon` | float | `0.50` | ITM protection floor |
| `min_volume` | int | `1000` | Minimum trade volume |
| `min_oi` | int | `500` | Minimum open interest |
| `max_spread_pct` | float | `5.0` | Max bid-ask spread % |
| `min_bid_qty` | int | `1` | Minimum bid quantity |
| `min_ask_qty` | int | `1` | Minimum ask quantity |
| `max_stale_cycles` | int | `5` | Reject if hash unchanged 5+ cycles |
| `snapshot_mode` | enum | `STRICT` | STRICT or TOLERANT |

#### PAPER_TRADING
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `starting_capital` | float | `100000.0` | Initial capital (Rs) |
| `lot_size` | int | `75` | NIFTY lot size |
| `max_risk_per_trade_pct` | float | `2.0` | Max risk % per trade |
| `max_daily_loss` | float | `5000.0` | Max daily loss (Rs) |
| `max_open_trades` | int | `1` | Max concurrent positions |
| `max_daily_trades` | int | `5` | Max trades per day |
| `max_consecutive_losses` | int | `3` | Max loss streak |
| `sizing_mode` | enum | `FIXED_LOTS` | FIXED_LOTS, FIXED_RUPEE, PCT_CAPITAL, PREMIUM_BUDGET |
| `fixed_lots` | int | `1` | Position size in lots |

#### EXECUTION
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | enum | `MID_SLIPPAGE` | LTP, MID, ASK, BID, MID_SLIPPAGE |
| `slippage_pct` | float | `0.5` | Slippage % |
| `brokerage_per_lot` | float | `20.0` | Brokerage per lot (Rs) |
| `exchange_charges_pct` | float | `0.05` | Exchange charges % |

#### EXIT_RULES
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sl_ratio` | float | `0.5` | Stop-loss = entry * 0.5 |
| `t1_ratio` | float | `2.0` | Target 1 = entry * 2.0 |
| `t2_ratio` | float | `3.0` | Target 2 = entry * 3.0 |
| `t3_ratio` | float | `4.0` | Target 3 = entry * 4.0 |
| `time_stop_minutes` | int | `0` | Exit after N min (0 = disabled) |
| `eod_exit` | bool | `True` | Force exit at session end |
| `trailing_stop` | bool | `False` | Enable trailing stop |
| `trailing_stop_pct` | float | `0.0` | Trailing stop % |
| `invalidation_exit` | bool | `True` | Exit on trigger reversal |

#### TIME_FILTERS
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `no_trade_first_minutes` | int | `15` | No entry first 15 min |
| `no_trade_lunch_start` | str | `"12:30"` | Lunch start (IST) |
| `no_trade_lunch_end` | str | `"13:15"` | Lunch end (IST) |
| `mandatory_squareoff_time` | str | `"15:15"` | Force exit time (IST) |
| `market_open` | str | `"09:15"` | Market open (IST) |
| `market_close` | str | `"15:30"` | Market close (IST) |

#### COOLDOWN
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `seconds` | int | `300` | Post-trade lockout (5 min) |
| `max_reentries` | int | `2` | Max re-entries per session |
| `allow_same_strike_reentry` | bool | `False` | Can't re-enter same strike |

#### HYSTERESIS
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `buffer_points` | float | `10.0` | Spot must exceed trigger by N points |
| `min_zone_hold_seconds` | float | `5.0` | Zone must hold 5 seconds |
| `rearm_distance_points` | float | `20.0` | Required movement after IDLE return |
| `invalidation_buffer_points` | float | `5.0` | Reversal buffer for trade invalidation |

#### TRADABILITY
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `require_bid` | bool | `True` | Bid must exist |
| `require_ask` | bool | `True` | Ask must exist |
| `min_bid_qty` | int | `50` | Minimum bid quantity |
| `min_ask_qty` | int | `50` | Minimum ask quantity |
| `min_recent_volume` | int | `500` | Minimum strike-specific volume |
| `max_spread_pct` | float | `10.0` | Max spread % for candidate |

#### CONFIRMATION
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | str | `"QUORUM"` | QUORUM, CANDLE, PREMIUM, HYBRID, DISABLED |
| `quorum` | int | `2` | Min checks to pass |
| `hold_duration_seconds` | float | `15.0` | Spot must hold beyond trigger |
| `premium_expansion_min_pct` | float | `5.0` | Min premium expansion |
| `volume_spike_multiplier` | float | `1.5` | Volume spike threshold |
| `spread_widen_max_pct` | float | `20.0` | Max spread widening |

#### STRATEGY
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | str | `"AUTO"` | AUTO, PRE_EXPIRY_MOMENTUM, DTE1_HYBRID, EXPIRY_DAY_TRUE_LOTTERY |

#### REFRESH (Adaptive Scheduler)
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `chain_idle_seconds` | int | `30` | Chain refresh (idle) |
| `chain_active_seconds` | int | `30` | Chain refresh (in zone) |
| `candidate_zone_seconds` | int | `5` | Quote refresh (zone active) |
| `candidate_found_seconds` | int | `2` | Quote refresh (candidate found) |
| `trade_quote_seconds` | int | `1` | Quote refresh (in trade) |
| `spot_drift_threshold` | float | `100.0` | Force refresh if spot drifts > 100 pts |
| `candidate_stale_seconds` | float | `60.0` | Force refresh if candidate > 60s old |

#### RISK
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `cooldown_after_loss` | bool | `True` | Enforce cooldown after loss |
| `no_trade_poor_quality` | bool | `True` | Don't trade on WARN quality |
| `no_trade_near_close_minutes` | int | `10` | No entry in final 10 min |

#### POLLING
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `interval_seconds` | int | `1` | Trigger cycle interval |
| `chain_refresh_seconds` | int | `30` | Analysis cycle interval |
| `retry_max_attempts` | int | `3` | Chain fetch retries |
| `retry_backoff_base_ms` | int | `500` | Backoff base (500ms -> 1s -> 2s) |

#### LOGGING
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `level` | enum | `INFO` | TRACE, DEBUG, INFO, WARN, ERROR |
| `json_output` | bool | `True` | JSON-formatted file logs |
| `log_dir` | str | `"engines/lottery/logs"` | Log directory |

#### STORAGE
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `db_path` | str | `"engines/lottery/data/lottery.db"` | SQLite path |
| `snapshot_dump_on_failure` | bool | `True` | Dump snapshot on error |
| `snapshot_dump_on_signal` | bool | `True` | Dump snapshot on signal |

#### ALERTING
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | bool | `False` | Enable notifications |
| `channels` | tuple | `()` | telegram, slack, email |
| `telegram_bot_token_env` | str | `"LOTTERY_TELEGRAM_TOKEN"` | Env var for bot token |
| `telegram_chat_id_env` | str | `"LOTTERY_TELEGRAM_CHAT_ID"` | Env var for chat ID |

---

## 23. Data Models Reference

**Source:** `engines/lottery/models.py`

### 23.1 Enums

| Enum | Values |
|------|--------|
| `OptionType` | CE, PE |
| `Side` | CE, PE |
| `TradeStatus` | OPEN, CLOSED, CANCELLED |
| `QualityStatus` | PASS, WARN, FAIL |
| `SignalValidity` | VALID, INVALID |
| `MachineState` | IDLE, ZONE_ACTIVE_CE, ZONE_ACTIVE_PE, CANDIDATE_FOUND, IN_TRADE, EXIT_PENDING, COOLDOWN |
| `RejectionReason` | DATA_QUALITY_FAIL, STALE_DATA, ZONE_INACTIVE, NO_BAND_CANDIDATE, SPREAD_TOO_WIDE, LIQUIDITY_TOO_LOW, RISK_REJECTION, COOLDOWN_ACTIVE, TIME_FILTER, INSUFFICIENT_OTM_POINTS, MAX_DAILY_TRADES, MAX_CONSECUTIVE_LOSSES, MAX_DAILY_LOSS |
| `ExitReason` | STOP_LOSS, TARGET_1, TARGET_2, TARGET_3, TIME_STOP, EOD_EXIT, TRAILING_STOP, INVALIDATION, MANUAL |

### 23.2 Config Enums

| Enum | Values |
|------|--------|
| `ExpiryMode` | NEAREST_WEEKLY, NEAREST_MONTHLY, SPECIFIC |
| `BandFitMode` | BINARY, DISTANCE |
| `TriggerMode` | DYNAMIC, STATIC |
| `WindowType` | FULL_CHAIN, ATM_SYMMETRIC, VISIBLE_RANGE |
| `DecayMode` | RAW, NORMALIZED |
| `BiasAggregation` | MEAN, VOLUME_WEIGHTED, DISTANCE_WEIGHTED |
| `AlphaMode` | FIXED, CALIBRATED |
| `SnapshotMode` | STRICT, TOLERANT |
| `SizingMode` | FIXED_LOTS, FIXED_RUPEE, PCT_CAPITAL, PREMIUM_BUDGET |
| `ExecutionMode` | LTP, MID, ASK, BID, MID_SLIPPAGE |
| `LogLevel` | TRACE, DEBUG, INFO, WARN, ERROR |

### 23.3 Core Data Models (all frozen dataclasses)

| Model | Key Fields |
|-------|------------|
| `UnderlyingTick` | symbol, exchange, ltp, open, high, low, prev_close, source_timestamp |
| `OptionRow` | symbol, expiry, strike, option_type, ltp, change, change_percent, volume, oi, oi_change, bid, ask, bid_qty, ask_qty, iv, last_trade_time, source_timestamp, ingested_at |
| `ChainSnapshot` | snapshot_id, symbol, expiry, spot_ltp, snapshot_timestamp, rows, spot_tick. Properties: call_rows, put_rows, strikes |
| `CalculatedRow` | strike, distance, abs_distance, call/put intrinsic/extrinsic, call/put decay_abs/ratio, call/put volume, liquidity_skew, call/put spread/spread_pct, call/put slope/theta_density, call/put ltp, call/put band_eligible, call/put candidate_score/components |
| `ExtrapolatedStrike` | strike, option_type, estimated_premium, adjusted_premium, steps_from_atm, alpha_used, in_band, score, score_components |
| `ScoredCandidate` | strike, option_type, ltp, score, components, band_fit, spread_pct, volume, distance, source |
| `PaperTrade` | trade_id, timestamp_entry/exit, side, symbol, expiry, strike, option_type, selection_price, confirmation_price, entry_price, exit_price, qty, lots, capital_before/after, sl/t1/t2/t3, pnl, charges, status, reason_entry/exit, exit_detail, signal_id, snapshot_id, config_version |
| `SignalEvent` | signal_id, timestamp, symbol, side_bias, zone, machine_state, selected_strike/option_type/premium, trigger_status, validity, rejection_reason/detail, snapshot_id, config_version, spot_ltp |
| `QualityCheck` | check_name, status, threshold, observed, result, reason |
| `QualityReport` | snapshot_id, symbol, overall_status, quality_score, checks, timestamp |
| `CapitalLedgerEntry` | entry_id, timestamp, symbol, trade_id, event, amount, running_capital, realized_pnl, unrealized_pnl, daily_pnl, drawdown, peak_capital |
| `DebugTrace` | cycle_id, timestamp, symbol, snapshot_id, config_version, fetch_summary, validation_result, derived_variables, side_bias_decision, strike_scan_results, final_selection, trade_decision, paper_execution, latency_ms |
| `StrikeRejectionAudit` | snapshot_id, symbol, strike, option_type, ltp, band_pass, distance_pass, direction_pass, tradability_pass, liquidity_pass, spread_pass, bias_pass, trigger_pass, score, accepted, rejection_primary, rejection_all, timestamp |
| `DivergenceReport` | report_id, symbol, timestamp, candidate_selected_time, entry_time, exit_time, selection_price, confirmation_price, simulated_entry/exit_price, spread_at_entry, truly_executable, tradability_detail, mfe/mae (abs + pct), trade_id, side, strike, pnl, status, rejected, rejection_reasons, slippage/slippage_pct |

---

## 24. External Integrations

### 24.1 REST API (FastAPI)

**Source:** `ClawWork/livebench/api/routers/lottery.py`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/lottery/status` | GET | Pipeline status (state, spot, quality, uptime) |
| `/api/lottery/config` | GET | Current config + version hash |
| `/api/lottery/raw-data` | GET | Latest option chain snapshot |
| `/api/lottery/formula-audit` | GET | Calculated metrics table |
| `/api/lottery/quality` | GET | Quality check results |
| `/api/lottery/signals` | GET | Signal events |
| `/api/lottery/trades` | GET | Paper trades |
| `/api/lottery/capital` | GET | Capital tracking |

- **WebSocket:** Live 1s streaming updates
- **Multi-symbol:** NIFTY, BANKNIFTY, FINNIFTY, SENSEX via dropdown
- **Architecture:** Lazy-loaded singleton (pipeline starts on first request)

### 24.2 Hybrid Execution Bridge

**Source:** `ClawWork/livebench/trading/hybrid_execution_bridge.py`

- Connects RegimeHunterPipeline to Lottery options paper trades
- Entry: confidence >= 55%, action in {BUY_CE, BUY_PE, BUY_CE_AT_SUPPORT, BUY_PE_AT_RESISTANCE}
- Exit: 10x target OR -70% stop OR 15:00 IST OR manual
- Loop interval: 60 seconds (background daemon thread)
- Data: `ClawWork/livebench/data/hybrid_bridge/` (JSON persisted positions)

### 24.3 Lottery Strike Selector

**Source:** `ClawWork/livebench/bots/regime_modules/lottery_strike_selector.py`

Per-index parameters:

| Index | Strike Interval | Lot Size | Premium Range | Min OI |
|-------|----------------|----------|---------------|--------|
| BANKNIFTY | 100 | 15 | Rs 20-100 | 500 |
| NIFTY50 | 50 | 25 | Rs 15-70 | — |
| FINNIFTY | 50 | 25 | — | — |
| MIDCPNIFTY | 25 | 50 | — | — |
| SENSEX | — | 10 | — | 300 |

Selection: OTM depth 1-8 intervals, ranked by OI change % DESC then OI DESC.

### 24.4 PostgreSQL Config Store

**Source:** `data_platform/db/lottery_otm_config.py`

- Table: `lottery_otm_config` (index_name, param_name, value, description, is_active)
- Parameters: delta_min/max, price_min/max, min_oi, min_volume, top_n, gamma_weight, liquidity_weight, momentum_weight, theta_penalty
- Async repository pattern with sync wrappers

---

## 25. File Inventory

### Core Pipeline (35 Python modules)

```
engines/lottery/
├── __init__.py
├── __main__.py
├── main.py                              # LotteryPipeline orchestrator
├── models.py                            # 15+ data models, 8+ enums
├── replay.py                            # Deterministic backtest engine
├── config/
│   ├── __init__.py
│   ├── settings.py                      # 21 frozen dataclasses
│   └── settings.yaml                    # 130+ default parameters
├── data_fetch/
│   ├── __init__.py
│   ├── provider.py                      # Abstract DataProvider interface
│   ├── fyers_adapter.py                 # FYERS REST implementation
│   └── fyers_ws.py                      # FYERS WebSocket client
├── data_quality/
│   ├── __init__.py
│   └── validator.py                     # 9 quality checks
├── calculations/
│   ├── __init__.py
│   ├── base_metrics.py                  # Distance, intrinsic, decay, liquidity
│   ├── advanced_metrics.py              # Curvature, theta density, side bias
│   ├── extrapolation.py                 # Far-OTM compression model
│   ├── scoring.py                       # 5-factor composite scoring
│   ├── candle_builder.py                # 1-min OHLC aggregation
│   ├── tradability.py                   # Bid/ask/spread/volume checks
│   ├── microstructure.py                # Bid/ask microstructure tracking
│   └── rejection_audit.py              # Per-strike rejection audit
├── strategy/
│   ├── __init__.py
│   ├── state_machine.py                 # 7-state FSM
│   ├── signal_engine.py                 # Entry/exit rules
│   ├── risk_guard.py                    # 9 pre-trade risk checks
│   ├── profiles.py                      # DTE-based strategy profiles
│   ├── dte_detector.py                  # Days-to-expiry detection
│   ├── refresh_scheduler.py             # Adaptive refresh timing
│   ├── hysteresis.py                    # Trigger debouncing
│   └── confirmation.py                  # Breakout confirmation
├── paper_trading/
│   ├── __init__.py
│   ├── broker.py                        # Simulated execution
│   └── capital_manager.py               # Position sizing, capital tracking
├── reporting/
│   ├── __init__.py
│   ├── tables.py                        # 7 JSON-serializable table generators
│   └── divergence.py                    # MFE/MAE/slippage analysis
├── storage/
│   ├── __init__.py
│   └── db.py                            # SQLite persistence (10 tables)
├── memory_state/
│   ├── __init__.py
│   └── runtime.py                       # In-memory state (bounded deques)
├── alerting/
│   ├── __init__.py
│   └── notifier.py                      # Telegram notifications
├── debugging/
│   ├── __init__.py
│   ├── logger.py                        # Structured JSON logging
│   └── trace.py                         # Per-cycle debug + failure buckets
└── tests/
    ├── __init__.py
    ├── test_calculations.py             # 25 unit tests
    ├── test_strategy.py                 # 30 unit tests
    ├── test_paper_trading.py            # 14 unit tests
    ├── test_integration.py              # 16 integration tests
    └── test_gaps.py                     # Gap module tests
```

### Documentation

```
engines/lottery/
├── LOTTERY_PIPELINE_FORENSIC_DOC.md     # This document
├── FORENSIC_AUDIT_REPORT.md             # Audit findings
├── FINAL_AUDIT_VERDICT.md               # Audit conclusions
└── MIGRATION_AND_DEVELOPER_GUIDE.md     # Developer onboarding
```

### Runtime Data

```
engines/lottery/
├── data/
│   ├── NIFTY/lottery.db (+WAL, SHM)
│   ├── BANKNIFTY/lottery.db (+WAL, SHM)
│   ├── SENSEX/lottery.db (+WAL, SHM)
│   └── NIFTY/test_replay.jsonl
└── logs/
    ├── NIFTY/lottery_YYYY-MM-DD.jsonl
    ├── BANKNIFTY/lottery_YYYY-MM-DD.jsonl
    └── SENSEX/lottery_YYYY-MM-DD.jsonl
```

---

## 26. Test Coverage

### 26.1 Test Modules

| Module | Tests | Coverage |
|--------|-------|----------|
| `test_calculations.py` | 25 | Base metrics, advanced metrics, extrapolation, scoring, band fit, window filter |
| `test_strategy.py` | 30 | State transitions, trigger resolution, signal engine, risk guard |
| `test_paper_trading.py` | 14 | Broker execution, fill modes, PnL, charges, capital manager, sizing |
| `test_integration.py` | 16 | Full pipeline flow, storage round-trip, reporting, replay |
| `test_gaps.py` | — | CandleBuilder, confirmation, hysteresis, tradability, rejection audit, microstructure, divergence, DTE profiles, refresh scheduler |

### 26.2 Integration Test Scenarios

- No-trade zone (spot between triggers)
- CE breakout (spot > upper trigger → candidate → trade → exit)
- PE breakout (spot < lower trigger → candidate → trade → exit)
- Full trade lifecycle (entry → monitor → SL/target → cooldown → re-entry)
- Multi-symbol DB isolation
- Replay determinism (same input → same output)
- Config version lineage

---

## 27. Key Thresholds Summary Table

| Category | Parameter | Value | Notes |
|----------|-----------|-------|-------|
| **Cycles** | Trigger cycle | 1s | Every second |
| | Analysis cycle | 30s | Every 30 seconds |
| | Retry attempts | 3 | Chain fetch retries |
| | Backoff | 500ms base | Exponential: 500ms, 1s, 2s |
| **Premium Band** | Min | Rs 2.10 | Lower bound |
| | Max | Rs 8.50 | Upper bound |
| | Fit mode | DISTANCE | Proximity-weighted scoring |
| **OTM Distance** | Min | 250 points | Minimum OTM distance |
| | Max | 450 points | Maximum OTM distance |
| **Exit Rules** | Stop-Loss | 0.5x entry | 50% premium loss |
| | Target 1 | 2.0x entry | 100% gain |
| | Target 2 | 3.0x entry | 200% gain |
| | Target 3 | 4.0x entry | 300% gain |
| | EOD squareoff | 15:15 IST | Mandatory |
| **Data Quality** | Max spot age | 2000ms | Reject if older |
| | Max chain age | 5000ms | Reject if older |
| | Max skew | 3000ms | Source time difference |
| | Min volume | 1000 | Chain quality |
| | Min OI | 500 | Chain quality |
| | Max spread | 5% | Chain quality |
| | Stale cycles | 5 | Consecutive unchanged |
| **Time Filters** | No trade start | 15 min | After 09:15 IST |
| | Lunch zone | 12:30-13:15 IST | No new entries |
| | Near close | 10 min before | No new entries |
| **Cooldown** | Duration | 300s (5 min) | After trade exit |
| | Max reentries | 2 | Per session |
| | Same strike | Blocked | By default |
| **Hysteresis** | Buffer | 10 points | Zone activation |
| | Hold time | 5s | Must sustain |
| | Rearm distance | 20 points | After IDLE return |
| | Invalidation buffer | 5 points | For trade exit |
| **Tradability** | Min bid qty | 50 | Candidate gate |
| | Min ask qty | 50 | Candidate gate |
| | Min volume | 500 | Strike-specific |
| | Max spread | 10% | Candidate gate |
| **Confirmation** | Mode | QUORUM | Default |
| | Quorum | 2 of 5 checks | Must pass |
| | Hold duration | 15s | Spot must hold |
| | Premium expansion | 5% | Min expansion |
| | Volume spike | 1.5x | Above average |
| **Risk Limits** | Starting capital | Rs 100,000 | Paper trading |
| | Max daily trades | 5 | Per day |
| | Max daily loss | Rs 5,000 | Loss limit |
| | Max consecutive losses | 3 | Loss streak |
| | Max open trades | 1 | Concurrent |
| | Max risk per trade | 2% | Of capital |
| **Execution** | Fill mode | MID_SLIPPAGE | Default |
| | Slippage | 0.5% | Added to mid |
| | Brokerage | Rs 20/lot | Per lot |
| | Exchange charges | 0.05% | Of brokerage |
| **Scoring** | All weights | 1.0 each | w1-w5 equal |
| | Tie epsilon | 0.01 | Score tolerance |
| **Extrapolation** | Fit window | 3 strikes | CE and PE |
| | Alpha mode | CALIBRATED | From regression |
| | Alpha range | [0.001, 0.5] | Clamped |
| | Min OTM strikes | 3 | Skip if fewer |

---

## End of Document

This forensic document covers the complete Lottery Strike Picker pipeline with every formula, threshold, state transition, data flow, and configuration parameter documented at forensic level. All values are extracted directly from the source code at `engines/lottery/`.

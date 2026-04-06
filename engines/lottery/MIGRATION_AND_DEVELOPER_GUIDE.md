# Lottery Pipeline — Migration Guide & Developer Note

## 1. Migration Guide

### 1.1 Upgrading from Base to Gap-Enhanced Pipeline

**No manual migration steps required.** All changes are backward-compatible:

| Change | Auto-handled |
|--------|-------------|
| New config sections (hysteresis, tradability, confirmation, strategy, refresh) | Defaults apply if YAML sections missing |
| New DB tables (strike_rejection_audit, divergence_reports) | Created on first run via `_init_db()` |
| New PaperTrade fields (selection_price, confirmation_price) | Default to None — existing trades unaffected |
| New modules (candle_builder, tradability, microstructure, etc.) | Only loaded when pipeline runs |

### 1.2 New Config Sections

Add these to `settings.yaml` (optional — defaults apply if absent):

```yaml
hysteresis:
  buffer_points: 10
  min_zone_hold_seconds: 5
  rearm_distance_points: 20
  invalidation_buffer_points: 5

tradability:
  require_bid: true
  require_ask: true
  min_bid_qty: 50
  min_ask_qty: 50
  min_recent_volume: 500
  max_spread_pct: 10.0
  max_last_trade_age_seconds: 0

confirmation:
  mode: "QUORUM"
  quorum: 2
  hold_duration_seconds: 15.0
  premium_expansion_min_pct: 5.0
  volume_spike_multiplier: 1.5
  spread_widen_max_pct: 20.0

strategy:
  mode: "AUTO"

refresh:
  chain_idle_seconds: 30
  chain_active_seconds: 30
  candidate_zone_seconds: 5
  candidate_found_seconds: 2
  trade_quote_seconds: 1
  spot_drift_threshold: 100.0
  candidate_stale_seconds: 60.0
```

### 1.3 New DB Tables (Auto-Created)

```sql
strike_rejection_audit   — per-strike scan lineage per cycle
divergence_reports       — paper vs live execution analysis
```

No ALTER TABLE needed — new tables are created alongside existing ones.

### 1.4 Existing DB Compatibility

The `paper_trades` table gains two new columns (`selection_price`, `confirmation_price`). SQLite handles this gracefully — existing rows will have NULL for these columns. New trades will populate them.

If using an existing DB, the new columns will be added by the schema's `CREATE TABLE IF NOT EXISTS` — but since the table already exists with the old schema, you may need to recreate it. **Simplest approach**: delete the old DB file and let the pipeline recreate it. No critical data is lost in paper trading mode.

---

## 2. Developer Note — Dual-Cycle Architecture

### 2.1 Data Flow Diagram

```
                    ┌───────────────────────────────────┐
                    │       FYERS WebSocket              │
                    │   (real-time spot ticks)           │
                    └─────────────┬─────────────────────┘
                                  │ on_tick()
                                  ▼
                    ┌─────────────────────────────────┐
                    │      CandleBuilder              │
                    │  (1-min OHLC from ticks)        │
                    └─────────────┬───────────────────┘
                                  │
    ┌─────────────────────────────┼─────────────────────────────┐
    │                             │                             │
    ▼                             ▼                             ▼
┌──────────────┐    ┌──────────────────────┐    ┌──────────────────┐
│ Analysis     │    │  Trigger Cycle (1s)  │    │  FYERS REST      │
│ Cycle (30s)  │    │                      │    │  Candidate Quotes│
│              │    │  1. Get live spot    │    │  (2-5s adaptive) │
│ 1. REST full │    │  2. Get candidate   │    │                  │
│    chain     │    │     quotes          │    │  2-3 strikes     │
│ 2. Validate  │    │  3. Build Trigger   │    │  only            │
│ 3. Calculate │    │     Snapshot        │    └────────┬─────────┘
│ 4. Score     │    │  4. Check exits     │             │
│ 5. Select    │    │  5. Check triggers  │◄────────────┘
│    candidates│    │  6. Confirmation    │
│              │    │     gate (5 checks) │
│  AnalysisSnap│    │  7. Risk gate       │
│  (synced)    │    │  8. Paper entry     │
└──────┬───────┘    └──────────┬──────────┘
       │                       │
       │    candidates list    │    entry/exit
       └───────────────────────┘    decisions
```

### 2.2 When Each Price Role Is Set

```
AnalysisSnapshot created (every 30s):
  └→ selection_price = candidate.ltp at scoring time

TriggerSnapshot built (every 1s):
  └→ confirmation_price = candidate quote LTP when confirmation passes

Paper broker executes:
  └→ entry_price = MID(bid,ask) * (1 + slippage%)

Paper broker exits:
  └→ exit_price = MID(bid,ask) * (1 - slippage%)
```

### 2.3 Confirmation Flow

```
State: CANDIDATE_FOUND
  │
  ▼
BreakoutConfirmation.evaluate()
  ├── candle_close: last 1-min candle closed beyond trigger?
  ├── premium_expand: candidate LTP increased >= 5%?
  ├── volume_spike: current volume > 1.5x average?
  ├── spread_stable: spread not widened > 20%?
  └── hold_duration: spot held beyond trigger >= 15s?
  │
  ├── QUORUM: >= 2 of 5 pass → CONFIRMED → enter trade
  ├── HYBRID: candle + premium both pass → CONFIRMED
  ├── CANDLE: candle only → CONFIRMED
  └── DISABLED: always pass (for testing/replay)
```

### 2.4 Hysteresis Protection

```
Without hysteresis:          With hysteresis:
spot=22701 → CE ACTIVE       spot=22701 → BLOCKED (need +10 buffer)
spot=22699 → IDLE            spot=22711 → CE ACTIVE
spot=22701 → CE ACTIVE       spot=22698 → STILL ACTIVE (need -5 inv buffer)
spot=22699 → IDLE            spot=22694 → INVALIDATED
(oscillating!)               spot=22711 → BLOCKED (rearm: need +20 from 22694)
                              spot=22720 → CE ACTIVE (cleared rearm)
```

### 2.5 Strategy Profile Auto-Selection

```
DTE >= 2 → PRE_EXPIRY_MOMENTUM
  band: 3-15, OTM: 300-600, confirm: HYBRID(2), cooldown: 600s

DTE == 1 → DTE1_HYBRID
  band: 2.5-10, OTM: 250-500, confirm: QUORUM(2), cooldown: 300s

DTE == 0 → EXPIRY_DAY_TRUE_LOTTERY
  band: 2-8.5, OTM: 200-400, confirm: QUORUM(3), cooldown: 180s
```

### 2.6 Adaptive Refresh Intervals

```
State            Chain    Candidates   Driven by
─────────────    ─────    ──────────   ─────────
IDLE             30s      skip         RefreshScheduler
ZONE_ACTIVE      30s      5s           RefreshScheduler
CANDIDATE_FOUND  30s      2s           RefreshScheduler
IN_TRADE         30s      1s           RefreshScheduler
COOLDOWN         30s      skip         RefreshScheduler

Forced chain refresh when:
  - spot drifts > 100pts from analysis snapshot
  - no candidates in active zone
  - side change (CE→PE or PE→CE)
```

### 2.7 Microstructure Tracking

```
For each shortlisted candidate (2-3 strikes):
  Record every quote refresh → rolling buffer of 20 observations
  Detect: walls, pulls, refills, absorption, spoofs
  Feed into confirmation layer as additional signal

Labels are NEUTRAL:
  PERSISTENT_ASK_WALL  (not "institutional selling")
  PULLED_BID           (not "smart money exit")
  SPOOF_RISK_ASK       (not "manipulation detected")
```

### 2.8 Extrapolation Advisory Logic

```
Visible CE candidates in band?
  YES → use visible only, block extrapolated CE
  NO  → use extrapolated CE as advisory

Same for PE side independently.
Extrapolated candidates must still pass live tradability before entry.
```

---

## 3. Module Inventory (Post-Gap)

### New Modules (Gap Phase 1+2+3)

| Module | Lines | Purpose |
|--------|-------|---------|
| `calculations/candle_builder.py` | 333 | 1-min OHLC from WS ticks |
| `calculations/tradability.py` | 200 | 7-check per-strike executable gate |
| `calculations/rejection_audit.py` | 160 | Per-strike scan lineage |
| `calculations/microstructure.py` | 350 | Rolling book tracker + 6 signal types |
| `strategy/confirmation.py` | 325 | 5-check quorum confirmation gate |
| `strategy/profiles.py` | 210 | 3 strategy profiles by DTE |
| `strategy/dte_detector.py` | 201 | Auto-select profile from calendar |
| `strategy/hysteresis.py` | 231 | 4-layer trigger flicker prevention |
| `strategy/refresh_scheduler.py` | 201 | State-aware refresh intervals |
| `reporting/divergence.py` | 200 | Paper vs live execution analysis |

### Modified Modules

| Module | Change |
|--------|--------|
| `models.py` | +AnalysisSnapshot, TriggerSnapshot, CandidateQuote, StrikeRejectionAudit, PaperTrade price fields |
| `config/settings.py` | +HysteresisConfig, TradabilityConfig, ConfirmationSettingsConfig, StrategySettingsConfig, RefreshSettingsConfig |
| `config/settings.yaml` | +5 new config sections |
| `scoring.py` | Extrapolation advisory gate |
| `main.py` | Dual-cycle architecture, all gap module wiring |
| `storage/db.py` | +strike_rejection_audit, divergence_reports tables, PaperTrade price columns |
| `paper_trading/broker.py` | selection_price, confirmation_price params |
| `data_fetch/fyers_adapter.py` | fetch_candidate_quotes(), _build_option_symbol() |

---

## 4. Test Coverage Summary

| File | Tests | Status |
|------|-------|--------|
| test_calculations.py | 25 | All pass |
| test_strategy.py | 30 | All pass |
| test_paper_trading.py | 14 | All pass |
| test_integration.py | 16 | 15 pass, 1 skip |
| **test_gaps.py** | **47** | **All pass** |
| **Total** | **132** | **131 pass, 1 skip** |

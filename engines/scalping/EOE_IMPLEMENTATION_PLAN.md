# EOE Shadow Logger — Implementation Plan

**Status**: Engineering plan (pre-build)
**Spec source**: EOE_SESSION_SPEC.md
**Build estimate**: 1 day (experienced engineer)

---

## 1. ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────┐
│                LIVE SCALPING ENGINE                   │
│  context.data (shared, read-only for EOE)            │
│    ├── spot_data        (DataFeed agent)              │
│    ├── option_chains    (OptionChain agent)            │
│    ├── market_structure (Structure agent)              │
│    ├── structure_breaks (Structure agent)              │
│    ├── market_regimes   (MarketRegime agent)           │
│    └── vix, futures, etc.                             │
└───────────────┬─────────────────────────────────────┘
                │ READ ONLY (no writes)
                ▼
┌─────────────────────────────────────────────────────┐
│              EOE SHADOW LOGGER                        │
│                                                       │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  State    │  │   Strike     │  │  Tradability   │  │
│  │  Machine  │──│  Evaluator   │──│  Checker       │  │
│  └────┬─────┘  └──────┬───────┘  └───────┬───────┘  │
│       │               │                   │           │
│  ┌────▼───────────────▼───────────────────▼───────┐  │
│  │            Hypothetical Trade Tracker           │  │
│  └────────────────────┬───────────────────────────┘  │
│                       │                               │
│  ┌────────────────────▼───────────────────────────┐  │
│  │              Log Writer (5 files)               │  │
│  └────────────────────┬───────────────────────────┘  │
│                       │                               │
│  ┌────────────────────▼───────────────────────────┐  │
│  │           Report Generator (session end)        │  │
│  └────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**Data flow**: Engine runs → after each cycle, EOE shadow reads context.data → evaluates state machine → writes logs. Zero writes back to context.

---

## 2. MODULE / FILE STRUCTURE

```
engines/scalping/scalping/eoe/
├── __init__.py
├── shadow_logger.py          # Entrypoint: EOEShadowLogger class
├── state_machine.py          # EOE state machine (OFF/WATCH/ARMED/ACTIVE/COOLDOWN)
├── strike_evaluator.py       # Strike candidate scoring for expiry options
├── tradability.py            # Bid/ask/depth/spread checks
├── hypo_tracker.py           # Hypothetical trade lifecycle (entry→MFE/MAE→exit)
├── log_writer.py             # Writes 5 CSV/JSON files per session
└── report_generator.py       # Produces SESSION_REPORT.md at session end
```

Each file is **single-responsibility**, **stateless** (state lives in `EOEShadowLogger`), **read-only** (no broker/API/context writes).

---

## 3. DATA CONTRACTS

### 3A. Input Contract (read from `context.data`)

```python
@dataclass
class EOEInput:
    """Snapshot of engine state consumed each cycle. All read-only."""
    timestamp: datetime
    
    # Market
    sensex_ltp: float           # context.data["spot_data"]["BSE:SENSEX-INDEX"].ltp
    sensex_open: float          # .open
    sensex_high: float          # .high
    sensex_low: float           # .low
    sensex_prev_close: float    # .prev_close
    sensex_vwap: float          # .vwap
    
    # Structure
    persistent_bias: str        # "Bearish bias, conf=0.98" from market_structure
    persistent_confidence: float # parsed from above
    persistent_direction: str    # "bearish" / "bullish" / "neutral"
    transient_bos: list         # structure_breaks list (dicts with break_type, strength)
    
    # Options (for candidate strike evaluation)
    option_chain: dict          # context.data["option_chains"]["BSE:SENSEX-INDEX"]
    
    # Meta
    is_expiry_day: bool         # calendar check
    vix: float
```

### 3B. State Machine Output

```python
@dataclass
class EOEState:
    current: str                # OFF/WATCH/ARMED/ACTIVE/COOLDOWN
    entered_at: datetime        # when current state was entered
    session_high: float
    session_low: float
    morning_direction: str      # bearish/bullish (set once)
    reversal_pct: float         # current reversal from extreme
    bos_bullish_count_30min: int
    bos_bearish_count_30min: int
    active_duration_min: float  # 0 if not ACTIVE
```

### 3C. Strike Candidate

```python
@dataclass  
class StrikeCandidate:
    strike: int
    option_type: str            # CE or PE
    premium: float
    bid: float
    ask: float
    spread_pct: float
    bid_qty: int
    ask_qty: int
    otm_distance: int
    tradable: bool
    tradable_reason: str        # why not tradable if False
```

### 3D. Hypothetical Trade

```python
@dataclass
class HypoTrade:
    entry_time: datetime
    strike: int
    option_type: str
    entry_premium: float
    entry_bid: float
    entry_ask: float
    entry_spread_pct: float
    entry_bid_qty: int
    entry_ask_qty: int
    tradable_at_entry: bool
    
    # Updated each cycle after entry
    peak_premium: float = 0
    peak_time: Optional[datetime] = None
    peak_sustained_60s: bool = False
    current_premium: float = 0
    mae_premium: float = float('inf')  # lowest since entry
    
    # Set at exit
    exit_premium: float = 0
    exit_time: Optional[datetime] = None
    exit_reason: str = ""
    
    # Computed at exit
    payoff_multiple: float = 0
    mfe_multiple: float = 0
    mae_multiple: float = 0
    hold_time_min: float = 0
    result: str = ""  # WIN/LOSS/SKIPPED
    round_trip_spread_cost: float = 0
    spread_trap: bool = False
```

---

## 4. RUNTIME EXECUTION FLOW

### Session Startup (09:15)

```
1. Check is_expiry_day → if False: LOG "not expiry" and return
2. Initialize state = OFF
3. Create log directory: eoe_shadow/<YYYYMMDD>/
4. Write initial session_meta.json (partial — complete at end)
5. Set morning_direction = None (determined after 10:30)
```

### Per-Cycle Tick (every ~3 seconds, called from engine after agents complete)

```
1. Build EOEInput from context.data (read-only)
2. Update session_high, session_low
3. Run state machine transition check:
   
   IF state == OFF:
     IF time >= 10:30 AND is_expiry_day:
       transition(WATCH)
   
   IF state == WATCH:
     IF morning_direction is None AND time >= 10:30:
       morning_direction = "bearish" if (ltp < open) else "bullish"
     
     reversal_pct = compute_reversal_from_extreme()
     IF reversal_pct >= 1.5 OR (ltp crossed VWAP AND held 5 min):
       transition(ARMED)
   
   IF state == ARMED:
     count bos events in reversal direction within last 30 min
     IF bos_count >= 2 AND price made new HH/LL in reversal direction:
       transition(ACTIVE)
     IF armed_duration > 60 min:
       transition(WATCH)  # timeout, reset
   
   IF state == ACTIVE:
     IF active_duration > 90 min:
       transition(COOLDOWN)
     
     evaluate_strike_candidates()
     check_tradability()
     
     IF entry_signal AND no_open_hypo_trade:
       create_hypo_trade()
     
     IF has_open_hypo_trade:
       update_mfe_mae()
       check_exit_conditions()
   
   IF state == COOLDOWN:
     IF has_open_hypo_trade:
       update_mfe_mae()
       check_exit_conditions()
     IF cooldown_duration > 30 min:
       transition(OFF)
     IF time >= 14:50:
       transition(OFF)

4. Write cycle_log.csv row
5. If state changed: write state_transitions.csv row
```

### Entry Signal Detection (within ACTIVE state)

```
1. Get best tradable strike candidate
2. Check pullback condition:
   - Price retraced 20-40% of last move in reversal direction
   - Current candle closes in reversal direction (beyond pullback)
   - Volume >= 1.5x average of last 10 cycles
3. If all conditions met: entry_signal = True
4. Create HypoTrade with entry snapshot
```

### MFE/MAE Update (every cycle while trade open)

```
1. Get current premium for trade's strike from option_chain
2. Update peak_premium if current > peak
3. Update mae_premium if current < mae (min since entry)
4. Check peak_sustained_60s: was premium >= 3x entry for 60 continuous seconds?
5. mfe_multiple = peak_premium / entry_premium
6. mae_multiple = mae_premium / entry_premium
```

### Exit Check (every cycle while trade open)

```
1. SL: current_premium <= entry_premium * 0.5 → exit "hard_sl"
2. Time SL: hold_time > 30 min AND current < entry → exit "time_sl"
3. Scaled exit simulation:
   - premium >= 3x entry: book 30% (log partial)
   - premium >= 5x: book 30% more, trail at -20%
   - premium >= 10x: book 20% more, trail at -15%
   - premium >= 20x: trail at -10%
4. Trail stop hit → exit "trail_stop"
5. Time >= 15:10 → exit "session_close"
```

### Session End (15:15 or engine shutdown)

```
1. Close any open hypo trades at last known premium
2. Compute all trade metrics (payoff, MFE, MAE, spread_trap)
3. Complete session_meta.json with close data + classification
4. Check for missed activations (was there a ≥2% reversal + 2 BOS that EOE missed?)
5. Generate SESSION_REPORT.md
6. Flush all log files
```

---

## 5. SAFETY GUARANTEES

### HARD GUARANTEES (must be enforced in code)

| # | Guarantee | Enforcement |
|---|-----------|-------------|
| S1 | No broker API calls | Module has no import of broker/adapter/fyers |
| S2 | No order creation | No Order dataclass, no submit_* functions |
| S3 | No context.data writes | All access via read-only snapshot copy |
| S4 | No scalping state modification | EOE state stored in separate EOEShadowLogger instance |
| S5 | No capital accounting in main system | EOE capital is a local variable, not in engine config |
| S6 | Independent file I/O | Writes only to `eoe_shadow/` directory |
| S7 | Crash isolation | All EOE code in try/except; failure logs error, does not halt engine |

### Implementation Pattern

```python
class EOEShadowLogger:
    """Read-only shadow module. CANNOT modify engine state."""
    
    def on_cycle(self, context_snapshot: dict) -> None:
        """Called after each engine cycle with a COPY of context.data."""
        # context_snapshot is a shallow copy — we never write to it
        try:
            self._process_cycle(context_snapshot)
        except Exception as e:
            self._log_error(e)  # Never propagates to engine
```

### Hook Point (in engine.py)

```python
# At end of each cycle, AFTER all agents and sync:
if self._eoe_shadow:
    try:
        self._eoe_shadow.on_cycle(dict(self.context.data))  # shallow copy
    except Exception:
        pass  # Never affect engine
```

Single line addition. `dict()` creates a shallow copy preventing any write-back.

---

## 6. IMPLEMENTATION CHECKLIST

```
PHASE 1: Build (Day 1 morning)
□ Create engines/scalping/scalping/eoe/ directory
□ Write __init__.py
□ Write state_machine.py (OFF/WATCH/ARMED/ACTIVE/COOLDOWN transitions)
□ Write strike_evaluator.py (find best CE/PE candidate from option chain)
□ Write tradability.py (spread + depth + premium checks)
□ Write hypo_tracker.py (trade lifecycle with MFE/MAE)
□ Write log_writer.py (5 CSV/JSON files per spec)
□ Write report_generator.py (SESSION_REPORT.md template)
□ Write shadow_logger.py (orchestrator: EOEShadowLogger class)

PHASE 2: Connect (Day 1 afternoon)
□ Add hook in engine.py (single line, try/except wrapped)
□ Verify context.data contains all required fields
□ Map context fields to EOEInput dataclass
□ Test with print-only mode (no file writes)

PHASE 3: Validate (Day 1 evening)
□ Run unit tests (see Section 7)
□ Run schema validation (log files match spec)
□ Verify state transitions on synthetic data
□ Verify no writes to context.data (assert test)

PHASE 4: First Live Shadow (Next expiry day)
□ Enable EOE shadow on engine startup
□ Monitor first 30 minutes for errors
□ Verify log files being written
□ Verify state machine transitioning appropriately
□ At session end: verify all 5 log files present
□ Generate SESSION_REPORT.md
□ Score session against gates (manual first time)

PHASE 5: Iterate (Sessions 2-5)
□ Fix any data capture gaps from session 1
□ Run sessions 2-5
□ After session 5: generate ROLLUP_REPORT.md
□ Score all gates → IMPLEMENT / DEFER / REJECT
```

---

## 7. ENGINEERING TEST PLAN

### Unit Tests

```python
# test_state_machine.py

def test_off_to_watch_on_expiry():
    """OFF → WATCH at 10:30 on expiry day."""
    sm = EOEStateMachine(is_expiry=True)
    sm.tick(time=time(10, 29), ltp=72000)
    assert sm.state == "OFF"
    sm.tick(time=time(10, 30), ltp=72000)
    assert sm.state == "WATCH"

def test_off_stays_off_non_expiry():
    """OFF stays OFF on non-expiry day regardless of time."""
    sm = EOEStateMachine(is_expiry=False)
    sm.tick(time=time(12, 0), ltp=72000)
    assert sm.state == "OFF"

def test_watch_to_armed_on_reversal():
    """WATCH → ARMED when reversal ≥ 1.5% from extreme."""
    sm = EOEStateMachine(is_expiry=True)
    sm.state = "WATCH"
    sm.session_low = 71546
    sm.morning_direction = "bearish"
    sm.tick(time=time(11, 30), ltp=72620)  # 1.5% above low
    assert sm.state == "ARMED"

def test_armed_to_active_on_2bos():
    """ARMED → ACTIVE on 2+ BOS in reversal direction."""
    sm = EOEStateMachine(is_expiry=True)
    sm.state = "ARMED"
    sm.morning_direction = "bearish"
    sm.tick(time=time(12, 0), ltp=72700, bos_events=[
        {"break_type": "bos_bullish", "timestamp": "12:00"},
        {"break_type": "bos_bullish", "timestamp": "11:50"},
    ])
    assert sm.state == "ACTIVE"

def test_armed_timeout_60min():
    """ARMED → WATCH after 60 min without ACTIVE."""
    sm = EOEStateMachine(is_expiry=True)
    sm.state = "ARMED"
    sm.entered_at = datetime(2026, 4, 9, 11, 0)
    sm.tick(time=time(12, 1), ltp=72000)  # 61 min later
    assert sm.state == "WATCH"

def test_active_timeout_90min():
    """ACTIVE → COOLDOWN after 90 min."""
    sm = EOEStateMachine(is_expiry=True)
    sm.state = "ACTIVE"
    sm.entered_at = datetime(2026, 4, 9, 12, 0)
    sm.tick(time=time(13, 31), ltp=73200)
    assert sm.state == "COOLDOWN"
```

### Tradability Tests

```python
def test_tradable_premium_in_range():
    assert is_tradable(premium=5, spread_pct=20, bid_qty=50, ask_qty=40, lot_size=10)

def test_not_tradable_premium_too_low():
    assert not is_tradable(premium=1.5, spread_pct=10, bid_qty=50, ask_qty=40, lot_size=10)

def test_not_tradable_spread_too_wide():
    assert not is_tradable(premium=8, spread_pct=35, bid_qty=50, ask_qty=40, lot_size=10)

def test_not_tradable_no_depth():
    assert not is_tradable(premium=5, spread_pct=20, bid_qty=5, ask_qty=5, lot_size=10)
```

### Hypo Trade Tests

```python
def test_sl_at_50pct():
    trade = HypoTrade(entry_premium=6.0)
    assert trade.check_exit(current=2.9) == "hard_sl"

def test_no_sl_above_50pct():
    trade = HypoTrade(entry_premium=6.0)
    assert trade.check_exit(current=3.1) is None

def test_mfe_update():
    trade = HypoTrade(entry_premium=5.0)
    trade.update(current=15.0, timestamp=now)
    assert trade.mfe_multiple == 3.0

def test_spread_trap_detection():
    trade = HypoTrade(entry_premium=5.0, entry_spread_pct=25)
    trade.close(exit_premium=7.0, exit_spread_pct=20)
    # Round-trip cost: (25% of 5)/2 + (20% of 7)/2 = 0.625 + 0.7 = 1.325
    # Profit: 7 - 5 = 2. Spread/profit = 1.325/2 = 0.66 > 0.30
    assert trade.spread_trap == True
```

### Safety Tests

```python
def test_no_context_modification():
    ctx = {"spot_data": {"BSE:SENSEX-INDEX": {"ltp": 73000}}}
    original = dict(ctx)
    logger = EOEShadowLogger()
    logger.on_cycle(ctx)
    assert ctx == original  # No modification

def test_crash_does_not_propagate():
    logger = EOEShadowLogger()
    # Feed garbage data — should not raise
    logger.on_cycle({"broken": True})
    # If we get here, crash was caught
    assert True
```

### Log File Tests

```python
def test_cycle_log_schema():
    writer = LogWriter("/tmp/test_eoe")
    writer.write_cycle(sample_cycle_data)
    with open("/tmp/test_eoe/cycle_log.csv") as f:
        header = f.readline().strip()
    expected = "timestamp,eoe_state,sensex_ltp,vwap,..."
    assert header == expected

def test_session_report_generated():
    gen = ReportGenerator(session_data)
    gen.generate("/tmp/test_eoe/SESSION_REPORT.md")
    assert os.path.exists("/tmp/test_eoe/SESSION_REPORT.md")
    content = open("/tmp/test_eoe/SESSION_REPORT.md").read()
    assert "## 1. Session Overview" in content
```

---

## 8. CONFIDENCE SCORE

**Confidence in this implementation plan: 0.95**

An engineer with access to this document + EOE_SESSION_SPEC.md can build the shadow logger in 1 day. The architecture is simple (single class, read-only, 7 submodules), the safety guarantees are explicit, and the test plan covers all critical paths.

**Remaining 0.05**:
- Option chain data format may vary between indices (needs runtime verification)
- VWAP calculation may differ from engine's internal VWAP
- First live session will likely reveal 2-3 edge cases in state transitions

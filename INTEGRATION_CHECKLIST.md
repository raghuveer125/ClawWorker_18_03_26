# ClawWork + fyersN7 Integration Checklist

**Created:** 2026-03-07
**Status:** 7 items pending, 2 completed

---

## Pending Integration Items

### 2. Index Configuration
**Goal:** Use fyersN7's complete `INDEX_CONFIG` in both projects

| Index | ClawWork | fyersN7 | After Integration |
|-------|----------|---------|-------------------|
| NIFTY50 | Yes | Yes | Yes |
| BANKNIFTY | Yes | Yes | Yes |
| FINNIFTY | Yes | Yes | Yes |
| SENSEX | No | Yes | Yes |
| MIDCPNIFTY | No | Yes | Yes |

**Source of truth:** `fyersN7/fyers-2026-03-05/scripts/pull_fyers_signal.py` (lines 29-90)

**Fields per index:**
- `display_name`, `spot_symbol`, `vix_symbol`
- `exchange`, `option_prefix`, `fut_prefix`
- `lot_size`, `strike_step`, `expiry_day`

---

### 3. Paper Trading State
**Goal:** Unified paper trading state format

| Aspect | ClawWork | fyersN7 | Unified |
|--------|----------|---------|---------|
| State File | `data/paper_trading_state.json` | `.paper_trade_state_*.json` | TBD |
| Trade Log | `data/paper_trading.jsonl` | `paper_trades.csv` | TBD |
| Equity | Dashboard only | `paper_equity.csv` | TBD |
| Per-index | No | Yes | Yes |

**State fields to merge:**
```json
{
  "open_positions": {},
  "daily_trades": 0,
  "daily_pnl": 0,
  "equity": 0,
  "last_update": ""
}
```

---

### 4. Adaptive Learning System
**Goal:** Merge learning loops into single system

| Aspect | ClawWork | fyersN7 | Unified |
|--------|----------|---------|---------|
| Training Data | `trade_history.jsonl` | `decision_journal.csv` | TBD |
| Model Output | `adaptive_optimization_output/` | `.adaptive_model.json` | TBD |
| Outcome Field | Win/Loss in record | `outcome` column | TBD |
| Trigger | Every N trades | `AUTO_TRAIN` interval | TBD |

**Common learning fields:**
- `date`, `time`, `symbol`, `side`
- `entry`, `sl`, `target`
- `confidence`, `score`
- `outcome` (Win/Loss)

---

### 5. Signal Schema
**Goal:** Common signal interface between engines

| Field | ClawWork | fyersN7 | Unified Name |
|-------|----------|---------|--------------|
| Direction | `signal_type` (BUY/SELL) | `side` (CE/PE) | TBD |
| Entry | `entry_price` | `entry` | TBD |
| Stop Loss | `stop_loss` | `sl` | TBD |
| Target 1 | `target` | `t1` | TBD |
| Target 2 | - | `t2` | TBD |
| Confidence | 0-1 float | 0-100 int | TBD |
| Timestamp | `timestamp` | `date` + `time` | TBD |
| Strike | - | `strike` | TBD |
| Reason | `analysis` dict | `reason` string | TBD |

---

### 6. Market Hours Control
**Goal:** Centralized market session logic

| Feature | ClawWork | fyersN7 | Unified |
|---------|----------|---------|---------|
| Start Time | 9:15 AM (code) | Shell script | Config |
| End Time | 3:30 PM (code) | Shell script | Config |
| Weekday Check | Python | Shell | Python |
| Expiry Logic | `enforce_expiry_rules` | `is_expiry_day_fallback()` | Merge |

**Target function:**
```python
def is_market_open(index: str) -> bool:
    # Check weekday, time, holidays
    pass

def is_expiry_day(index: str) -> bool:
    # Check index-specific expiry day
    pass
```

---

### 7. State Persistence Pattern
**Goal:** Shared state management utilities

| Pattern | ClawWork | fyersN7 | Unified |
|---------|----------|---------|---------|
| Load | `json.load()` | `load_state()` | Shared util |
| Save | `json.dump()` | `save_state()` | Shared util |
| Backup | No | No | Add |
| Validation | No | Basic | Add schema |

**Target module:** `shared/state_manager.py`

---

### 8. Utility Functions
**Goal:** Extract common utilities to shared module

| Function | ClawWork | fyersN7 | Shared Module |
|----------|----------|---------|---------------|
| `to_float()` | Inline | `to_float()` | `shared/utils.py` |
| `to_int()` | Inline | `to_int()` | `shared/utils.py` |
| `parse_dt()` | Various | `parse_dt()` | `shared/utils.py` |
| `load_csv_rows()` | Various | `load_csv_rows()` | `shared/utils.py` |
| `append_csv()` | Various | `append_csv()` | `shared/utils.py` |
| `ensure_csv()` | Various | `ensure_csv()` | `shared/utils.py` |

---

### 9. Configuration Approach
**Goal:** Unified configuration system

| Aspect | ClawWork | fyersN7 | Unified |
|--------|----------|---------|---------|
| Style | Python dataclass | Env vars | Hybrid |
| Runtime | Config object | CLI args | Config + CLI override |
| Profiles | Single config | `--profile` flag | Profile-based configs |

**Target structure:**
```
configs/
  base.yaml
  profiles/
    expiry.yaml
    aggressive.yaml
    strict.yaml
```

---

## Completed Items

### 1. FYERS API Authentication (Completed 2026-03-07)

**Implementation:** `shared_project_engine/auth/` module

**Files created:**
```
shared_project_engine/
  __init__.py
  auth/
    __init__.py      - Package exports
    config.py        - Constants and defaults
    env_manager.py   - .env file handling
    fyers_auth.py    - OAuth login flow
    fyers_client.py  - REST API client
    cli.py           - Command-line interface
    README.md        - Documentation
.env                 - Shared credentials
.env.example         - Template
```

**Usage:**
```bash
# Login
python3 -m shared_project_engine.auth.cli login

# Check status
python3 -m shared_project_engine.auth.cli status

# In code
from shared_project_engine.auth import quick_login, get_client
```

**Features:**
- Unified `.env` at project root
- Browser-based OAuth login with auto-callback
- SSL/TLS handling for corporate networks
- REST client with endpoint fallbacks
- CLI for login/status/test

---

### 1b. fyersN7 Signal View Integration (Completed 2026-03-07)

**Implementation:** Integrated fyersN7 signal display into ClawWork React frontend

**Files modified/created:**
```
ClawWork/livebench/api/server.py    - Added fyersN7 API endpoints
ClawWork/frontend/src/api.js        - Added fetchFyersN7* functions
ClawWork/frontend/src/pages/SignalView.jsx  - New signal view component
ClawWork/frontend/src/App.jsx       - Added /signals route
ClawWork/frontend/src/components/Sidebar.jsx - Added navigation link
shared_project_engine/launcher/start.sh     - Disabled separate 8787 server
```

**API Endpoints:**
- `GET /api/fyersn7/dates` - List available dates
- `GET /api/fyersn7/signals/{date}` - Get decision journal signals
- `GET /api/fyersn7/trades/{date}` - Get paper trades
- `GET /api/fyersn7/events/{date}` - Get entry/exit events
- `GET /api/fyersn7/summary/{date}` - Get summary stats per index

**Features:**
- Multi-index signal view (SENSEX, NIFTY50, BANKNIFTY, FINNIFTY, MIDCPNIFTY)
- Real-time auto-refresh every 15 seconds
- Summary cards with Spot, VIX, PCR, P&L, Win Rate
- Index comparison table
- Recent signals table with confidence/status
- Paper trades table with P&L
- Date selector for historical data
- Removed need for separate 8787 web server

---

## Integration Order (Recommended)

1. **FYERS Auth** - Foundation, enables API calls
2. **Index Config** - Required for multi-index support
3. **Utility Functions** - Used by everything else
4. **State Persistence** - Standardize before paper trading
5. **Signal Schema** - Define interface before integration
6. **Market Hours** - Safety layer
7. **Paper Trading State** - Core trading flow
8. **Adaptive Learning** - Enhancement layer
9. **Configuration** - Final polish

---

## Notes

- Each item should be tested independently before moving to next
- Keep backward compatibility during transition
- Document any breaking changes

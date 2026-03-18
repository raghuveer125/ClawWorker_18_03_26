# Paper Trading Integration - Phase 1B Setup

## Overview

This document explains how the Phase 1B ICT Sniper configuration runs through the default AutoTrader paper-trading path and existing dashboard for real-time monitoring.

**Current Status:** Dashboard infrastructure is fully operational and ready for Phase 1B paper trading validation (March 4-10, 2026).

`paper_trading_runner.py` remains in the repo as a legacy manual tool, but default startup flows now use API-managed `AutoTrader` only.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  FYERS Screener Loop + FastAPI Server                      │
│  • Refreshes market snapshots for supported indices        │
│  • Hosts the AutoTrader control plane                      │
│  • Exposes status, positions, and trade history endpoints  │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│  AutoTrader (livebench/trading/auto_trader.py)             │
│  • Reads screener snapshots and evaluates ensemble output  │
│  • Manages open positions and closes trades                │
│  • Persists positions.json and trades_log.jsonl            │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│  React Dashboard (frontend/src/pages/BotEnsemble.jsx)      │
│  • Real-time bot performance metrics                       │
│  • Daily P&L (paper + live mode)                          │
│  • Win rate, trade history, open positions                 │
│  • Phase 1B configuration display                          │
└─────────────────────────────────────────────────────────────┘
```

## Phase 1B Configuration

These are the optimal parameters deployed from backtesting (53.7% WR):

```json
{
  "swing_lookback": 9,          // ICT swing identification period
  "mss_swing_len": 2,           // Mean Standard Swing length
  "vol_multiplier": 1.2,        // Volume spike multiplier
  "displacement_multiplier": 1.3, // FVG displacement
  "mtf_mode": "permissive",     // Multi-timeframe: always allow ≥80% confidence
  "confidence_gate": 0.70       // Minimum signal confidence threshold
}
```

### MTF Confidence-Gating Logic

The MTF filter uses 3-tier confidence logic to preserve high-quality signals:

| Confidence | Behavior | Outcome |
|-----------|----------|---------|
| ≥ 80% | Always allow signal | ✓ Full signal |
| 70-79% | Allow but penalize weight | ⚠️ Reduced weight |
| < 70% | Block signal | ✗ No signal |

This eliminates the "87% blocking" issue from Phase 1A while preserving signal integrity.

## Quick Start

### 1. **Configure Environment Variables**

Create or update `.env` file in project root:

```bash
# API Configuration
API_URL=http://localhost:8001/api
API_PORT=8001
FRONTEND_PORT=3001

# Market Data
MARKET_DATA_PROVIDER=fyers      # or "mock" for testing
FYERS_ACCESS_TOKEN=your_token_here

# Paper Trading
PAPER_TRADING_INTERVAL=60       # seconds between analysis
```

### 2. **Start All Services**

```bash
# Make script executable
chmod +x start_paper_trading.sh

# Start complete paper trading system
./start_paper_trading.sh

# Or manually start individual services:
# Terminal 1: API Server
cd livebench && python3 -m api.server

# Terminal 2: Frontend
cd frontend && npm run dev

# AutoTrader is started by the API in paper mode by default.
# Optional legacy/manual path only:
python3 paper_trading_runner.py
```

### 3. **Access Dashboard**

Open browser: **http://localhost:3001**

Navigate to **"Bot Ensemble"** page to monitor:
- Real-time signals
- Open positions
- Daily P&L
- Win rate

## Monitoring Paper Trading

### Dashboard Metrics

**Top Section - Mode Indicators:**
- `Daily P&L (PAPER)` - Paper trading P&L for current session
- `Daily P&L (LIVE)` - Live trading P&L (if enabled)
- `Trades (PAPER/MAX)` - Currently: 0/5 trades taken

**Bot Cards - ICT Sniper Status:**
- `Win Rate` - Percentage of profitable trades (target: 53.7%)
- `Trades` - Total trades closed this session
- `P&L` - Cumulative profit/loss in ₹

**Ensemble Stats:**
- `Total Signals` - Signals analyzed across all indices
- `Signals Accepted` - After confidence gate filtering
- `Signal Rejection Rate` - Signals blocked by MTF filter

**Recent Signals:**
- Latest 5 signals with timestamp, type (BUY/SELL), confidence

### Log Files

Monitor real-time activity:

```bash
# API Server + AutoTrader Debug
tail -f logs/api_server.log

# Screener Loop
tail -f logs/fyers_screener_loop.log

# Persistent Trade Log (for analysis)
cat livebench/data/auto_trader/trades_log.jsonl
```

### Key Metrics to Track

1. **Win Rate** - Target: 53.7% (Phase 1B baseline)
   - Professional traders: 55-65%
   - Your target from adaptive learning: 60%+

2. **Confidence Gate Rejection Rate** - Should be < 40%
   - Indicates good signal quality
   - If > 50%: Market regime change, Phase 1C filters needed

3. **Daily P&L** - Expected range in ₹:
   - Conservative: ₹500-2,000 per day
   - Phase 1B baseline: ₹25,647 over 577 trades
   - Risk limit: ₹500 max daily loss

4. **Signals vs Trades Ratio** - Indicates selectivity:
   - Ratio = Trades / Signals Analyzed
   - Phase 1B: ~5-10% (selective, high quality)
   - If > 50%: Too many low-confidence signals

## Trade Lifecycle

### 1. Signal Generation

The AutoTrader path:
1. Reads current screener snapshots generated by the FYERS loop
2. Evaluates ensemble decisions with the current market context
3. Produces a trade decision with confidence and risk parameters

```python
# Example API request
POST /api/bots/analyze
{
  "index": "NIFTY50",
  "market_data": {
    "ltp": 24500.0,
    "high": 24550.0,
    "low": 24450.0,
    "volume": 1500000
  }
}

# Expected response
{
  "decision": {
    "action": "BUY",
    "signal_type": "BUY",
    "confidence": 0.82,  # 82% confidence
    "analysis": {...}
  }
}
```

### 2. Confidence Gate Filter

- If confidence ≥ 0.70: Signal accepted
- If confidence < 0.70: Signal rejected
- Accepted signals create open trades

### 3. Trade Entry

- Entry Price: Current LTP
- Stop Loss: -1.5% from entry
- Target: +3% from entry

```
Example BUY trade:
  Entry: 24500
  SL:    24500 × 0.985 = 24135  (down 1.5%)
  Target: 24500 × 1.03 = 25235  (up 3%)
```

### 4. Trade Exit (Real-time Monitoring)

Open trades are checked every iteration:
- **WIN**: Hit target price → Record as WIN
- **LOSS**: Hit stop loss → Record as LOSS
- **Hold**: Still monitoring

### 5. Trade Recording

When trade closes:

```python
POST /api/bots/record-trade
{
  "index": "NIFTY50",
  "exit_price": 25235.0,
  "outcome": "WIN",
  "pnl": 735.0
}
```

Dashboard automatically updates with new metrics.

## Performance Expectations

Based on Phase 1B backtesting results:

| Metric | Value | Notes |
|--------|-------|-------|
| **Win Rate** | 53.7% | Across NIFTY50, BANKNIFTY, FINNIFTY |
| **Trades/Day** | 3-5 | Market dependent |
| **Avg Win** | ₹1,200 | 3% target on ₹750K capital |
| **Avg Loss** | ₹900 | 1.5% stop loss |
| **Daily P&L** | ₹500-2,000 | Variable, risk-limited |
| **Max Drawdown** | ₹500 | Enforced daily loss limit |

### Adaptive Learning Insights

From the 20-iteration optimization:
- **Volume Multiplier Sweet Spot**: 1.03-1.12 (Phase 1B: 1.2)
- **Failure Patterns**: 62% low-confidence, 35% chop regime
- **Realistic Target**: 58-62% WR (not 97%)

## Troubleshooting

### Issue: "Port already in use"

```bash
# Kill process on port 8001 (API)
lsof -ti :8001 | xargs kill -9

# Kill process on port 3001 (Frontend)
lsof -ti :3001 | xargs kill -9

# Then restart
./start_paper_trading.sh
```

### Issue: "FYERS_ACCESS_TOKEN not set"

The system automatically falls back to mock data:
- Generates realistic market movements
- Useful for testing without Fyers credentials
- Still tests signal generation and recording

To use real Fyers data:
```bash
export FYERS_ACCESS_TOKEN="your_token"
./start_paper_trading.sh
```

### Issue: "No signals generated"

Check logs:
```bash
tail -f logs/api_server.log

# Look for:
# AutoTrader started in paper mode
# AutoTrader closed ... outcome=WIN
# Screener refresh failures / stale data warnings
```

If no signals for 30+ minutes:
1. Check market is open (9:15-15:30 IST, weekdays)
2. Check if confidence gate is too high (try 0.60)
3. Verify market data is being fetched
4. Check API server is running: `curl http://localhost:8001/api/`

### Issue: "Dashboard not updating"

1. Check frontend is running: `curl http://localhost:3001/`
2. Open browser dev tools (F12) → Network tab
3. Look for API calls to `/api/bots/status` every 10s
4. If missing: Frontend disconnected, refresh page
5. If errors: Check API server logs

### Issue: High signal rejection rate (>60%)

This indicates market conditions don't match Phase 1B assumptions:
1. **Normal**: Market in chop phase, waiting for direction
2. **Action**: Continue trading (this is expected occasionally)
3. **Monitor**: If sustained >3 days, Phase 1C filters may be needed

## Advancing to Phase 1C

After Phase 1B paper trading validation (March 4-10), next steps:

### Phase 1C Implementation

Add confidence-based filters on top of Phase 1B:

```python
# Phase 1C Config additions
{
  "phase_1b": {...},  # Keep Phase 1B base
  "phase_1c_additions": {
    "confidence_gate_strict": 0.75,  # +5% from 0.70
    "chop_filter_enabled": True,     # Use ATR chop detection
    "chop_threshold": 0.35,          # Block if chop > 35%
    "regime_filter": "stronger",     # More aggressive regime check
    "expected_wr_improvement": "58-62%"
  }
}
```

Expected improvements:
- Win Rate: 53.7% → 58-62%
- Signal Rejection: ~40% → ~50%
- Trade Quality: Higher confidence signals only

## File Reference

Key files for paper trading integration:

```
ClawWork/
├── paper_trading_runner.py         # Legacy manual-only runner
├── start_paper_trading.sh           # Startup orchestration script
├── PAPER_TRADING_INTEGRATION.md    # This file
├── livebench/
│   ├── api/server.py               # FastAPI backend
│   ├── trading/auto_trader.py      # Default paper trading engine
│   ├── bots/
│   │   ├── ict_sniper.py          # Phase 1B config deployed
│   │   ├── ensemble.py             # Bot orchestration
│   │   └── multi_timeframe.py      # Confidence-gated MTF filter
│   └── data/auto_trader/
│       ├── trades_log.jsonl        # Trade log
│       └── positions.json          # Open position state
├── frontend/
│   └── src/
│       ├── pages/BotEnsemble.jsx   # Dashboard visualization
│       ├── api.js                  # API client layer
│       └── ...
└── logs/
    ├── api_server.log              # API debug
    ├── fyers_screener_loop.log     # Screener activity
    └── frontend.log                # Frontend dev
```

## Next Steps

1. ✅ **Infrastructure Ready** (Dashboard, API, Bot ensemble)
2. ✅ **Phase 1B Deployed** (54% WR validated)
3. 🚀 **Paper Trading (March 4-10)**
   - Start `./start_paper_trading.sh`
   - Monitor dashboard daily
   - Track metrics vs expectations
4. 📊 **Analysis (March 10)**
   - Validate 53.7% WR
   - Identify failure patterns
   - Check confidence gate effectiveness
5. 🔄 **Phase 1C Implementation**
   - Add advanced filters (March 11-17)
   - Target: 58-62% WR
   - Re-validate with extended period

## Support

If paper trading metrics don't match expected 53.7% WR:

1. **Check Phase 1B config is deployed**
   ```python
   # In livebench/bots/ict_sniper.py
   config = {
     "swing_lookback": 9,
     "mss_swing_len": 2,
     "vol_multiplier": 1.2,
     "displacement_multiplier": 1.3,
   }
   ```

2. **Verify MTF mode is permissive**
   ```python
   # In livebench/bots/ensemble.py
   self.set_mode("permissive")  # Should be this, not "strict"
   ```

3. **Confirm confidence gate at 70%**
   ```python
   # In livebench/trading/auto_trader.py and bot config
   "confidence_gate": 0.70,  # Must be exactly this
   ```

4. **Check market data quality**
   - Mock data: Realistic for testing
   - Fyers data: Real market, better for validation
   - Compare daily signal count with expected (~50-100)

---

**Last Updated:** March 2, 2026  
**Phase 1B Status:** ✅ Deployed (53.7% WR validated)  
**Paper Trading Window:** March 4-10, 2026

# 🎯 Paper Trading Integration - Quick Start Guide

## Status: Phase 1B Ready for Validation

**Backtesting Results:** 53.7% Win Rate | ₹25,647 P&L | 577 Trades  
**Window:** March 4-10, 2026  
**Target:** Validate Phase 1B performance in live market conditions  

---

## What's Ready Now

✅ **Dashboard Infrastructure** - Fully operational and monitoring real-time bot performance  
✅ **API Endpoints** - All trading, signal generation, and status endpoints functional  
✅ **Phase 1B Configuration** - Optimal parameters deployed (swing=9, mss=2, vol=1.2, disp=1.3)  
✅ **MTF Filter** - Confidence-gated (80%+ always pass, 70-79% penalize, <70% block)  
✅ **AutoTrader Paper Engine** - API-managed paper trading is ready to execute signals and record outcomes  

---

## 🚀 Quick Start (3 Steps)

### Step 1: Verify Setup
```bash
cd /path/to/ClawWork_FyersN7/ClawWork
python3 verify_paper_trading_setup.py
```

Expected output: "✓ All critical checks passed!"

### Step 2: Configure Environment (if needed)
```bash
# Edit .env file (create if missing)
nano .env

# Add/update:
API_URL=http://localhost:8001/api
API_PORT=8001
FRONTEND_PORT=3001
MARKET_DATA_PROVIDER=fyers          # or "mock" for testing
FYERS_ACCESS_TOKEN=your_token_here  # Optional, falls back to mock data
```

### Step 3: Start Paper Trading
```bash
./start_paper_trading.sh

# Wait for output showing:
# ✓ All Systems Running
# 📊 Dashboard: http://localhost:3001
# 🔧 API Server: http://localhost:8001/api/
```

**That's it!** Open your browser → http://localhost:3001 → Navigate to "Bot Ensemble" page

`start_paper_trading.sh` now starts the API, frontend, and FYERS screener loop. The API-owned `AutoTrader` is the default paper-trading engine. `paper_trading_runner.py` remains available only as a legacy manual tool.

---

## 📊 What You'll See on Dashboard

### Real-Time Metrics
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Daily P&L (PAPER)    │  Trades        │  Win Rate
+₹850                │  2/5            │  50.0%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ICT Sniper Status
  Win Rate: 50.0%  |  Trades: 2  |  P&L: +₹850

Recent Signals
  [14:23] BUY NIFTY50 @ 24500 (82% conf) ✓ WIN
  [14:15] SELL BANKNIFTY @ 49000 (75% conf) ⏳ OPEN
```

### Configuration Display
```
Min Conf: 70%  |  Max Loss: ₹500  |  SL: 1.5%  |  Target: 3%
Phase 1B Active: swing=9, mss=2, vol=1.2, disp=1.3
```

---

## 📈 Expected Performance (Based on Backtesting)

| Metric | Target | Status |
|--------|--------|--------|
| Win Rate | 53.7% | Phase 1B baseline |
| Trades/Day | 3-5 | Market dependent |
| Daily P&L | ₹500-2,000 | Conservative estimate |
| Max Drawdown | ₹500/day | Risk-limited |
| Confidence Gate | 70%+ | All signals filtered |

⚠️ **Note:** Live market conditions may differ from backtesting. 40-50% WR is acceptable if consistent.

---

## 📝 Monitoring During Paper Trading

### 1. **Check Real-Time Logs**
```bash
# Terminal window 1: AutoTrader + API activity
tail -f logs/api_server.log

# Terminal window 2: Screener loop feeding AutoTrader
tail -f logs/fyers_screener_loop.log

# Terminal window 3: View closed trade history
tail -f livebench/data/auto_trader/trades_log.jsonl
```

### 2. **Dashboard Updates Every 10 Seconds**
- Watch for "Live" badge on dashboard
- Refresh if metrics don't update (Ctrl+Shift+R)

### 3. **Daily Metrics to Check**
- Signal count (should be 50-150/day)
- Signal acceptance rate (70-80% after MTF filter)
- Win rate (target 50-60%)
- Daily P&L (should be positive on average)

---

## 🔧 Advanced: Customizing Parameters

All Phase 1B parameters are configurable in:
- **ICT Sniper Config:** `livebench/bots/ict_sniper.py` (lines ~50-70)
- **AutoTrader Runtime + Risk Controls:** `livebench/trading/auto_trader.py`
- **API Autostart / Control Plane:** `livebench/api/server.py`

```python
# Example: Adjust confidence gate
PHASE_1B_CONFIG = {
    "confidence_gate": 0.70,        # Increase to 0.75 for stricter filtering
    "swing_lookback": 9,            # Decrease to 7 for faster signals
    "vol_multiplier": 1.2,          # Increase to 1.5 for higher volume spikes
}
```

**Warning:** Changes affect signal generation. Stick with Phase 1B (9, 2, 1.2, 1.3) for consistent results.

---

## ✅ Verification Checklist

Before starting:
- [ ] `verify_paper_trading_setup.py` shows all checks passed
- [ ] `.env` file has correct API_URL and ports
- [ ] Ports 8001 and 3001 are available (`lsof -i :8001` returns nothing)
- [ ] Python 3.9+ installed (`python3 --version`)
- [ ] Required packages installed (`pip list | grep fastapi`)

During trading:
- [ ] Dashboard loads at http://localhost:3001
- [ ] "Bot Ensemble" page shows real-time metrics
- [ ] Signals appear in Recent Signals list
- [ ] Closed trades appear in `livebench/data/auto_trader/trades_log.jsonl`
- [ ] P&L updates on dashboard

---

## 🛑 Stopping Paper Trading

### Graceful Shutdown
```bash
# Press Ctrl+C in the terminal running start_paper_trading.sh
# This will cleanly stop all services and save state

# Or kill by process:
pkill -f "uvicorn"
pkill -f "npm run dev"
```

### Session Persistence
- All open trades saved to `livebench/data/auto_trader/positions.json`
- Trade history logged to `livebench/data/auto_trader/trades_log.jsonl`
- Can resume later without losing state

---

## 📋 File Reference

```
ClawWork/
├── 📄 PAPER_TRADING_INTEGRATION.md  ← Detailed documentation
├── 📄 README.md                      ← This file
├── 🚀 start_paper_trading.sh         ← Run this to start
├── ✅ verify_paper_trading_setup.py  ← Run this first
├── 🐍 paper_trading_runner.py        ← Legacy manual-only runner
│
├── frontend/                         ← React dashboard (http://3001)
│   └── src/pages/BotEnsemble.jsx    ← Bot monitoring page
│
├── livebench/
│   ├── api/server.py                ← FastAPI backend (http://8001)
│   ├── trading/auto_trader.py       ← Default paper trading engine
│   ├── bots/
│   │   ├── ict_sniper.py           ← Phase 1B configuration
│   │   ├── ensemble.py              ← Bot orchestration
│   │   └── multi_timeframe.py       ← MTF confidence filter
│   └── data/
│       └── auto_trader/
│           ├── trades_log.jsonl     ← Closed trade history
│           └── positions.json       ← Open position state
│
└── logs/                            ← Real-time activity logs
    ├── api_server.log
    ├── fyers_screener_loop.log
    └── frontend.log
```

---

## 🆘 Troubleshooting

### Problem: "Connection refused" when loading dashboard
```bash
# Check if API server is running
curl http://localhost:8001/api/

# If error, check logs
tail -f logs/api_server.log

# Restart services
./start_paper_trading.sh
```

### Problem: No signals generated for 1+ hour
```bash
# Check if market is open (9:15 AM - 3:30 PM IST, weekdays)
date '+%H:%M:%S'

# Check AutoTrader / screener activity
grep "AutoTrader\|auto-trader\|rejected\|confidence" logs/api_server.log
tail -f logs/fyers_screener_loop.log

# If too many rejections, review ensemble / confidence settings:
# livebench/api/server.py and livebench/trading/auto_trader.py
```

### Problem: Dashboard shows outdated metrics
```bash
# Refresh page
Ctrl+Shift+R  (hard refresh)

# Check if frontend is serving
curl http://localhost:3001/

# Check browser console
F12 → Console tab → Look for errors

# Restart frontend
lsof -ti :3001 | xargs kill -9
./start_paper_trading.sh
```

### Problem: "FYERS_ACCESS_TOKEN not set"
```bash
# This is normal - system uses mock data by default
# Mock data is realistic for backtesting signals

# To use real Fyers data:
export FYERS_ACCESS_TOKEN="your_token"

# Get token from Fyers app:
# 1. Open Fyers mobile app
# 2. Settings → Developer → Generate API Token
# 3. Use Bearer token from response
```

---

## 📚 Next Steps

After Phase 1B Validation (March 4-10):

### Phase 1C Implementation (March 11-17)
Add advanced confidence filters targeting 58-62% WR:
```python
Configuration: {
  "base": Phase 1B,
  "additions": {
    "confidence_gate_strict": 0.75,  # +5% from 0.70
    "chop_filter": True,              # Block chop > 35%
    "expected_wr": "58-62%"
  }
}
```

### Phase 2: Index-Specific Tuning
- NIFTY50: Swing=8, Vol=1.15
- BANKNIFTY: Swing=10, Vol=1.25
- FINNIFTY: Swing=9, Vol=1.2

### Phase 3: ML Enhancement
- 500+ trades collected
- Train deep learning model
- Ensemble with ML predictions

---

## 📞 Support

**Question:** Where are live trades recorded?
**Answer:** `livebench/data/auto_trader/trades_log.jsonl` + Dashboard in real-time

**Question:** Can I switch between paper and live?
**Answer:** Yes, via dashboard "Toggle Mode" button (setup required separately)

**Question:** What if Phase 1B underperforms?
**Answer:** This is normal in live markets. Continue to Phase 1C filters for improvement.

**Question:** How do I modify Phase 1B parameters?
**Answer:** Edit the bot / AutoTrader configuration in `livebench/bots/ict_sniper.py` and `livebench/trading/auto_trader.py` (restart required)

---

## 🎯 Success Criteria

✅ Phase 1B validation successful when:
1. **Win Rate:** 48-60% (±5% from 53.7% baseline)
2. **Consistency:** Positive P&L on 4/5 days minimum
3. **Signal Quality:** 70%+ signals after confidence gate
4. **Zero Errors:** Clean shutdown with all trades recorded

If these are met → Proceed to Phase 1C  
If not met → Investigate failure patterns before Phase 1C  

---

**Ready to start? Run:** 
```bash
./start_paper_trading.sh
```

**Then open:** http://localhost:3001

---

*Phase 1B Configuration: swing=9, mss=2, vol=1.2, disp=1.3, MTF=permissive*  
*Expected: 53.7% WR | ₹25,647 P&L | Trades: 577*  
*Paper Trading Window: March 4-10, 2026*

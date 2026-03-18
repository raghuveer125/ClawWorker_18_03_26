# ✅ Paper Trading Pre-Launch Checklist

**Date:** March 4, 2026  
**Window:** March 4-10, 2026  
**Config:** Phase 1B (swing=9, mss=2, vol=1.2, disp=1.3)  

---

## 🔍 Pre-Launch Verification (Run This First!)

```bash
cd /path/to/ClawWork_FyersN7/ClawWork
python3 verify_paper_trading_setup.py

# Must see: ✓ All critical checks passed!
```

---

## ✅ Essential Infrastructure Checks

### 1. **System & Environment**
- [ ] Python 3.9+ installed
  ```bash
  python3 --version  # Should show 3.9+
  ```

- [ ] Project root accessible
  ```bash
  cd /path/to/ClawWork_FyersN7/ClawWork
  pwd  # Should show ClawWork
  ```

- [ ] Required directories exist
  ```bash
  test -d livebench && echo "✓ livebench" || echo "✗ missing"
  test -d frontend && echo "✓ frontend" || echo "✗ missing"
  test -d logs && echo "✓ logs" || echo "✗ missing"
  ```

### 2. **Key Files Present**
- [ ] Paper trading runner
  ```bash
  test -f paper_trading_runner.py && echo "✓ Found" || echo "✗ Missing"
  ```

- [ ] Startup script
  ```bash
  test -f start_paper_trading.sh && echo "✓ Found" || echo "✗ Missing"
  test -x start_paper_trading.sh && echo "✓ Executable" || echo "✗ Not executable"
  ```

- [ ] Verification script
  ```bash
  test -f verify_paper_trading_setup.py && echo "✓ Found" || echo "✗ Missing"
  ```

- [ ] ICT Sniper bot
  ```bash
  test -f livebench/bots/ict_sniper.py && echo "✓ Found" || echo "✗ Missing"
  ```

- [ ] API server
  ```bash
  test -f livebench/api/server.py && echo "✓ Found" || echo "✗ Missing"
  ```

- [ ] Dashboard
  ```bash
  test -f frontend/src/pages/BotEnsemble.jsx && echo "✓ Found" || echo "✗ Missing"
  ```

### 3. **Port Availability**
- [ ] Port 8001 (API) is free
  ```bash
  lsof -i :8001 | wc -l  # Should show 1 (only header)
  ```

- [ ] Port 3001 (Frontend) is free
  ```bash
  lsof -i :3001 | wc -l  # Should show 1 (only header)
  ```

### 4. **Configuration**
- [ ] .env file exists (or will use defaults)
  ```bash
  test -f .env && echo "✓ .env exists" || echo "? .env missing (will use defaults)"
  ```

- [ ] .env has correct API_URL (if present)
  ```bash
  grep "API_URL.*8001" .env || echo "✓ Default will be used (http://localhost:8001/api)"
  ```

- [ ] MARKET_DATA_PROVIDER is set
  ```bash
  grep "MARKET_DATA_PROVIDER" .env || echo "✓ Will default to 'fyers' with mock fallback"
  ```

### 5. **Phase 1B Configuration**
- [ ] swing_lookback = 9
  ```bash
  grep -A 10 "PHASE_1B_CONFIG\|swing_lookback.*=" paper_trading_runner.py | grep "swing_lookback.*9"
  ```

- [ ] mss_swing_len = 2
  ```bash
  grep -A 10 "PHASE_1B_CONFIG" paper_trading_runner.py | grep "mss_swing_len.*2"
  ```

- [ ] vol_multiplier = 1.2
  ```bash
  grep -A 10 "PHASE_1B_CONFIG" paper_trading_runner.py | grep "vol_multiplier.*1\.2"
  ```

- [ ] displacement_multiplier = 1.3
  ```bash
  grep -A 10 "PHASE_1B_CONFIG" paper_trading_runner.py | grep "displacement_multiplier.*1\.3"
  ```

- [ ] confidence_gate = 0.70
  ```bash
  grep "confidence_gate.*0\.70" paper_trading_runner.py
  ```

### 6. **MTF Filter**
- [ ] MTF mode is "permissive"
  ```bash
  grep "permissive" livebench/bots/ensemble.py
  ```

- [ ] Confidence-based filtering active
  ```bash
  grep -i "confidence\|signal_confidence" livebench/bots/multi_timeframe.py | head -1
  ```

---

## 🚀 Launch Steps

### Step 1: Launch Verification
```bash
python3 verify_paper_trading_setup.py

# Expected output should include:
# ✓ Python 3.X.X (required: 3.9+)
# ✓ Directory exists: livebench/
# ✓ Directory exists: frontend/
# ✓ Python file: paper_trading_runner.py
# ✓ Phase 1B param: swing_lookback = 9
# ✓ Phase 1B param: mss_swing_len = 2
# ✓ Phase 1B param: vol_multiplier = 1.2
# ✓ Phase 1B param: displacement_multiplier = 1.3
# ✓ MTF mode: Permissive (confidence-gated)
# ✓ Port 8001 available (API Server)
# ✓ Port 3001 available (React Frontend)
# ✓ All critical checks passed!
```

### Step 2: Start Trading System
```bash
./start_paper_trading.sh

# Expected output should include:
# ✓ API Server is ready (Xs)
# ✓ Frontend is ready (Xs)
# ✓ Paper Trading Runner PID: XXXX
# 
# ✓ All Systems Running
# 📊 Dashboard: http://localhost:3001
# 🔧 API Server: http://localhost:8001/api/
# 
# Phase 1B Config Active:
# • swing_lookback=9
# • mss_swing_len=2
# • vol_multiplier=1.2
# • displacement_multiplier=1.3
# • confidence_gate=0.70
```

### Step 3: Access Dashboard
- [ ] Open browser: **http://localhost:3001**
- [ ] Page loads without errors
- [ ] Navigate to **"Bot Ensemble"** page
- [ ] See bot cards loading

### Step 4: Verify Real-Time Updates
- [ ] Dashboard shows "Live" badge
- [ ] Metrics update every 10 seconds
- [ ] No browser console errors (F12)
- [ ] Can see "ICT Sniper" bot card

---

## 📊 Daily Monitoring Checklist

Run once per day during March 4-10:

### Morning (Start of Trading)
- [ ] System started: `./start_paper_trading.sh`
- [ ] Dashboard loads: http://localhost:3001
- [ ] Bot Ensemble page shows ICT Sniper online
- [ ] Logs show signals being analyzed:
  ```bash
  tail -f logs/paper_trading.log | grep "signals analyzed"
  ```

### During Trading (Hourly)
- [ ] Dashboard metrics updating (live badge visible)
- [ ] Signal count increasing (~8-10 signals/hour expected)
- [ ] Win rate fluctuating around 50% (40-60% acceptable)
- [ ] P&L tracking correctly (wins add, losses subtract)
- [ ] No error messages in logs:
  ```bash
  tail -20 logs/paper_trading.log | grep -i "error"  # Should be empty
  ```

### End of Trading (3:30 PM IST)
- [ ] Final daily metrics recorded
- [ ] P&L for the day calculated
- [ ] Trades logged to paper_trading.jsonl:
  ```bash
  wc -l livebench/data/paper_trading.jsonl  # Count of trades
  ```

### Daily Summary Check
```bash
# Count trades for the day
grep "2026-03-0[4-7]" livebench/data/paper_trading.jsonl | wc -l

# Calculate win rate
grep "2026-03-0[4-7]" livebench/data/paper_trading.jsonl | grep '"WIN"' | wc -l
grep "2026-03-0[4-7]" livebench/data/paper_trading.jsonl | wc -l
# Math: wins / total = WR

# Check P&L trend
grep "2026-03-0[4-7]" livebench/data/paper_trading.jsonl | \
  jq '.pnl' | awk '{sum+=$1} END {print "Total P&L: ₹" int(sum)}'
```

---

## 🎯 Success Metrics

### Daily Target
- **Signals Analyzed:** 50-150 (market dependent)
- **Signals Accepted:** 35-100 (after confidence gate)
- **Trades Closed:** 2-5
- **Win Rate:** 40-60% (±10% from 53.7% baseline acceptable)
- **Daily P&L:** +₹200 to +₹2,000 (positive is good)

### Daily Red Flags
- ❌ **Zero signals for 1+ hour** (market issue or system problem)
- ❌ **<20% signal acceptance** (confidence gate too high?    - ❌ **Win rate <30%** (3 days in a row suggests problem)
- ❌ **Large consecutive losses** (>₹1,000/day)
- ❌ **Dashboard not updating** (system connectivity issue)
- ❌ **Error spam in logs** (check logs immediately)

---

## 🔧 Quick Troubleshooting

### "Dashboard shows outdated metrics"
```bash
# Hard refresh browser
Ctrl+Shift+R (Windows/Linux)
Cmd+Shift+R (Mac)

# Or restart services:
# Press Ctrl+C in terminal running start_paper_trading.sh
# Then: ./start_paper_trading.sh
```

### "No trades being recorded"
```bash
# Check if paper trading runner is running
ps aux | grep paper_trading_runner

# Check logs for errors
tail -50 logs/paper_trading.log | grep -i "error"

# Check if market is open (9:15 AM - 3:30 PM IST, weekdays)
date '+%H:%M:%S'
```

### "High signal rejection (>60%)"
```bash
# This is normal in choppy markets - just means high selectivity
# If persistent, check confidence gate:
grep "confidence_gate" paper_trading_runner.py

# Can temporarily lower to 0.60 for more signals:
# Edit paper_trading_runner.py line 36
# Restart: ./start_paper_trading.sh
```

### "Market data not being fetched"
```bash
# Check if Fyers token is set (optional, falls back to mock)
echo $FYERS_ACCESS_TOKEN

# Check logs for data fetch errors
tail -50 logs/paper_trading.log | grep -i "fyers\|mock"

# Mock data should show:
# "using mock data" = Normal, still generates realistic signals
```

---

## 📝 Daily Log Template

Copy this and fill daily:

```
Date: March _, 2026
Market: Open (9:15 AM - 3:30 PM IST)

SIGNALS:
  - Total analyzed: ___
  - Total accepted: ___
  - Rejection rate: ___%
  - Avg confidence: ___%

TRADES:
  - Trades closed: ___
  - Wins: ___ | Losses: ___ | Breakeven: ___
  - Win rate: ___%
  - Daily P&L: +/- ₹_____

SYSTEM:
  - Uptime: Started __ AM, stopped __ PM
  - Errors: ☐ None ☐ [describe]
  - Dashboard: ☐ Working ☐ Issues: [describe]

OBSERVATIONS:
  - Market regime: ☐ Trending ☐ Choppy ☐ Ranging
  - Signal quality: ☐ Excellent ☐ Good ☐ Poor
  - Confidence gate effectiveness: ☐ Too strict ☐ Good ☐ Too loose

NOTES:
  [Any issues, observations, or adjustments]
```

---

## ✨ Sign-Off Checklist

Before considering paper trading "complete":

### March 10 Analysis
- [ ] Reviewed all trades from March 4-10
- [ ] Calculated actual win rate (target: 48-60%)
- [ ] Identified failure patterns (if any)
- [ ] Compared to backtesting baseline (53.7%)
- [ ] Determined next phase (1C or debug)

### Validation Rules
- [ ] **WR 48-60%:** Phase 1B validated ✓ → Proceed to Phase 1C
- [ ] **WR 40-47%:** Acceptable ✓ → Investigate before Phase 1C
- [ ] **WR <40%:** Flag ✗ → Debug before Phase 1C
- [ ] **Consistent +P&L:** Indicates robustness ✓
- [ ] **High volatility P&L:** Indicates market variance ⚠️

### Data Preservation
- [ ] Backed up paper_trading.jsonl
- [ ] Saved paper_trading_state.json (session state)
- [ ] Exported logs for analysis
- [ ] Documented adjustments to Phase 1B config (if any)

---

## 🚀 Ready to Launch!

When you've completed all checks above, you're ready:

```bash
# Final verification
python3 verify_paper_trading_setup.py

# Launch
./start_paper_trading.sh

# Access dashboard
# Browser: http://localhost:3001 → Bot Ensemble

# Monitor daily through March 10
tail -f logs/paper_trading.log
```

**March 4-10:** Paper Trading Active  
**March 10:** Analysis & Assessment  
**March 11+:** Phase 1C or Optimization  

---

**All set? Start with:** `python3 verify_paper_trading_setup.py`

Good luck! 🎯

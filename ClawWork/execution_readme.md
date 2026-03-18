# Auto-Trader Execution Guide

## Quick Start

```bash
# 1. Start the dashboard (Terminal 1)
./start_dashboard.sh

# 2. Start auto-trader (Terminal 2)
curl http://localhost:8001/api/auto-trader/start
```

## Auto-Trader Commands

### Control Commands
```bash
# Start auto-trading (paper mode by default)
curl http://localhost:8001/api/auto-trader/start

# Stop auto-trading
curl http://localhost:8001/api/auto-trader/stop

# Pause (keeps monitoring, no new trades)
curl http://localhost:8001/api/auto-trader/pause

# Resume trading
curl http://localhost:8001/api/auto-trader/resume

# Emergency stop (closes all positions)
curl http://localhost:8001/api/auto-trader/emergency-stop

# Reset daily counters (call at market open)
curl http://localhost:8001/api/auto-trader/reset-daily
```

### Status Commands
```bash
# Check loop status
curl http://localhost:8001/api/auto-trader/loop-status

# Get full status
curl http://localhost:8001/api/auto-trader/status

# Check trading mode (paper/live)
curl http://localhost:8001/api/auto-trader/trading-mode

# Get open positions
curl http://localhost:8001/api/auto-trader/positions

# Get performance summary
curl http://localhost:8001/api/auto-trader/performance
```

### Bot Ensemble Commands
```bash
# Get all bots status
curl http://localhost:8001/api/bots/status

# Get bot leaderboard
curl http://localhost:8001/api/bots/leaderboard

# Get ensemble stats
curl http://localhost:8001/api/bots/ensemble-stats
```

## Monitoring Logs

```bash
# Watch auto-trader activity
tail -f logs/api.log | grep AutoTrader

# All API logs
tail -f logs/api.log

# Screener logs
tail -f logs/screener.log

# Frontend logs
tail -f logs/frontend.log
```

## Trading Mode Configuration

Edit `.env` to switch between paper and live trading:

```bash
# Paper Trading (default - safe for testing)
FYERS_DRY_RUN=true
FYERS_ALLOW_LIVE_ORDERS=false

# Live Trading (REAL MONEY - use with caution!)
FYERS_DRY_RUN=false
FYERS_ALLOW_LIVE_ORDERS=true
```

**Important**: Restart the dashboard after changing `.env`

## Risk Configuration

Default settings in `livebench/trading/auto_trader.py`:

| Setting | Value | Description |
|---------|-------|-------------|
| `max_daily_loss` | 2,000 | Stop trading if daily loss exceeds |
| `max_daily_trades` | 10 | Maximum trades per day |
| `max_daily_profit` | 10,000 | Take profit for the day |
| `max_position_size` | 5,000 | Maximum capital per trade |
| `max_concurrent_positions` | 2 | Maximum open positions |
| `max_loss_per_trade` | 500 | Maximum loss per single trade |
| `stop_loss_pct` | 1.5% | Stop loss percentage |
| `target_pct` | 3.0% | Target percentage |
| `min_probability` | 70% | Minimum confidence to trade |
| `min_consensus` | 60% | Minimum bot consensus |

## Indices Tracked

- **NIFTY50** - Nifty 50 Index Options
- **BANKNIFTY** - Bank Nifty Index Options
- **SENSEX** - BSE Sensex Index Options

## Trading Logic

1. **Screener** runs every 30 seconds, generates market signals
2. **Auto-trader loop** runs every 10 seconds:
   - Fetches latest screener data
   - 6 bots analyze market conditions
   - If consensus >= 60% and confidence >= 70%:
     - **BEARISH** market → Buy PE (Put Option)
     - **BULLISH** market → Buy CE (Call Option)
3. **Position monitoring**:
   - Exit at stop loss (1.5%)
   - Exit at target (3%)
   - EOD square-off at 3:15 PM

## The 6 Trading Bots

| Bot | Strategy |
|-----|----------|
| TrendFollower | Follows market momentum |
| ReversalHunter | Looks for reversals |
| MomentumScalper | Quick momentum plays |
| OIAnalyst | Open Interest analysis |
| VolatilityTrader | Volatility-based trades |
| MLPredictor | Machine learning (needs training) |

## Dashboard URLs

- **Local**: http://localhost:3001
- **Remote**: https://trading.bhoomidaksh.xyz
- **API**: http://localhost:8001
- **API Docs**: http://localhost:8001/docs

## Troubleshooting

### No trades executing?
1. Check confidence level: `curl http://localhost:8001/api/auto-trader/loop-status`
2. Confidence needs to be >= 70% for trades
3. Check if within trading hours (9:30 AM - 3:00 PM)

### Auto-trader not starting?
1. Ensure dashboard is running: `./start_dashboard.sh`
2. Check API logs: `tail -f logs/api.log`

### Screener data not updating?
1. Check screener logs: `tail -f logs/screener.log`
2. Verify FYERS authentication: `SKIP_AUTH=0 ./start_dashboard.sh`

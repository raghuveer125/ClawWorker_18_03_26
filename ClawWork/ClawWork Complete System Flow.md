ClawWork Complete System Flow
What Happens When You Run ./start_dashboard.sh
PHASE 1: STARTUP SEQUENCE (0-10 seconds)
Step 1.1: Environment Loading

.env file loaded:
├── FYERS_APP_ID          → API credentials
├── FYERS_SECRET_ID       → API credentials  
├── FYERS_ACCESS_TOKEN    → Daily token
├── FYERS_DRY_RUN         → true = Paper mode
└── FYERS_ALLOW_LIVE_ORDERS → false = Safety lock
Step 1.2: FYERS Auto-Authentication

scripts/fyers_auto_auth.py executes:
├── Opens headless browser
├── Logs into FYERS portal
├── Captures OAuth token
├── Saves to .env file
└── Token valid for 24 hours
Step 1.3: Backend API Server Starts

uvicorn api.server:app --port 8001
├── FastAPI app initializes
├── WebSocket endpoints ready
├── CORS enabled for frontend
└── Data directories created
Step 1.4: Frontend Dashboard Starts

npm run dev --port 3001
├── React/Vite app compiles
├── WebSocket client connects
└── Dashboard UI renders
Step 1.5: Cloudflare Tunnel (Optional)

cloudflared tunnel run
└── Exposes local server at trading.bhoomidaksh.xyz
PHASE 2: ENSEMBLE COORDINATOR INITIALIZATION
When first API request arrives, EnsembleCoordinator initializes:

Step 2.1: Shared Memory Creation

SharedMemory()
├── data_dir = "data/bot_data/"
├── Loads historical trade records
├── Loads bot performance stats
└── Initializes persistence layer
Step 2.2: Trading Bots Initialization (5 Core Bots)

self.bots = [
    TrendFollowerBot(memory),      # Weight: 1.5 (67% WR, +18K P&L)
    ReversalHunterBot(memory),     # Weight: 0.0 (DISABLED - 22% WR)
    MomentumScalperBot(memory),    # Weight: 1.8 (100% WR, +3.6K P&L)
    OIAnalystBot(memory),          # Weight: 1.4 (61% WR, +20K P&L)
    VolatilityTraderBot(memory),   # Weight: 0.3 (40% WR, -1K P&L)
]
Step 2.3: ML Bot Initialization (6th Bot)

MLTradingBot(data_dir)
├── Checks for trained model (ml_model.pkl)
├── If trained: Active with weight 1.5
├── If not: Passive, collecting features
└── Auto-activates after 500+ trades
Step 2.4: LLM Trading Bot (7th Bot - Optional)

LLMTradingBot(memory)
├── Requires OPENAI_API_KEY
├── Uses GPT-4o-mini for reasoning
├── Analyzes market with TRUE AI
└── Weight: 2.0 when active
Step 2.5: LLM Veto Layer (Capital Protection)

LLMVetoLayer(model="gpt-4o-mini")
├── Reviews ALL signals before execution
├── Blocks risky trades
├── Tracks "saved losses"
└── Independent safety layer
Step 2.6: Multi-Timeframe Engine

MultiTimeframeEngine()
├── Tracks 15m, 5m, 1m candles
├── Calculates trend for each timeframe
├── Blocks trades against 15m trend
└── "Never fight the boss"
Step 2.7: Adaptive Risk Controller

AdaptiveRiskController(data_dir)
├── Loads historical trade outcomes
├── Calculates win rates by condition
├── Sets risk level (NORMAL/ELEVATED/HIGH)
├── Can HALT trading if losses mount
└── Independent learning layer
Step 2.8: Parameter Optimizer

ParameterOptimizer(data_dir)
├── Loads parameter history
├── Tracks win rates per parameter set
├── Evolutionary optimization
└── Auto-applies best parameters daily
Step 2.9: Institutional Risk Layer (HEDGE FUND GRADE)

InstitutionalRiskLayer(data_dir)
├── Regime Detection
│   ├── TRENDING_BULL / TRENDING_BEAR
│   ├── HIGH_VOL / LOW_VOL
│   ├── CHOPPY (BLOCKS ALL TRADING)
│   ├── RANGING / BREAKOUT
│   └── Calculates: Choppiness Index, ADX, Volatility Percentile
├── Bot Expectancy Calculator
│   ├── Win%, Avg Win, Avg Loss
│   ├── Profit Factor, Sharpe Ratio, Sortino Ratio
│   └── Rolling 60-trade validation
├── Portfolio Exposure Control
│   ├── Max 30% total exposure
│   ├── Max 10% single position
│   └── Max 15% per index
└── Decision Quality Scoring (0-100)
Step 2.10: Capital Allocator (NEW)

InstitutionalCapitalAllocator(total_capital=100000)
├── Strategy Sleeves:
│   ├── TREND: 35% (TrendFollower, PatternScanner)
│   ├── MEAN_REVERSION: 25% (OIAnalyzer, Contrarian)
│   ├── MOMENTUM: 20% (MomentumBot)
│   ├── EVENT: 10% (VolatilityBot)
│   └── DEFENSIVE: 10% (Cash buffer)
├── Kelly Criterion Sizing
│   ├── K = W - [(1-W) / R]
│   ├── Uses fractional Kelly (0.25x)
│   └── Minimum 20 trades for activation
├── Drawdown Protection
│   ├── >5% drawdown: Recovery mode ON
│   ├── >7% drawdown: 50% size reduction
│   ├── >8% drawdown: 75% size reduction
│   └── >10% drawdown: HALT
└── VaR Budget: 5% total limit
Step 2.11: Model Drift Detector (NEW)

ModelDriftDetector(auto_quarantine=True)
├── Performance Drift Monitoring
│   ├── Compares live vs backtest
│   ├── Thresholds: 10%/20%/35%/50%
│   └── Auto-quarantine at 35%+
├── Feature Drift (PSI)
│   ├── Population Stability Index
│   ├── Detects market condition changes
│   └── Alerts when PSI > 0.15
├── Model Health Score (0-100)
│   ├── Performance: 35%
│   ├── Feature drift: 20%
│   ├── Regime accuracy: 20%
│   ├── Data quality: 15%
│   └── Staleness: 10%
└── Auto-Rehabilitation after 5 days
Step 2.12: Execution Engine (NEW)

ExecutionEngine(max_slippage=0.01)
├── Liquidity Assessment
│   ├── DEEP: Spread <0.1%, Depth >5000
│   ├── NORMAL: Spread <0.3%, Depth >2000
│   ├── THIN: Spread <0.8%, Depth >500
│   └── ILLIQUID: Block large orders
├── Slippage Prediction
│   ├── Base slippage by liquidity
│   ├── Size impact factor
│   ├── Volatility adjustment
│   └── Urgency premium
├── Market Impact Model (Almgren-Chriss)
│   ├── Temporary impact: η * σ * √(V/T)
│   ├── Permanent impact: γ * σ * V/T
│   └── Decay time calculation
└── Execution Strategies
    ├── MARKET: Immediate (high slippage)
    ├── LIMIT: Price protection
    ├── TWAP: Time-weighted (illiquid)
    ├── VWAP: Volume-weighted
    ├── ICEBERG: Hidden quantity
    └── SNIPER: Wait for optimal
Step 2.13: Deep Learning Engine

DeepLearningEngine(data_dir)
├── Pattern Discovery
│   ├── Records all trade contexts
│   ├── Clusters similar conditions
│   └── Finds winning patterns
├── Historical Analysis
│   ├── Checks "have we seen this before?"
│   ├── What was the outcome?
│   └── Adjusts confidence ±30
└── Persists to disk (never forgets)
Step 2.14: Regime Detector

RegimeDetector()
├── Trend Analysis (RSI, MACD, ADX)
├── Volatility Analysis (ATR, IV percentile)
├── Volume Analysis (relative volume)
└── Outputs: TRENDING_UP, TRENDING_DOWN, 
             RANGING, HIGH_VOL, LOW_VOL
PHASE 3: AUTO-TRADER INITIALIZATION
When /api/auto-trader/start is called:

Step 3.1: AutoTrader Creation

AutoTrader(
    ensemble=ensemble,
    fyers_client=fyers_api,
    mode=TradingMode.PAPER,  # or LIVE
    risk_config=RiskConfig(
        max_daily_loss=2000,
        max_daily_trades=10,
        max_position_size=5000,
        max_concurrent_positions=2,
        min_probability=70,
        stop_loss_pct=1.5,
        target_pct=3.0,
    )
)
Step 3.2: State Loading

Loads from data/auto_trader/:
├── positions.json (open positions)
├── trades_log.jsonl (all trades)
├── daily_summary.jsonl (daily stats)
└── learning_insights.json (patterns)
PHASE 4: MAIN TRADING LOOP
The auto-trader runs a continuous loop:

Step 4.0: Daily Reset Check (9:15 AM)

if now.hour == 9 and now.minute == 15:
    ensemble.reset_daily()
    ├── Reset daily_trades = []
    ├── Reset daily_pnl = 0
    ├── Reset capital_allocator.reset_daily()
    ├── Rebalance sleeves for current regime
    └── Run parameter_optimizer.optimize()
        ├── Analyze last N trades per bot
        ├── Find best performing parameters
        ├── Apply new parameters to bots
        └── Log changes
Step 4.1: Risk Check (Every Loop)

can_trade, reason = check_can_trade()
├── Mode != DISABLED
├── Not paused
├── daily_pnl > -max_daily_loss ($2000)
├── daily_pnl < max_daily_profit ($10000)
├── daily_trades < max_trades (10)
├── open_positions < max_concurrent (2)
├── Time > 9:30 AM (first 15 min blocked)
└── Time < 3:00 PM (last 30 min blocked)
Step 4.2: Market Data Fetch

fyers_api.get_quotes(indices)
Returns for each index:
├── ltp: 22450.50
├── change_pct: +0.45
├── high: 22500
├── low: 22380
├── volume: 1234567
└── open: 22400
Step 4.3: Option Chain Fetch

fyers_api.get_option_chain(index, strikes=10)
Returns:
├── atm_strike: 22450
├── ce_data: [{strike, ltp, oi, volume, iv, delta, gamma, theta}]
├── pe_data: [{strike, ltp, oi, volume, iv, delta, gamma, theta}]
├── pcr: 1.15
├── max_pain: 22400
└── total_oi: 5000000
PHASE 5: ENSEMBLE ANALYSIS (12-Step Decision Pipeline)
STEP 0: INSTITUTIONAL REGIME GATE (PRIMARY GATEKEEPER)

inst_regime = institutional_layer.detect_regime(index, market_data)

Calculations:
├── Choppiness Index (14-period)
│   ├── CI = 100 * LOG10(SUM(ATR) / (Highest - Lowest)) / LOG10(14)
│   ├── CI > 62: CHOPPY (NO TRADE)
│   └── CI < 38: TRENDING
├── ADX (Average Directional Index)
│   ├── ADX < 20: No trend
│   ├── ADX 20-40: Trend developing
│   └── ADX > 40: Strong trend
├── Volatility Percentile
│   ├── Current IV vs 30-day range
│   └── >80th percentile: HIGH_VOL
└── Trading Condition:
    ├── NO_TRADE: CHOPPY market → BLOCK
    ├── POOR: HIGH_VOL → 30% size
    ├── CAUTION: Uncertain → 60% size
    ├── GOOD: Clear trend → 100% size
    └── EXCELLENT: Strong trend → 100% size + preferred bots
STEP 1: TIME & RISK FILTERS

institutional_gate = get_market_session()
├── PRE_MARKET: Before 9:15 → NO TRADE
├── MARKET_OPEN: 9:15-9:30 → NO TRADE
├── MORNING: 9:30-12:00 → OK
├── AFTERNOON: 12:00-14:00 → OK
├── CLOSING: 14:00-15:30 → CAUTION
└── POST_MARKET: After 15:30 → NO TRADE

day_type = get_trading_day_type()
├── EXPIRY: Thursday → Special rules
├── MONTHLY_EXPIRY: Last Thursday → Very special
├── NORMAL: Regular day
└── BUDGET_DAY, RBI_DAY → Avoid
STEP 2: REGIME DETECTION

regime_analysis = regime_detector.detect_regime(index, market_data)

Calculations:
├── Trend Direction
│   ├── RSI: (Avg Gain / Avg Loss) normalized to 0-100
│   ├── MACD: EMA12 - EMA26 with Signal line
│   └── Price vs MA20: Above = BULLISH
├── Regime Classification
│   ├── STRONG_UPTREND: RSI>60, MACD>0, Price>MA
│   ├── UPTREND: 2/3 bullish
│   ├── DOWNTREND: 2/3 bearish
│   ├── STRONG_DOWNTREND: RSI<40, MACD<0, Price<MA
│   └── RANGING: Mixed signals
└── Regime-Adjusted Weights:
    ├── UPTREND: TrendFollower×1.3, OIAnalyst×1.2
    ├── DOWNTREND: ReversalHunter×0.8, MomentumScalper×0.9
    └── RANGING: VolatilityTrader×1.1
STEP 3: DEEP LEARNING PATTERN CHECK

should_trade, reason, conf_adj = deep_learning.should_trade(market_data)

Process:
├── Extract current market fingerprint
│   ├── change_pct, pcr, iv, volume_ratio
│   ├── hour, day_of_week, regime
│   └── oi_buildup, momentum
├── Search historical patterns (cosine similarity)
├── Find top 5 similar past situations
├── Calculate weighted outcome
│   ├── >60% wins → +15 confidence
│   ├── >70% wins → +25 confidence
│   ├── <40% wins → -20 confidence
│   └── <30% wins → -30 confidence
└── Return: (trade_ok, "PATTERN_BULLISH/BEARISH", conf_adj)
STEP 4: COLLECT BOT SIGNALS

for bot in [TrendFollower, Reversal, Momentum, OI, Volatility]:
    signal = bot.analyze(index, market_data, option_chain)
    
Each bot calculates:
├── signal_type: STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL
├── confidence: 0-100
├── option_type: CE or PE
├── strike: Recommended strike price
├── entry: Entry price
├── target: Target price (entry × 1.03 for CE)
├── stop_loss: Stop loss (entry × 0.985 for CE)
└── reasoning: "Trend confirmed by..."

TrendFollowerBot calculations:
├── min_trend_pct: 0.3% move required
├── strong_trend_pct: 0.8% for high conviction
├── momentum_threshold: 0.1% recent momentum
├── EMA crossover check
└── Volume confirmation

OIAnalystBot calculations:
├── PCR analysis (put-call ratio)
│   ├── PCR > 1.3: Bullish
│   ├── PCR < 0.7: Bearish
│   └── 0.9-1.1: Neutral
├── OI change analysis
│   ├── CE OI ↑ + Price ↑ = Short covering (bullish)
│   ├── PE OI ↑ + Price ↓ = Long unwinding (bearish)
├── Max Pain vs LTP
└── Institutional signals (RES+, SUP+, SC, LU)
STEP 4.5: MULTI-TIMEFRAME FILTER

mtf_analysis = mtf_engine.analyze(index, ltp)

Calculations:
├── 15m Trend (Boss timeframe)
│   ├── EMA9 vs EMA21
│   ├── RSI 14-period
│   └── Trend: UP, DOWN, NEUTRAL
├── 5m Trend (Tactical)
│   └── Same calculations
├── 1m Trend (Entry timing)
│   └── Same calculations
├── Alignment Score
│   ├── STRONG: All 3 agree → +15 confidence
│   ├── GOOD: 15m+5m agree → +10 confidence
│   ├── WEAK: Only 15m clear → +5 confidence
│   └── CONFLICTING: Block trade
└── Filter Rules:
    ├── CE signal + 15m DOWN → BLOCKED
    ├── PE signal + 15m UP → BLOCKED
    └── "Never fight the boss"
STEP 5: LLM VETO LAYER (Capital Protection)

approved_signals, veto_decisions = veto_layer.review_signals(
    signals, market_data, recent_trades
)

LLM Prompt includes:
├── Current market conditions
├── Recent trade history
├── Each bot's signal and reasoning
├── Risk factors

LLM evaluates:
├── Is the signal logical?
├── Are we over-trading?
├── Is there hidden risk?
├── Should we reduce size?

Veto Reasons:
├── "Counter-trend trade in strong momentum"
├── "Too many similar positions"
├── "Volatility too high for this setup"
└── "Pattern historically loses money"
STEP 6: CALCULATE WEIGHTED CONSENSUS

bullish_signals = [s for s in signals if s.signal_type in [STRONG_BUY, BUY]]
bearish_signals = [s for s in signals if s.signal_type in [STRONG_SELL, SELL]]

Weighted Score Calculation:
├── For each signal:
│   ├── base_weight = bot.performance.weight (0-2)
│   ├── regime_weight = regime_weights[bot.name]
│   ├── strong_bonus = 1.2 if STRONG_BUY/SELL
│   └── final_weight = base × regime × bonus
├── weighted_confidence = Σ(confidence × weight) / Σ(weight)
└── consensus = len(agreeing_bots) / total_bots

Decision Rules:
├── consensus ≥ 40% AND weighted_confidence > min → TRADE
├── Single bot ≥ 80% confidence → Override
└── Else → NO TRADE
STEP 7: FINAL VALIDATION

validate_decision(decision, market_data)
├── confidence ≥ min_confidence (60)
├── No existing position on same index
├── risk_per_trade ≤ max_per_trade_risk (1000)
└── Pass all checks → OK
STEP 8: ENHANCE DECISION

decision.reasoning = f"[{pattern_recommendation}] " + decision.reasoning
├── Add pattern insight
├── Add regime info
└── Add bot contributions
STEP 9: ADAPTIVE RISK CONTROLLER

allowed, reason, modifications = risk_controller.should_allow_trade(...)

Calculations:
├── Current Risk Level
│   ├── Calculate rolling win rate (last 20 trades)
│   ├── Calculate rolling P&L
│   └── Level: NORMAL, ELEVATED, HIGH, CRITICAL
├── Circuit Breakers
│   ├── 3 consecutive losses → ELEVATED
│   ├── 5 consecutive losses → HIGH
│   ├── 7 consecutive losses → HALT
│   └── Daily loss > limit → HALT
├── Position Size Modification
│   ├── NORMAL: 1.0x
│   ├── ELEVATED: 0.75x
│   ├── HIGH: 0.5x
│   └── CRITICAL: 0x (halt)
└── Time-based adjustments
    ├── Friday afternoon: 0.8x
    ├── Expiry day: 0.7x
    └── First hour: 0.8x
STEP 10: INSTITUTIONAL FINAL CHECK

allowed, reason, modifications = institutional_layer.pre_trade_check(
    index, proposed_trade, current_positions, capital, market_data, signals
)

Checks:
├── Exposure Check
│   ├── total_exposure < 30% of capital
│   ├── single_position < 10%
│   └── per_index < 15%
├── Bot Expectancy Check
│   ├── Contributing bots expectancy > 0
│   ├── Profit factor > 1.0
│   └── At least 20 trades history
├── Decision Quality Score (0-100)
│   ├── Setup quality: 30 points
│   ├── Timing quality: 20 points
│   ├── Risk/Reward: 20 points
│   ├── Regime alignment: 15 points
│   └── Consensus strength: 15 points
└── Minimum quality: 50/100 to proceed
STEP 11: MODEL DRIFT CHECK (NEW)

for bot_name in decision.contributing_bots:
    allowed, reason = drift_detector.should_allow_trade(bot_name)
    
Health Check:
├── Performance Drift
│   ├── Compare live win rate vs backtest
│   ├── Calculate deviation %
│   └── >35% drift → QUARANTINE
├── Feature Drift (PSI)
│   ├── Compare input distributions
│   └── PSI > 0.25 → Alert
├── Model Status
│   ├── HEALTHY: Full weight
│   ├── MONITORING: 80% weight
│   ├── DEGRADED: 50% weight
│   └── QUARANTINED: BLOCKED
└── If insufficient healthy bots → NO TRADE
STEP 12: CAPITAL ALLOCATION (NEW)

capital_decision = capital_allocator.request_capital(
    bot_name, proposed_trade, market_regime, signals
)

Calculations:
├── Determine Strategy Sleeve
│   ├── TrendFollower → TREND sleeve
│   ├── OIAnalyzer → MEAN_REVERSION sleeve
│   ├── MomentumBot → MOMENTUM sleeve
│   └── Map bot to sleeve
├── Check Sleeve Availability
│   ├── max_sleeve_capital = total × current_allocation
│   ├── available = max - active
│   └── If 0 → BLOCKED
├── Kelly Sizing
│   ├── K = W - [(1-W) / R]
│   ├── W = win_rate, R = avg_win/avg_loss
│   ├── Apply fractional Kelly (0.25)
│   └── Cap at 25% of capital
├── Regime Adjustment
│   ├── TRENDING_BULL: 1.0x
│   ├── HIGH_VOL: 0.5x
│   ├── CHOPPY: 0.3x
│   └── Apply to position size
├── Drawdown Factor
│   ├── >5% DD: 0.75x
│   ├── >7% DD: 0.50x
│   ├── >8% DD: BLOCKED
│   └── >10% DD: HALT ALL
└── VaR Check
    ├── proposed_var = position × 2% (assumption)
    └── If VaR budget exceeded → reduce size
PHASE 6: TRADE EXECUTION
Step 6.1: Execution Engine Pre-Check (NEW)

approved, reason, execution_plan = execution_engine.should_execute(
    symbol, side, quantity
)

Process:
├── Update Order Book (if available)
│   ├── Best bid/ask
│   ├── Total depth
│   └── Spread calculation
├── Assess Liquidity
│   ├── Spread score (0-3)
│   ├── Depth score (0-3)
│   ├── Balance score (0-2)
│   └── Total → DEEP/NORMAL/THIN/ILLIQUID
├── Estimate Slippage
│   ├── base = SLIPPAGE_BASE[liquidity]
│   ├── size_impact = participation × 0.5
│   ├── spread_cost = spread_pct / 2
│   ├── vol_factor = (vol - 0.20) × 0.005
│   └── total = sum × urgency_multiplier
├── Estimate Market Impact
│   ├── temp_impact = η × σ × √(V/T)
│   ├── perm_impact = γ × σ × V/T
│   └── total_cost = (temp + perm) × position_value
├── Choose Execution Strategy
│   ├── ILLIQUID → TWAP (split orders)
│   ├── HIGH_SLIPPAGE → ICEBERG
│   ├── MODERATE → LIMIT
│   └── NORMAL → MARKET
└── Block if slippage > 2× tolerance
Step 6.2: Capital Deployment

capital_allocator.deploy_capital(trade_id, allocated_capital, sleeve)
├── Record deployed_capital[trade_id] = amount
├── Update sleeve.active_capital += amount
├── Update available_capital -= amount
├── Update VaR consumption
└── Log deployment
Step 6.3: Order Execution

if mode == TradingMode.LIVE:
    fyers_api.place_order(
        symbol=f"NSE:{index}{expiry}{strike}{option_type}",
        qty=quantity,
        type="MARKET" or "LIMIT",
        side="BUY",
        productType="INTRADAY"
    )
else:  # PAPER mode
    position = Position(
        symbol=symbol,
        entry_price=current_price,
        quantity=quantity,
        stop_loss=decision.stop_loss,
        target=decision.target
    )
    self.positions[position.id] = position
Step 6.4: Record Trade Context

deep_learning.record_trade_entry(TradeContext(
    trade_id, timestamp, index,
    ltp, change_pct, high, low, volume,
    pcr, ce_oi, pe_oi, oi_changes,
    max_pain, iv, iv_percentile,
    market_session, day_type,
    market_regime, vix,
    action, strike, entry, target, stop_loss,
    confidence, consensus, contributing_bots
))

ml_extractor.record_features(ml_features)
PHASE 7: POSITION MONITORING
Step 7.1: Continuous Price Check

while position.status == "open":
    current_price = get_live_price(position.symbol)
    
    # Target hit?
    if current_price >= position.target:
        close_position(position, "TARGET_HIT")
    
    # Stop loss hit?
    elif current_price <= position.stop_loss:
        close_position(position, "STOP_LOSS")
    
    # Trailing stop update
    elif current_price > position.entry * 1.02:
        position.stop_loss = max(
            position.stop_loss,
            position.entry * 1.01  # Lock in 1% profit
        )
    
    sleep(1)  # Check every second
Step 7.2: Time-Based Exit

# 3:00 PM - Close all positions
if now.hour == 15 and now.minute == 0:
    for position in open_positions:
        close_position(position, "END_OF_DAY")
PHASE 8: TRADE CLOSURE & LEARNING
Step 8.1: Close Position

def close_trade(index, exit_price, outcome, pnl, exit_reason):
    position = find_position(index)
    
    # Update performance
    if outcome == "WIN":
        ensemble_performance["wins"] += 1
    else:
        ensemble_performance["losses"] += 1
    ensemble_performance["total_pnl"] += pnl
    daily_pnl += pnl
Step 8.2: Deep Learning Recording

deep_learning.record_trade_exit(
    trade_id, exit_price, outcome, pnl, pnl_pct, exit_reason
)
├── Links to entry context
├── Updates pattern database
└── Persists to disk
Step 8.3: ML Feature Update

ml_extractor.update_outcome(trade_id, outcome, pnl_pct)
├── Marks feature set with outcome
├── Ready for model training
└── Accumulates for 500+ sample threshold
Step 8.4: Veto Layer Learning

veto_layer.record_outcome(signal, outcome, pnl_pct)
├── Tracks which signals it approved/rejected
├── Calculates "saved losses" from rejections
└── Improves future decisions
Step 8.5: Bot Learning

for bot_name in contributing_bots:
    bot.learn(TradeRecord(...))
    ├── Updates win_rate
    ├── Updates profit_factor
    ├── Adjusts weight
    └── Persists stats
Step 8.6: Adaptive Risk Learning

risk_controller.record_trade(TradeOutcome(
    timestamp, index, option_type, bots_involved,
    entry_price, exit_price, pnl, pnl_pct, outcome,
    market_conditions, mtf_mode, confidence, holding_time
))
├── Updates rolling statistics
├── Adjusts risk level
├── Updates circuit breaker counters
└── Persists to disk
Step 8.7: Institutional Layer Recording

institutional_layer.record_trade_outcome(bot_name, trade)
├── Updates bot expectancy
├── Recalculates Sharpe/Sortino
├── Updates profit factor
└── Adjusts future allocations
Step 8.8: Capital Release (NEW)

capital_allocator.release_capital(position_id, pnl, sleeve)
├── deployed_capital.pop(position_id)
├── sleeve.active_capital -= original_capital
├── available_capital += (original + pnl)
├── sleeve.pnl_today += pnl
├── Update sleeve win/loss stats
├── Update drawdown tracking
└── If new high → recovery_mode = False
Step 8.9: Drift Detector Recording (NEW)

drift_detector.record_trade(bot_name, {
    pnl, pnl_pct, holding_time, regime,
    entry_time, exit_time, direction
})
├── Adds to live_trades[bot_name]
├── Triggers performance drift check
├── May create DriftAlert
└── May quarantine if severe drift
Step 8.10: Execution Quality Recording (NEW)

execution_engine.record_execution(
    order_id, symbol, side, ordered_qty, filled_qty,
    avg_fill_price, arrival_price, execution_time_sec, strategy
)
├── Calculate slippage_vs_arrival
├── Calculate implementation_shortfall
├── Calculate quality_score (0-100)
├── Update symbol execution stats
└── Track actual vs predicted slippage
Step 8.11: Parameter Optimizer Recording

parameter_optimizer.record_trade_with_params(
    bot_name, current_parameters, outcome, pnl, market_conditions
)
├── Links parameters to outcome
├── Builds parameter performance history
└── Ready for next optimization cycle
PHASE 9: DAILY RESET (9:15 AM Next Day)
Step 9.1: Reset Counters

ensemble.reset_daily()
├── daily_trades = []
├── daily_pnl = 0
├── risk_controller.reset_daily()
└── capital_allocator.reset_daily()
Step 9.2: Capital Rebalancing

capital_allocator.rebalance_sleeves(regime)
├── Get regime-specific weights
│   ├── TRENDING_BULL: TREND×1.3, MOMENTUM×1.2
│   ├── HIGH_VOL: DEFENSIVE×1.5, EVENT×1.3
│   └── CHOPPY: DEFENSIVE×2.0, MEAN_REV×1.5
├── Apply performance factors
│   ├── Calculate sleeve win rates
│   ├── Calculate Sharpe ratios
│   └── Adjust allocations
├── Normalize to 100%
└── Apply min/max constraints
Step 9.3: Parameter Optimization

results = parameter_optimizer.optimize()
├── For each bot:
│   ├── Analyze parameter → outcome correlations
│   ├── Find best performing parameter sets
│   ├── Apply evolutionary selection
│   └── Update bot.parameters
├── Log changes:
│   └── "TrendFollower.min_trend_pct: 0.3 → 0.35"
└── Persist new parameters
Step 9.4: Model Rehabilitation Check

for bot_name in quarantined_bots:
    if days_in_quarantine >= 5:
        health = drift_detector.check_model_health(bot_name)
        if health.overall_score > 60:
            drift_detector.model_status[bot_name] = MONITORING
            └── Bot can trade again at 80% weight
SUMMARY: COMPLETE DECISION CHAIN

./start_dashboard.sh
│
├── 1. Auth & Services Start
│   └── FYERS login, API, Frontend, Tunnel
│
├── 2. First API Request → Ensemble Init
│   ├── 5 Core Bots (TrendFollower, Reversal, Momentum, OI, Volatility)
│   ├── ML Bot (auto-activates after 500 trades)
│   ├── LLM Bot (if OpenAI key present)
│   ├── LLM Veto Layer
│   ├── Multi-Timeframe Engine
│   ├── Adaptive Risk Controller
│   ├── Parameter Optimizer
│   ├── Institutional Risk Layer
│   ├── Capital Allocator (NEW)
│   ├── Drift Detector (NEW)
│   ├── Execution Engine (NEW)
│   └── Deep Learning Engine
│
├── 3. Auto-Trader Start
│   └── Continuous trading loop begins
│
├── 4. Each Trading Cycle:
│   │
│   ├── STEP 0:  Regime Gate (CHOPPY → BLOCK)
│   ├── STEP 1:  Time filters (9:30-15:00)
│   ├── STEP 2:  Regime detection (adjust weights)
│   ├── STEP 3:  Pattern check (historical)
│   ├── STEP 4:  Collect 5-7 bot signals
│   ├── STEP 4.5: MTF filter (15m trend)
│   ├── STEP 5:  LLM Veto review
│   ├── STEP 6:  Calculate consensus
│   ├── STEP 7:  Validate decision
│   ├── STEP 8:  Enhance reasoning
│   ├── STEP 9:  Adaptive risk check
│   ├── STEP 10: Institutional final check
│   ├── STEP 11: Drift check (quarantine bad bots)
│   ├── STEP 12: Capital allocation (sleeves + Kelly)
│   │
│   └── EXECUTE if all 12 steps pass
│
├── 5. Position Management
│   ├── Target hit → Close (WIN)
│   ├── Stop loss hit → Close (LOSS)
│   └── 3:00 PM → Close all
│
├── 6. Learning (After Each Trade)
│   ├── Deep Learning records pattern
│   ├── ML features updated
│   ├── Bot weights adjusted
│   ├── Risk level recalculated
│   ├── Expectancy updated
│   ├── Capital released to sleeve
│   ├── Drift monitored
│   ├── Execution quality tracked
│   └── Parameters recorded
│
└── 7. Daily Reset (9:15 AM)
    ├── Counters reset
    ├── Sleeves rebalanced
    ├── Parameters optimized
    └── Quarantined bots checked
KEY CALCULATIONS REFERENCE
Metric	Formula
Choppiness Index	100 × LOG10(SUM(ATR14) / (H14 - L14)) / LOG10(14)
Kelly Fraction	K = W - [(1-W) / R] where W=win%, R=avg_win/avg_loss
PSI (Feature Drift)	Σ (actual - expected) × ln(actual/expected)
Sharpe Ratio	(Return - RiskFree) / StdDev(Return)
Profit Factor	Gross Profit / Gross Loss
VaR (95%)	Position × Volatility × 1.65 / √252
Temp Impact	η × σ × √(Volume/Time)
Perm Impact	γ × σ × Volume/Time
This is your TRUE MULTI-STRATEGY HEDGE FUND architecture with:

Prevention > Reaction philosophy
12 independent safety layers
5 strategy sleeves
Institutional-grade risk controls
Self-learning at every level
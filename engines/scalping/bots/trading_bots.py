"""
Trading Bots - Trading intelligence and execution layer.
Uses Fyers API for real market data validation.
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

# Add paths for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bot_army"))

from .base_bot import BaseBot, BotContext, BotResult, BotStatus

# Import Fyers integration
try:
    from integrations.fyers_data import FyersDataProvider, get_fyers_data
    HAS_FYERS = True
except ImportError:
    HAS_FYERS = False
    FyersDataProvider = None

# Import knowledge layer
try:
    from knowledge import get_trade_memory
    HAS_MEMORY = True
except ImportError:
    HAS_MEMORY = False


class BacktestBot(BaseBot):
    """
    Backtest Bot - Runs strategies on historical data.

    Uses Fyers API for real market data.
    Can use LLM debate to analyze results and suggest improvements.
    """

    BOT_TYPE = "backtest"
    REQUIRES_LLM = True

    def __init__(
        self,
        symbol: str = "NSE:NIFTY50-INDEX",
        resolution: str = "5m",
        days: int = 30,
        strategy_path: Optional[str] = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.symbol = symbol
        self.resolution = resolution
        self.days = days
        self.strategy_path = strategy_path

    def get_description(self) -> str:
        return f"Backtests strategies on {self.symbol} ({self.resolution})"

    async def execute(self, context: BotContext) -> BotResult:
        """Run backtest and analyze results."""
        # Get market data
        candles = await self._fetch_market_data()
        if not candles:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.FAILED,
                errors=["Failed to fetch market data"],
            )

        # Run strategy (simplified example)
        strategy = context.data.get("strategy") or self._get_default_strategy()
        trades = self._run_strategy(candles, strategy)

        # Calculate metrics
        metrics = self._calculate_metrics(trades, candles)

        # Use LLM debate to analyze if results are poor
        analysis = None
        if metrics["win_rate"] < 0.5 or metrics["profit_factor"] < 1.0:
            analysis = await self._analyze_with_llm(candles, trades, metrics, context)

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "symbol": self.symbol,
                "resolution": self.resolution,
                "candles_count": len(candles),
                "trades": trades[-10:],  # Last 10 trades
                "total_trades": len(trades),
                "analysis": analysis,
            },
            metrics=metrics,
            next_bot="parameter_optimizer" if metrics["win_rate"] < 0.5 else "risk_sentinel",
        )

    async def _fetch_market_data(self) -> List[Dict]:
        """Fetch data from Fyers, fall back to sample data."""
        candles_data = []

        if HAS_FYERS:
            try:
                provider = get_fyers_data()
                candles = provider.get_history(
                    symbol=self.symbol,
                    resolution=self.resolution,
                    days=self.days,
                )
                # Convert Candle objects to dicts
                candles_data = [
                    {
                        "timestamp": c.timestamp,
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                    }
                    for c in candles
                ]
            except Exception:
                pass

        # Fall back to sample data if no candles fetched
        if not candles_data:
            candles_data = self._generate_sample_data()

        return candles_data

    def _generate_sample_data(self) -> List[Dict]:
        """Generate sample data for testing."""
        import random

        candles = []
        base_price = 22500
        current_price = base_price

        for i in range(self.days * 75):  # ~75 5min candles per day
            current_price += random.gauss(0, base_price * 0.001)
            candles.append({
                "timestamp": int((datetime.now() - timedelta(minutes=5*i)).timestamp()),
                "open": current_price + random.gauss(0, 10),
                "high": current_price + abs(random.gauss(0, 20)),
                "low": current_price - abs(random.gauss(0, 20)),
                "close": current_price,
                "volume": random.randint(10000, 100000),
            })

        return list(reversed(candles))

    def _get_default_strategy(self) -> Dict:
        """Default simple crossover strategy."""
        return {
            "type": "sma_crossover",
            "fast_period": 9,
            "slow_period": 21,
            "stop_loss_pct": 0.5,
            "target_pct": 1.0,
        }

    def _run_strategy(self, candles: List[Dict], strategy: Dict) -> List[Dict]:
        """Run strategy and generate trades."""
        trades = []

        if strategy["type"] == "sma_crossover":
            fast = strategy["fast_period"]
            slow = strategy["slow_period"]

            for i in range(slow, len(candles)):
                fast_sma = sum(c["close"] for c in candles[i-fast:i]) / fast
                slow_sma = sum(c["close"] for c in candles[i-slow:i]) / slow

                prev_fast_sma = sum(c["close"] for c in candles[i-fast-1:i-1]) / fast
                prev_slow_sma = sum(c["close"] for c in candles[i-slow-1:i-1]) / slow

                # Crossover detection
                if prev_fast_sma <= prev_slow_sma and fast_sma > slow_sma:
                    entry = candles[i]["close"]
                    sl = entry * (1 - strategy["stop_loss_pct"]/100)
                    tp = entry * (1 + strategy["target_pct"]/100)

                    # Check exit in next candles
                    exit_price = entry
                    exit_reason = "timeout"
                    for j in range(i+1, min(i+50, len(candles))):
                        if candles[j]["low"] <= sl:
                            exit_price = sl
                            exit_reason = "stop_loss"
                            break
                        if candles[j]["high"] >= tp:
                            exit_price = tp
                            exit_reason = "target"
                            break
                        exit_price = candles[j]["close"]

                    trades.append({
                        "entry_time": candles[i]["timestamp"],
                        "entry_price": entry,
                        "exit_price": exit_price,
                        "exit_reason": exit_reason,
                        "pnl_pct": (exit_price - entry) / entry * 100,
                        "direction": "long",
                    })

        return trades

    def _calculate_metrics(self, trades: List[Dict], candles: List[Dict]) -> Dict[str, float]:
        """Calculate backtest metrics."""
        if not trades:
            return {
                "win_rate": 0,
                "profit_factor": 0,
                "total_return": 0,
                "max_drawdown": 0,
                "sharpe_ratio": 0,
            }

        wins = [t for t in trades if t["pnl_pct"] > 0]
        losses = [t for t in trades if t["pnl_pct"] <= 0]

        total_profit = sum(t["pnl_pct"] for t in wins)
        total_loss = abs(sum(t["pnl_pct"] for t in losses))

        return {
            "win_rate": len(wins) / len(trades) if trades else 0,
            "profit_factor": total_profit / total_loss if total_loss > 0 else 0,
            "total_return": sum(t["pnl_pct"] for t in trades),
            "avg_win": total_profit / len(wins) if wins else 0,
            "avg_loss": total_loss / len(losses) if losses else 0,
            "trade_count": len(trades),
        }

    async def _analyze_with_llm(
        self,
        candles: List[Dict],
        trades: List[Dict],
        metrics: Dict,
        context: BotContext,
    ) -> Optional[Dict]:
        """Use LLM debate to analyze poor results."""
        task = f"""
        Analyze this backtest result and suggest improvements:

        Strategy: SMA Crossover (9/21)
        Symbol: {self.symbol}
        Period: {self.days} days

        Metrics:
        - Win Rate: {metrics['win_rate']:.1%}
        - Profit Factor: {metrics['profit_factor']:.2f}
        - Total Return: {metrics['total_return']:.2f}%
        - Trade Count: {metrics['trade_count']}

        Recent losing trades:
        {json.dumps([t for t in trades[-5:] if t['pnl_pct'] < 0], indent=2)}

        Suggest specific parameter changes or alternative entry/exit rules.
        Use actual market data patterns to justify your recommendations.
        """

        try:
            result = await self.request_llm_debate(
                task=task,
                project_path=str(context.data.get("project_path", ".")),
                max_rounds=3,
            )
            return result
        except Exception:
            return None


class RegimeBot(BaseBot):
    """
    Market Regime Bot - Detects market conditions.

    Identifies:
    - Trending vs Range-bound
    - High vs Low volatility
    - Bullish vs Bearish bias
    """

    BOT_TYPE = "regime"
    REQUIRES_LLM = False

    def __init__(
        self,
        symbol: str = "NSE:NIFTY50-INDEX",
        lookback_days: int = 20,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.symbol = symbol
        self.lookback_days = lookback_days

    def get_description(self) -> str:
        return f"Detects market regime for {self.symbol}"

    async def execute(self, context: BotContext) -> BotResult:
        """Analyze market regime."""
        # Fetch data
        candles = await self._fetch_data()
        if not candles:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.FAILED,
                errors=["No market data"],
            )

        # Calculate regime indicators
        regime = self._detect_regime(candles)

        # Emit regime change event if significant
        if context.data.get("last_regime") != regime["type"]:
            await self._emit_event("regime_changed", {
                "symbol": self.symbol,
                "old_regime": context.data.get("last_regime"),
                "new_regime": regime["type"],
                "confidence": regime["confidence"],
            })

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output=regime,
            metrics={
                "volatility": regime["volatility"],
                "trend_strength": regime["trend_strength"],
                "confidence": regime["confidence"],
            },
        )

    async def _fetch_data(self) -> List[Dict]:
        """Fetch data from Fyers, fall back to sample data."""
        candles_data = []

        if HAS_FYERS:
            try:
                provider = get_fyers_data()
                candles = provider.get_history(
                    symbol=self.symbol,
                    resolution="1h",
                    days=self.lookback_days,
                )
                # Convert Candle objects to dicts
                candles_data = [
                    {
                        "timestamp": c.timestamp,
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                    }
                    for c in candles
                ]
            except Exception:
                pass

        # Fall back to sample data if no candles fetched
        if not candles_data:
            candles_data = self._generate_sample_data()

        return candles_data

    def _generate_sample_data(self) -> List[Dict]:
        """Generate sample data for testing without Fyers."""
        import random

        candles = []
        base_price = 22500
        current_price = base_price

        for i in range(self.lookback_days * 7):  # 7 hours per day
            current_price += random.gauss(0, base_price * 0.002)
            candles.append({
                "timestamp": int((datetime.now() - timedelta(hours=i)).timestamp()),
                "open": current_price + random.gauss(0, 10),
                "high": current_price + abs(random.gauss(0, 30)),
                "low": current_price - abs(random.gauss(0, 30)),
                "close": current_price,
                "volume": random.randint(100000, 500000),
            })

        return list(reversed(candles))

    def _detect_regime(self, candles: List[Dict]) -> Dict:
        """Detect market regime from price data."""
        if len(candles) < 20:
            return {"type": "unknown", "confidence": 0}

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]

        # Calculate indicators
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20

        # Volatility (ATR-like)
        atr_values = []
        for i in range(1, min(14, len(candles))):
            tr = max(
                highs[-i] - lows[-i],
                abs(highs[-i] - closes[-i-1]),
                abs(lows[-i] - closes[-i-1]),
            )
            atr_values.append(tr)
        atr = sum(atr_values) / len(atr_values) if atr_values else 0
        volatility = atr / closes[-1] * 100 if closes[-1] else 0

        # Trend strength (simple directional)
        price_change = (closes[-1] - closes[0]) / closes[0] * 100
        trend_strength = abs(price_change)

        # Determine regime
        if trend_strength > 5:
            regime_type = "trending_bullish" if price_change > 0 else "trending_bearish"
        elif volatility > 1.5:
            regime_type = "high_volatility_range"
        else:
            regime_type = "low_volatility_range"

        # Confidence based on clarity of signals
        confidence = min(trend_strength / 10 + (2 - volatility) / 2, 1.0)

        return {
            "type": regime_type,
            "volatility": round(volatility, 3),
            "trend_strength": round(trend_strength, 3),
            "bias": "bullish" if closes[-1] > sma20 else "bearish",
            "sma20": round(sma20, 2),
            "sma50": round(sma50, 2),
            "current_price": closes[-1],
            "confidence": round(max(0, min(1, confidence)), 2),
        }


class RiskSentinelBot(BaseBot):
    """
    Risk Sentinel - Monitors and enforces risk limits.

    Monitors:
    - Drawdown limits
    - Position exposure
    - Daily loss limits
    - Strategy correlation
    """

    BOT_TYPE = "risk_sentinel"
    REQUIRES_LLM = False

    def __init__(
        self,
        max_drawdown_pct: float = 5.0,
        max_daily_loss_pct: float = 2.0,
        max_position_pct: float = 20.0,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_position_pct = max_position_pct

    def get_description(self) -> str:
        return f"Monitors risk: max DD {self.max_drawdown_pct}%, daily loss {self.max_daily_loss_pct}%"

    async def execute(self, context: BotContext) -> BotResult:
        """Check all risk parameters."""
        portfolio = context.data.get("portfolio", {})
        positions = context.data.get("positions", [])

        risk_checks = []
        breaches = []

        # Drawdown check
        current_dd = portfolio.get("drawdown_pct", 0)
        dd_check = {
            "check": "drawdown",
            "current": current_dd,
            "limit": self.max_drawdown_pct,
            "passed": current_dd < self.max_drawdown_pct,
        }
        risk_checks.append(dd_check)
        if not dd_check["passed"]:
            breaches.append(f"Drawdown breach: {current_dd:.2f}% > {self.max_drawdown_pct}%")

        # Daily loss check
        daily_pnl = portfolio.get("daily_pnl_pct", 0)
        daily_check = {
            "check": "daily_loss",
            "current": daily_pnl,
            "limit": -self.max_daily_loss_pct,
            "passed": daily_pnl > -self.max_daily_loss_pct,
        }
        risk_checks.append(daily_check)
        if not daily_check["passed"]:
            breaches.append(f"Daily loss breach: {daily_pnl:.2f}%")

        # Position concentration check
        for pos in positions:
            pos_pct = pos.get("weight_pct", 0)
            if pos_pct > self.max_position_pct:
                breaches.append(f"Position {pos.get('symbol')} too large: {pos_pct:.1f}%")

        # Emit risk breach event if any
        if breaches:
            await self._emit_event("risk_breach", {
                "breaches": breaches,
                "action": "stop_all_trading",
            })

        status = BotStatus.BLOCKED if breaches else BotStatus.SUCCESS

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=status,
            output={
                "risk_checks": risk_checks,
                "breaches": breaches,
                "action": "halt_trading" if breaches else "continue",
            },
            errors=breaches if breaches else [],
            metrics={
                "drawdown": current_dd,
                "daily_pnl": daily_pnl,
                "breach_count": len(breaches),
            },
        )


class ExecutionBot(BaseBot):
    """
    Execution Bot - Handles order placement and management.

    Features:
    - Optimal order timing
    - Slippage control
    - Order splitting for large orders
    """

    BOT_TYPE = "execution"
    REQUIRES_LLM = False

    def __init__(
        self,
        dry_run: bool = True,
        max_slippage_pct: float = 0.1,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.dry_run = dry_run
        self.max_slippage_pct = max_slippage_pct

    def get_description(self) -> str:
        mode = "DRY RUN" if self.dry_run else "LIVE"
        return f"Executes orders ({mode})"

    async def execute(self, context: BotContext) -> BotResult:
        """Execute pending orders."""
        orders = context.data.get("orders", [])

        if not orders:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SUCCESS,
                output={"message": "No orders to execute"},
            )

        executed = []
        failed = []

        for order in orders:
            if self.dry_run:
                # Simulate execution
                executed.append({
                    **order,
                    "status": "simulated",
                    "fill_price": order.get("price", 0),
                    "slippage": 0,
                })
            else:
                # Real execution would go here
                # Using Fyers API
                result = await self._execute_order(order)
                if result.get("success"):
                    executed.append(result)
                else:
                    failed.append(result)

        # Emit order events
        for order in executed:
            await self._emit_event("order_placed", order)

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS if not failed else BotStatus.FAILED,
            output={
                "executed": executed,
                "failed": failed,
            },
            metrics={
                "orders_executed": len(executed),
                "orders_failed": len(failed),
            },
            errors=[f["error"] for f in failed] if failed else [],
        )

    async def _execute_order(self, order: Dict) -> Dict:
        """Execute order via Fyers (placeholder)."""
        # This would integrate with Fyers order API
        return {
            **order,
            "status": "pending",
            "error": "Live trading not implemented",
            "success": False,
        }

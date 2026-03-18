"""
Backtesting Framework for Multi-Bot Ensemble Trading System

Fetches historical data from Fyers, simulates trading, and optimizes parameters.

Usage:
    python -m livebench.backtesting.backtest --days 30 --index NIFTY50
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

# Import index config from shared engine
try:
    _PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))
    from shared_project_engine.indices import ACTIVE_INDICES as _SHARED_INDICES, get_market_index_config
    from shared_project_engine.market import MarketDataClient
except ImportError:
    _SHARED_INDICES = ["SENSEX", "NIFTY50", "BANKNIFTY", "FINNIFTY"]
    get_market_index_config = None
    MarketDataClient = None


# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))
LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKTEST_DATA_DIR = LIVEBENCH_ROOT / "data" / "backtest"


def _import_backtest_bot_deps():
    """Support both `livebench.*` imports and top-level `bots/backtesting` imports."""
    try:
        from ..bots.trend_follower import TrendFollowerBot
        from ..bots.reversal_hunter import ReversalHunterBot
        from ..bots.momentum_scalper import MomentumScalperBot
        from ..bots.oi_analyst import OIAnalystBot
        from ..bots.volatility_trader import VolatilityTraderBot
        from ..bots.base import SharedMemory
        from ..bots.multi_timeframe import MultiTimeframeEngine
    except ImportError:
        from bots.trend_follower import TrendFollowerBot
        from bots.reversal_hunter import ReversalHunterBot
        from bots.momentum_scalper import MomentumScalperBot
        from bots.oi_analyst import OIAnalystBot
        from bots.volatility_trader import VolatilityTraderBot
        from bots.base import SharedMemory
        from bots.multi_timeframe import MultiTimeframeEngine

    return (
        TrendFollowerBot,
        ReversalHunterBot,
        MomentumScalperBot,
        OIAnalystBot,
        VolatilityTraderBot,
        SharedMemory,
        MultiTimeframeEngine,
    )


@dataclass
class BacktestCandle:
    """Single OHLCV candle"""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass
class BacktestTrade:
    """Simulated trade record"""
    index: str
    entry_time: datetime
    exit_time: datetime
    option_type: str  # CE or PE
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    outcome: str  # WIN, LOSS, BREAKEVEN
    contributing_bots: List[str]
    confidence: float
    exit_reason: str


@dataclass
class BacktestResult:
    """Complete backtest results"""
    index: str
    period_start: str
    period_end: str
    total_candles: int
    total_signals: int
    total_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float
    total_pnl: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float
    trades: List[Dict] = field(default_factory=list)
    bot_performance: Dict[str, Dict] = field(default_factory=dict)
    parameter_suggestions: Dict[str, Any] = field(default_factory=dict)


class FyersHistoryClient:
    """Fetch historical data from Fyers"""

    SYMBOL_MAP = {
        "NIFTY50": "NSE:NIFTY50-INDEX",
        "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
        "FINNIFTY": "NSE:FINNIFTY-INDEX",
        "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
        "SENSEX": "BSE:SENSEX-INDEX",
    }

    def __init__(self):
        default_env_file = _PROJECT_ROOT / ".env"
        env_file = str(default_env_file) if default_env_file.exists() else None
        self.client = (
            MarketDataClient(env_file=env_file, fallback_to_local=bool(os.getenv("FYERS_ACCESS_TOKEN")))
            if MarketDataClient is not None
            else None
        )

    def _resolve_symbol(self, index: str) -> str:
        if get_market_index_config is not None:
            config = get_market_index_config(index)
            symbol = str(config.get("spot_symbol", "")).strip()
            if symbol:
                return symbol
        return self.SYMBOL_MAP.get(index, f"NSE:{index}-INDEX")

    def fetch_history(
        self,
        index: str,
        from_date: str,
        to_date: str,
        resolution: str = "5"  # 5-minute candles
    ) -> List[BacktestCandle]:
        """Fetch historical candles - tries API first, falls back to simulation"""
        symbol = self._resolve_symbol(index)

        if self.client is not None:
            try:
                payload = self.client.get_history_range(
                    symbol=symbol,
                    resolution=resolution,
                    range_from=from_date,
                    range_to=to_date,
                    date_format="1",
                    cont_flag="1",
                )
                candles = self._parse_candles(payload)
                if candles:
                    source = str(payload.get("_source", "local"))
                    print(f"[Backtest] Fetched {len(candles)} candles for {index} via shared market client ({source})")
                    return candles
            except Exception as e:
                print(f"[Backtest] Error fetching shared history for {index}: {e}")

        # Fall back to simulated data
        print(f"[Backtest] API unavailable - using simulated data for {index}")
        return self._generate_simulated_data(index, from_date, to_date, resolution)

    def _generate_simulated_data(
        self,
        index: str,
        from_date: str,
        to_date: str,
        resolution: str
    ) -> List[BacktestCandle]:
        """Generate realistic simulated market data based on typical NIFTY patterns"""
        import random
        import math

        # Base prices for different indices
        base_prices = {
            "NIFTY50": 22500,
            "BANKNIFTY": 48000,
            "FINNIFTY": 22000,
            "MIDCPNIFTY": 10500,
            "SENSEX": 74000,
        }

        base_price = base_prices.get(index, 22500)
        candles = []

        # Parse dates
        start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=IST)
        end = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=IST)

        # Resolution in minutes
        res_minutes = int(resolution) if resolution.isdigit() else 5

        current = start.replace(hour=9, minute=15)
        price = base_price

        # Trend parameters (changes every few days)
        trend = 0
        trend_strength = 0
        volatility = 0.001  # Base volatility

        day_count = 0

        while current <= end:
            # Skip weekends
            if current.weekday() >= 5:
                current += timedelta(days=1)
                current = current.replace(hour=9, minute=15)
                continue

            # New day - set trend
            if current.hour == 9 and current.minute == 15:
                day_count += 1
                # Trend: -1 (bearish), 0 (sideways), 1 (bullish)
                trend = random.choices([-1, 0, 1], weights=[0.3, 0.4, 0.3])[0]
                trend_strength = random.uniform(0.0005, 0.002)
                volatility = random.uniform(0.0008, 0.002)

                # Gap opening (0.2% to 0.8%)
                gap = random.uniform(-0.008, 0.008)
                price = price * (1 + gap)

            # Trading hours only (9:15 to 15:30)
            if current.hour < 9 or (current.hour == 9 and current.minute < 15):
                current += timedelta(minutes=res_minutes)
                continue
            if current.hour > 15 or (current.hour == 15 and current.minute > 30):
                current += timedelta(days=1)
                current = current.replace(hour=9, minute=15)
                continue

            # Generate candle
            # Time-of-day patterns
            hour = current.hour
            if hour == 9:  # Opening volatility
                vol_mult = 1.5
            elif hour == 14 or hour == 15:  # Closing volatility
                vol_mult = 1.3
            else:
                vol_mult = 1.0

            # Random walk with trend
            change = random.gauss(trend * trend_strength, volatility * vol_mult)

            # Mean reversion (if moved too far from base)
            deviation = (price - base_price) / base_price
            if abs(deviation) > 0.03:  # More than 3% from base
                change -= deviation * 0.1

            new_price = price * (1 + change)

            # Generate OHLC
            open_price = price
            close_price = new_price

            # High/Low based on volatility
            intrabar_vol = volatility * vol_mult * 0.5
            high_price = max(open_price, close_price) * (1 + random.uniform(0, intrabar_vol))
            low_price = min(open_price, close_price) * (1 - random.uniform(0, intrabar_vol))

            candles.append(BacktestCandle(
                timestamp=current,
                open=round(open_price, 2),
                high=round(high_price, 2),
                low=round(low_price, 2),
                close=round(close_price, 2),
                volume=random.randint(100000, 500000)
            ))

            price = new_price
            current += timedelta(minutes=res_minutes)

        print(f"[Backtest] Generated {len(candles)} simulated candles for {index}")
        return candles

    def _parse_candles(self, payload: Dict) -> List[BacktestCandle]:
        """Parse candle data from API response"""
        candles = []

        # Try different response formats
        raw_candles = None
        if isinstance(payload.get("data"), dict):
            raw_candles = payload["data"].get("candles", [])
        elif isinstance(payload.get("candles"), list):
            raw_candles = payload["candles"]

        if not raw_candles:
            return []

        for c in raw_candles:
            if len(c) >= 5:
                ts = c[0]
                # Convert epoch to datetime
                if ts > 1_000_000_000_000:
                    ts = ts / 1000
                dt = datetime.fromtimestamp(ts, tz=IST)

                candles.append(BacktestCandle(
                    timestamp=dt,
                    open=float(c[1]),
                    high=float(c[2]),
                    low=float(c[3]),
                    close=float(c[4]),
                    volume=int(c[5]) if len(c) > 5 else 0
                ))

        return sorted(candles, key=lambda x: x.timestamp)


class Backtester:
    """
    Backtesting engine for the multi-bot ensemble

    Simulates trading on historical data and tracks performance.
    """

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.getenv(
            "BOT_DATA_DIR",
            str(DEFAULT_BACKTEST_DATA_DIR)
        ))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.fyers = FyersHistoryClient()

        (
            TrendFollowerBot,
            ReversalHunterBot,
            MomentumScalperBot,
            OIAnalystBot,
            VolatilityTraderBot,
            SharedMemory,
            MultiTimeframeEngine,
        ) = _import_backtest_bot_deps()

        # Initialize bots with fresh memory for backtest
        self.memory = SharedMemory(str(self.data_dir / "backtest_memory"))
        self.bots = [
            TrendFollowerBot(self.memory),
            ReversalHunterBot(self.memory),
            MomentumScalperBot(self.memory),
            OIAnalystBot(self.memory),
            VolatilityTraderBot(self.memory),
        ]

        # Initialize Multi-Timeframe Engine
        self.mtf_engine = MultiTimeframeEngine()
        self.mtf_engine.set_mode("permissive")  # Use permissive mode for backtesting (penalize, never block)
        self.use_mtf = True  # Enable MTF filtering by default

        # Track bot signals for analysis
        self.bot_signals: Dict[str, List] = {bot.name: [] for bot in self.bots}
        self.bot_trades: Dict[str, List] = {bot.name: [] for bot in self.bots}

        # Track MTF filtering stats
        self.mtf_stats = {
            "signals_blocked": 0,
            "signals_passed": 0,
            "ce_blocked": 0,
            "pe_blocked": 0,
        }

    def run_backtest(
        self,
        index: str,
        days: int = 30,
        resolution: str = "5"
    ) -> BacktestResult:
        """
        Run backtest on historical data

        Args:
            index: Index to backtest (NIFTY50, BANKNIFTY, etc.)
            days: Number of days to backtest
            resolution: Candle resolution (5, 15, 60 minutes)

        Returns:
            BacktestResult with complete analysis
        """
        print(f"\n{'='*60}")
        print(f"BACKTESTING {index} - Last {days} days")
        print(f"{'='*60}\n")

        # Calculate date range
        end_date = datetime.now(IST)
        start_date = end_date - timedelta(days=days)

        from_date = start_date.strftime("%Y-%m-%d")
        to_date = end_date.strftime("%Y-%m-%d")

        # Fetch historical data - primary resolution
        candles = self.fyers.fetch_history(index, from_date, to_date, resolution)

        if not candles:
            print("[Backtest] No data available for backtest")
            return BacktestResult(
                index=index,
                period_start=from_date,
                period_end=to_date,
                total_candles=0,
                total_signals=0,
                total_trades=0,
                wins=0, losses=0, breakeven=0,
                win_rate=0, total_pnl=0,
                avg_win=0, avg_loss=0,
                profit_factor=0, max_drawdown=0,
                sharpe_ratio=0
            )

        # For MTF filtering, also fetch 1m candles and build higher timeframes
        if self.use_mtf:
            print(f"\n[MTF] Building multi-timeframe data from 1m candles...")
            candles_1m = self.fyers.fetch_history(index, from_date, to_date, "1")
            if candles_1m and len(candles_1m) > 15:
                # Build MTF data from 1m candles
                candles_1m_dict = [
                    {
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                        "timestamp": c.timestamp.timestamp()
                    }
                    for c in candles_1m
                ]
                self.mtf_engine.build_candles_from_1m(index, candles_1m_dict)
                print(f"[MTF] Built 1m/5m/15m candles: 1m={len(candles_1m)}, 5m={len(candles_1m)//5}, 15m={len(candles_1m)//15}")
            else:
                print("[MTF] Insufficient 1m candles, building from primary resolution")
                # Fall back to building from primary candles
                self._build_mtf_from_candles(index, candles)

        if not candles:
            print("[Backtest] No data available for backtest")
            return BacktestResult(
                index=index,
                period_start=from_date,
                period_end=to_date,
                total_candles=0,
                total_signals=0,
                total_trades=0,
                wins=0, losses=0, breakeven=0,
                win_rate=0, total_pnl=0,
                avg_win=0, avg_loss=0,
                profit_factor=0, max_drawdown=0,
                sharpe_ratio=0
            )

        # Run simulation
        trades = self._simulate_trading(index, candles)

        # Calculate results
        result = self._calculate_results(index, candles, trades, from_date, to_date)

        # Generate parameter suggestions
        result.parameter_suggestions = self._generate_suggestions(result)

        # Save results
        self._save_results(result)

        return result

    def _simulate_trading(
        self,
        index: str,
        candles: List[BacktestCandle]
    ) -> List[BacktestTrade]:
        """Simulate trading on historical candles"""
        trades: List[BacktestTrade] = []
        position = None
        prev_candle = None

        # Price history for momentum calculation
        price_history = []

        # Track day's open for calculating daily change
        current_day = None
        day_open = None

        for i, candle in enumerate(candles):
            # Skip first few candles to build history
            if i < 5:
                price_history.append(candle.close)
                prev_candle = candle
                continue

            # Track day's open
            candle_day = candle.timestamp.date()
            if candle_day != current_day:
                current_day = candle_day
                day_open = candle.open  # First candle of day sets the open

            # Check if within trading hours (9:15 - 15:30)
            hour = candle.timestamp.hour
            minute = candle.timestamp.minute

            if hour < 9 or (hour == 9 and minute < 15):
                continue
            if hour > 15 or (hour == 15 and minute > 30):
                continue

            # Build market data from candle
            market_data = self._build_market_data(
                candle, prev_candle, price_history, index, day_open
            )

            # If we have a position, check for exit
            if position:
                exit_price, exit_reason = self._check_exit(
                    position, candle, market_data
                )

                if exit_price:
                    # Close position
                    pnl = (exit_price - position["entry_price"]) * (
                        1 if position["option_type"] == "CE" else -1
                    )
                    pnl_pct = (pnl / position["entry_price"]) * 100

                    trade = BacktestTrade(
                        index=index,
                        entry_time=position["entry_time"],
                        exit_time=candle.timestamp,
                        option_type=position["option_type"],
                        entry_price=position["entry_price"],
                        exit_price=exit_price,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        outcome="WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN",
                        contributing_bots=position["bots"],
                        confidence=position["confidence"],
                        exit_reason=exit_reason
                    )
                    trades.append(trade)

                    # Track bot performance
                    for bot_name in position["bots"]:
                        self.bot_trades[bot_name].append(trade)

                    position = None

            # If no position, check for entry
            if not position:
                signals = self._collect_signals(index, market_data)

                if signals:
                    # Calculate consensus
                    bullish = [s for s in signals if s["direction"] == "BUY"]
                    bearish = [s for s in signals if s["direction"] == "SELL"]

                    # More lenient for backtesting: 1 bot minimum
                    min_bots = 1
                    min_confidence = 50

                    # Apply MTF filter before taking position
                    if len(bullish) >= min_bots:
                        avg_confidence = sum(s["confidence"] for s in bullish) / len(bullish)
                        if avg_confidence >= min_confidence:
                            # MTF Filter: Check if CE is allowed (pass signal confidence)
                            if self.use_mtf:
                                allowed, reason, conf_adj = self.mtf_engine.should_allow_signal(
                                    index, "CE", candle.close,
                                    signal_confidence=avg_confidence  # HIGH-CONF signals preserve from blocking
                                )
                                if not allowed:
                                    self.mtf_stats["signals_blocked"] += 1
                                    self.mtf_stats["ce_blocked"] += 1
                                    # Don't take this trade - against higher timeframe
                                else:
                                    self.mtf_stats["signals_passed"] += 1
                                    avg_confidence = min(95, avg_confidence + conf_adj)
                                    position = {
                                        "entry_time": candle.timestamp,
                                        "entry_price": candle.close,
                                        "option_type": "CE",
                                        "target": candle.close * 1.02,
                                        "stop_loss": candle.close * 0.98,
                                        "bots": [s["bot"] for s in bullish],
                                        "confidence": avg_confidence
                                    }
                            else:
                                position = {
                                    "entry_time": candle.timestamp,
                                    "entry_price": candle.close,
                                    "option_type": "CE",
                                    "target": candle.close * 1.02,
                                    "stop_loss": candle.close * 0.98,
                                    "bots": [s["bot"] for s in bullish],
                                    "confidence": avg_confidence
                                }

                    elif len(bearish) >= min_bots:
                        avg_confidence = sum(s["confidence"] for s in bearish) / len(bearish)
                        if avg_confidence >= min_confidence:
                            # MTF Filter: Check if PE is allowed (pass signal confidence)
                            if self.use_mtf:
                                allowed, reason, conf_adj = self.mtf_engine.should_allow_signal(
                                    index, "PE", candle.close,
                                    signal_confidence=avg_confidence  # HIGH-CONF signals preserve from blocking
                                )
                                if not allowed:
                                    self.mtf_stats["signals_blocked"] += 1
                                    self.mtf_stats["pe_blocked"] += 1
                                    # Don't take this trade - against higher timeframe
                                else:
                                    self.mtf_stats["signals_passed"] += 1
                                    avg_confidence = min(95, avg_confidence + conf_adj)
                                    position = {
                                        "entry_time": candle.timestamp,
                                        "entry_price": candle.close,
                                        "option_type": "PE",
                                        "target": candle.close * 0.98,
                                        "stop_loss": candle.close * 1.02,
                                        "bots": [s["bot"] for s in bearish],
                                        "confidence": avg_confidence
                                    }
                            else:
                                position = {
                                    "entry_time": candle.timestamp,
                                    "entry_price": candle.close,
                                    "option_type": "PE",
                                    "target": candle.close * 0.98,
                                    "stop_loss": candle.close * 1.02,
                                    "bots": [s["bot"] for s in bearish],
                                    "confidence": avg_confidence
                                }

            # Update history
            price_history.append(candle.close)
            if len(price_history) > 50:
                price_history = price_history[-50:]
            prev_candle = candle

            # Update MTF engine with new candle data
            if self.use_mtf:
                candle_dict = {
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "timestamp": candle.timestamp.timestamp()
                }
                self.mtf_engine.add_candle(index, "5m", candle_dict)

        # Close any open position at end
        if position and candles:
            last_candle = candles[-1]
            pnl = (last_candle.close - position["entry_price"]) * (
                1 if position["option_type"] == "CE" else -1
            )
            pnl_pct = (pnl / position["entry_price"]) * 100

            trades.append(BacktestTrade(
                index=index,
                entry_time=position["entry_time"],
                exit_time=last_candle.timestamp,
                option_type=position["option_type"],
                entry_price=position["entry_price"],
                exit_price=last_candle.close,
                pnl=pnl,
                pnl_pct=pnl_pct,
                outcome="WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN",
                contributing_bots=position["bots"],
                confidence=position["confidence"],
                exit_reason="EOD"
            ))

        # Print MTF stats
        if self.use_mtf:
            total = self.mtf_stats["signals_blocked"] + self.mtf_stats["signals_passed"]
            if total > 0:
                block_rate = (self.mtf_stats["signals_blocked"] / total) * 100
                print(f"\n[MTF] Filter Stats: {self.mtf_stats['signals_blocked']}/{total} signals blocked ({block_rate:.1f}%)")
                print(f"[MTF] CE blocked: {self.mtf_stats['ce_blocked']}, PE blocked: {self.mtf_stats['pe_blocked']}")

        return trades

    def _build_mtf_from_candles(self, index: str, candles: List[BacktestCandle]):
        """Build MTF data from primary resolution candles as fallback"""
        # Convert candles to dict format
        candles_dict = [
            {
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "timestamp": c.timestamp.timestamp()
            }
            for c in candles
        ]

        # Add candles to MTF engine at primary resolution
        # MTF engine will use these to estimate trends
        for candle_dict in candles_dict[-50:]:  # Use last 50 candles
            self.mtf_engine.add_candle(index, "5m", candle_dict)

    def _build_market_data(
        self,
        candle: BacktestCandle,
        prev_candle: BacktestCandle,
        price_history: List[float],
        index: str,
        day_open: float = None
    ) -> Dict[str, Any]:
        """Build market data dict from candle"""
        # Calculate change from day's open (more realistic)
        if day_open and day_open > 0:
            change_pct = ((candle.close - day_open) / day_open) * 100
        elif prev_candle:
            change_pct = ((candle.close - prev_candle.close) / prev_candle.close) * 100
        else:
            change_pct = 0

        # Previous change (from earlier in the day)
        prev_change_pct = 0
        if len(price_history) >= 2 and day_open and day_open > 0:
            prev_change_pct = ((price_history[-1] - day_open) / day_open) * 100

        momentum = change_pct - prev_change_pct

        # Estimate derived values
        range_pct = ((candle.high - candle.low) / candle.close) * 100

        # More varied IV percentile based on market conditions
        base_iv = 50
        if abs(change_pct) > 1.0:
            base_iv = 70  # High IV during big moves
        elif abs(change_pct) < 0.2:
            base_iv = 30  # Low IV during consolidation

        return {
            "ltp": candle.close,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "change_pct": change_pct,
            "prev_change_pct": prev_change_pct,
            "momentum": momentum,
            "range_pct": range_pct,
            # Estimated values (in real trading these come from option chain)
            "pcr": 1.0 + (change_pct * 0.1),  # More sensitive PCR
            "ce_oi": 100000 + int(change_pct * 10000),
            "pe_oi": 100000 - int(change_pct * 10000),
            "iv_percentile": base_iv + range_pct * 5,
            "vix": 15 + abs(change_pct) * 2,
            "market_bias": "BULLISH" if change_pct > 0.3 else "BEARISH" if change_pct < -0.3 else "NEUTRAL",
        }

    def _collect_signals(self, index: str, market_data: Dict) -> List[Dict]:
        """Collect signals from all bots"""
        signals = []

        for bot in self.bots:
            try:
                signal = bot.analyze(index, market_data)
                if signal:
                    direction = "BUY" if signal.signal_type.value in ["BUY", "STRONG_BUY"] else "SELL"
                    signals.append({
                        "bot": bot.name,
                        "direction": direction,
                        "confidence": signal.confidence,
                        "reasoning": signal.reasoning
                    })
                    self.bot_signals[bot.name].append(signal)
            except Exception as e:
                pass  # Skip bot errors in backtest

        return signals

    def _check_exit(
        self,
        position: Dict,
        candle: BacktestCandle,
        market_data: Dict
    ) -> Tuple[Optional[float], str]:
        """Check if position should be exited"""
        option_type = position["option_type"]
        entry = position["entry_price"]
        target = position["target"]
        stop_loss = position["stop_loss"]

        # For CE: profit when price goes up
        # For PE: profit when price goes down
        if option_type == "CE":
            if candle.high >= target:
                return target, "TARGET"
            if candle.low <= stop_loss:
                return stop_loss, "STOPLOSS"
        else:  # PE
            if candle.low <= target:
                return target, "TARGET"
            if candle.high >= stop_loss:
                return stop_loss, "STOPLOSS"

        # Time-based exit (after 2 hours)
        entry_time = position["entry_time"]
        if (candle.timestamp - entry_time).total_seconds() > 7200:  # 2 hours
            return candle.close, "TIMEOUT"

        return None, ""

    def _calculate_results(
        self,
        index: str,
        candles: List[BacktestCandle],
        trades: List[BacktestTrade],
        from_date: str,
        to_date: str
    ) -> BacktestResult:
        """Calculate backtest statistics"""
        wins = [t for t in trades if t.outcome == "WIN"]
        losses = [t for t in trades if t.outcome == "LOSS"]
        breakeven = [t for t in trades if t.outcome == "BREAKEVEN"]

        total_pnl = sum(t.pnl for t in trades)
        total_signals = sum(len(signals) for signals in self.bot_signals.values())

        win_rate = (len(wins) / len(trades) * 100) if trades else 0
        avg_win = (sum(t.pnl for t in wins) / len(wins)) if wins else 0
        avg_loss = (sum(t.pnl for t in losses) / len(losses)) if losses else 0

        gross_profit = sum(t.pnl for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl for t in losses)) if losses else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Calculate drawdown
        cumulative = 0
        peak = 0
        max_drawdown = 0
        for trade in trades:
            cumulative += trade.pnl
            peak = max(peak, cumulative)
            drawdown = peak - cumulative
            max_drawdown = max(max_drawdown, drawdown)

        # Sharpe ratio (simplified)
        if trades:
            returns = [t.pnl_pct for t in trades]
            avg_return = sum(returns) / len(returns)
            variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
            std_dev = variance ** 0.5
            sharpe_ratio = (avg_return / std_dev) if std_dev > 0 else 0
        else:
            sharpe_ratio = 0

        # Bot performance breakdown
        bot_performance = {}
        for bot_name, bot_trades in self.bot_trades.items():
            if bot_trades:
                bot_wins = [t for t in bot_trades if t.outcome == "WIN"]
                bot_performance[bot_name] = {
                    "signals": len(self.bot_signals.get(bot_name, [])),
                    "trades": len(bot_trades),
                    "wins": len(bot_wins),
                    "win_rate": len(bot_wins) / len(bot_trades) * 100,
                    "total_pnl": sum(t.pnl for t in bot_trades),
                    "avg_confidence": sum(t.confidence for t in bot_trades) / len(bot_trades)
                }
            else:
                bot_performance[bot_name] = {
                    "signals": len(self.bot_signals.get(bot_name, [])),
                    "trades": 0,
                    "wins": 0,
                    "win_rate": 0,
                    "total_pnl": 0,
                    "avg_confidence": 0
                }

        return BacktestResult(
            index=index,
            period_start=from_date,
            period_end=to_date,
            total_candles=len(candles),
            total_signals=total_signals,
            total_trades=len(trades),
            wins=len(wins),
            losses=len(losses),
            breakeven=len(breakeven),
            win_rate=round(win_rate, 2),
            total_pnl=round(total_pnl, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            profit_factor=round(profit_factor, 2),
            max_drawdown=round(max_drawdown, 2),
            sharpe_ratio=round(sharpe_ratio, 2),
            trades=[asdict(t) for t in trades],
            bot_performance=bot_performance
        )

    def _generate_suggestions(self, result: BacktestResult) -> Dict[str, Any]:
        """Generate parameter optimization suggestions based on results"""
        suggestions = {
            "confidence_threshold": {},
            "bot_weights": {},
            "risk_management": {},
            "general": []
        }

        # Analyze win rate
        if result.win_rate < 50:
            suggestions["general"].append(
                "Win rate below 50% - consider increasing minimum confidence threshold"
            )
            suggestions["confidence_threshold"]["min_confidence"] = 70  # Up from 60

        if result.win_rate >= 60:
            suggestions["general"].append(
                "Good win rate! Consider increasing position size slightly"
            )

        # Analyze profit factor
        if result.profit_factor < 1:
            suggestions["general"].append(
                "Profit factor < 1 - system is losing money. Review entry/exit logic"
            )
            suggestions["risk_management"]["tighter_stops"] = True

        if result.profit_factor >= 2:
            suggestions["general"].append(
                "Excellent profit factor! System is performing well"
            )

        # Analyze bot performance
        for bot_name, perf in result.bot_performance.items():
            if perf["trades"] >= 5:
                if perf["win_rate"] >= 60:
                    suggestions["bot_weights"][bot_name] = {
                        "action": "increase",
                        "suggested_weight": 1.5,
                        "reason": f"High win rate ({perf['win_rate']:.1f}%)"
                    }
                elif perf["win_rate"] < 40:
                    suggestions["bot_weights"][bot_name] = {
                        "action": "decrease",
                        "suggested_weight": 0.5,
                        "reason": f"Low win rate ({perf['win_rate']:.1f}%)"
                    }

        # Risk management
        if result.max_drawdown > result.total_pnl:
            suggestions["risk_management"]["reduce_position_size"] = True
            suggestions["risk_management"]["reason"] = "Drawdown exceeds total profit"

        return suggestions

    def _save_results(self, result: BacktestResult):
        """Save backtest results to file"""
        filename = f"backtest_{result.index}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = self.data_dir / filename

        with open(filepath, 'w') as f:
            json.dump(asdict(result), f, indent=2, default=str)

        print(f"\n[Backtest] Results saved to: {filepath}")

    def print_report(self, result: BacktestResult):
        """Print formatted backtest report"""
        print("\n" + "=" * 60)
        print("BACKTEST RESULTS")
        print("=" * 60)

        print(f"\nIndex: {result.index}")
        print(f"Period: {result.period_start} to {result.period_end}")
        print(f"Total Candles: {result.total_candles}")

        print(f"\n--- TRADING SUMMARY ---")
        print(f"Total Signals: {result.total_signals}")
        print(f"Total Trades: {result.total_trades}")
        print(f"Wins: {result.wins}")
        print(f"Losses: {result.losses}")
        print(f"Breakeven: {result.breakeven}")

        print(f"\n--- PERFORMANCE ---")
        print(f"Win Rate: {result.win_rate}%")
        print(f"Total P&L: {result.total_pnl:+.2f}")
        print(f"Average Win: {result.avg_win:+.2f}")
        print(f"Average Loss: {result.avg_loss:.2f}")
        print(f"Profit Factor: {result.profit_factor}")
        print(f"Max Drawdown: {result.max_drawdown:.2f}")
        print(f"Sharpe Ratio: {result.sharpe_ratio}")

        print(f"\n--- BOT PERFORMANCE ---")
        for bot_name, perf in result.bot_performance.items():
            status = "GOOD" if perf['win_rate'] >= 60 else "OK" if perf['win_rate'] >= 40 else "POOR"
            print(f"  {bot_name}: {perf['trades']} trades, "
                  f"{perf['win_rate']:.1f}% win rate, "
                  f"{perf['total_pnl']:+.2f} P&L [{status}]")

        print(f"\n--- SUGGESTIONS ---")
        for suggestion in result.parameter_suggestions.get("general", []):
            print(f"  - {suggestion}")

        for bot_name, weight_info in result.parameter_suggestions.get("bot_weights", {}).items():
            print(f"  - {bot_name}: {weight_info['action']} weight to {weight_info['suggested_weight']} "
                  f"({weight_info['reason']})")

        print("\n" + "=" * 60)

    def run_comparison(
        self,
        index: str,
        days: int = 30,
        resolution: str = "5"
    ) -> Dict[str, Any]:
        """
        Run comparative backtest: With MTF vs Without MTF

        This shows the impact of multi-timeframe filtering on reducing losses.
        """
        print("\n" + "=" * 70)
        print("MULTI-TIMEFRAME COMPARISON BACKTEST")
        print("=" * 70)

        results = {}

        # Run WITHOUT MTF filtering first
        print("\n[1/2] Running backtest WITHOUT MTF filtering...")
        self.use_mtf = False
        self.mtf_stats = {"signals_blocked": 0, "signals_passed": 0, "ce_blocked": 0, "pe_blocked": 0}
        result_no_mtf = self.run_backtest(index, days, resolution)
        results["without_mtf"] = result_no_mtf

        # Reset bot states for fair comparison
        self._reset_bot_states()

        # Run WITH MTF filtering
        print("\n[2/2] Running backtest WITH MTF filtering...")
        self.use_mtf = True
        self.mtf_stats = {"signals_blocked": 0, "signals_passed": 0, "ce_blocked": 0, "pe_blocked": 0}
        result_with_mtf = self.run_backtest(index, days, resolution)
        results["with_mtf"] = result_with_mtf

        # Print comparison report
        self._print_comparison(results)

        return results

    def _reset_bot_states(self):
        """Reset bot states for fair comparison"""
        _, _, _, _, _, SharedMemory, _ = _import_backtest_bot_deps()

        # Re-initialize memory and bot states
        self.memory = SharedMemory(str(self.data_dir / "backtest_memory"))
        for bot in self.bots:
            bot.recent_signals = []

        self.bot_signals = {bot.name: [] for bot in self.bots}
        self.bot_trades = {bot.name: [] for bot in self.bots}

    def _print_comparison(self, results: Dict[str, BacktestResult]):
        """Print MTF comparison report"""
        no_mtf = results["without_mtf"]
        with_mtf = results["with_mtf"]

        print("\n" + "=" * 70)
        print("MTF COMPARISON RESULTS")
        print("=" * 70)

        print("\n{:<25} {:>15} {:>15} {:>15}".format(
            "Metric", "Without MTF", "With MTF", "Improvement"
        ))
        print("-" * 70)

        # Compare key metrics
        def calc_improvement(old, new, higher_is_better=True):
            if old == 0:
                return "N/A"
            diff = new - old
            pct = (diff / abs(old)) * 100 if old != 0 else 0
            if higher_is_better:
                symbol = "+" if pct >= 0 else ""
            else:
                symbol = "+" if pct <= 0 else ""
                pct = -pct
            return f"{symbol}{pct:.1f}%"

        metrics = [
            ("Total Trades", no_mtf.total_trades, with_mtf.total_trades, False),
            ("Win Rate %", no_mtf.win_rate, with_mtf.win_rate, True),
            ("Total P&L", no_mtf.total_pnl, with_mtf.total_pnl, True),
            ("Profit Factor", no_mtf.profit_factor, with_mtf.profit_factor, True),
            ("Max Drawdown", no_mtf.max_drawdown, with_mtf.max_drawdown, False),
            ("Avg Win", no_mtf.avg_win, with_mtf.avg_win, True),
            ("Avg Loss", abs(no_mtf.avg_loss), abs(with_mtf.avg_loss), False),
        ]

        for name, old_val, new_val, higher_better in metrics:
            improvement = calc_improvement(old_val, new_val, higher_better)
            print("{:<25} {:>15.2f} {:>15.2f} {:>15}".format(
                name, old_val, new_val, improvement
            ))

        # Calculate losses prevented
        losses_no_mtf = no_mtf.losses
        losses_with_mtf = with_mtf.losses
        losses_prevented = losses_no_mtf - losses_with_mtf

        print("\n" + "-" * 70)
        print("LOSS REDUCTION ANALYSIS")
        print("-" * 70)
        print(f"Losses WITHOUT MTF: {losses_no_mtf}")
        print(f"Losses WITH MTF:    {losses_with_mtf}")
        print(f"Losses Prevented:   {losses_prevented} ({(losses_prevented/losses_no_mtf*100) if losses_no_mtf > 0 else 0:.1f}%)")

        # P&L difference
        pnl_diff = with_mtf.total_pnl - no_mtf.total_pnl
        print(f"\nP&L Improvement: {pnl_diff:+.2f}")

        if pnl_diff > 0:
            print("\n✓ MTF FILTERING IMPROVED PERFORMANCE")
        elif pnl_diff < 0:
            print("\n✗ MTF FILTERING REDUCED PERFORMANCE (may need tuning)")
        else:
            print("\n= MTF FILTERING HAD NO IMPACT")

        print("\n" + "=" * 70)


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(description="Backtest Multi-Bot Ensemble")
    parser.add_argument("--index", default="NIFTY50", help="Index to backtest")
    parser.add_argument("--days", type=int, default=30, help="Days to backtest")
    parser.add_argument("--resolution", default="5", help="Candle resolution (1, 5, 15, 60)")
    parser.add_argument("--all-indices", action="store_true", help="Run on all indices")
    parser.add_argument("--compare-mtf", action="store_true",
                        help="Compare performance with vs without MTF filtering")
    parser.add_argument("--no-mtf", action="store_true",
                        help="Disable MTF filtering (default is enabled)")

    args = parser.parse_args()

    # Load .env
    env_file = Path(__file__).resolve().parents[3] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value

    backtester = Backtester()

    # Set MTF mode
    if args.no_mtf:
        backtester.use_mtf = False
        print("[Config] MTF filtering DISABLED")
    else:
        print("[Config] MTF filtering ENABLED (15m/5m/1m alignment)")

    if args.all_indices:
        indices = _SHARED_INDICES  # From shared_project_engine
    else:
        indices = [args.index]

    all_results = []

    # MTF Comparison mode
    if args.compare_mtf:
        for index in indices:
            comparison = backtester.run_comparison(index, args.days, args.resolution)
            # Reset for next index
            backtester._reset_bot_states()
    else:
        # Normal backtest
        for index in indices:
            result = backtester.run_backtest(index, args.days, args.resolution)
            backtester.print_report(result)
            all_results.append(result)

    # Summary if multiple indices
    if len(all_results) > 1:
        print("\n" + "=" * 60)
        print("OVERALL SUMMARY")
        print("=" * 60)
        total_trades = sum(r.total_trades for r in all_results)
        total_wins = sum(r.wins for r in all_results)
        total_pnl = sum(r.total_pnl for r in all_results)

        print(f"Total Trades: {total_trades}")
        print(f"Overall Win Rate: {(total_wins/total_trades*100):.1f}%" if total_trades > 0 else "N/A")
        print(f"Total P&L: {total_pnl:+.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Paper Trading Backtest - 1 Week Historical Data with Capital Tracking.

Usage:
    python backtest_paper_trading.py [--capital 100000] [--days 7] [--fast]

Features:
- Multi-index support (SENSEX, NIFTY50)
- 1L capital tracking with P&L
- New scalping indicators integration
- Results on http://localhost:3001/scalping
"""

import os
import sys
import json
import time
import asyncio
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

# Add project paths
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("PaperTrading")

# Configuration
SCALPING_API = "http://localhost:8002"
FYERS_API_URL = "https://api-t1.fyers.in/data"


@dataclass
class Candle:
    """OHLCV candle data."""
    timestamp: int
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @classmethod
    def from_fyers(cls, raw: list) -> "Candle":
        ts, o, h, l, c, v = raw
        return cls(
            timestamp=int(ts),
            dt=datetime.fromtimestamp(ts),
            open=float(o),
            high=float(h),
            low=float(l),
            close=float(c),
            volume=int(v)
        )


@dataclass
class Position:
    """Open position."""
    id: str
    index: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    entry_time: datetime
    quantity: int
    stop_loss: float
    target: float
    status: str = "OPEN"
    exit_price: float = 0.0
    exit_time: Optional[datetime] = None
    pnl: float = 0.0


@dataclass
class PaperTradingState:
    """Paper trading state with capital tracking."""
    initial_capital: float = 100000.0
    current_capital: float = 100000.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    open_positions: List[Position] = field(default_factory=list)
    closed_positions: List[Position] = field(default_factory=list)
    max_drawdown: float = 0.0
    peak_capital: float = 100000.0

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def equity(self) -> float:
        return self.current_capital + self.unrealized_pnl

    def update_drawdown(self):
        if self.equity > self.peak_capital:
            self.peak_capital = self.equity
        drawdown = (self.peak_capital - self.equity) / self.peak_capital * 100
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown


class FyersDataFetcher:
    """Fetches historical data from Fyers API."""

    SYMBOL_MAP = {
        "SENSEX": "BSE:SENSEX-INDEX",
        "NIFTY50": "NSE:NIFTY50-INDEX",
        "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
        "FINNIFTY": "NSE:FINNIFTY-INDEX",
    }

    def __init__(self):
        self.access_token = os.getenv("FYERS_ACCESS_TOKEN")
        self.app_id = os.getenv("FYERS_APP_ID", "").split("-")[0]

        if not self.access_token:
            raise ValueError("FYERS_ACCESS_TOKEN not found in .env")

    def get_historical_candles(
        self,
        symbol: str,
        days: int = 7,
        resolution: str = "1"
    ) -> List[Candle]:
        """Fetch historical candles."""
        fyers_symbol = self.SYMBOL_MAP.get(symbol.upper(), symbol)

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        url = f"{FYERS_API_URL}/history"
        headers = {"Authorization": f"{self.app_id}:{self.access_token}"}
        params = {
            "symbol": fyers_symbol,
            "resolution": resolution,
            "date_format": "0",
            "range_from": int(start_date.timestamp()),
            "range_to": int(end_date.timestamp()),
            "cont_flag": "1"
        }

        logger.info(f"Fetching {days} days of {symbol} data...")

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            data = response.json()

            if data.get("s") != "ok":
                logger.error(f"Fyers API error: {data}")
                return []

            raw_candles = data.get("candles", [])
            candles = [Candle.from_fyers(c) for c in raw_candles]
            logger.info(f"Fetched {len(candles)} candles for {symbol}")
            return candles

        except Exception as e:
            logger.error(f"Failed to fetch data: {e}")
            return []


class ScalpingAPIClient:
    """Client for the scalping dashboard API."""

    def __init__(self, base_url: str = SCALPING_API):
        self.base_url = base_url

    def get_state(self) -> Optional[Dict]:
        try:
            resp = requests.get(f"{self.base_url}/api/scalping/status", timeout=5)
            return resp.json() if resp.ok else None
        except:
            return None

    def update_agent(self, agent_id: int, status: str, output: Dict):
        try:
            requests.post(
                f"{self.base_url}/api/scalping/agents/{agent_id}/update",
                json={"status": status, "output": output},
                timeout=5
            )
        except:
            pass

    def add_signal(self, signal: Dict):
        try:
            requests.post(
                f"{self.base_url}/api/scalping/backtest/signal",
                json=signal,
                timeout=5
            )
        except:
            pass

    def add_trade(self, trade: Dict):
        try:
            requests.post(
                f"{self.base_url}/api/scalping/backtest/trade",
                json=trade,
                timeout=5
            )
        except:
            pass

    def clear_backtest(self):
        try:
            requests.post(
                f"{self.base_url}/api/scalping/backtest/clear",
                timeout=5
            )
        except:
            pass

    def update_portfolio(self, portfolio: Dict):
        """Update portfolio state on dashboard."""
        try:
            requests.post(
                f"{self.base_url}/api/scalping/backtest/portfolio",
                json=portfolio,
                timeout=5
            )
        except:
            pass


class EnhancedBacktestEngine:
    """Enhanced backtest engine with paper trading and new indicators."""

    # Position sizing
    POSITION_SIZE_PCT = 0.10  # 10% of capital per trade
    MAX_POSITIONS = 3
    STOP_LOSS_PCT = 0.015  # 1.5%
    TARGET_PCT = 0.025  # 2.5%
    TRADE_FEE = 40  # Per trade fee

    def __init__(self, index: str, state: PaperTradingState, delay: float = 0.05):
        self.index = index
        self.state = state
        self.delay = delay
        self.api = ScalpingAPIClient()
        self.running = False
        self.trade_counter = 0

        # Indicator state
        self.candle_buffer: List[Candle] = []
        self.vwap = 0.0
        self.ema9 = 0.0
        self.ema21 = 0.0
        self.ema50 = 0.0
        self.rsi = 50.0
        self.atr = 0.0
        self.momentum_1m = 0.0
        self.momentum_3m = 0.0
        self.volume_acceleration = 1.0

        # Signal tracking
        self.last_signal_time = None
        self.signal_cooldown_minutes = 5

    def calculate_indicators(self, candle: Candle):
        """Calculate technical indicators including new scalping indicators."""
        self.candle_buffer.append(candle)
        if len(self.candle_buffer) > 100:
            self.candle_buffer = self.candle_buffer[-100:]

        closes = [c.close for c in self.candle_buffer]
        highs = [c.high for c in self.candle_buffer]
        lows = [c.low for c in self.candle_buffer]
        volumes = [c.volume for c in self.candle_buffer]

        # VWAP
        if sum(volumes) > 0:
            self.vwap = sum(c.close * c.volume for c in self.candle_buffer) / sum(volumes)

        # EMAs
        if len(closes) >= 9:
            self.ema9 = self._ema(closes, 9)
        if len(closes) >= 21:
            self.ema21 = self._ema(closes, 21)
        if len(closes) >= 50:
            self.ema50 = self._ema(closes, 50)

        # RSI
        if len(closes) >= 14:
            self.rsi = self._rsi(closes, 14)

        # ATR
        if len(closes) >= 14:
            self.atr = self._atr(highs, lows, closes, 14)

        # Momentum (new scalping indicators)
        if len(closes) >= 1:
            self.momentum_1m = ((candle.close - closes[-1]) / closes[-1] * 100) if closes[-1] > 0 else 0
        if len(closes) >= 3:
            self.momentum_3m = ((candle.close - closes[-3]) / closes[-3] * 100) if closes[-3] > 0 else 0

        # Volume acceleration
        if len(volumes) >= 5:
            avg_vol = sum(volumes[-5:]) / 5
            self.volume_acceleration = candle.volume / avg_vol if avg_vol > 0 else 1.0

    def _ema(self, data: List[float], period: int) -> float:
        if len(data) < period:
            return data[-1] if data else 0
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _rsi(self, data: List[float], period: int = 14) -> float:
        if len(data) < period + 1:
            return 50.0
        deltas = [data[i] - data[i-1] for i in range(1, len(data))]
        gains = [d if d > 0 else 0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-period:]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _atr(self, highs: List[float], lows: List[float], closes: List[float], period: int) -> float:
        if len(closes) < period + 1:
            return 0.0
        tr_values = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            tr_values.append(tr)
        return sum(tr_values[-period:]) / period if tr_values else 0.0

    def check_signal_cooldown(self, current_time: datetime) -> bool:
        """Check if we're in signal cooldown."""
        if self.last_signal_time is None:
            return False
        elapsed = (current_time - self.last_signal_time).total_seconds() / 60
        return elapsed < self.signal_cooldown_minutes

    def generate_signal(self, candle: Candle) -> Optional[Dict]:
        """Generate trading signal with enhanced criteria."""
        if len(self.candle_buffer) < 30:
            return None

        if self.check_signal_cooldown(candle.dt):
            return None

        if len(self.state.open_positions) >= self.MAX_POSITIONS:
            return None

        signal = None
        close = candle.close

        # Enhanced signal criteria with new indicators
        bullish_conditions = [
            self.ema9 > self.ema21,
            close > self.vwap,
            self.rsi > 30 and self.rsi < 65,
            self.momentum_1m > 0.02,  # Positive momentum
            self.volume_acceleration > 1.1,  # Volume surge
        ]

        bearish_conditions = [
            self.ema9 < self.ema21,
            close < self.vwap,
            self.rsi > 35 and self.rsi < 70,
            self.momentum_1m < -0.02,  # Negative momentum
            self.volume_acceleration > 1.1,
        ]

        bullish_score = sum(bullish_conditions)
        bearish_score = sum(bearish_conditions)

        # Require at least 4 conditions
        if bullish_score >= 4:
            confidence = 60 + bullish_score * 5
            signal = {
                "type": "LONG",
                "index": self.index,
                "price": close,
                "vwap": round(self.vwap, 2),
                "ema9": round(self.ema9, 2),
                "ema21": round(self.ema21, 2),
                "rsi": round(self.rsi, 1),
                "momentum_1m": round(self.momentum_1m, 3),
                "momentum_3m": round(self.momentum_3m, 3),
                "volume_acceleration": round(self.volume_acceleration, 2),
                "reason": f"Bullish setup ({bullish_score}/5 conditions)",
                "confidence": confidence,
                "timestamp": candle.dt.isoformat()
            }
        elif bearish_score >= 4:
            confidence = 60 + bearish_score * 5
            signal = {
                "type": "SHORT",
                "index": self.index,
                "price": close,
                "vwap": round(self.vwap, 2),
                "ema9": round(self.ema9, 2),
                "ema21": round(self.ema21, 2),
                "rsi": round(self.rsi, 1),
                "momentum_1m": round(self.momentum_1m, 3),
                "momentum_3m": round(self.momentum_3m, 3),
                "volume_acceleration": round(self.volume_acceleration, 2),
                "reason": f"Bearish setup ({bearish_score}/5 conditions)",
                "confidence": confidence,
                "timestamp": candle.dt.isoformat()
            }

        return signal

    def open_position(self, signal: Dict, candle: Candle) -> Position:
        """Open a new position based on signal."""
        self.trade_counter += 1
        position_size = self.state.current_capital * self.POSITION_SIZE_PCT
        entry_price = signal["price"]

        # Calculate SL and Target
        if signal["type"] == "LONG":
            stop_loss = entry_price * (1 - self.STOP_LOSS_PCT)
            target = entry_price * (1 + self.TARGET_PCT)
        else:
            stop_loss = entry_price * (1 + self.STOP_LOSS_PCT)
            target = entry_price * (1 - self.TARGET_PCT)

        position = Position(
            id=f"PT-{self.index[:3]}-{self.trade_counter:04d}",
            index=self.index,
            side=signal["type"],
            entry_price=entry_price,
            entry_time=candle.dt,
            quantity=int(position_size / entry_price),
            stop_loss=round(stop_loss, 2),
            target=round(target, 2),
        )

        # Deduct fee
        self.state.current_capital -= self.TRADE_FEE

        self.state.open_positions.append(position)
        self.last_signal_time = candle.dt

        return position

    def check_exits(self, candle: Candle) -> List[Position]:
        """Check for SL/Target hits and close positions."""
        closed = []

        for pos in self.state.open_positions[:]:
            exit_price = None
            exit_reason = None

            if pos.side == "LONG":
                if candle.low <= pos.stop_loss:
                    exit_price = pos.stop_loss
                    exit_reason = "SL hit"
                elif candle.high >= pos.target:
                    exit_price = pos.target
                    exit_reason = "Target hit"
            else:  # SHORT
                if candle.high >= pos.stop_loss:
                    exit_price = pos.stop_loss
                    exit_reason = "SL hit"
                elif candle.low <= pos.target:
                    exit_price = pos.target
                    exit_reason = "Target hit"

            if exit_price:
                pos.exit_price = exit_price
                pos.exit_time = candle.dt
                pos.status = exit_reason

                # Calculate P&L
                if pos.side == "LONG":
                    pos.pnl = (exit_price - pos.entry_price) * pos.quantity
                else:
                    pos.pnl = (pos.entry_price - exit_price) * pos.quantity

                # Deduct exit fee
                pos.pnl -= self.TRADE_FEE

                # Update state
                self.state.realized_pnl += pos.pnl
                self.state.current_capital += pos.pnl
                self.state.total_trades += 1

                if pos.pnl > 0:
                    self.state.winning_trades += 1
                else:
                    self.state.losing_trades += 1

                self.state.open_positions.remove(pos)
                self.state.closed_positions.append(pos)
                closed.append(pos)

        # Update unrealized P&L
        self.state.unrealized_pnl = 0
        for pos in self.state.open_positions:
            if pos.side == "LONG":
                self.state.unrealized_pnl += (candle.close - pos.entry_price) * pos.quantity
            else:
                self.state.unrealized_pnl += (pos.entry_price - candle.close) * pos.quantity

        self.state.update_drawdown()

        return closed

    async def process_candle(self, candle: Candle, candle_idx: int, total: int):
        """Process a single candle."""
        self.calculate_indicators(candle)

        # Check exits first
        closed_positions = self.check_exits(candle)

        # Generate signal
        signal = self.generate_signal(candle)

        if signal:
            # Open position
            position = self.open_position(signal, candle)

            # Send to dashboard
            self.api.add_signal(signal)
            self.api.add_trade({
                "id": position.id,
                "index": self.index,
                "type": position.side,
                "entry": position.entry_price,
                "sl": position.stop_loss,
                "target": position.target,
                "status": "OPEN",
                "timestamp": candle.dt.isoformat()
            })

        # Update closed trades
        for pos in closed_positions:
            self.api.add_trade({
                "id": pos.id,
                "index": self.index,
                "type": pos.side,
                "entry": pos.entry_price,
                "exit": pos.exit_price,
                "pnl": round(pos.pnl, 2),
                "status": pos.status,
                "timestamp": pos.exit_time.isoformat() if pos.exit_time else ""
            })

        # Update dashboard agents
        self.api.update_agent(0, "running", {
            "index": self.index,
            "ltp": candle.close,
            "vwap": round(self.vwap, 2),
            "volume": candle.volume,
            "timestamp": candle.dt.isoformat()
        })

        # Update portfolio
        self.api.update_portfolio({
            "capital": round(self.state.current_capital, 2),
            "realized_pnl": round(self.state.realized_pnl, 2),
            "unrealized_pnl": round(self.state.unrealized_pnl, 2),
            "equity": round(self.state.equity, 2),
            "total_trades": self.state.total_trades,
            "win_rate": round(self.state.win_rate, 1),
            "max_drawdown": round(self.state.max_drawdown, 2),
            "open_positions": len(self.state.open_positions)
        })

    async def run(self, candles: List[Candle]):
        """Run backtest on all candles."""
        self.running = True
        total = len(candles)

        logger.info(f"Starting {self.index} backtest: {total} candles")

        start_time = time.time()

        for i, candle in enumerate(candles):
            if not self.running:
                break

            await self.process_candle(candle, i, total)

            if (i + 1) % 100 == 0 or i == total - 1:
                pct = (i + 1) / total * 100
                logger.info(
                    f"[{self.index}] {i+1}/{total} ({pct:.0f}%) | "
                    f"LTP: {candle.close:,.0f} | "
                    f"PnL: {self.state.realized_pnl:+,.0f} | "
                    f"Trades: {self.state.total_trades}"
                )

            await asyncio.sleep(self.delay)

        elapsed = time.time() - start_time
        return {
            "index": self.index,
            "candles": total,
            "trades": self.state.total_trades,
            "pnl": self.state.realized_pnl,
            "win_rate": self.state.win_rate,
            "elapsed_seconds": elapsed
        }

    def stop(self):
        self.running = False


async def run_multi_index_backtest(
    indices: List[str],
    days: int,
    capital: float,
    delay: float
):
    """Run backtest on multiple indices."""

    indices_str = ', '.join(indices)
    capital_str = f"Rs {capital:,.0f}"
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║   Paper Trading Backtest - 1 Week Historical Data                ║
╠══════════════════════════════════════════════════════════════════╣
║   Indices: {indices_str:<52} ║
║   Days: {days:<55} ║
║   Capital: {capital_str:<52} ║
║   View results: http://localhost:3001/scalping                   ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    # Check API
    api = ScalpingAPIClient()
    if not api.get_state():
        logger.error("Scalping API not running. Start with: ./start.sh all")
        return

    # Clear old data
    api.clear_backtest()
    logger.info("Cleared previous backtest data")

    # Fetch data
    fetcher = FyersDataFetcher()
    all_candles = {}

    for index in indices:
        candles = fetcher.get_historical_candles(index, days=days)
        if candles:
            # Filter market hours (9:15 - 15:30)
            market_candles = [
                c for c in candles
                if 915 <= c.dt.hour * 100 + c.dt.minute <= 1530
            ]
            all_candles[index] = market_candles
            logger.info(f"{index}: {len(market_candles)} market candles")

    if not all_candles:
        logger.error("No data fetched for any index")
        return

    # Create shared state
    state = PaperTradingState(
        initial_capital=capital,
        current_capital=capital,
        peak_capital=capital
    )

    # Run backtests sequentially
    results = []
    for index, candles in all_candles.items():
        engine = EnhancedBacktestEngine(index, state, delay=delay)
        try:
            result = await engine.run(candles)
            results.append(result)
        except KeyboardInterrupt:
            logger.info(f"\nStopping {index} backtest...")
            engine.stop()
            break

    # Final summary
    print("\n" + "=" * 70)
    print("PAPER TRADING BACKTEST SUMMARY")
    print("=" * 70)

    for r in results:
        print(f"\n{r['index']}:")
        print(f"  Candles processed: {r['candles']}")
        print(f"  Trades executed: {r['trades']}")

    print(f"\nOVERALL RESULTS:")
    print(f"  Initial Capital: Rs {state.initial_capital:,.0f}")
    print(f"  Final Capital: Rs {state.current_capital:,.0f}")
    print(f"  Total Realized P&L: Rs {state.realized_pnl:+,.0f}")
    print(f"  Return: {(state.realized_pnl / state.initial_capital * 100):+.2f}%")
    print(f"  Total Trades: {state.total_trades}")
    print(f"  Winning Trades: {state.winning_trades}")
    print(f"  Losing Trades: {state.losing_trades}")
    print(f"  Win Rate: {state.win_rate:.1f}%")
    print(f"  Max Drawdown: {state.max_drawdown:.2f}%")
    print("=" * 70)


async def main():
    parser = argparse.ArgumentParser(description="Paper Trading Backtest")
    parser.add_argument("--indices", nargs="+", default=["SENSEX", "NIFTY50"],
                       help="Indices to test (default: SENSEX NIFTY50)")
    parser.add_argument("--days", type=int, default=7,
                       help="Days of history (default: 7)")
    parser.add_argument("--capital", type=float, default=100000,
                       help="Starting capital (default: 100000)")
    parser.add_argument("--delay", type=float, default=0.02,
                       help="Delay between candles (default: 0.02s)")
    parser.add_argument("--fast", action="store_true",
                       help="Fast mode (0.005s delay)")

    args = parser.parse_args()
    delay = 0.005 if args.fast else args.delay

    try:
        await run_multi_index_backtest(
            indices=args.indices,
            days=args.days,
            capital=args.capital,
            delay=delay
        )
    except KeyboardInterrupt:
        logger.info("\nBacktest interrupted by user")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
Backtest Replay - Simulates live market by replaying historical candles.

Usage:
    python backtest_replay.py [--index SENSEX] [--days 1] [--delay 1.0]

This script:
1. Fetches historical 1-minute candles from Fyers
2. Runs the scalping engine with historical data
3. Updates the scalping API state
4. Results appear on http://localhost:3001/scalping
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
from dataclasses import dataclass
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
logger = logging.getLogger("BacktestReplay")

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
        days: int = 1,
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
            "date_format": "0",  # Unix timestamp format
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
            logger.info(f"Fetched {len(candles)} candles")
            return candles

        except Exception as e:
            logger.error(f"Failed to fetch data: {e}")
            return []


class ScalpingAPIClient:
    """Client for the scalping dashboard API."""

    def __init__(self, base_url: str = SCALPING_API):
        self.base_url = base_url

    def get_state(self) -> Optional[Dict]:
        """Get current scalping state."""
        try:
            resp = requests.get(f"{self.base_url}/api/scalping/status", timeout=5)
            return resp.json() if resp.ok else None
        except:
            return None

    def update_agent(self, agent_id: int, status: str, output: Dict):
        """Update an agent's status."""
        try:
            requests.post(
                f"{self.base_url}/api/scalping/agents/{agent_id}/update",
                json={"status": status, "output": output},
                timeout=5
            )
        except:
            pass

    def add_signal(self, signal: Dict):
        """Add a trading signal."""
        try:
            requests.post(
                f"{self.base_url}/api/scalping/backtest/signal",
                json=signal,
                timeout=5
            )
        except:
            pass

    def add_trade(self, trade: Dict):
        """Add a simulated trade."""
        try:
            requests.post(
                f"{self.base_url}/api/scalping/backtest/trade",
                json=trade,
                timeout=5
            )
        except:
            pass

    def clear_backtest(self):
        """Clear previous backtest data."""
        try:
            requests.post(
                f"{self.base_url}/api/scalping/backtest/clear",
                timeout=5
            )
        except:
            pass


class BacktestEngine:
    """Runs backtest simulation with historical candles."""

    def __init__(self, index: str, delay: float = 1.0):
        self.index = index
        self.delay = delay
        self.api = ScalpingAPIClient()
        self.running = False

        # Simple indicators state
        self.candle_buffer: List[Candle] = []
        self.vwap = 0.0
        self.ema9 = 0.0
        self.ema21 = 0.0
        self.rsi = 50.0
        self.signals_generated = 0
        self.trades_simulated = 0

    def calculate_indicators(self, candle: Candle):
        """Calculate technical indicators from candle buffer."""
        self.candle_buffer.append(candle)

        # Keep last 50 candles
        if len(self.candle_buffer) > 50:
            self.candle_buffer = self.candle_buffer[-50:]

        closes = [c.close for c in self.candle_buffer]
        volumes = [c.volume for c in self.candle_buffer]

        # VWAP
        if sum(volumes) > 0:
            self.vwap = sum(c.close * c.volume for c in self.candle_buffer) / sum(volumes)

        # EMAs
        if len(closes) >= 9:
            self.ema9 = self._ema(closes, 9)
        if len(closes) >= 21:
            self.ema21 = self._ema(closes, 21)

        # RSI
        if len(closes) >= 14:
            self.rsi = self._rsi(closes, 14)

    def _ema(self, data: List[float], period: int) -> float:
        """Calculate EMA."""
        if len(data) < period:
            return data[-1] if data else 0
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _rsi(self, data: List[float], period: int = 14) -> float:
        """Calculate RSI."""
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

    def generate_signal(self, candle: Candle) -> Optional[Dict]:
        """Generate trading signal based on indicators."""
        if len(self.candle_buffer) < 21:
            return None

        signal = None
        close = candle.close

        # Simple crossover strategy
        if self.ema9 > self.ema21 and close > self.vwap and self.rsi < 70:
            # Bullish signal
            if self.rsi < 40:  # Oversold bounce
                signal = {
                    "type": "LONG",
                    "index": self.index,
                    "price": close,
                    "vwap": round(self.vwap, 2),
                    "ema9": round(self.ema9, 2),
                    "ema21": round(self.ema21, 2),
                    "rsi": round(self.rsi, 1),
                    "reason": "EMA bullish + RSI oversold bounce",
                    "confidence": 75,
                    "timestamp": candle.dt.isoformat()
                }
        elif self.ema9 < self.ema21 and close < self.vwap and self.rsi > 30:
            # Bearish signal
            if self.rsi > 60:  # Overbought reversal
                signal = {
                    "type": "SHORT",
                    "index": self.index,
                    "price": close,
                    "vwap": round(self.vwap, 2),
                    "ema9": round(self.ema9, 2),
                    "ema21": round(self.ema21, 2),
                    "rsi": round(self.rsi, 1),
                    "reason": "EMA bearish + RSI overbought reversal",
                    "confidence": 70,
                    "timestamp": candle.dt.isoformat()
                }

        return signal

    async def process_candle(self, candle: Candle, index: int, total: int):
        """Process a single candle."""
        # Calculate indicators
        self.calculate_indicators(candle)

        # Update data agent status
        self.api.update_agent(0, "running", {
            "ltp": candle.close,
            "vwap": round(self.vwap, 2),
            "volume": candle.volume,
            "timestamp": candle.dt.isoformat()
        })

        # Update analysis agents
        regime = "ranging"
        if self.ema21 > 0:
            regime = "trending" if abs(self.ema9 - self.ema21) / self.ema21 > 0.002 else "ranging"
        self.api.update_agent(4, "running", {
            "regime": regime,
            "ema9": round(self.ema9, 2),
            "ema21": round(self.ema21, 2)
        })

        self.api.update_agent(5, "running", {
            "rsi": round(self.rsi, 1),
            "momentum": "bullish" if self.rsi > 50 else "bearish"
        })

        # Generate signal
        signal = self.generate_signal(candle)
        if signal:
            self.signals_generated += 1
            self.api.add_signal(signal)

            # Simulate trade (for demo)
            if self.signals_generated % 3 == 0:  # Every 3rd signal becomes a trade
                self.trades_simulated += 1
                self.api.add_trade({
                    "id": f"BT-{self.trades_simulated:04d}",
                    "index": self.index,
                    "type": signal["type"],
                    "entry": signal["price"],
                    "status": "simulated",
                    "timestamp": signal["timestamp"]
                })

    async def run(self, candles: List[Candle]):
        """Run backtest on all candles."""
        self.running = True
        total = len(candles)

        logger.info(f"Starting backtest: {total} candles, {self.delay}s delay")
        logger.info(f"Index: {self.index}")
        logger.info("-" * 60)

        start_time = time.time()

        for i, candle in enumerate(candles):
            if not self.running:
                break

            await self.process_candle(candle, i, total)

            # Progress log every 30 candles
            if (i + 1) % 30 == 0 or i == total - 1:
                elapsed = time.time() - start_time
                remaining = (total - i - 1) * self.delay
                pct = (i + 1) / total * 100

                logger.info(
                    f"[{i+1}/{total}] {candle.dt.strftime('%H:%M')} | "
                    f"Close: {candle.close:,.2f} | "
                    f"RSI: {self.rsi:.0f} | "
                    f"Signals: {self.signals_generated} | "
                    f"Progress: {pct:.0f}%"
                )

            await asyncio.sleep(self.delay)

        total_time = time.time() - start_time
        logger.info("-" * 60)
        logger.info(f"Backtest complete!")
        logger.info(f"  Candles processed: {total}")
        logger.info(f"  Signals generated: {self.signals_generated}")
        logger.info(f"  Trades simulated: {self.trades_simulated}")
        logger.info(f"  Time elapsed: {total_time/60:.1f} minutes")

    def stop(self):
        """Stop the backtest."""
        self.running = False


async def main():
    parser = argparse.ArgumentParser(description="Backtest Replay - Simulate live market")
    parser.add_argument("--index", default="SENSEX", help="Index (default: SENSEX)")
    parser.add_argument("--days", type=int, default=1, help="Days of history (default: 1)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between candles (default: 1.0s)")
    parser.add_argument("--fast", action="store_true", help="Fast mode (0.1s delay)")

    args = parser.parse_args()
    delay = 0.1 if args.fast else args.delay

    print("""
╔═══════════════════════════════════════════════════════════════╗
║   Backtest Replay - Historical Data Simulation                ║
╠═══════════════════════════════════════════════════════════════╣
║   This replays historical candles through the scalping engine ║
║   View results at: http://localhost:3001/scalping             ║
╚═══════════════════════════════════════════════════════════════╝
    """)

    # Check if scalping API is running
    api = ScalpingAPIClient()
    if not api.get_state():
        logger.error("Scalping API not running. Start with: ./start.sh all")
        return

    # Clear previous backtest data
    api.clear_backtest()
    logger.info("Cleared previous backtest data")

    # Fetch historical data
    fetcher = FyersDataFetcher()
    candles = fetcher.get_historical_candles(args.index, days=args.days)

    if not candles:
        logger.error("No candles fetched. Check your Fyers credentials.")
        return

    # Filter to market hours (9:15 AM - 3:30 PM)
    market_candles = [
        c for c in candles
        if 915 <= c.dt.hour * 100 + c.dt.minute <= 1530
    ]

    logger.info(f"Filtered to {len(market_candles)} market-hours candles")

    if not market_candles:
        logger.error("No candles in market hours")
        return

    # Run backtest
    engine = BacktestEngine(args.index, delay=delay)

    try:
        await engine.run(market_candles)
    except KeyboardInterrupt:
        logger.info("\nStopping backtest...")
        engine.stop()


if __name__ == "__main__":
    asyncio.run(main())

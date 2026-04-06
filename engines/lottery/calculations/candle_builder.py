"""1-minute candle builder from WebSocket spot ticks.

Accumulates real-time ticks into OHLC candles per minute.
At startup, seeds from FYERS historical API for warmup.
Marks data as DEGRADED if WebSocket disconnects.

Usage:
    builder = CandleBuilder(config, symbol)
    builder.warmup(fyers_client)         # seed last 10 candles from REST
    builder.on_tick(ltp, timestamp)      # call on each WS tick
    candle = builder.current_candle      # incomplete current minute
    candles = builder.completed_candles  # last N completed candles
    confirmed = builder.is_candle_confirmed_beyond(trigger_price)
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..config import LotteryConfig

logger = logging.getLogger(__name__)

# Max completed candles to retain
_MAX_CANDLES = 30


@dataclass
class Candle:
    """One-minute OHLC candle."""
    timestamp: datetime          # start of the minute (floor to :00)
    open: float
    high: float
    low: float
    close: float
    tick_count: int = 0
    complete: bool = False       # True once the next minute starts

    @property
    def body(self) -> float:
        """Candle body size (absolute)."""
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        """Full candle range (high - low)."""
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "tick_count": self.tick_count,
            "complete": self.complete,
        }


class CandleBuilder:
    """Builds 1-minute candles from real-time spot ticks.

    Thread-safe for use with WebSocket callback.
    """

    def __init__(self, config: LotteryConfig, symbol: str) -> None:
        self._config = config
        self._symbol = symbol
        self._completed: deque[Candle] = deque(maxlen=_MAX_CANDLES)
        self._current: Optional[Candle] = None
        self._current_minute: Optional[int] = None  # epoch minute
        self._last_tick_time: Optional[datetime] = None
        self._degraded = False
        self._tick_count_total = 0

    @property
    def current_candle(self) -> Optional[Candle]:
        """The in-progress (incomplete) candle for the current minute."""
        return self._current

    @property
    def completed_candles(self) -> list[Candle]:
        """List of completed 1-min candles (oldest first)."""
        return list(self._completed)

    @property
    def last_completed(self) -> Optional[Candle]:
        """Most recently completed candle."""
        return self._completed[-1] if self._completed else None

    @property
    def is_degraded(self) -> bool:
        """True if data quality is degraded (WS disconnect, gap, etc)."""
        return self._degraded

    @property
    def candle_count(self) -> int:
        """Total completed candles available."""
        return len(self._completed)

    @property
    def tick_count(self) -> int:
        return self._tick_count_total

    # ── Tick Processing ────────────────────────────────────────────

    def on_tick(self, ltp: float, timestamp: Optional[datetime] = None) -> None:
        """Process a spot tick and update the current candle.

        Args:
            ltp: Spot price.
            timestamp: Tick timestamp (defaults to now UTC).
        """
        if ltp <= 0:
            return

        now = timestamp or datetime.now(timezone.utc)
        tick_minute = self._epoch_minute(now)
        self._tick_count_total += 1
        self._last_tick_time = now
        self._degraded = False

        # First tick ever
        if self._current is None:
            self._current = Candle(
                timestamp=self._minute_start(tick_minute),
                open=ltp, high=ltp, low=ltp, close=ltp,
                tick_count=1,
            )
            self._current_minute = tick_minute
            return

        # Same minute — update current candle
        if tick_minute == self._current_minute:
            self._current.high = max(self._current.high, ltp)
            self._current.low = min(self._current.low, ltp)
            self._current.close = ltp
            self._current.tick_count += 1
            return

        # New minute — finalize current candle and start new one
        self._current.complete = True
        self._completed.append(self._current)

        # Check for skipped minutes (WS gap)
        gap = tick_minute - self._current_minute
        if gap > 1:
            logger.warning(
                "Candle gap: %d minutes skipped (from %s to %s)",
                gap - 1, self._current.timestamp.isoformat(), now.isoformat(),
            )
            self._degraded = True

        self._current = Candle(
            timestamp=self._minute_start(tick_minute),
            open=ltp, high=ltp, low=ltp, close=ltp,
            tick_count=1,
        )
        self._current_minute = tick_minute

    def mark_degraded(self) -> None:
        """Mark data as degraded (call on WS disconnect)."""
        self._degraded = True
        logger.warning("CandleBuilder marked DEGRADED for %s", self._symbol)

    def mark_recovered(self) -> None:
        """Mark data as recovered (call on WS reconnect)."""
        self._degraded = False
        logger.info("CandleBuilder recovered for %s", self._symbol)

    # ── Warmup from Historical API ─────────────────────────────────

    def warmup(self, fyers_client, fyers_symbol: str, count: int = 10) -> int:
        """Seed candle history from FYERS historical API.

        Args:
            fyers_client: FyersClient instance.
            fyers_symbol: FYERS symbol (e.g., "NSE:NIFTY50-INDEX").
            count: Number of 1-min candles to fetch.

        Returns:
            Number of candles seeded.
        """
        try:
            import math
            now = datetime.now(timezone.utc)
            # Fetch last N+5 minutes of 1-min candles
            range_to = int(now.timestamp())
            range_from = range_to - (count + 5) * 60

            result = fyers_client.history(
                symbol=fyers_symbol,
                resolution="1",
                range_from=range_from,
                range_to=range_to,
            )

            if not result.get("success"):
                logger.warning("Candle warmup failed: %s", result.get("error"))
                return 0

            data = result.get("data", {})
            candles_raw = data.get("candles") or data.get("data", {}).get("candles", [])

            if not candles_raw:
                logger.warning("No candles returned for warmup")
                return 0

            seeded = 0
            for candle in candles_raw[-count:]:
                # FYERS candle format: [timestamp, open, high, low, close, volume]
                if len(candle) < 5:
                    continue
                ts = datetime.fromtimestamp(candle[0], tz=timezone.utc)
                c = Candle(
                    timestamp=ts,
                    open=float(candle[1]),
                    high=float(candle[2]),
                    low=float(candle[3]),
                    close=float(candle[4]),
                    tick_count=0,
                    complete=True,
                )
                self._completed.append(c)
                seeded += 1

            if seeded > 0:
                # Set current minute based on last candle
                last = self._completed[-1]
                self._current_minute = self._epoch_minute(last.timestamp)
                logger.info(
                    "Candle warmup: %d candles seeded for %s (latest=%s close=%.2f)",
                    seeded, self._symbol, last.timestamp.isoformat(), last.close,
                )

            return seeded

        except Exception as e:
            logger.warning("Candle warmup exception: %s", e)
            return 0

    # ── Confirmation Queries ───────────────────────────────────────

    def is_candle_confirmed_beyond(self, trigger_price: float, direction: str = "above") -> bool:
        """Check if the last completed candle closed beyond a trigger.

        Args:
            trigger_price: The price level to check against.
            direction: "above" (CE breakout) or "below" (PE breakout).

        Returns:
            True if last completed candle confirms the breakout.
        """
        last = self.last_completed
        if last is None or not last.complete:
            return False

        if direction == "above":
            return last.close > trigger_price
        else:
            return last.close < trigger_price

    def is_momentum_expanding(self, lookback: int = 3) -> bool:
        """Check if recent candle bodies are expanding (momentum building).

        Args:
            lookback: Number of recent candles to check.

        Returns:
            True if candle bodies show increasing size.
        """
        candles = self.completed_candles
        if len(candles) < lookback:
            return False

        recent = candles[-lookback:]
        bodies = [c.body for c in recent]

        # At least increasing trend in last N candles
        increasing = sum(1 for i in range(1, len(bodies)) if bodies[i] > bodies[i - 1])
        return increasing >= lookback - 1

    def get_recent_range(self, lookback: int = 5) -> Optional[float]:
        """Average range of recent candles (for volatility estimation).

        Args:
            lookback: Number of candles.

        Returns:
            Average high-low range, or None if insufficient data.
        """
        candles = self.completed_candles
        if len(candles) < lookback:
            return None

        recent = candles[-lookback:]
        return sum(c.range for c in recent) / len(recent)

    def to_dict(self) -> dict:
        """Serialize state for API/dashboard."""
        return {
            "symbol": self._symbol,
            "completed_count": len(self._completed),
            "tick_count": self._tick_count_total,
            "degraded": self._degraded,
            "current": self._current.to_dict() if self._current else None,
            "last_completed": self.last_completed.to_dict() if self.last_completed else None,
            "last_tick_time": self._last_tick_time.isoformat() if self._last_tick_time else None,
        }

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _epoch_minute(dt: datetime) -> int:
        """Convert datetime to epoch minute (floor)."""
        return int(dt.timestamp()) // 60

    @staticmethod
    def _minute_start(epoch_minute: int) -> datetime:
        """Convert epoch minute back to datetime (start of minute)."""
        return datetime.fromtimestamp(epoch_minute * 60, tz=timezone.utc)

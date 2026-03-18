"""
Fyers Data Provider - Live and Historical market data for bot army.

Provides:
- Live quotes
- Historical OHLCV data for backtesting
- Regime detection signals (volatility, trend)
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add shared project engine to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from shared_project_engine.auth import FyersClient
    HAS_FYERS = True
except ImportError:
    HAS_FYERS = False
    FyersClient = None

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None


@dataclass
class Candle:
    """Single OHLCV candle."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open


@dataclass
class MarketSnapshot:
    """Current market state."""
    symbol: str
    ltp: float  # Last traded price
    open: float
    high: float
    low: float
    prev_close: float
    volume: int
    change: float
    change_pct: float
    bid: float = 0
    ask: float = 0
    timestamp: str = ""


@dataclass
class RegimeData:
    """Market regime analysis."""
    symbol: str
    regime: str  # trending_up, trending_down, ranging, volatile
    volatility: str  # low, normal, high
    trend_strength: float  # 0-1
    bias: str  # bullish, bearish, neutral
    atr: float = 0
    atr_pct: float = 0
    sma_20: float = 0
    sma_50: float = 0
    rsi: float = 50
    confidence: float = 0.5


class FyersDataProvider:
    """
    Data provider for bot army using Fyers API.

    Provides both live and historical data for:
    - Backtesting strategies
    - Regime detection
    - Risk monitoring
    """

    RESOLUTION_MAP = {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "D": "D",
        "W": "W",
        "M": "M",
    }

    def __init__(self, env_file: Optional[str] = None):
        if not HAS_FYERS:
            raise ImportError("FyersClient not available. Install shared_project_engine.")
        self.client = FyersClient(env_file=env_file)
        self._cache: Dict[str, Tuple[datetime, Any]] = {}
        self._cache_ttl = 60  # seconds

    def get_quote(self, symbol: str) -> Optional[MarketSnapshot]:
        """Get current quote for a symbol."""
        result = self.client.quotes(symbol)

        if not result.get("success"):
            return None

        data = result.get("data", {})
        quotes = data.get("d", []) if isinstance(data, dict) else []

        if not quotes:
            return None

        q = quotes[0].get("v", {})

        return MarketSnapshot(
            symbol=symbol,
            ltp=q.get("lp", 0),
            open=q.get("open_price", 0),
            high=q.get("high_price", 0),
            low=q.get("low_price", 0),
            prev_close=q.get("prev_close_price", 0),
            volume=q.get("volume", 0),
            change=q.get("ch", 0),
            change_pct=q.get("chp", 0),
            bid=q.get("bid", 0),
            ask=q.get("ask", 0),
            timestamp=datetime.now().isoformat(),
        )

    def get_history(
        self,
        symbol: str,
        resolution: str = "5m",
        days: int = 30,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Candle]:
        """
        Get historical OHLCV data.

        Args:
            symbol: Fyers symbol (e.g., "NSE:NIFTY50-INDEX")
            resolution: Candle resolution (1m, 5m, 15m, 30m, 1h, D, W, M)
            days: Number of days of history (ignored if from_date/to_date provided)
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)

        Returns:
            List of Candle objects
        """
        # Convert resolution
        fyers_res = self.RESOLUTION_MAP.get(resolution, resolution)

        # Calculate date range
        if not from_date or not to_date:
            end = datetime.now()
            start = end - timedelta(days=days)
            from_date = start.strftime("%Y-%m-%d")
            to_date = end.strftime("%Y-%m-%d")

        # Check cache
        cache_key = f"{symbol}:{resolution}:{from_date}:{to_date}"
        if cache_key in self._cache:
            cached_time, cached_data = self._cache[cache_key]
            if (datetime.now() - cached_time).total_seconds() < self._cache_ttl:
                return cached_data

        # Fetch from API
        result = self.client.history(
            symbol=symbol,
            resolution=fyers_res,
            from_date=from_date,
            to_date=to_date,
        )

        if not result.get("success"):
            return []

        data = result.get("data", {})
        raw_candles = data.get("candles", []) if isinstance(data, dict) else []

        candles = []
        for c in raw_candles:
            if len(c) >= 6:
                candles.append(Candle(
                    timestamp=int(c[0]),
                    open=float(c[1]),
                    high=float(c[2]),
                    low=float(c[3]),
                    close=float(c[4]),
                    volume=float(c[5]),
                ))

        # Cache
        self._cache[cache_key] = (datetime.now(), candles)

        return candles

    def detect_regime(
        self,
        symbol: str,
        lookback_days: int = 20,
        resolution: str = "D",
    ) -> RegimeData:
        """
        Detect current market regime for a symbol.

        Analyzes:
        - Trend (using SMA crossovers)
        - Volatility (using ATR)
        - Momentum (using RSI)

        Returns:
            RegimeData with regime classification
        """
        candles = self.get_history(
            symbol=symbol,
            resolution=resolution,
            days=lookback_days + 10,  # Extra for indicator warmup
        )

        if len(candles) < 20:
            return RegimeData(
                symbol=symbol,
                regime="unknown",
                volatility="unknown",
                trend_strength=0,
                bias="neutral",
                confidence=0,
            )

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        # Calculate indicators
        sma_20 = self._sma(closes, 20)
        sma_50 = self._sma(closes, min(50, len(closes)))
        atr = self._atr(highs, lows, closes, 14)
        rsi = self._rsi(closes, 14)

        current_price = closes[-1]
        atr_pct = (atr / current_price * 100) if current_price > 0 else 0

        # Determine volatility level
        if atr_pct < 0.8:
            volatility = "low"
        elif atr_pct < 1.5:
            volatility = "normal"
        else:
            volatility = "high"

        # Determine trend
        if sma_20 > sma_50 * 1.005 and current_price > sma_20:
            regime = "trending_up"
            trend_strength = min(1.0, (sma_20 - sma_50) / sma_50 * 20)
            bias = "bullish"
        elif sma_20 < sma_50 * 0.995 and current_price < sma_20:
            regime = "trending_down"
            trend_strength = min(1.0, (sma_50 - sma_20) / sma_50 * 20)
            bias = "bearish"
        elif volatility == "high":
            regime = "volatile"
            trend_strength = 0.2
            bias = "neutral"
        else:
            regime = "ranging"
            trend_strength = 0.3
            bias = "bullish" if rsi > 50 else "bearish" if rsi < 50 else "neutral"

        # Confidence based on data quality and indicator alignment
        confidence = 0.7
        if len(candles) < 30:
            confidence -= 0.2
        if volatility == "high":
            confidence -= 0.1

        return RegimeData(
            symbol=symbol,
            regime=regime,
            volatility=volatility,
            trend_strength=trend_strength,
            bias=bias,
            atr=atr,
            atr_pct=atr_pct,
            sma_20=sma_20,
            sma_50=sma_50,
            rsi=rsi,
            confidence=confidence,
        )

    def get_backtest_data(
        self,
        symbol: str,
        resolution: str = "5m",
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Get data formatted for backtesting.

        Returns dict with:
        - candles: List of Candle objects
        - stats: Summary statistics
        - regime: Current regime
        """
        candles = self.get_history(symbol, resolution, days)

        if not candles:
            return {"candles": [], "stats": {}, "regime": None}

        closes = [c.close for c in candles]
        returns = [(closes[i] - closes[i-1]) / closes[i-1]
                   for i in range(1, len(closes))]

        stats = {
            "candle_count": len(candles),
            "start_date": candles[0].datetime.isoformat() if candles else None,
            "end_date": candles[-1].datetime.isoformat() if candles else None,
            "start_price": candles[0].close if candles else 0,
            "end_price": candles[-1].close if candles else 0,
            "high": max(c.high for c in candles),
            "low": min(c.low for c in candles),
            "total_volume": sum(c.volume for c in candles),
            "avg_return": sum(returns) / len(returns) if returns else 0,
            "volatility": self._std(returns) if returns else 0,
        }

        regime = self.detect_regime(symbol, days)

        return {
            "candles": candles,
            "stats": stats,
            "regime": regime,
        }

    def _sma(self, values: List[float], period: int) -> float:
        """Simple Moving Average."""
        if len(values) < period:
            return sum(values) / len(values) if values else 0
        return sum(values[-period:]) / period

    def _atr(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14,
    ) -> float:
        """Average True Range."""
        if len(highs) < 2:
            return 0

        trs = []
        for i in range(1, len(highs)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            trs.append(tr)

        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 0

        return sum(trs[-period:]) / period

    def _rsi(self, closes: List[float], period: int = 14) -> float:
        """Relative Strength Index."""
        if len(closes) < period + 1:
            return 50

        gains = []
        losses = []

        for i in range(1, len(closes)):
            change = closes[i] - closes[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _std(self, values: List[float]) -> float:
        """Standard deviation."""
        if len(values) < 2:
            return 0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance ** 0.5


# Singleton
_provider: Optional[FyersDataProvider] = None


def get_fyers_data(env_file: Optional[str] = None) -> FyersDataProvider:
    """Get or create Fyers data provider singleton."""
    global _provider
    if _provider is None:
        _provider = FyersDataProvider(env_file=env_file)
    return _provider

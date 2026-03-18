"""
Scalping Indicator Adapter - Bridge between shared_project_engine and Hub V2.

This adapter provides scalping-specific indicators to Hub V2 layers,
using the IndicatorDataAdapter from shared_project_engine.
"""

import time
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from .base_adapter import BaseAdapter, AdapterStatus

# Import from shared_project_engine
import sys
from pathlib import Path

# Add shared_project_engine to path
SHARED_ENGINE_PATH = Path(__file__).resolve().parents[3] / "shared_project_engine"
if str(SHARED_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(SHARED_ENGINE_PATH))

try:
    from shared_project_engine.market.indicator_adapter import (
        IndicatorDataAdapter,
        IndicatorSnapshot,
    )
    HAS_INDICATOR_ADAPTER = True
except ImportError:
    HAS_INDICATOR_ADAPTER = False
    IndicatorDataAdapter = None
    IndicatorSnapshot = None

logger = logging.getLogger(__name__)


@dataclass
class SignalCooldown:
    """
    Track signal cooldown to prevent overtrading.

    Implements:
    - min_signal_gap_seconds: Minimum time between signals
    - max_trades_per_hour: Maximum trades allowed per hour
    """
    min_signal_gap_seconds: int = 30
    max_trades_per_hour: int = 10

    _last_signal_time: float = field(default=0.0, repr=False)
    _hourly_signals: List[float] = field(default_factory=list, repr=False)

    def can_signal(self) -> bool:
        """Check if a new signal is allowed."""
        now = time.time()

        # Check minimum gap
        if now - self._last_signal_time < self.min_signal_gap_seconds:
            return False

        # Check hourly limit
        hour_ago = now - 3600
        self._hourly_signals = [t for t in self._hourly_signals if t > hour_ago]

        if len(self._hourly_signals) >= self.max_trades_per_hour:
            return False

        return True

    def record_signal(self):
        """Record that a signal was generated."""
        now = time.time()
        self._last_signal_time = now
        self._hourly_signals.append(now)

    def get_cooldown_remaining(self) -> float:
        """Get seconds remaining in cooldown."""
        elapsed = time.time() - self._last_signal_time
        remaining = self.min_signal_gap_seconds - elapsed
        return max(0, remaining)

    def get_hourly_remaining(self) -> int:
        """Get remaining signals allowed this hour."""
        hour_ago = time.time() - 3600
        self._hourly_signals = [t for t in self._hourly_signals if t > hour_ago]
        return max(0, self.max_trades_per_hour - len(self._hourly_signals))


class ScalpingIndicatorAdapter(BaseAdapter):
    """
    Adapter that provides scalping indicators to Hub V2.

    Uses IndicatorDataAdapter from shared_project_engine for calculations
    and adds Hub V2 specific features like signal cooldown.
    """

    SUPPORTED_INDICES = ["SENSEX", "NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

    def __init__(
        self,
        min_signal_gap: int = 30,
        max_trades_per_hour: int = 10,
    ):
        """
        Initialize the adapter.

        Args:
            min_signal_gap: Minimum seconds between signals
            max_trades_per_hour: Maximum signals per hour
        """
        if not HAS_INDICATOR_ADAPTER:
            raise ImportError(
                "IndicatorDataAdapter not found. "
                "Ensure shared_project_engine is installed."
            )

        self._indicator_adapter = IndicatorDataAdapter()
        self._cooldowns: Dict[str, SignalCooldown] = {}
        self._last_update: Dict[str, float] = {}
        self._min_signal_gap = min_signal_gap
        self._max_trades_per_hour = max_trades_per_hour

        logger.info(
            f"ScalpingIndicatorAdapter initialized: "
            f"gap={min_signal_gap}s, max={max_trades_per_hour}/hr"
        )

    @property
    def name(self) -> str:
        return "scalping_indicators"

    @property
    def supported_indices(self) -> List[str]:
        return self.SUPPORTED_INDICES

    def _get_cooldown(self, index: str) -> SignalCooldown:
        """Get or create cooldown tracker for an index."""
        if index not in self._cooldowns:
            self._cooldowns[index] = SignalCooldown(
                min_signal_gap_seconds=self._min_signal_gap,
                max_trades_per_hour=self._max_trades_per_hour,
            )
        return self._cooldowns[index]

    def get_status(self) -> AdapterStatus:
        """Get adapter status."""
        stats = self._indicator_adapter.get_stats()
        return AdapterStatus(
            connected=True,
            last_update=max(self._last_update.values()) if self._last_update else None,
            latency_ms=0,
            error=None,
        )

    def get_quote(self, symbol: str, **kwargs) -> Dict[str, Any]:
        """Get real-time quote for a symbol."""
        # Delegate to indicator adapter's underlying market adapter
        snapshot = self._indicator_adapter.get_indicator_snapshot(
            index_name=symbol,
            include_history=False,
        )
        return {
            "ltp": snapshot.ltp,
            "change_pct": snapshot.change_pct,
            "timestamp": snapshot.timestamp,
        }

    def get_quotes(self, symbols: List[str], **kwargs) -> Dict[str, Any]:
        """Get quotes for multiple symbols."""
        results = {}
        for symbol in symbols:
            results[symbol] = self.get_quote(symbol)
        return {"success": True, "data": results}

    def get_history(
        self,
        symbol: str,
        resolution: str = "5",
        lookback_days: int = 5,
        **kwargs
    ) -> Dict[str, Any]:
        """Get historical candles (not directly supported - use market adapter)."""
        return {"candles": [], "message": "Use MarketDataAdapter for history"}

    def get_option_chain(
        self,
        symbol: str,
        strike_count: int = 10,
        **kwargs
    ) -> Dict[str, Any]:
        """Get option chain data with indicators."""
        snapshot = self._indicator_adapter.get_indicator_snapshot(
            index_name=symbol,
            strike_count=strike_count,
        )
        return {
            "atm_strike": snapshot.atm_strike,
            "strikes": snapshot.strikes_data,
            "greeks": snapshot.greeks.to_dict() if hasattr(snapshot.greeks, 'to_dict') else {},
            "option_flow": snapshot.option_flow.to_dict() if hasattr(snapshot.option_flow, 'to_dict') else {},
        }

    def get_index_data(
        self,
        index_name: str,
        include_history: bool = True,
        include_options: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """Get comprehensive index data with all indicators."""
        return self.get_scalping_snapshot(index_name)

    def get_scalping_snapshot(
        self,
        index_name: str,
        strike_count: int = 10,
    ) -> Dict[str, Any]:
        """
        Get complete scalping snapshot with all indicators.

        This is the main method for Hub V2 consumption.

        Args:
            index_name: Index name
            strike_count: Number of strikes to include

        Returns:
            Complete indicator snapshot as dictionary
        """
        index_name = index_name.upper()

        # Get indicator snapshot
        snapshot = self._indicator_adapter.get_indicator_snapshot(
            index_name=index_name,
            strike_count=strike_count,
            include_history=True,
        )

        # Update last update time
        self._last_update[index_name] = time.time()

        # Get cooldown info
        cooldown = self._get_cooldown(index_name)

        # Convert to dict and add Hub V2 specific fields
        result = snapshot.to_dict()
        result["cooldown"] = {
            "can_signal": cooldown.can_signal(),
            "seconds_remaining": cooldown.get_cooldown_remaining(),
            "hourly_remaining": cooldown.get_hourly_remaining(),
        }

        return result

    def get_scalping_signal(
        self,
        index_name: str,
        min_momentum: float = 0.05,
        min_volume_accel: float = 1.2,
        max_spread_pct: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Get scalping signal with cooldown protection.

        Args:
            index_name: Index name
            min_momentum: Minimum momentum threshold
            min_volume_accel: Minimum volume acceleration
            max_spread_pct: Maximum acceptable spread

        Returns:
            Signal dict with direction, confidence, and reasons
        """
        index_name = index_name.upper()
        cooldown = self._get_cooldown(index_name)

        # Check cooldown first
        if not cooldown.can_signal():
            return {
                "index": index_name,
                "signal": "COOLDOWN",
                "confidence": 0,
                "reasons": [],
                "warnings": [
                    f"Cooldown: {cooldown.get_cooldown_remaining():.0f}s remaining",
                    f"Hourly limit: {cooldown.get_hourly_remaining()} remaining",
                ],
            }

        # Get signal from indicator adapter
        signal = self._indicator_adapter.get_scalping_signals(
            index_name=index_name,
            min_momentum=min_momentum,
            min_volume_accel=min_volume_accel,
            max_spread_pct=max_spread_pct,
        )

        # Record signal if it's actionable
        if signal.get("signal") in ("LONG", "SHORT"):
            cooldown.record_signal()

        return signal

    def get_all_snapshots(self) -> Dict[str, Dict]:
        """Get snapshots for all supported indices."""
        results = {}
        for index in self.SUPPORTED_INDICES:
            try:
                results[index] = self.get_scalping_snapshot(index)
            except Exception as e:
                logger.error(f"Failed to get snapshot for {index}: {e}")
                results[index] = {"error": str(e)}
        return results

    def reset_cooldown(self, index_name: str):
        """Reset cooldown for an index (use with caution)."""
        index_name = index_name.upper()
        if index_name in self._cooldowns:
            self._cooldowns[index_name] = SignalCooldown(
                min_signal_gap_seconds=self._min_signal_gap,
                max_trades_per_hour=self._max_trades_per_hour,
            )
            logger.info(f"Reset cooldown for {index_name}")

    def get_stats(self) -> Dict[str, Any]:
        """Get adapter statistics."""
        return {
            "name": self.name,
            "supported_indices": self.SUPPORTED_INDICES,
            "tracked_indices": list(self._last_update.keys()),
            "cooldowns": {
                idx: {
                    "can_signal": cd.can_signal(),
                    "hourly_remaining": cd.get_hourly_remaining(),
                }
                for idx, cd in self._cooldowns.items()
            },
            "indicator_adapter_stats": self._indicator_adapter.get_stats(),
        }

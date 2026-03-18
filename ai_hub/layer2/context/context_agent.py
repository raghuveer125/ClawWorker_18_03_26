"""
Context Agent - Retrieves relevant data from Layer 0 for reasoning.

Gathers market data, indicators, and computed values needed for
chain-of-thought reasoning about goals and tasks.
"""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ContextSnapshot:
    """Point-in-time context for reasoning."""
    timestamp: float
    market_data: Dict[str, Any] = field(default_factory=dict)
    indicators: Dict[str, Any] = field(default_factory=dict)
    positions: List[Dict] = field(default_factory=list)
    recent_trades: List[Dict] = field(default_factory=list)
    regime: Optional[str] = None
    volatility_state: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "market_data": self.market_data,
            "indicators": self.indicators,
            "positions": self.positions,
            "recent_trades": self.recent_trades,
            "regime": self.regime,
            "volatility_state": self.volatility_state,
            "metadata": self.metadata,
        }


class ContextAgent:
    """
    Retrieves and assembles context from Layer 0 for reasoning.

    Responsibilities:
    - Fetch current market state from DataPipe
    - Gather relevant indicators for goal type
    - Track position and recent trade context
    - Detect market regime and volatility state
    """

    AGENT_TYPE = "context"

    # Indicators needed by goal type
    GOAL_INDICATORS = {
        "trade": ["vwap", "atr", "fvg_zones", "spread", "order_imbalance"],
        "optimize": ["atr", "volume_profile"],
        "analyze": ["vwap", "atr", "fvg_zones", "volume_profile"],
        "learn": ["volume_profile", "order_imbalance"],
        "monitor": ["spread", "order_imbalance"],
        "improve": [],
    }

    def __init__(self, data_pipe=None):
        """
        Initialize context agent.

        Args:
            data_pipe: Layer 0 DataPipe instance (optional, will use singleton)
        """
        self.data_pipe = data_pipe
        self._cache: Dict[str, ContextSnapshot] = {}
        self._cache_ttl = 1.0  # seconds

    def _get_data_pipe(self):
        """Lazy load data pipe."""
        if self.data_pipe is None:
            try:
                from ...layer0.pipe.data_pipe import get_data_pipe
                self.data_pipe = get_data_pipe()
            except ImportError:
                logger.warning("DataPipe not available")
        return self.data_pipe

    async def get_context(
        self,
        targets: List[str],
        goal_type: str = "analyze",
        include_positions: bool = True,
        include_trades: bool = False,
    ) -> ContextSnapshot:
        """
        Get context snapshot for reasoning.

        Args:
            targets: List of symbols/indices to get context for
            goal_type: Type of goal (determines which indicators to fetch)
            include_positions: Include current positions
            include_trades: Include recent trade history

        Returns:
            ContextSnapshot with assembled data
        """
        import time
        timestamp = time.time()

        # Check cache
        cache_key = f"{','.join(targets)}:{goal_type}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if timestamp - cached.timestamp < self._cache_ttl:
                return cached

        # Get required indicators for goal type
        required_indicators = self.GOAL_INDICATORS.get(goal_type, [])

        # Fetch market data
        market_data = await self._fetch_market_data(targets, required_indicators)

        # Extract indicators
        indicators = self._extract_indicators(market_data, required_indicators)

        # Get positions if needed
        positions = []
        if include_positions:
            positions = await self._fetch_positions(targets)

        # Get recent trades if needed
        recent_trades = []
        if include_trades:
            recent_trades = await self._fetch_recent_trades(targets)

        # Detect regime
        regime = self._detect_regime(market_data, indicators)

        # Detect volatility state
        volatility_state = self._detect_volatility(indicators)

        snapshot = ContextSnapshot(
            timestamp=timestamp,
            market_data=market_data,
            indicators=indicators,
            positions=positions,
            recent_trades=recent_trades,
            regime=regime,
            volatility_state=volatility_state,
            metadata={
                "targets": targets,
                "goal_type": goal_type,
                "indicators_requested": required_indicators,
            }
        )

        # Cache it
        self._cache[cache_key] = snapshot

        logger.info(
            f"Context assembled for {targets}: regime={regime}, "
            f"volatility={volatility_state}, indicators={len(indicators)}"
        )

        return snapshot

    async def _fetch_market_data(
        self,
        targets: List[str],
        indicators: List[str]
    ) -> Dict[str, Any]:
        """Fetch market data from Layer 0."""
        data_pipe = self._get_data_pipe()
        if not data_pipe:
            return self._get_mock_market_data(targets)

        result = {}
        for target in targets:
            try:
                # Subscribe and get latest data
                data = data_pipe.get_latest(target)
                if data:
                    result[target] = data
            except Exception as e:
                logger.error(f"Error fetching data for {target}: {e}")

        return result if result else self._get_mock_market_data(targets)

    def _get_mock_market_data(self, targets: List[str]) -> Dict[str, Any]:
        """Mock data for testing without Layer 0."""
        import random
        mock = {}
        for target in targets:
            base_price = 22000 if "NIFTY" in target else 48000
            mock[target] = {
                "symbol": target,
                "ltp": base_price + random.uniform(-100, 100),
                "open": base_price,
                "high": base_price + random.uniform(50, 150),
                "low": base_price - random.uniform(50, 150),
                "close": base_price + random.uniform(-50, 50),
                "volume": random.randint(100000, 500000),
                "change_pct": random.uniform(-1, 1),
            }
        return mock

    def _extract_indicators(
        self,
        market_data: Dict[str, Any],
        required: List[str]
    ) -> Dict[str, Any]:
        """Extract indicator values from market data."""
        indicators = {}

        for symbol, data in market_data.items():
            symbol_indicators = {}
            for indicator in required:
                # Check if indicator is in enriched data
                if indicator in data:
                    symbol_indicators[indicator] = data[indicator]
                elif f"{indicator}_data" in data:
                    symbol_indicators[indicator] = data[f"{indicator}_data"]

            if symbol_indicators:
                indicators[symbol] = symbol_indicators

        return indicators

    async def _fetch_positions(self, targets: List[str]) -> List[Dict]:
        """Fetch current positions."""
        # Would integrate with position manager
        # For now return empty
        return []

    async def _fetch_recent_trades(self, targets: List[str]) -> List[Dict]:
        """Fetch recent trades."""
        # Would integrate with trade history
        return []

    def _detect_regime(
        self,
        market_data: Dict[str, Any],
        indicators: Dict[str, Any]
    ) -> str:
        """
        Detect current market regime.

        Returns: 'trending_up', 'trending_down', 'ranging', 'volatile'
        """
        for symbol, data in market_data.items():
            change_pct = data.get("change_pct", 0)

            # Check VWAP if available
            symbol_indicators = indicators.get(symbol, {})
            vwap = symbol_indicators.get("vwap", {})

            if isinstance(vwap, dict):
                vwap_value = vwap.get("value", data.get("ltp", 0))
            else:
                vwap_value = data.get("ltp", 0)

            ltp = data.get("ltp", 0)

            # Simple regime detection
            if abs(change_pct) > 1.5:
                return "trending_up" if change_pct > 0 else "trending_down"
            elif ltp > vwap_value * 1.002:
                return "trending_up"
            elif ltp < vwap_value * 0.998:
                return "trending_down"

        return "ranging"

    def _detect_volatility(self, indicators: Dict[str, Any]) -> str:
        """
        Detect volatility state.

        Returns: 'low', 'normal', 'high', 'extreme'
        """
        for symbol, ind in indicators.items():
            atr = ind.get("atr", {})
            if isinstance(atr, dict):
                atr_value = atr.get("value", 0)
                atr_pct = atr.get("percentage", 0)
            else:
                atr_pct = 0

            if atr_pct > 2.0:
                return "extreme"
            elif atr_pct > 1.2:
                return "high"
            elif atr_pct < 0.5:
                return "low"

        return "normal"

    def get_context_summary(self, snapshot: ContextSnapshot) -> str:
        """Create human-readable summary of context."""
        lines = [
            f"Market Context @ {datetime.fromtimestamp(snapshot.timestamp).strftime('%H:%M:%S')}",
            f"Regime: {snapshot.regime or 'unknown'}",
            f"Volatility: {snapshot.volatility_state or 'unknown'}",
        ]

        for symbol, data in snapshot.market_data.items():
            ltp = data.get("ltp", 0)
            change = data.get("change_pct", 0)
            lines.append(f"  {symbol}: {ltp:.2f} ({change:+.2f}%)")

        if snapshot.positions:
            lines.append(f"Open Positions: {len(snapshot.positions)}")

        return "\n".join(lines)

    def clear_cache(self):
        """Clear context cache."""
        self._cache.clear()

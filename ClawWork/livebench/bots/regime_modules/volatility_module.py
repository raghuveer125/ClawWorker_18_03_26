"""
Volatility Module for Regime Hunter Pipeline

Analyzes market volatility using:
- VIX (India VIX)
- Intraday Range %
- ATR (Average True Range)
- IV levels from options

Answers: "How risky is the market right now?"
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .base_module import BaseModule, ModuleOutput


class VolatilityLevel(Enum):
    """Volatility classification"""
    EXTREME = "EXTREME"  # VIX >= 25 or Range >= 2.0%
    HIGH = "HIGH"        # VIX >= 18 or Range >= 1.5%
    NORMAL = "NORMAL"    # VIX >= 14 or Range >= 0.8%
    LOW = "LOW"          # Below normal thresholds
    COMPRESSED = "COMPRESSED"  # Very low vol, potential breakout


@dataclass
class VolatilityOutput(ModuleOutput):
    """Output specific to Volatility Module"""
    level: VolatilityLevel
    vix: float
    range_pct: float
    risk_multiplier: float  # Suggested position size multiplier
    warning: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["level"] = self.level.value
        return d


class VolatilityModule(BaseModule):
    """
    Volatility Analysis Module

    Thresholds (can be overridden per index):
    - VIX_EXTREME: 25
    - VIX_HIGH: 18
    - VIX_NORMAL: 14
    - RANGE_EXTREME: 2.0%
    - RANGE_HIGH: 1.5%
    - RANGE_NORMAL: 0.8%
    """

    # Default thresholds
    DEFAULT_THRESHOLDS = {
        "vix_extreme": 25,
        "vix_high": 18,
        "vix_normal": 14,
        "vix_compressed": 11,
        "range_extreme": 2.0,
        "range_high": 1.5,
        "range_normal": 0.8,
        "range_compressed": 0.4,
    }

    # Index-specific default adjustments
    INDEX_ADJUSTMENTS = {
        "BANKNIFTY": {
            "vix_extreme": 28,  # BANKNIFTY tolerates higher vol
            "range_extreme": 2.5,
            "range_high": 1.8,
        },
        "FINNIFTY": {
            "vix_extreme": 26,
            "range_extreme": 2.2,
        },
        "SENSEX": {
            "vix_extreme": 24,  # SENSEX is typically calmer
            "range_extreme": 1.8,
        },
    }

    def __init__(self, data_dir: str = None):
        super().__init__(
            name="volatility_module",
            description="Analyzes VIX, Range%, and ATR for risk assessment",
            data_dir=data_dir
        )

        # Apply default index adjustments if not already configured
        for index, adjustments in self.INDEX_ADJUSTMENTS.items():
            if index not in self.config.index_overrides:
                self.config.index_overrides[index] = adjustments
        self.save_config()

    def _get_threshold(self, param: str, index: str) -> float:
        """Get threshold for parameter, checking index overrides"""
        return self.config.get_threshold(
            param,
            index,
            self.DEFAULT_THRESHOLDS.get(param, 0)
        )

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        historical_data: Optional[List[Dict]] = None
    ) -> VolatilityOutput:
        """
        Analyze volatility conditions

        Args:
            index: Index name
            market_data: Current market data with vix, ltp, high, low
            historical_data: Recent candles for ATR calculation

        Returns:
            VolatilityOutput with level and risk multiplier
        """
        # Extract data
        vix = market_data.get("vix", market_data.get("india_vix", 15))
        ltp = market_data.get("ltp", 0)
        high = market_data.get("high", ltp)
        low = market_data.get("low", ltp)

        # Calculate range %
        range_pct = ((high - low) / ltp * 100) if ltp > 0 else 0

        # Calculate ATR if historical data available
        atr = self._calculate_atr(historical_data) if historical_data else 0
        atr_pct = (atr / ltp * 100) if ltp > 0 and atr > 0 else 0

        # Get index-specific thresholds
        vix_extreme = self._get_threshold("vix_extreme", index)
        vix_high = self._get_threshold("vix_high", index)
        vix_normal = self._get_threshold("vix_normal", index)
        vix_compressed = self._get_threshold("vix_compressed", index)
        range_extreme = self._get_threshold("range_extreme", index)
        range_high = self._get_threshold("range_high", index)
        range_normal = self._get_threshold("range_normal", index)
        range_compressed = self._get_threshold("range_compressed", index)

        # Classify volatility level
        level, warning = self._classify_volatility(
            vix, range_pct, atr_pct,
            vix_extreme, vix_high, vix_normal, vix_compressed,
            range_extreme, range_high, range_normal, range_compressed
        )

        # Calculate risk multiplier based on volatility
        risk_multiplier = self._calculate_risk_multiplier(level)

        # Generate recommendation
        recommendation = self._generate_recommendation(level, vix, range_pct)

        # Build output
        output = VolatilityOutput(
            module_name=self.name,
            timestamp=datetime.now().isoformat(),
            index=index,
            confidence=self._calculate_confidence(vix, range_pct, level),
            factors={
                "vix": round(vix, 2),
                "range_pct": round(range_pct, 2),
                "atr_pct": round(atr_pct, 2),
                "high": high,
                "low": low,
                "ltp": ltp,
                "thresholds": {
                    "vix_extreme": vix_extreme,
                    "vix_high": vix_high,
                    "range_extreme": range_extreme,
                },
            },
            recommendation=recommendation,
            level=level,
            vix=round(vix, 2),
            range_pct=round(range_pct, 2),
            risk_multiplier=risk_multiplier,
            warning=warning,
        )

        # Store in history
        self.signal_history.append(output)
        if len(self.signal_history) > 100:
            self.signal_history.pop(0)

        return output

    def _classify_volatility(
        self,
        vix: float,
        range_pct: float,
        atr_pct: float,
        vix_extreme: float,
        vix_high: float,
        vix_normal: float,
        vix_compressed: float,
        range_extreme: float,
        range_high: float,
        range_normal: float,
        range_compressed: float
    ) -> tuple[VolatilityLevel, Optional[str]]:
        """Classify volatility level with optional warning"""
        warning = None

        # EXTREME: Either VIX or Range is extreme
        if vix >= vix_extreme or range_pct >= range_extreme:
            if vix >= vix_extreme and range_pct >= range_extreme:
                warning = "DANGER: Both VIX and Range at extreme levels!"
            return VolatilityLevel.EXTREME, warning

        # HIGH: Either VIX or Range is high
        if vix >= vix_high or range_pct >= range_high:
            if vix >= vix_high and range_pct < range_normal:
                warning = "VIX elevated but range compressed - potential move brewing"
            return VolatilityLevel.HIGH, warning

        # COMPRESSED: Very low volatility - potential breakout setup
        if vix <= vix_compressed and range_pct <= range_compressed:
            warning = "Volatility compressed - breakout expected"
            return VolatilityLevel.COMPRESSED, warning

        # NORMAL: Within normal ranges
        if vix >= vix_normal or range_pct >= range_normal:
            return VolatilityLevel.NORMAL, warning

        # LOW: Below normal
        return VolatilityLevel.LOW, warning

    def _calculate_risk_multiplier(self, level: VolatilityLevel) -> float:
        """Calculate position size multiplier based on volatility"""
        multipliers = {
            VolatilityLevel.EXTREME: 0.25,     # Quarter size
            VolatilityLevel.HIGH: 0.5,         # Half size
            VolatilityLevel.NORMAL: 1.0,       # Full size
            VolatilityLevel.LOW: 1.2,          # Slightly larger
            VolatilityLevel.COMPRESSED: 0.8,   # Reduced - breakout risk
        }
        return multipliers.get(level, 1.0)

    def _calculate_confidence(
        self,
        vix: float,
        range_pct: float,
        level: VolatilityLevel
    ) -> float:
        """Calculate confidence in volatility assessment"""
        # Higher confidence when signals are clear
        if level == VolatilityLevel.EXTREME:
            # Very clear signal
            return min(95, 70 + vix + range_pct * 5)
        elif level == VolatilityLevel.COMPRESSED:
            # Clear compressed signal
            return min(90, 60 + (20 - vix) * 2)
        elif level in [VolatilityLevel.HIGH, VolatilityLevel.LOW]:
            return 70
        else:
            # Normal - less certainty about direction
            return 60

    def _calculate_atr(self, historical_data: List[Dict], period: int = 14) -> float:
        """Calculate Average True Range from historical data"""
        if not historical_data or len(historical_data) < 2:
            return 0

        true_ranges = []
        for i in range(1, min(period + 1, len(historical_data))):
            curr = historical_data[i]
            prev = historical_data[i - 1]

            high = curr.get("high", curr.get("ltp", 0))
            low = curr.get("low", curr.get("ltp", 0))
            prev_close = prev.get("close", prev.get("ltp", 0))

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)

        return sum(true_ranges) / len(true_ranges) if true_ranges else 0

    def _generate_recommendation(
        self,
        level: VolatilityLevel,
        vix: float,
        range_pct: float
    ) -> str:
        """Generate actionable recommendation"""
        recommendations = {
            VolatilityLevel.EXTREME: (
                f"EXTREME VOLATILITY (VIX: {vix:.1f}, Range: {range_pct:.1f}%). "
                "Reduce position size by 75%. Consider staying flat. "
                "If trading, use wider stops and smaller targets."
            ),
            VolatilityLevel.HIGH: (
                f"HIGH VOLATILITY (VIX: {vix:.1f}, Range: {range_pct:.1f}%). "
                "Reduce position size by 50%. Use wider stops. "
                "Good for momentum plays, avoid mean reversion."
            ),
            VolatilityLevel.NORMAL: (
                f"NORMAL VOLATILITY (VIX: {vix:.1f}, Range: {range_pct:.1f}%). "
                "Standard position sizing. All strategies valid."
            ),
            VolatilityLevel.LOW: (
                f"LOW VOLATILITY (VIX: {vix:.1f}, Range: {range_pct:.1f}%). "
                "Can increase position size slightly. "
                "Good for range trading, watch for breakout setups."
            ),
            VolatilityLevel.COMPRESSED: (
                f"COMPRESSED VOLATILITY (VIX: {vix:.1f}, Range: {range_pct:.1f}%). "
                "Breakout imminent! Reduce size, prepare for expansion. "
                "Look for consolidation breakout entries."
            ),
        }
        return recommendations.get(level, "Unable to assess volatility")

    def validate(
        self,
        output: ModuleOutput,
        actual_outcome: Dict[str, Any]
    ) -> bool:
        """
        Validate if volatility assessment was correct

        Args:
            output: Previous volatility output
            actual_outcome: What happened next (e.g., next candle data)

        Returns:
            True if risk assessment was appropriate
        """
        if not isinstance(output, VolatilityOutput):
            return False

        actual_range = actual_outcome.get("range_pct", 0)
        predicted_level = output.level

        # Check if risk multiplier suggestion was appropriate
        if predicted_level == VolatilityLevel.EXTREME:
            # Should have seen high movement
            return actual_range >= 1.5
        elif predicted_level == VolatilityLevel.COMPRESSED:
            # Should have seen breakout
            return actual_range >= 1.0
        elif predicted_level in [VolatilityLevel.NORMAL, VolatilityLevel.LOW]:
            # Should not have seen extreme movement
            return actual_range < 2.0

        return True

    def get_risk_for_trade(self, index: str, market_data: Dict) -> Dict[str, Any]:
        """
        Quick method to get risk assessment for a trade

        Returns dict with risk_multiplier and warnings
        """
        output = self.analyze(index, market_data)
        return {
            "level": output.level.value,
            "risk_multiplier": output.risk_multiplier,
            "warning": output.warning,
            "vix": output.vix,
            "range_pct": output.range_pct,
            "recommendation": output.recommendation,
        }

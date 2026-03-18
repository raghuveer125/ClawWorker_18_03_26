"""
Trend Module for Regime Hunter Pipeline

Analyzes price action and momentum using:
- Price change %
- Candle body analysis
- Higher highs / Lower lows
- Moving average relationships
- Momentum indicators

Answers: "Where is price going and how strong is the move?"
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .base_module import BaseModule, ModuleOutput


class TrendDirection(Enum):
    """Trend direction classification"""
    STRONG_UP = "STRONG_UP"       # Clear uptrend, strong momentum
    UP = "UP"                      # Uptrend
    SIDEWAYS = "SIDEWAYS"         # Ranging / consolidation
    DOWN = "DOWN"                  # Downtrend
    STRONG_DOWN = "STRONG_DOWN"   # Clear downtrend, strong momentum


class TrendPhase(Enum):
    """Current phase in trend"""
    IMPULSE = "IMPULSE"           # Strong directional move
    CORRECTION = "CORRECTION"     # Pullback in trend
    CONSOLIDATION = "CONSOLIDATION"  # Tight range
    BREAKOUT = "BREAKOUT"         # Breaking out of range
    EXHAUSTION = "EXHAUSTION"     # Trend losing steam


@dataclass
class TrendOutput(ModuleOutput):
    """Output specific to Trend Module"""
    direction: TrendDirection
    strength: float  # 0-100
    phase: TrendPhase
    momentum: float  # Positive = bullish momentum
    support: float
    resistance: float
    trend_quality: str  # CLEAN, CHOPPY, MIXED

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["direction"] = self.direction.value
        d["phase"] = self.phase.value
        return d


class TrendModule(BaseModule):
    """
    Trend Analysis Module

    Analyzes price action to determine trend direction and strength.

    Thresholds (can be overridden per index):
    - STRONG_TREND_CHANGE: 1.0%
    - TREND_CHANGE: 0.5%
    - SIDEWAYS_RANGE: 0.3%
    """

    # Default thresholds
    DEFAULT_THRESHOLDS = {
        "strong_trend_change": 1.0,    # % change for strong trend
        "trend_change": 0.5,           # % change for trend
        "sideways_range": 0.3,         # % change considered sideways
        "body_ratio_strong": 0.7,      # Body/Range ratio for strong candle
        "body_ratio_weak": 0.3,        # Body/Range ratio for weak candle
        "momentum_lookback": 5,        # Candles to check for momentum
    }

    # Index-specific adjustments
    INDEX_ADJUSTMENTS = {
        "BANKNIFTY": {
            "strong_trend_change": 1.2,  # BANKNIFTY moves more
            "trend_change": 0.6,
        },
        "NIFTY50": {
            "strong_trend_change": 0.8,  # NIFTY moves less
            "trend_change": 0.4,
        },
        "SENSEX": {
            "strong_trend_change": 0.7,
            "trend_change": 0.35,
        },
        "MIDCPNIFTY": {
            "strong_trend_change": 1.5,  # More volatile
            "trend_change": 0.8,
        },
    }

    def __init__(self, data_dir: str = None):
        super().__init__(
            name="trend_module",
            description="Analyzes price action and momentum for trend direction",
            data_dir=data_dir
        )

        # Apply default index adjustments if not configured
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
    ) -> TrendOutput:
        """
        Analyze trend direction and strength

        Args:
            index: Index name
            market_data: Current market data with OHLC
            historical_data: Recent candles for pattern analysis

        Returns:
            TrendOutput with direction, strength, and phase
        """
        # Extract current data
        ltp = market_data.get("ltp", 0)
        open_price = market_data.get("open", ltp)
        high = market_data.get("high", ltp)
        low = market_data.get("low", ltp)
        change_pct = market_data.get("change_pct", 0)
        prev_close = market_data.get("prev_close", open_price)

        # Calculate derived metrics
        range_pts = high - low
        body_pts = abs(ltp - open_price)
        body_ratio = (body_pts / range_pts) if range_pts > 0 else 0
        range_pct = (range_pts / ltp * 100) if ltp > 0 else 0

        # Get thresholds
        strong_trend = self._get_threshold("strong_trend_change", index)
        trend_change = self._get_threshold("trend_change", index)
        sideways = self._get_threshold("sideways_range", index)
        body_strong = self._get_threshold("body_ratio_strong", index)

        # Analyze trend direction
        direction = self._classify_direction(
            change_pct, body_ratio,
            strong_trend, trend_change, sideways, body_strong
        )

        # Calculate trend strength
        strength = self._calculate_strength(
            change_pct, body_ratio, range_pct, historical_data
        )

        # Detect trend phase
        phase = self._detect_phase(
            direction, strength, range_pct, historical_data
        )

        # Calculate momentum
        momentum = self._calculate_momentum(change_pct, historical_data)

        # Calculate support/resistance
        support, resistance = self._calculate_sr_levels(
            ltp, high, low, historical_data
        )

        # Assess trend quality
        trend_quality = self._assess_trend_quality(
            direction, strength, body_ratio, historical_data
        )

        # Generate recommendation
        recommendation = self._generate_recommendation(
            direction, strength, phase, momentum, trend_quality
        )

        # Calculate confidence
        confidence = self._calculate_confidence(
            direction, strength, phase, body_ratio
        )

        output = TrendOutput(
            module_name=self.name,
            timestamp=datetime.now().isoformat(),
            index=index,
            confidence=confidence,
            factors={
                "change_pct": round(change_pct, 2),
                "range_pct": round(range_pct, 2),
                "body_ratio": round(body_ratio, 2),
                "ltp": ltp,
                "open": open_price,
                "high": high,
                "low": low,
                "thresholds": {
                    "strong_trend": strong_trend,
                    "trend_change": trend_change,
                    "sideways": sideways,
                },
            },
            recommendation=recommendation,
            direction=direction,
            strength=strength,
            phase=phase,
            momentum=round(momentum, 2),
            support=round(support, 2),
            resistance=round(resistance, 2),
            trend_quality=trend_quality,
        )

        # Store in history
        self.signal_history.append(output)
        if len(self.signal_history) > 100:
            self.signal_history.pop(0)

        return output

    def _classify_direction(
        self,
        change_pct: float,
        body_ratio: float,
        strong_trend: float,
        trend_change: float,
        sideways: float,
        body_strong: float
    ) -> TrendDirection:
        """Classify trend direction"""
        # Strong trends need both change and body
        if change_pct >= strong_trend and body_ratio >= body_strong:
            return TrendDirection.STRONG_UP
        elif change_pct <= -strong_trend and body_ratio >= body_strong:
            return TrendDirection.STRONG_DOWN

        # Normal trends
        if change_pct >= trend_change:
            return TrendDirection.UP
        elif change_pct <= -trend_change:
            return TrendDirection.DOWN

        # Sideways
        if abs(change_pct) <= sideways:
            return TrendDirection.SIDEWAYS

        # Weak direction
        return TrendDirection.UP if change_pct > 0 else TrendDirection.DOWN

    def _calculate_strength(
        self,
        change_pct: float,
        body_ratio: float,
        range_pct: float,
        historical_data: Optional[List[Dict]]
    ) -> float:
        """Calculate trend strength (0-100)"""
        # Base strength from current candle
        strength = min(100, abs(change_pct) * 30 + body_ratio * 40)

        # Bonus for range expansion
        if range_pct > 1.0:
            strength += 10

        # Historical confirmation
        if historical_data and len(historical_data) >= 3:
            recent_changes = [d.get("change_pct", 0) for d in historical_data[-3:]]

            # Consistent direction
            if all(c > 0 for c in recent_changes) or all(c < 0 for c in recent_changes):
                strength += 15

            # Increasing momentum
            if len(recent_changes) >= 2:
                if abs(recent_changes[-1]) > abs(recent_changes[-2]):
                    strength += 5

        return min(100, max(0, strength))

    def _detect_phase(
        self,
        direction: TrendDirection,
        strength: float,
        range_pct: float,
        historical_data: Optional[List[Dict]]
    ) -> TrendPhase:
        """Detect current trend phase"""
        # Consolidation: low range, sideways direction
        if direction == TrendDirection.SIDEWAYS and range_pct < 0.5:
            return TrendPhase.CONSOLIDATION

        # Breakout: directional move with expanding range
        if strength >= 70 and range_pct >= 1.0:
            return TrendPhase.BREAKOUT

        # Impulse: strong directional move
        if strength >= 60:
            return TrendPhase.IMPULSE

        # Check for exhaustion (historical needed)
        if historical_data and len(historical_data) >= 5:
            recent_changes = [d.get("change_pct", 0) for d in historical_data[-5:]]

            # Many consecutive candles same direction with decreasing momentum
            same_dir = all(c > 0 for c in recent_changes) or all(c < 0 for c in recent_changes)
            decreasing = abs(recent_changes[-1]) < abs(recent_changes[-2]) < abs(recent_changes[-3])

            if same_dir and decreasing:
                return TrendPhase.EXHAUSTION

        # Correction: weak counter move
        if strength < 40 and direction != TrendDirection.SIDEWAYS:
            return TrendPhase.CORRECTION

        return TrendPhase.IMPULSE

    def _calculate_momentum(
        self,
        change_pct: float,
        historical_data: Optional[List[Dict]]
    ) -> float:
        """Calculate momentum score (-100 to +100)"""
        # Current momentum
        momentum = change_pct * 20  # Scale up

        # Historical momentum (if available)
        if historical_data and len(historical_data) >= 3:
            recent_changes = [d.get("change_pct", 0) for d in historical_data[-3:]]
            avg_change = sum(recent_changes) / len(recent_changes)
            momentum += avg_change * 15

        return max(-100, min(100, momentum))

    def _calculate_sr_levels(
        self,
        ltp: float,
        high: float,
        low: float,
        historical_data: Optional[List[Dict]]
    ) -> tuple[float, float]:
        """Calculate support and resistance levels"""
        # Simple: use day's high/low as immediate S/R
        support = low
        resistance = high

        # If historical data, find better levels
        if historical_data and len(historical_data) >= 5:
            highs = [d.get("high", d.get("ltp", 0)) for d in historical_data[-5:]]
            lows = [d.get("low", d.get("ltp", 0)) for d in historical_data[-5:]]

            resistance = max(highs) if highs else high
            support = min(lows) if lows else low

        return support, resistance

    def _assess_trend_quality(
        self,
        direction: TrendDirection,
        strength: float,
        body_ratio: float,
        historical_data: Optional[List[Dict]]
    ) -> str:
        """Assess if trend is clean or choppy"""
        # Clean trend: consistent direction, good bodies
        if direction in [TrendDirection.STRONG_UP, TrendDirection.STRONG_DOWN]:
            if body_ratio >= 0.6 and strength >= 60:
                return "CLEAN"

        # Check historical for consistency
        if historical_data and len(historical_data) >= 5:
            changes = [d.get("change_pct", 0) for d in historical_data[-5:]]
            direction_changes = sum(
                1 for i in range(1, len(changes))
                if (changes[i] > 0) != (changes[i-1] > 0)
            )

            if direction_changes >= 3:
                return "CHOPPY"
            elif direction_changes <= 1:
                return "CLEAN"

        return "MIXED"

    def _calculate_confidence(
        self,
        direction: TrendDirection,
        strength: float,
        phase: TrendPhase,
        body_ratio: float
    ) -> float:
        """Calculate confidence in trend assessment"""
        confidence = 50  # Base

        # Strong direction increases confidence
        if direction in [TrendDirection.STRONG_UP, TrendDirection.STRONG_DOWN]:
            confidence += 20
        elif direction in [TrendDirection.UP, TrendDirection.DOWN]:
            confidence += 10

        # High strength increases confidence
        confidence += strength * 0.2

        # Clear phases increase confidence
        if phase in [TrendPhase.IMPULSE, TrendPhase.BREAKOUT]:
            confidence += 10
        elif phase == TrendPhase.CONSOLIDATION:
            confidence -= 10  # Less certainty in ranging

        # Good body ratio increases confidence
        if body_ratio >= 0.6:
            confidence += 5

        return min(95, max(30, confidence))

    def _generate_recommendation(
        self,
        direction: TrendDirection,
        strength: float,
        phase: TrendPhase,
        momentum: float,
        trend_quality: str
    ) -> str:
        """Generate actionable recommendation"""
        base = f"Direction: {direction.value} | Strength: {strength:.0f} | Phase: {phase.value}"

        if direction == TrendDirection.STRONG_UP:
            action = "FAVOR CE positions. Buy dips, trail stops."
        elif direction == TrendDirection.STRONG_DOWN:
            action = "FAVOR PE positions. Sell rallies, trail stops."
        elif direction == TrendDirection.UP:
            action = "Lean bullish. Look for CE entries on pullbacks."
        elif direction == TrendDirection.DOWN:
            action = "Lean bearish. Look for PE entries on bounces."
        else:
            action = "SIDEWAYS - Range trade or wait for breakout."

        quality_note = f" Trend quality: {trend_quality}."

        if phase == TrendPhase.EXHAUSTION:
            quality_note += " WARNING: Trend may be exhausting."
        elif phase == TrendPhase.BREAKOUT:
            quality_note += " BREAKOUT in progress - momentum entry possible."

        return f"{base}. {action}{quality_note}"

    def validate(
        self,
        output: ModuleOutput,
        actual_outcome: Dict[str, Any]
    ) -> bool:
        """
        Validate if trend assessment was correct

        Args:
            output: Previous trend output
            actual_outcome: What happened (next candle data)

        Returns:
            True if direction prediction was correct
        """
        if not isinstance(output, TrendOutput):
            return False

        actual_change = actual_outcome.get("change_pct", 0)
        predicted_dir = output.direction

        # Check if direction was correct
        if predicted_dir in [TrendDirection.STRONG_UP, TrendDirection.UP]:
            return actual_change > 0
        elif predicted_dir in [TrendDirection.STRONG_DOWN, TrendDirection.DOWN]:
            return actual_change < 0
        elif predicted_dir == TrendDirection.SIDEWAYS:
            return abs(actual_change) < 0.5

        return False

    def get_trend_for_trade(self, index: str, market_data: Dict) -> Dict[str, Any]:
        """
        Quick method to get trend assessment for a trade

        Returns dict with direction and key metrics
        """
        output = self.analyze(index, market_data)
        return {
            "direction": output.direction.value,
            "strength": output.strength,
            "phase": output.phase.value,
            "momentum": output.momentum,
            "support": output.support,
            "resistance": output.resistance,
            "quality": output.trend_quality,
            "confidence": output.confidence,
            "recommendation": output.recommendation,
        }

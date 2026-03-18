"""
Market Regime Detector

Identifies the current market regime:
- TRENDING_UP: Clear uptrend with higher highs
- TRENDING_DOWN: Clear downtrend with lower lows
- RANGING: Sideways consolidation
- HIGH_VOLATILITY: Volatile with no clear direction
- BREAKOUT: Breaking out of range

This helps bots adapt their strategies to current market conditions.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum
import json
from pathlib import Path


class MarketRegime(Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    BREAKOUT_UP = "BREAKOUT_UP"
    BREAKOUT_DOWN = "BREAKOUT_DOWN"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeAnalysis:
    """Result of regime detection"""
    regime: MarketRegime
    confidence: float  # 0-100
    trend_strength: float  # 0-100, how strong is the trend
    volatility_level: str  # LOW, NORMAL, HIGH, EXTREME
    bias: str  # BULLISH, BEARISH, NEUTRAL
    key_levels: Dict[str, float]  # Support, resistance, pivot
    recommendation: str
    factors: Dict[str, Any]


class RegimeDetector:
    """
    Detects current market regime using multiple indicators

    Uses:
    - Price action (change %, range)
    - VIX levels
    - OI patterns
    - PCR trends
    - Intraday price structure
    """

    def __init__(self):
        self.regime_history: List[Dict] = []
        self.lookback_periods = 5  # Number of data points to consider

    def detect_regime(
        self,
        index: str,
        market_data: Dict[str, Any],
        historical_data: Optional[List[Dict]] = None
    ) -> RegimeAnalysis:
        """
        Detect current market regime

        Args:
            index: Index name
            market_data: Current market data
            historical_data: Recent historical data points

        Returns:
            RegimeAnalysis with detected regime
        """
        ltp = market_data.get("ltp", 0)
        change_pct = market_data.get("change_pct", 0)
        high = market_data.get("high", ltp)
        low = market_data.get("low", ltp)
        open_price = market_data.get("open", ltp)
        vix = market_data.get("vix", market_data.get("india_vix", 15))
        pcr = market_data.get("pcr", 1.0)

        # Calculate key metrics
        range_pct = ((high - low) / ltp * 100) if ltp > 0 else 0
        body_pct = abs(ltp - open_price) / ltp * 100 if ltp > 0 else 0

        # Determine volatility level
        volatility_level = self._classify_volatility(range_pct, vix)

        # Detect trend
        trend_direction, trend_strength = self._detect_trend(
            change_pct, range_pct, body_pct, historical_data
        )

        # Detect regime
        regime = self._classify_regime(
            trend_direction, trend_strength, volatility_level, range_pct
        )

        # Determine bias from PCR and OI
        bias = self._determine_bias(market_data)

        # Calculate key levels
        key_levels = self._calculate_key_levels(ltp, high, low, market_data)

        # Generate recommendation
        recommendation = self._generate_recommendation(
            regime, trend_strength, volatility_level, bias
        )

        # Confidence based on clarity of signals
        confidence = self._calculate_confidence(
            trend_strength, volatility_level, regime
        )

        return RegimeAnalysis(
            regime=regime,
            confidence=confidence,
            trend_strength=trend_strength,
            volatility_level=volatility_level,
            bias=bias,
            key_levels=key_levels,
            recommendation=recommendation,
            factors={
                "change_pct": round(change_pct, 2),
                "range_pct": round(range_pct, 2),
                "body_pct": round(body_pct, 2),
                "vix": round(vix, 2),
                "pcr": round(pcr, 2),
            }
        )

    def _classify_volatility(self, range_pct: float, vix: float) -> str:
        """Classify volatility level"""
        # Combine range and VIX for volatility assessment
        if vix >= 25 or range_pct >= 2.0:
            return "EXTREME"
        elif vix >= 18 or range_pct >= 1.5:
            return "HIGH"
        elif vix >= 14 or range_pct >= 0.8:
            return "NORMAL"
        else:
            return "LOW"

    def _detect_trend(
        self,
        change_pct: float,
        range_pct: float,
        body_pct: float,
        historical_data: Optional[List[Dict]]
    ) -> tuple:
        """
        Detect trend direction and strength

        Returns: (direction, strength)
        direction: 1 for up, -1 for down, 0 for sideways
        strength: 0-100
        """
        # Simple trend detection from current data
        if change_pct > 0.5 and body_pct > range_pct * 0.5:
            direction = 1
            strength = min(100, abs(change_pct) * 30 + body_pct * 10)
        elif change_pct < -0.5 and body_pct > range_pct * 0.5:
            direction = -1
            strength = min(100, abs(change_pct) * 30 + body_pct * 10)
        elif abs(change_pct) < 0.3:
            direction = 0
            strength = 100 - abs(change_pct) * 100  # Higher strength for tighter range
        else:
            direction = 1 if change_pct > 0 else -1
            strength = min(100, abs(change_pct) * 20)

        # Enhance with historical data if available
        if historical_data and len(historical_data) >= 3:
            recent_changes = [d.get("change_pct", 0) for d in historical_data[-3:]]
            avg_change = sum(recent_changes) / len(recent_changes)

            if all(c > 0 for c in recent_changes):
                direction = 1
                strength = min(100, strength + 20)
            elif all(c < 0 for c in recent_changes):
                direction = -1
                strength = min(100, strength + 20)
            elif max(recent_changes) - min(recent_changes) < 0.5:
                direction = 0
                strength = min(100, strength + 10)

        return direction, min(100, max(0, strength))

    def _classify_regime(
        self,
        trend_direction: int,
        trend_strength: float,
        volatility_level: str,
        range_pct: float
    ) -> MarketRegime:
        """Classify market regime"""
        # High volatility overrides trend
        if volatility_level == "EXTREME":
            return MarketRegime.HIGH_VOLATILITY

        # Strong trends
        if trend_strength >= 70:
            if trend_direction == 1:
                return MarketRegime.TRENDING_UP
            elif trend_direction == -1:
                return MarketRegime.TRENDING_DOWN

        # Breakouts (moderate trend with expanding range)
        if trend_strength >= 50 and volatility_level in ["HIGH", "EXTREME"]:
            if trend_direction == 1:
                return MarketRegime.BREAKOUT_UP
            elif trend_direction == -1:
                return MarketRegime.BREAKOUT_DOWN

        # Ranging (low trend strength)
        if trend_strength <= 40 and volatility_level in ["LOW", "NORMAL"]:
            return MarketRegime.RANGING

        # Default based on trend direction
        if trend_direction == 1:
            return MarketRegime.TRENDING_UP
        elif trend_direction == -1:
            return MarketRegime.TRENDING_DOWN

        return MarketRegime.RANGING

    def _determine_bias(self, market_data: Dict) -> str:
        """Determine market bias from options data"""
        pcr = market_data.get("pcr", 1.0)
        ce_oi_change = market_data.get("ce_oi_change", 0)
        pe_oi_change = market_data.get("pe_oi_change", 0)
        change_pct = market_data.get("change_pct", 0)

        # PCR-based bias
        pcr_bias = 0
        if pcr >= 1.3:
            pcr_bias = 2  # Strong bullish
        elif pcr >= 1.1:
            pcr_bias = 1  # Mild bullish
        elif pcr <= 0.7:
            pcr_bias = -2  # Strong bearish
        elif pcr <= 0.9:
            pcr_bias = -1  # Mild bearish

        # OI-based bias
        oi_bias = 0
        if pe_oi_change > ce_oi_change * 1.5:
            oi_bias = 1  # More put writing = bullish
        elif ce_oi_change > pe_oi_change * 1.5:
            oi_bias = -1  # More call writing = bearish

        # Price-based bias
        price_bias = 1 if change_pct > 0.3 else (-1 if change_pct < -0.3 else 0)

        # Combine biases
        total_bias = pcr_bias + oi_bias + price_bias

        if total_bias >= 2:
            return "BULLISH"
        elif total_bias <= -2:
            return "BEARISH"
        else:
            return "NEUTRAL"

    def _calculate_key_levels(
        self,
        ltp: float,
        high: float,
        low: float,
        market_data: Dict
    ) -> Dict[str, float]:
        """Calculate key price levels"""
        # Simple pivot calculation
        pivot = (high + low + ltp) / 3
        r1 = 2 * pivot - low
        s1 = 2 * pivot - high
        r2 = pivot + (high - low)
        s2 = pivot - (high - low)

        # Max Pain as key level
        max_pain = market_data.get("max_pain", 0)

        return {
            "pivot": round(pivot, 2),
            "resistance_1": round(r1, 2),
            "resistance_2": round(r2, 2),
            "support_1": round(s1, 2),
            "support_2": round(s2, 2),
            "max_pain": max_pain,
            "day_high": high,
            "day_low": low,
        }

    def _generate_recommendation(
        self,
        regime: MarketRegime,
        trend_strength: float,
        volatility_level: str,
        bias: str
    ) -> str:
        """Generate trading recommendation based on regime"""
        recommendations = {
            MarketRegime.TRENDING_UP: f"Trend following: Buy dips, trail stops. Bias: {bias}",
            MarketRegime.TRENDING_DOWN: f"Trend following: Sell rallies, trail stops. Bias: {bias}",
            MarketRegime.RANGING: "Range trading: Buy support, sell resistance. Use tight stops.",
            MarketRegime.HIGH_VOLATILITY: "CAUTION: High volatility. Reduce position size or stay flat.",
            MarketRegime.BREAKOUT_UP: "Breakout detected UP. Consider momentum longs with wider stops.",
            MarketRegime.BREAKOUT_DOWN: "Breakout detected DOWN. Consider momentum shorts with wider stops.",
            MarketRegime.UNKNOWN: "Unclear market structure. Wait for clarity.",
        }

        base = recommendations.get(regime, "Wait for clear setup.")

        if volatility_level == "EXTREME":
            base = "EXTREME VOLATILITY: " + base + " Consider reducing size 50%."

        return base

    def _calculate_confidence(
        self,
        trend_strength: float,
        volatility_level: str,
        regime: MarketRegime
    ) -> float:
        """Calculate confidence in regime detection"""
        # Base confidence from trend strength
        confidence = trend_strength

        # Reduce confidence in high volatility
        if volatility_level == "EXTREME":
            confidence *= 0.6
        elif volatility_level == "HIGH":
            confidence *= 0.8

        # Clear regimes have higher confidence
        if regime in [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]:
            confidence = min(100, confidence * 1.1)
        elif regime == MarketRegime.HIGH_VOLATILITY:
            confidence *= 0.7

        return min(100, max(0, confidence))

    def get_regime_for_strategy(self, regime: MarketRegime) -> Dict[str, Any]:
        """Get strategy recommendations for a given regime"""
        strategies = {
            MarketRegime.TRENDING_UP: {
                "preferred_bots": ["TrendFollower", "MomentumScalper"],
                "avoid_bots": ["ReversalHunter"],
                "position_bias": "LONG",
                "risk_multiplier": 1.0,
            },
            MarketRegime.TRENDING_DOWN: {
                "preferred_bots": ["TrendFollower", "MomentumScalper"],
                "avoid_bots": ["ReversalHunter"],
                "position_bias": "SHORT",
                "risk_multiplier": 1.0,
            },
            MarketRegime.RANGING: {
                "preferred_bots": ["ReversalHunter", "OIAnalyst"],
                "avoid_bots": ["TrendFollower", "MomentumScalper"],
                "position_bias": "NEUTRAL",
                "risk_multiplier": 0.8,
            },
            MarketRegime.HIGH_VOLATILITY: {
                "preferred_bots": ["VolatilityTrader"],
                "avoid_bots": ["MomentumScalper"],
                "position_bias": "NEUTRAL",
                "risk_multiplier": 0.5,  # Reduce risk in high vol
            },
            MarketRegime.BREAKOUT_UP: {
                "preferred_bots": ["MomentumScalper", "TrendFollower"],
                "avoid_bots": ["ReversalHunter"],
                "position_bias": "LONG",
                "risk_multiplier": 1.2,  # Slightly more aggressive
            },
            MarketRegime.BREAKOUT_DOWN: {
                "preferred_bots": ["MomentumScalper", "TrendFollower"],
                "avoid_bots": ["ReversalHunter"],
                "position_bias": "SHORT",
                "risk_multiplier": 1.2,
            },
        }

        return strategies.get(regime, {
            "preferred_bots": [],
            "avoid_bots": [],
            "position_bias": "NEUTRAL",
            "risk_multiplier": 0.5,
        })

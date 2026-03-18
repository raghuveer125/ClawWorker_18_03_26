"""
Multi-Timeframe Analysis Engine

Professional trading principle: "Trade with the trend, not against it"

Timeframe Hierarchy:
- 15m: Primary trend direction (THE BOSS - never fight this)
- 5m:  Signal generation and confirmation
- 1m:  Entry timing for best prices

This module reduces losses by:
1. Blocking trades against the higher timeframe trend
2. Requiring alignment across timeframes for high-confidence trades
3. Using 1m for precise entry timing (better premium prices)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
import statistics


class Trend(Enum):
    """Market trend direction"""
    STRONG_UP = "STRONG_UP"
    UP = "UP"
    NEUTRAL = "NEUTRAL"
    DOWN = "DOWN"
    STRONG_DOWN = "STRONG_DOWN"


class TimeframeAlignment(Enum):
    """Alignment status across timeframes"""
    FULLY_ALIGNED = "FULLY_ALIGNED"      # All timeframes agree - HIGH confidence
    MOSTLY_ALIGNED = "MOSTLY_ALIGNED"    # 2/3 timeframes agree - MEDIUM confidence
    CONFLICTING = "CONFLICTING"          # Timeframes disagree - LOW confidence / NO TRADE


@dataclass
class TimeframeData:
    """Data for a single timeframe"""
    timeframe: str  # "1m", "5m", "15m"
    candles: List[Dict] = field(default_factory=list)
    trend: Trend = Trend.NEUTRAL
    strength: float = 0.0  # 0-100
    momentum: float = 0.0
    support: float = 0.0
    resistance: float = 0.0
    last_update: datetime = field(default_factory=datetime.now)


@dataclass
class MTFAnalysis:
    """Multi-timeframe analysis result"""
    index: str
    timestamp: datetime

    # Individual timeframe analysis
    tf_1m: TimeframeData
    tf_5m: TimeframeData
    tf_15m: TimeframeData

    # Combined analysis
    alignment: TimeframeAlignment = TimeframeAlignment.CONFLICTING
    primary_trend: Trend = Trend.NEUTRAL
    confidence_boost: float = 0.0  # Additional confidence from alignment

    # Trade filters
    allow_ce: bool = True
    allow_pe: bool = True
    recommended_action: str = "WAIT"

    reasoning: str = ""


class MultiTimeframeEngine:
    """
    Multi-Timeframe Analysis Engine

    Reduces losses by ensuring trades align with higher timeframe trends.

    Key Rules:
    1. NEVER trade against 15m trend (the primary filter)
    2. 5m provides signal generation
    3. 1m provides entry timing
    4. Full alignment = confidence boost
    5. Conflicting timeframes = no trade
    """

    def __init__(self):
        # Candle storage by index and timeframe
        self.candles: Dict[str, Dict[str, List[Dict]]] = {}

        # Configuration - Balanced for profitability while reducing losses
        self.config = {
            "trend_ema_fast": 8,
            "trend_ema_slow": 21,
            "strong_trend_threshold": 0.8,  # % change for strong trend
            "neutral_zone": 0.3,  # % range considered neutral
            "min_candles_1m": 30,
            "min_candles_5m": 20,
            "min_candles_15m": 10,
            "alignment_confidence_boost": 15,  # Extra confidence when aligned
            "only_block_strong_trends": True,  # Only block when STRONG trend opposes

            # MTF MODE: "strict", "balanced", "permissive"
            # - strict: Block all counter-trend trades (maximize loss prevention)
            # - balanced: Only block on strong trends (default - good for most markets)
            # - permissive: Apply penalties only, never block (for range-bound markets)
            "mode": "balanced",
        }

        # Timeframe weights for combined analysis
        self.timeframe_weights = {
            "15m": 0.5,   # Primary trend - highest weight
            "5m": 0.35,   # Signal generation
            "1m": 0.15,   # Entry timing - lowest weight
        }

    def add_candle(self, index: str, timeframe: str, candle: Dict):
        """Add a new candle for a specific index and timeframe"""
        if index not in self.candles:
            self.candles[index] = {"1m": [], "5m": [], "15m": []}

        if timeframe not in self.candles[index]:
            self.candles[index][timeframe] = []

        self.candles[index][timeframe].append(candle)

        # Keep only required candles
        max_candles = {
            "1m": 60,   # 1 hour of 1m candles
            "5m": 50,   # ~4 hours of 5m candles
            "15m": 30,  # ~7.5 hours of 15m candles
        }

        if len(self.candles[index][timeframe]) > max_candles.get(timeframe, 50):
            self.candles[index][timeframe] = self.candles[index][timeframe][-max_candles[timeframe]:]

    def build_candles_from_1m(self, index: str, candles_1m: List[Dict]):
        """
        Build 5m and 15m candles from 1m candles

        This is useful when only 1m data is available from the API
        """
        if index not in self.candles:
            self.candles[index] = {"1m": [], "5m": [], "15m": []}

        # Store 1m candles
        self.candles[index]["1m"] = candles_1m[-60:]  # Keep last 60

        # Build 5m candles (aggregate 5 x 1m candles)
        self.candles[index]["5m"] = self._aggregate_candles(candles_1m, 5)

        # Build 15m candles (aggregate 15 x 1m candles)
        self.candles[index]["15m"] = self._aggregate_candles(candles_1m, 15)

    def _aggregate_candles(self, candles: List[Dict], period: int) -> List[Dict]:
        """Aggregate candles into larger timeframe"""
        if len(candles) < period:
            return []

        aggregated = []
        for i in range(0, len(candles) - period + 1, period):
            chunk = candles[i:i + period]
            if not chunk:
                continue

            agg_candle = {
                "timestamp": chunk[0].get("timestamp", chunk[0].get("time", 0)),
                "open": chunk[0]["open"],
                "high": max(c["high"] for c in chunk),
                "low": min(c["low"] for c in chunk),
                "close": chunk[-1]["close"],
                "volume": sum(c.get("volume", 0) for c in chunk),
            }
            aggregated.append(agg_candle)

        return aggregated

    def analyze(self, index: str, current_price: float = None) -> MTFAnalysis:
        """
        Perform multi-timeframe analysis

        Returns comprehensive analysis with trade filters
        """
        now = datetime.now()

        # Analyze each timeframe
        tf_1m = self._analyze_timeframe(index, "1m", current_price)
        tf_5m = self._analyze_timeframe(index, "5m", current_price)
        tf_15m = self._analyze_timeframe(index, "15m", current_price)

        # Determine alignment
        alignment, primary_trend = self._calculate_alignment(tf_1m, tf_5m, tf_15m)

        # Calculate trade filters based on 15m trend (THE BOSS)
        allow_ce, allow_pe = self._calculate_trade_filters(tf_15m.trend, tf_5m.trend)

        # Calculate confidence boost from alignment
        confidence_boost = self._calculate_confidence_boost(alignment)

        # Determine recommended action
        recommended_action = self._get_recommended_action(
            alignment, tf_15m.trend, tf_5m.trend, tf_1m.trend
        )

        # Build reasoning
        reasoning = self._build_reasoning(
            tf_15m, tf_5m, tf_1m, alignment, allow_ce, allow_pe
        )

        return MTFAnalysis(
            index=index,
            timestamp=now,
            tf_1m=tf_1m,
            tf_5m=tf_5m,
            tf_15m=tf_15m,
            alignment=alignment,
            primary_trend=primary_trend,
            confidence_boost=confidence_boost,
            allow_ce=allow_ce,
            allow_pe=allow_pe,
            recommended_action=recommended_action,
            reasoning=reasoning,
        )

    def _analyze_timeframe(
        self,
        index: str,
        timeframe: str,
        current_price: float = None
    ) -> TimeframeData:
        """Analyze a single timeframe"""
        candles = self.candles.get(index, {}).get(timeframe, [])

        tf_data = TimeframeData(
            timeframe=timeframe,
            candles=candles[-20:],  # Keep last 20 for reference
            last_update=datetime.now()
        )

        if len(candles) < 5:
            return tf_data

        # Calculate trend using price action
        closes = [c["close"] for c in candles[-20:]]

        if len(closes) < 5:
            return tf_data

        # Simple trend: compare recent average to older average
        recent_avg = statistics.mean(closes[-5:])
        older_avg = statistics.mean(closes[-10:-5]) if len(closes) >= 10 else closes[0]

        change_pct = ((recent_avg - older_avg) / older_avg) * 100 if older_avg > 0 else 0

        # Determine trend
        strong_threshold = self.config["strong_trend_threshold"]
        neutral_zone = self.config["neutral_zone"]

        if change_pct >= strong_threshold:
            tf_data.trend = Trend.STRONG_UP
            tf_data.strength = min(100, 50 + change_pct * 20)
        elif change_pct >= neutral_zone:
            tf_data.trend = Trend.UP
            tf_data.strength = 30 + change_pct * 20
        elif change_pct <= -strong_threshold:
            tf_data.trend = Trend.STRONG_DOWN
            tf_data.strength = min(100, 50 + abs(change_pct) * 20)
        elif change_pct <= -neutral_zone:
            tf_data.trend = Trend.DOWN
            tf_data.strength = 30 + abs(change_pct) * 20
        else:
            tf_data.trend = Trend.NEUTRAL
            tf_data.strength = 20

        # Calculate momentum (rate of change)
        if len(closes) >= 3:
            tf_data.momentum = ((closes[-1] - closes[-3]) / closes[-3]) * 100

        # Calculate support/resistance (simple: recent low/high)
        recent_candles = candles[-10:]
        tf_data.support = min(c["low"] for c in recent_candles)
        tf_data.resistance = max(c["high"] for c in recent_candles)

        return tf_data

    def _calculate_alignment(
        self,
        tf_1m: TimeframeData,
        tf_5m: TimeframeData,
        tf_15m: TimeframeData
    ) -> Tuple[TimeframeAlignment, Trend]:
        """Calculate alignment across timeframes"""

        trends = [tf_15m.trend, tf_5m.trend, tf_1m.trend]

        # Count bullish vs bearish
        bullish = sum(1 for t in trends if t in [Trend.UP, Trend.STRONG_UP])
        bearish = sum(1 for t in trends if t in [Trend.DOWN, Trend.STRONG_DOWN])
        neutral = sum(1 for t in trends if t == Trend.NEUTRAL)

        # Primary trend is always from 15m (THE BOSS)
        primary_trend = tf_15m.trend

        # Determine alignment
        if bullish == 3 or bearish == 3:
            return TimeframeAlignment.FULLY_ALIGNED, primary_trend
        elif bullish >= 2 or bearish >= 2:
            return TimeframeAlignment.MOSTLY_ALIGNED, primary_trend
        else:
            return TimeframeAlignment.CONFLICTING, primary_trend

    def _calculate_trade_filters(
        self,
        trend_15m: Trend,
        trend_5m: Trend
    ) -> Tuple[bool, bool]:
        """
        Calculate which option types are allowed

        KEY RULE: Only block trades against STRONG 15m trends
        Regular trends just reduce confidence, don't block entirely
        """
        allow_ce = True
        allow_pe = True

        # Only block on STRONG trends (balanced approach)
        if self.config.get("only_block_strong_trends", True):
            # Only STRONG trends trigger blocking
            if trend_15m == Trend.STRONG_DOWN:
                allow_ce = False  # Don't buy calls in strong downtrend
            elif trend_15m == Trend.STRONG_UP:
                allow_pe = False  # Don't buy puts in strong uptrend
        else:
            # Original strict mode - block on any trend
            if trend_15m in [Trend.STRONG_DOWN, Trend.DOWN]:
                allow_ce = False
            elif trend_15m in [Trend.STRONG_UP, Trend.UP]:
                allow_pe = False

        # If 15m is neutral/weak, use 5m only for STRONG signals
        if trend_15m == Trend.NEUTRAL:
            if trend_5m == Trend.STRONG_DOWN:
                allow_ce = False
            elif trend_5m == Trend.STRONG_UP:
                allow_pe = False

        return allow_ce, allow_pe

    def _calculate_confidence_boost(self, alignment: TimeframeAlignment) -> float:
        """Calculate additional confidence from timeframe alignment"""
        if alignment == TimeframeAlignment.FULLY_ALIGNED:
            return self.config["alignment_confidence_boost"]
        elif alignment == TimeframeAlignment.MOSTLY_ALIGNED:
            return self.config["alignment_confidence_boost"] * 0.5
        else:
            return -10  # Penalty for conflicting signals

    def _get_recommended_action(
        self,
        alignment: TimeframeAlignment,
        trend_15m: Trend,
        trend_5m: Trend,
        trend_1m: Trend
    ) -> str:
        """Get recommended action based on MTF analysis"""

        # Conflicting = WAIT
        if alignment == TimeframeAlignment.CONFLICTING:
            return "WAIT - Timeframes conflicting"

        # Fully aligned = strong signal
        if alignment == TimeframeAlignment.FULLY_ALIGNED:
            if trend_15m in [Trend.STRONG_UP, Trend.UP]:
                return "BUY CE - All timeframes bullish"
            elif trend_15m in [Trend.STRONG_DOWN, Trend.DOWN]:
                return "BUY PE - All timeframes bearish"
            else:
                return "WAIT - Neutral trend"

        # Mostly aligned = conditional
        if alignment == TimeframeAlignment.MOSTLY_ALIGNED:
            if trend_15m in [Trend.STRONG_UP, Trend.UP]:
                return "BUY CE - Primary trend bullish"
            elif trend_15m in [Trend.STRONG_DOWN, Trend.DOWN]:
                return "BUY PE - Primary trend bearish"
            else:
                return "WAIT - No clear direction"

        return "WAIT"

    def _build_reasoning(
        self,
        tf_15m: TimeframeData,
        tf_5m: TimeframeData,
        tf_1m: TimeframeData,
        alignment: TimeframeAlignment,
        allow_ce: bool,
        allow_pe: bool
    ) -> str:
        """Build human-readable reasoning"""
        parts = []

        parts.append(f"15m: {tf_15m.trend.value} (strength: {tf_15m.strength:.0f}%)")
        parts.append(f"5m: {tf_5m.trend.value} (strength: {tf_5m.strength:.0f}%)")
        parts.append(f"1m: {tf_1m.trend.value} (strength: {tf_1m.strength:.0f}%)")
        parts.append(f"Alignment: {alignment.value}")

        filters = []
        if not allow_ce:
            filters.append("CE BLOCKED (against trend)")
        if not allow_pe:
            filters.append("PE BLOCKED (against trend)")

        if filters:
            parts.append("Filters: " + ", ".join(filters))

        return " | ".join(parts)

    def should_allow_signal(
        self,
        index: str,
        option_type: str,  # "CE" or "PE"
        current_price: float = None,
        signal_confidence: float = None  # HIGH-CONVICTION SIGNALS bypass trend filters
    ) -> Tuple[bool, str, float]:
        """
        Check if a signal should be allowed based on MTF analysis

        Args:
            index: Index code
            option_type: "CE" or "PE"
            current_price: Current price (optional for current value)
            signal_confidence: Signal confidence (0-100). HIGH signals (80%+) bypass trend blocks.

        Returns:
            (allowed: bool, reason: str, confidence_adjustment: float)

        Modes:
        - strict: Block all counter-trend trades (maximize loss prevention)
        - balanced: Only block on strong trends (default - good for most markets)
        - permissive: Apply penalties only, never block (for range-bound markets)
        
        HIGH-CONVICTION RULE:
        - Signals with 80%+ confidence ALWAYS allowed (trust expert signals like ICT Sniper)
        - Signals with 70-79% confidence: penalties only, never blocked
        - Signals with <70% confidence: full MTF filtering applied
        """
        analysis = self.analyze(index, current_price)
        mode = self.config.get("mode", "balanced")
        confidence_adj = analysis.confidence_boost

        # HIGH-CONVICTION GATE: Preserve expert signals
        if signal_confidence and signal_confidence >= 80:
            # Apply confidence boost but NEVER block - expert signals are trusted
            return True, f"[PRESERVE] High-conviction {signal_confidence:.0f}% signal {analysis.reasoning}", confidence_adj

        # MEDIUM-CONVICTION GATE: Penalties only, no blocking
        if signal_confidence and signal_confidence >= 70:
            # Apply penalties but never block
            if option_type == "CE" and analysis.tf_15m.trend in [Trend.STRONG_DOWN, Trend.DOWN]:
                confidence_adj -= 8  # Mild penalty
            elif option_type == "PE" and analysis.tf_15m.trend in [Trend.STRONG_UP, Trend.UP]:
                confidence_adj -= 8  # Mild penalty
            return True, f"[MODERATE] {signal_confidence:.0f}% signal penalized {analysis.reasoning}", confidence_adj

        # MODE: PERMISSIVE - Never block, only apply penalties
        if mode == "permissive":
            # Apply penalties based on trend opposition
            if option_type == "CE":
                if analysis.tf_15m.trend == Trend.STRONG_DOWN:
                    confidence_adj -= 25  # Strong penalty
                elif analysis.tf_15m.trend == Trend.DOWN:
                    confidence_adj -= 15
            elif option_type == "PE":
                if analysis.tf_15m.trend == Trend.STRONG_UP:
                    confidence_adj -= 25
                elif analysis.tf_15m.trend == Trend.UP:
                    confidence_adj -= 15

            return True, f"[PERMISSIVE] {analysis.reasoning}", confidence_adj

        # MODE: STRICT - Block any counter-trend trade
        if mode == "strict":
            if option_type == "CE" and analysis.tf_15m.trend in [Trend.DOWN, Trend.STRONG_DOWN]:
                return False, f"CE blocked - 15m {analysis.tf_15m.trend.value} [STRICT]", -20
            if option_type == "PE" and analysis.tf_15m.trend in [Trend.UP, Trend.STRONG_UP]:
                return False, f"PE blocked - 15m {analysis.tf_15m.trend.value} [STRICT]", -20

            if analysis.alignment == TimeframeAlignment.CONFLICTING:
                return False, "Timeframes conflicting [STRICT]", -15

            return True, f"[STRICT] {analysis.reasoning}", confidence_adj

        # MODE: BALANCED (default) - Only block on STRONG trends
        if option_type == "CE" and not analysis.allow_ce:
            return False, f"CE blocked - 15m STRONG {analysis.tf_15m.trend.value}", -20

        if option_type == "PE" and not analysis.allow_pe:
            return False, f"PE blocked - 15m STRONG {analysis.tf_15m.trend.value}", -20

        # Penalty for trading against weak trends (not blocked, just penalized)
        if option_type == "CE" and analysis.tf_15m.trend == Trend.DOWN:
            confidence_adj -= 10  # Penalty but not blocked
        elif option_type == "PE" and analysis.tf_15m.trend == Trend.UP:
            confidence_adj -= 10  # Penalty but not blocked

        # Conflicting timeframes: apply penalty but allow (balanced approach)
        if analysis.alignment == TimeframeAlignment.CONFLICTING:
            confidence_adj -= 10  # Penalty instead of blocking
            return True, f"Conflicting TFs (penalty applied) | {analysis.reasoning}", confidence_adj

        # Allowed with confidence adjustment
        return True, analysis.reasoning, confidence_adj

    def set_mode(self, mode: str):
        """
        Set MTF filtering mode

        Args:
            mode: "strict", "balanced", or "permissive"
        """
        valid_modes = ["strict", "balanced", "permissive"]
        if mode not in valid_modes:
            raise ValueError(f"Invalid mode: {mode}. Must be one of {valid_modes}")
        self.config["mode"] = mode
        print(f"[MTF] Mode set to: {mode}")

    def auto_adjust_mode(self, index: str, market_data: Dict[str, Any] = None) -> str:
        """
        Automatically adjust MTF mode based on market volatility.

        High volatility (trending) → STRICT mode (block counter-trend)
        Normal volatility → BALANCED mode (default)
        Low volatility (ranging) → PERMISSIVE mode (allow reversals)

        Returns the new mode that was set.
        """
        # Calculate volatility from candles
        candles = self.candles.get(index, {}).get("15m", [])

        if len(candles) < 5:
            return self.config["mode"]  # Not enough data

        # Calculate average true range (ATR) as volatility measure
        atr_values = []
        for i in range(1, min(10, len(candles))):
            c = candles[-i]
            prev_c = candles[-i-1] if i < len(candles) - 1 else c
            tr = max(
                c["high"] - c["low"],
                abs(c["high"] - prev_c["close"]),
                abs(c["low"] - prev_c["close"])
            )
            atr_values.append(tr)

        if not atr_values:
            return self.config["mode"]

        avg_atr = sum(atr_values) / len(atr_values)
        current_price = candles[-1]["close"]
        atr_pct = (avg_atr / current_price) * 100 if current_price > 0 else 0

        # Also check change from open
        change_pct = abs(market_data.get("change_pct", 0)) if market_data else 0

        # Decision logic
        old_mode = self.config["mode"]

        if atr_pct > 0.5 or change_pct > 1.0:
            # High volatility / trending day
            new_mode = "strict"
        elif atr_pct < 0.2 and change_pct < 0.3:
            # Low volatility / ranging day
            new_mode = "permissive"
        else:
            # Normal conditions
            new_mode = "balanced"

        if new_mode != old_mode:
            self.config["mode"] = new_mode
            print(f"[MTF] Auto-switched mode: {old_mode} → {new_mode} (ATR: {atr_pct:.2f}%, Change: {change_pct:.2f}%)")

        return new_mode

    def get_entry_timing(self, index: str, option_type: str) -> Dict[str, Any]:
        """
        Use 1m timeframe to suggest optimal entry timing

        Returns entry suggestion based on 1m momentum and support/resistance
        """
        tf_1m = self._analyze_timeframe(index, "1m")

        suggestion = {
            "timing": "NOW",
            "reason": "No specific timing signal",
            "wait_for": None,
        }

        if not tf_1m.candles:
            return suggestion

        current_price = tf_1m.candles[-1]["close"] if tf_1m.candles else 0

        if option_type == "CE":
            # For CE, better to enter on pullback (near support)
            if current_price and tf_1m.support:
                distance_to_support = ((current_price - tf_1m.support) / current_price) * 100
                if distance_to_support < 0.1:  # Very close to support
                    suggestion["timing"] = "OPTIMAL"
                    suggestion["reason"] = "Price near 1m support - good entry for CE"
                elif tf_1m.momentum < -0.05:  # Pulling back
                    suggestion["timing"] = "WAIT"
                    suggestion["reason"] = "1m showing pullback - wait for support"
                    suggestion["wait_for"] = tf_1m.support

        elif option_type == "PE":
            # For PE, better to enter on rally (near resistance)
            if current_price and tf_1m.resistance:
                distance_to_resistance = ((tf_1m.resistance - current_price) / current_price) * 100
                if distance_to_resistance < 0.1:  # Very close to resistance
                    suggestion["timing"] = "OPTIMAL"
                    suggestion["reason"] = "Price near 1m resistance - good entry for PE"
                elif tf_1m.momentum > 0.05:  # Rallying
                    suggestion["timing"] = "WAIT"
                    suggestion["reason"] = "1m showing rally - wait for resistance"
                    suggestion["wait_for"] = tf_1m.resistance

        return suggestion

    def get_status(self) -> Dict[str, Any]:
        """Get current MTF engine status"""
        status = {
            "indices": {},
            "config": self.config,
        }

        for index in self.candles:
            analysis = self.analyze(index)
            status["indices"][index] = {
                "15m_trend": analysis.tf_15m.trend.value,
                "5m_trend": analysis.tf_5m.trend.value,
                "1m_trend": analysis.tf_1m.trend.value,
                "alignment": analysis.alignment.value,
                "allow_ce": analysis.allow_ce,
                "allow_pe": analysis.allow_pe,
                "recommendation": analysis.recommended_action,
                "candle_counts": {
                    "1m": len(self.candles[index].get("1m", [])),
                    "5m": len(self.candles[index].get("5m", [])),
                    "15m": len(self.candles[index].get("15m", [])),
                }
            }

        return status

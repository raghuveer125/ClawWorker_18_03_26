"""
Signal Quality Agent - Gate between Analysis and Execution layers.

Agent 19: SignalQualityAgent

Purpose:
- Validates signal quality before execution
- Checks regime compatibility
- Filters weak signals
- Reduces noise reaching execution layer

This agent runs AFTER analysis layer and BEFORE execution layer.
Only high-quality signals pass through to Entry/Exit agents.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from ..base import BaseBot, BotContext, BotResult, BotStatus
from ..config import ScalpingConfig


class SignalGrade(Enum):
    """Signal quality grades."""
    A_PLUS = "A+"  # Exceptional - all factors aligned
    A = "A"        # Strong - most factors aligned
    B = "B"        # Good - acceptable for trading
    C = "C"        # Marginal - risky, reduce size
    D = "D"        # Weak - skip this signal
    F = "F"        # Failed - do not trade


@dataclass
class QualityScore:
    """Detailed quality scoring breakdown."""
    confidence_score: float  # 0-1, from signal confidence
    regime_score: float      # 0-1, regime compatibility
    volume_score: float      # 0-1, volume confirmation
    liquidity_score: float   # 0-1, tradeable liquidity
    momentum_score: float    # 0-1, momentum alignment
    risk_score: float        # 0-1, risk/reward quality
    total_score: float       # Weighted average
    grade: SignalGrade
    reasons: List[str]
    pass_filter: bool


@dataclass
class FilteredSignal:
    """Signal with quality assessment."""
    original_signal: Dict[str, Any]
    quality: QualityScore
    recommended_size_pct: float  # 0-100, position size recommendation
    execution_priority: int      # 1-10, higher = execute first


class SignalQualityAgent(BaseBot):
    """
    Agent 19: Signal Quality Agent

    Acts as a quality gate between Analysis and Execution layers.

    Evaluates:
    1. Confidence Score - Signal's own confidence level
    2. Regime Compatibility - Does signal match current regime?
    3. Volume Confirmation - Is there volume supporting the move?
    4. Liquidity Score - Can we execute without slippage?
    5. Momentum Alignment - Is momentum in signal direction?
    6. Risk/Reward Quality - Is the setup worth the risk?

    Grading:
    - A+: Score >= 0.9 - Full size, high priority
    - A:  Score >= 0.8 - Full size
    - B:  Score >= 0.7 - Normal size
    - C:  Score >= 0.6 - Reduced size (50%)
    - D:  Score >= 0.5 - Skip or minimal size (25%)
    - F:  Score < 0.5  - Do not trade

    This prevents weak signals from reaching execution.
    """

    BOT_TYPE = "signal_quality"
    REQUIRES_LLM = False  # Fast, deterministic checks

    # Quality thresholds
    MINIMUM_CONFIDENCE = 0.5
    MINIMUM_TOTAL_SCORE = 0.5
    A_PLUS_THRESHOLD = 0.9
    A_THRESHOLD = 0.8
    B_THRESHOLD = 0.7
    C_THRESHOLD = 0.6
    D_THRESHOLD = 0.5

    # Scoring weights
    WEIGHT_CONFIDENCE = 0.25
    WEIGHT_REGIME = 0.20
    WEIGHT_VOLUME = 0.15
    WEIGHT_LIQUIDITY = 0.15
    WEIGHT_MOMENTUM = 0.15
    WEIGHT_RISK = 0.10

    # Regime compatibility matrix
    REGIME_SIGNAL_COMPAT = {
        "TRENDING_BULLISH": {"CE": 1.0, "PE": 0.3},
        "TRENDING_BEARISH": {"CE": 0.3, "PE": 1.0},
        "RANGE_BOUND": {"CE": 0.6, "PE": 0.6},
        "VOLATILE_EXPANSION": {"CE": 0.8, "PE": 0.8},
        "VOLATILE_CONTRACTION": {"CE": 0.4, "PE": 0.4},
        "EXPIRY_PINNING": {"CE": 0.5, "PE": 0.5},
        "UNKNOWN": {"CE": 0.5, "PE": 0.5},
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._filtered_signals: List[FilteredSignal] = []
        self._filter_stats = {
            "total_evaluated": 0,
            "passed": 0,
            "failed": 0,
            "grade_distribution": {"A+": 0, "A": 0, "B": 0, "C": 0, "D": 0, "F": 0},
        }

    def get_description(self) -> str:
        return "Signal quality gate - filters weak signals before execution"

    async def execute(self, context: BotContext) -> BotResult:
        """
        Evaluate all pending signals and filter based on quality.
        """
        config = context.data.get("config", ScalpingConfig())
        weights = self._resolve_weights(context)

        # Get signals from analysis layer
        pending_signals = context.data.get("pending_signals", [])
        strike_selections_raw = context.data.get("strike_selections", {})

        # Flatten strike_selections dict into a list of signals
        strike_selections = []
        if isinstance(strike_selections_raw, dict):
            for symbol, selections in strike_selections_raw.items():
                for sel in (selections if isinstance(selections, list) else []):
                    # Convert strike selection object/dict to signal dict
                    if hasattr(sel, '__dict__'):
                        payload = dict(getattr(sel, "__dict__", {}))
                    else:
                        payload = dict(sel) if isinstance(sel, dict) else {}
                    option_symbol = str(payload.get("option_symbol", payload.get("symbol", "")) or "")
                    payload["symbol"] = symbol
                    payload["underlying_symbol"] = symbol
                    if option_symbol and option_symbol != symbol:
                        payload["option_symbol"] = option_symbol
                    sig = payload
                    strike_selections.append(sig)
        elif isinstance(strike_selections_raw, list):
            strike_selections = strike_selections_raw

        # Get market context (handle both dict and list types)
        market_regimes = context.data.get("market_regimes", {})
        volume_data_raw = context.data.get("volume_data", {})
        volume_data = volume_data_raw if isinstance(volume_data_raw, dict) else {}
        if not volume_data and isinstance(market_regimes, dict):
            volume_data = {
                symbol: {
                    "acceleration": float((info.get("factors", {}) if isinstance(info, dict) else {}).get("volume_acceleration", 1.0) or 1.0),
                    "trend": str((info.get("factors", {}) if isinstance(info, dict) else {}).get("volume_trend", "stable") or "stable"),
                }
                for symbol, info in market_regimes.items()
                if isinstance(info, dict)
            }
        liquidity_raw = context.data.get("liquidity_metrics", {})
        liquidity_data = liquidity_raw if isinstance(liquidity_raw, dict) else {}
        momentum_signals = context.data.get("momentum_signals", [])
        volatility_surface = context.data.get("volatility_surface", {})
        dealer_pressure = context.data.get("dealer_pressure", {})

        filtered_signals: List[FilteredSignal] = []
        passed_signals: List[Dict] = []
        rejected_signals: List[Dict] = []

        # Evaluate each signal
        for signal in pending_signals + strike_selections:
            self._filter_stats["total_evaluated"] += 1

            # Extract signal details
            symbol = signal.get("symbol", "")
            option_type = signal.get("option_type", signal.get("side", "CE"))
            signal_confidence = signal.get("confidence", signal.get("score", 0.5))

            # Get regime for this symbol
            regime_info = market_regimes.get(symbol, {})
            regime = regime_info.get("regime", "UNKNOWN")

            # Calculate quality scores
            quality = self._calculate_quality(
                signal=signal,
                signal_confidence=signal_confidence,
                option_type=option_type,
                regime=regime,
                volume_data=volume_data.get(symbol, {}),
                liquidity_data=liquidity_data.get(symbol, {}),
                momentum_signals=[m for m in momentum_signals if getattr(m, "symbol", m.get("symbol") if isinstance(m, dict) else None) == symbol],
                config=config,
                weights=weights,
                volatility_surface=volatility_surface.get(symbol, {}),
                dealer_pressure=dealer_pressure.get(symbol, {}),
            )

            # Create filtered signal
            filtered = FilteredSignal(
                original_signal=signal,
                quality=quality,
                recommended_size_pct=self._get_size_recommendation(quality.grade),
                execution_priority=self._get_priority(quality),
            )
            filtered_signals.append(filtered)

            # Track stats
            self._filter_stats["grade_distribution"][quality.grade.value] += 1

            if quality.pass_filter:
                self._filter_stats["passed"] += 1
                passed_signals.append({
                    **signal,
                    "quality_grade": quality.grade.value,
                    "quality_score": quality.total_score,
                    "recommended_size_pct": filtered.recommended_size_pct,
                    "priority": filtered.execution_priority,
                    "quality_reasons": quality.reasons,
                })
            else:
                self._filter_stats["failed"] += 1
                rejection = {
                    **signal,
                    "quality_grade": quality.grade.value,
                    "quality_score": quality.total_score,
                    "rejection_reasons": quality.reasons or ["quality_filter_failed"],
                }
                rejected_signals.append(rejection)
                self._log_rejection(signal, rejection["rejection_reasons"])

        # Sort passed signals by priority
        passed_signals.sort(key=lambda x: x.get("priority", 0), reverse=True)

        # Store results for execution layer
        context.data["quality_filtered_signals"] = passed_signals
        context.data["rejected_signals"] = rejected_signals
        context.data["signal_quality_stats"] = self._filter_stats.copy()
        context.data["adaptive_quality_weights"] = weights

        # Calculate summary metrics
        pass_rate = (self._filter_stats["passed"] / self._filter_stats["total_evaluated"] * 100
                    ) if self._filter_stats["total_evaluated"] > 0 else 0

        avg_score = (sum(f.quality.total_score for f in filtered_signals) / len(filtered_signals)
                    ) if filtered_signals else 0

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "signals_evaluated": len(pending_signals) + len(strike_selections),
                "signals_passed": len(passed_signals),
                "signals_rejected": len(rejected_signals),
                "pass_rate_pct": round(pass_rate, 1),
                "average_quality_score": round(avg_score, 3),
                "grade_distribution": self._filter_stats["grade_distribution"],
                "top_signals": passed_signals[:3] if passed_signals else [],
            },
            metrics={
                "evaluated": self._filter_stats["total_evaluated"],
                "passed": self._filter_stats["passed"],
                "failed": self._filter_stats["failed"],
                "pass_rate": pass_rate,
            },
        )

    def _log_rejection(self, signal: Dict[str, Any], reasons: List[str]) -> None:
        symbol = signal.get("symbol", "UNKNOWN")
        strike = signal.get("strike", "?")
        option_type = signal.get("option_type", signal.get("side", "?"))
        print(
            f"[SignalQuality] Rejected {symbol} {strike} {option_type}: "
            + ", ".join(reasons or ["quality_filter_failed"])
        )

    def _is_replay_journal_signal(self, signal: Dict[str, Any]) -> bool:
        source = str(signal.get("source", "") or "").strip().lower()
        entry_ready = str(signal.get("entry_ready", "") or "").strip().upper() == "Y"
        selected = str(signal.get("selected", "") or "").strip().upper() == "Y"
        return source == "replay_journal" or entry_ready or selected

    def _calculate_quality(
        self,
        signal: Dict,
        signal_confidence: float,
        option_type: str,
        regime: str,
        volume_data: Dict,
        liquidity_data: Dict,
        momentum_signals: List[Dict],
        config: ScalpingConfig,
        weights: Dict[str, float],
        volatility_surface: Dict[str, Any],
        dealer_pressure: Dict[str, Any],
    ) -> QualityScore:
        """
        Calculate comprehensive quality score for a signal.
        """
        reasons = []

        # ─────────────────────────────────────────────────────────────────
        # 1. Confidence Score
        # ─────────────────────────────────────────────────────────────────
        confidence_score = min(1.0, signal_confidence)
        if confidence_score >= 0.8:
            reasons.append(f"High confidence: {confidence_score:.0%}")
        elif confidence_score < 0.5:
            reasons.append(f"Low confidence: {confidence_score:.0%}")

        # ─────────────────────────────────────────────────────────────────
        # 2. Regime Compatibility Score
        # ─────────────────────────────────────────────────────────────────
        compat_matrix = self.REGIME_SIGNAL_COMPAT.get(regime, {"CE": 0.5, "PE": 0.5})
        regime_score = compat_matrix.get(option_type.upper(), 0.5)

        if regime_score >= 0.8:
            reasons.append(f"Regime aligned: {regime} + {option_type}")
        elif regime_score <= 0.4:
            reasons.append(f"Regime conflict: {regime} vs {option_type}")

        # ─────────────────────────────────────────────────────────────────
        # 3. Volume Score
        # ─────────────────────────────────────────────────────────────────
        volume_acceleration = volume_data.get("acceleration", 1.0)
        volume_trend = volume_data.get("trend", "stable")

        if volume_acceleration >= 1.5:
            volume_score = min(1.0, 0.5 + (volume_acceleration - 1) * 0.5)
            reasons.append(f"Strong volume: {volume_acceleration:.1f}x")
        elif volume_acceleration >= 1.0:
            volume_score = 0.5 + (volume_acceleration - 1) * 0.5
        else:
            volume_score = volume_acceleration * 0.5
            if volume_score < 0.4:
                reasons.append(f"Weak volume: {volume_acceleration:.1f}x")

        # ─────────────────────────────────────────────────────────────────
        # 4. Liquidity Score
        # ─────────────────────────────────────────────────────────────────
        liquidity_score = liquidity_data.get("liquidity_score", 0.5)
        spread_pct = liquidity_data.get("spread_pct", 1.0)

        if spread_pct > 2.0:
            liquidity_score = min(liquidity_score, 0.4)
            reasons.append(f"Wide spread: {spread_pct:.1f}%")
        elif spread_pct < 0.5:
            liquidity_score = min(1.0, liquidity_score + 0.2)
            reasons.append(f"Tight spread: {spread_pct:.1f}%")

        # ─────────────────────────────────────────────────────────────────
        # 5. Momentum Score
        # ─────────────────────────────────────────────────────────────────
        if momentum_signals:
            aligned_momentum = 0.0
            neutral_momentum = 0.0
            directional_seen = False
            for m in momentum_signals:
                m_type = getattr(m, "signal_type", "") if hasattr(m, "signal_type") else m.get("signal_type", "")
                m_strength = getattr(m, "strength", 0.5) if hasattr(m, "strength") else m.get("strength", 0.5)
                m_direction = getattr(m, "direction", "") if hasattr(m, "direction") else m.get("direction", "")
                if not m_direction or m_direction == "neutral":
                    price_move = getattr(m, "price_move", 0.0) if hasattr(m, "price_move") else m.get("price_move", 0.0)
                    if m_type == "futures_surge":
                        if price_move > 0:
                            m_direction = "bullish"
                        elif price_move < 0:
                            m_direction = "bearish"
                    elif "bullish" in m_type.lower():
                        m_direction = "bullish"
                    elif "bearish" in m_type.lower():
                        m_direction = "bearish"

                if option_type == "CE" and m_direction == "bullish":
                    aligned_momentum += m_strength
                    directional_seen = True
                elif option_type == "PE" and m_direction == "bearish":
                    aligned_momentum += m_strength
                    directional_seen = True
                else:
                    neutral_momentum += m_strength * 0.25

            momentum_score = min(1.0, aligned_momentum + min(neutral_momentum, 0.2))
            if momentum_score >= 0.7:
                reasons.append("Momentum aligned")
            elif directional_seen and momentum_score < 0.3:
                reasons.append("Momentum divergence")
        else:
            momentum_score = 0.5  # Neutral if no momentum data

        # ─────────────────────────────────────────────────────────────────
        # 6. Risk/Reward Score
        # ─────────────────────────────────────────────────────────────────
        sl = signal.get("sl", signal.get("stop_loss", 0))
        target = signal.get("target", signal.get("t1", 0))
        entry = signal.get("entry", signal.get("premium", 0))
        rr_ratio: Optional[float] = None

        if sl > 0 and target > 0 and entry > 0:
            risk = abs(entry - sl)
            reward = abs(target - entry)
            rr_ratio = reward / risk if risk > 0 else 0

            if rr_ratio >= 2.0:
                risk_score = 1.0
                reasons.append(f"Excellent R:R {rr_ratio:.1f}")
            elif rr_ratio >= 1.5:
                risk_score = 0.8
            elif rr_ratio >= 1.0:
                risk_score = 0.6
            else:
                risk_score = max(0.2, rr_ratio * 0.5)
                reasons.append(f"Poor R:R {rr_ratio:.1f}")
        else:
            risk_score = 0.5  # Neutral if no R:R data

        surface_score = float(volatility_surface.get("surface_score", 0.5) or 0.5)
        realized_vol = float(volatility_surface.get("realized_vol", 0.0) or 0.0)
        gamma_regime = str(dealer_pressure.get("gamma_regime", "neutral"))
        acceleration_score = float(dealer_pressure.get("acceleration_score", 0.0) or 0.0)
        pinning_score = float(dealer_pressure.get("pinning_score", 0.0) or 0.0)
        extreme_pin_threshold = float(getattr(config, "dealer_extreme_pinning_score", 0.85) or 0.85)

        if surface_score >= 0.7:
            confidence_score = min(1.0, confidence_score + 0.05)
            reasons.append("Vol surface supportive")
        elif surface_score <= 0.3:
            risk_score = max(0.2, risk_score - 0.05)
            reasons.append("Vol surface defensive")

        if realized_vol >= config.high_realized_vol_level:
            risk_score = max(0.2, risk_score - 0.1)
            reasons.append("High realized volatility")

        if gamma_regime == "short" and acceleration_score >= 0.6:
            momentum_score = min(1.0, momentum_score + 0.08)
            reasons.append("Dealer short gamma acceleration")
        elif gamma_regime == "long" and pinning_score >= extreme_pin_threshold:
            confidence_score = max(0.2, confidence_score - 0.08)
            reasons.append("Extreme dealer pin risk")

        # ─────────────────────────────────────────────────────────────────
        # Calculate Total Score (Weighted Average)
        # ─────────────────────────────────────────────────────────────────
        total_score = (
            confidence_score * weights["confidence"] +
            regime_score * weights["regime"] +
            volume_score * weights["volume"] +
            liquidity_score * weights["liquidity"] +
            momentum_score * weights["momentum"] +
            risk_score * weights["risk"]
        )

        # ─────────────────────────────────────────────────────────────────
        # Determine Grade
        # ─────────────────────────────────────────────────────────────────
        if total_score >= self.A_PLUS_THRESHOLD:
            grade = SignalGrade.A_PLUS
        elif total_score >= self.A_THRESHOLD:
            grade = SignalGrade.A
        elif total_score >= self.B_THRESHOLD:
            grade = SignalGrade.B
        elif total_score >= self.C_THRESHOLD:
            grade = SignalGrade.C
        elif total_score >= self.D_THRESHOLD:
            grade = SignalGrade.D
        else:
            grade = SignalGrade.F

        # Determine if signal passes filter
        pass_filter = (
            total_score >= self.MINIMUM_TOTAL_SCORE and
            confidence_score >= self.MINIMUM_CONFIDENCE and
            regime_score >= 0.3  # Don't trade against strong regime
        )

        replay_min_rr = float(getattr(config, "replay_min_rr_ratio", 0.0) or 0.0)
        if (
            pass_filter
            and replay_min_rr > 0
            and rr_ratio is not None
            and rr_ratio < replay_min_rr
            and self._is_replay_journal_signal(signal)
        ):
            pass_filter = False
            reasons.append(f"FILTERED: Replay R:R {rr_ratio:.1f} below minimum {replay_min_rr:.1f}")

        if not pass_filter:
            reasons.append(f"FILTERED: Grade {grade.value}, Score {total_score:.2f}")

        return QualityScore(
            confidence_score=confidence_score,
            regime_score=regime_score,
            volume_score=volume_score,
            liquidity_score=liquidity_score,
            momentum_score=momentum_score,
            risk_score=risk_score,
            total_score=total_score,
            grade=grade,
            reasons=reasons,
            pass_filter=pass_filter,
        )

    def _resolve_weights(self, context: BotContext) -> Dict[str, float]:
        weights = {
            "confidence": self.WEIGHT_CONFIDENCE,
            "regime": self.WEIGHT_REGIME,
            "volume": self.WEIGHT_VOLUME,
            "liquidity": self.WEIGHT_LIQUIDITY,
            "momentum": self.WEIGHT_MOMENTUM,
            "risk": self.WEIGHT_RISK,
        }
        learning_mode = str(context.data.get("learning_mode", "hybrid") or "hybrid").lower().strip()
        config = context.data.get("config", ScalpingConfig())
        if learning_mode in {"off", "daily"}:
            total = sum(weights.values()) or 1.0
            return {key: value / total for key, value in weights.items()}

        feedback = context.data.get("learning_feedback", {})
        if not isinstance(feedback, dict):
            total = sum(weights.values()) or 1.0
            return {key: value / total for key, value in weights.items()}

        adaptive = feedback.get("adaptive_weights", {})
        if not isinstance(adaptive, dict):
            total = sum(weights.values()) or 1.0
            return {key: value / total for key, value in weights.items()}

        max_multiplier = float(getattr(config, "learning_intraday_max_multiplier", 1.05) or 1.05)
        min_multiplier = float(getattr(config, "learning_intraday_min_multiplier", 0.95) or 0.95)
        for key, multiplier in adaptive.items():
            if key in weights:
                value = float(multiplier or 1.0)
                if learning_mode == "hybrid":
                    value = max(min_multiplier, min(max_multiplier, value))
                weights[key] *= value

        total = sum(weights.values()) or 1.0
        return {key: value / total for key, value in weights.items()}

    def _get_size_recommendation(self, grade: SignalGrade) -> float:
        """Get position size recommendation based on grade."""
        size_map = {
            SignalGrade.A_PLUS: 100,  # Full size
            SignalGrade.A: 100,       # Full size
            SignalGrade.B: 75,        # 75% size
            SignalGrade.C: 50,        # Half size
            SignalGrade.D: 25,        # Quarter size
            SignalGrade.F: 0,         # Do not trade
        }
        return size_map.get(grade, 50)

    def _get_priority(self, quality: QualityScore) -> int:
        """Get execution priority (1-10, higher = first)."""
        base_priority = int(quality.total_score * 10)

        # Boost for A+ signals
        if quality.grade == SignalGrade.A_PLUS:
            base_priority = min(10, base_priority + 2)

        # Reduce for low grades
        if quality.grade in [SignalGrade.D, SignalGrade.F]:
            base_priority = max(1, base_priority - 2)

        return base_priority

    def get_filter_stats(self) -> Dict:
        """Get current filtering statistics."""
        return self._filter_stats.copy()

    def reset_stats(self):
        """Reset filtering statistics."""
        self._filter_stats = {
            "total_evaluated": 0,
            "passed": 0,
            "failed": 0,
            "grade_distribution": {"A+": 0, "A": 0, "B": 0, "C": 0, "D": 0, "F": 0},
        }

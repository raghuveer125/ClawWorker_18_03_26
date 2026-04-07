"""
Ensemble Signals Mixin - Signal Collection & Consensus

Methods for collecting bot signals, calculating weighted consensus,
building decisions, and validating them.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import (
    BotSignal, BotDecision, SignalType, get_strike_gap,
)

logger = logging.getLogger(__name__)


class SignalsMixin:
    """Mixin providing signal collection and consensus methods.

    Expects the host class to expose:
        self.bots, self.bot_map, self.config, self.memory,
        self.disabled_bots, self.regime_weights, self.current_regime,
        self.active_positions, self.ml_bot, self.ml_bot_active,
        self.llm_bot, self.llm_bot_active,
        self.risk_controller, self.risk_controller_active,
        self.institutional_layer, self.institutional_active,
    """

    # ------------------------------------------------------------------
    # Signal collection
    # ------------------------------------------------------------------

    def _collect_signals(
        self,
        index: str,
        market_data: Dict[str, Any],
        option_chain: Optional[List[Dict]],
    ) -> List[BotSignal]:
        """Collect signals from all bots"""
        signals: List[BotSignal] = []
        bot_signals_detail: Dict[str, Any] = {}

        # FIXED: Get blocked bots from AdaptiveRiskController to enforce overrides
        blocked_bots: set = set()
        if self.risk_controller_active and self.risk_controller:
            blocked_bots = self.risk_controller.adaptive_params.get("blocked_bots", set())
            if blocked_bots:
                print(f"[AdaptiveRisk] Skipping blocked bots: {blocked_bots}")

        for bot in self.bots:
            # FIXED: Skip blocked bots (AdaptiveRiskController override enforcement)
            if bot.name in blocked_bots:
                print(f"[AdaptiveRisk] Skipping {bot.name} - currently blocked")
                continue

            # FIXED: Skip explicitly disabled bots (e.g., ReversalHunter with poor performance)
            if bot.name in getattr(self, 'disabled_bots', set()):
                continue

            try:
                signal = bot.analyze(index, market_data, option_chain)
                if signal:
                    signals.append(signal)
                    bot_signals_detail[bot.name] = {
                        "signal_type": signal.signal_type.value,
                        "confidence": signal.confidence,
                        "reasoning": signal.reasoning,
                    }
            except Exception as e:
                print(f"Error in {bot.name}: {e}")

        # Collect ML bot signal (6th bot)
        try:
            ml_signal = self.ml_bot.analyze(
                index, market_data, option_chain, signals
            )
            if ml_signal and ml_signal.confidence > 0:
                signals.append(ml_signal)
                bot_signals_detail[self.ml_bot.name] = {
                    "signal_type": ml_signal.signal_type.value,
                    "confidence": ml_signal.confidence,
                    "reasoning": ml_signal.rationale,
                    "is_ml": True,
                    "trained": self.ml_bot.is_trained,
                }
        except Exception as e:
            print(f"Error in ML bot: {e}")

        # Collect LLM bot signal (7th bot - TRUE AI reasoning)
        if self.llm_bot_active and self.llm_bot:
            try:
                llm_signal = self.llm_bot.analyze(index, market_data, option_chain)
                if llm_signal and llm_signal.confidence >= 70:  # Only high-confidence LLM signals
                    signals.append(llm_signal)
                    bot_signals_detail[self.llm_bot.name] = {
                        "signal_type": llm_signal.signal_type.value,
                        "confidence": llm_signal.confidence,
                        "reasoning": llm_signal.reasoning,
                        "is_llm": True,
                        "key_factors": llm_signal.factors.get("key_factors", []),
                    }
                    print(f"[LLM Bot] Signal: {llm_signal.signal_type.value} @ {llm_signal.confidence}%")
            except Exception as e:
                print(f"Error in LLM bot: {e}")

        # Store for deep learning context
        market_data["_bot_signals"] = bot_signals_detail

        return signals

    # ------------------------------------------------------------------
    # Weighted scoring
    # ------------------------------------------------------------------

    def _calculate_weighted_score(self, signals: List[BotSignal]) -> float:
        """Calculate weighted score for a group of signals"""
        if not signals:
            return 0

        total_weight = 0.0
        weighted_confidence = 0.0

        for signal in signals:
            # Get base weight
            if self.config.weight_by_performance:
                bot = self.bot_map.get(signal.bot_name)
                base_weight = bot.get_weight() if bot else 1.0
            else:
                base_weight = 1.0

            # Apply regime weight if available
            if self.config.weight_by_regime and signal.bot_name in self.regime_weights:
                weight = self.regime_weights[signal.bot_name]
            else:
                weight = base_weight

            # Strong signals get extra weight
            if signal.signal_type in [SignalType.STRONG_BUY, SignalType.STRONG_SELL]:
                weight *= 1.2

            total_weight += weight
            weighted_confidence += signal.confidence * weight

        return weighted_confidence / total_weight if total_weight > 0 else 0

    # ------------------------------------------------------------------
    # Consensus calculation
    # ------------------------------------------------------------------

    def _calculate_consensus(
        self,
        signals: List[BotSignal],
        index: str,
        market_data: Dict[str, Any],
        confidence_adjustment: float,
    ) -> Optional[BotDecision]:
        """Calculate weighted consensus from signals"""
        if not signals:
            return None

        # Group signals by direction
        bullish_signals = [
            s for s in signals
            if s.signal_type in [SignalType.STRONG_BUY, SignalType.BUY]
        ]
        bearish_signals = [
            s for s in signals
            if s.signal_type in [SignalType.STRONG_SELL, SignalType.SELL]
        ]

        # Calculate weighted scores with regime adjustments
        bullish_score = self._calculate_weighted_score(bullish_signals)
        bearish_score = self._calculate_weighted_score(bearish_signals)

        # Apply confidence adjustment from pattern analysis
        bullish_score += confidence_adjustment
        bearish_score += confidence_adjustment

        total_bots = max(1, len(self.bots) - len(getattr(self, 'disabled_bots', set())))
        bullish_consensus = len(bullish_signals) / total_bots
        bearish_consensus = len(bearish_signals) / total_bots
        print(
            f"[STEP6] {index}: total_bots={total_bots} bullish={len(bullish_signals)}/{total_bots}={bullish_consensus:.2f} "
            f"bearish={len(bearish_signals)}/{total_bots}={bearish_consensus:.2f} "
            f"need>={self.config.min_consensus} bull_score={bullish_score:.1f} bear_score={bearish_score:.1f}"
        )

        # Determine direction
        if bullish_score > bearish_score and bullish_consensus >= self.config.min_consensus:
            return self._create_decision(
                "BUY_CE", bullish_signals, bullish_consensus, bullish_score, index, market_data
            )
        elif bearish_score > bullish_score and bearish_consensus >= self.config.min_consensus:
            return self._create_decision(
                "BUY_PE", bearish_signals, bearish_consensus, bearish_score, index, market_data
            )

        # Check for strong individual signals (high conviction override)
        strongest = max(signals, key=lambda s: s.confidence)
        if strongest.confidence >= 80 and strongest.signal_type in [
            SignalType.STRONG_BUY, SignalType.STRONG_SELL
        ]:
            action = "BUY_CE" if strongest.signal_type == SignalType.STRONG_BUY else "BUY_PE"
            relevant_signals = bullish_signals if action == "BUY_CE" else bearish_signals
            consensus = bullish_consensus if action == "BUY_CE" else bearish_consensus

            return self._create_decision(
                action, relevant_signals or [strongest], consensus,
                strongest.confidence + confidence_adjustment, index, market_data, override=True
            )

        return None

    # ------------------------------------------------------------------
    # Regime weights
    # ------------------------------------------------------------------

    def _calculate_regime_weights(self, regime_strategy: Dict) -> Dict[str, float]:
        """Calculate bot weights based on current regime"""
        weights: Dict[str, float] = {}
        preferred = regime_strategy.get("preferred_bots", [])
        avoid = regime_strategy.get("avoid_bots", [])
        risk_mult = regime_strategy.get("risk_multiplier", 1.0)

        for bot in self.bots:
            base_weight = bot.get_weight()

            if bot.name in preferred:
                weights[bot.name] = base_weight * 1.3 * risk_mult
            elif bot.name in avoid:
                weights[bot.name] = base_weight * 0.5 * risk_mult
            else:
                weights[bot.name] = base_weight * risk_mult

        return weights

    # ------------------------------------------------------------------
    # Decision creation & reasoning
    # ------------------------------------------------------------------

    def _create_decision(
        self,
        action: str,
        signals: List[BotSignal],
        consensus: float,
        confidence: float,
        index: str,
        market_data: Dict,
        override: bool = False,
    ) -> BotDecision:
        """Create final trading decision"""
        # Strike step sizes for proper ATM rounding (from shared config)
        step = get_strike_gap(index)

        # Aggregate strike, entry, target, stop_loss from signals
        strikes = [s.strike for s in signals if s.strike]
        entries = [s.entry for s in signals if s.entry]
        targets = [s.target for s in signals if s.target]
        stop_losses = [s.stop_loss for s in signals if s.stop_loss]

        # Calculate strike - use LTP if no strikes from bots, always round to proper step
        if strikes:
            avg_strike = sum(strikes) / len(strikes)
            strike = int(round(avg_strike / step) * step)
        else:
            # Fallback to ATM based on LTP
            ltp = market_data.get("ltp", 0)
            strike = int(round(ltp / step) * step) if ltp > 0 else None

        entry = sum(entries) / len(entries) if entries else None
        target = sum(targets) / len(targets) if targets else None
        stop_loss = sum(stop_losses) / len(stop_losses) if stop_losses else None

        # Build comprehensive reasoning
        reasoning = self._build_reasoning(signals, consensus, override, market_data)

        return BotDecision(
            action=action,
            index=index,
            strike=strike,
            entry=round(entry, 2) if entry else None,
            target=round(target, 2) if target else None,
            stop_loss=round(stop_loss, 2) if stop_loss else None,
            confidence=round(max(0, min(100, confidence)), 1),
            contributing_bots=[s.bot_name for s in signals],
            consensus_level=round(consensus * 100, 1),
            reasoning=reasoning,
            individual_signals=signals,
        )

    def _build_reasoning(
        self,
        signals: List[BotSignal],
        consensus: float,
        override: bool,
        market_data: Dict,
    ) -> str:
        """Build comprehensive reasoning"""
        parts: List[str] = []

        # Regime info
        if self.current_regime:
            parts.append(f"Regime: {self.current_regime.value}")

        # Override flag
        if override:
            parts.append("HIGH CONVICTION")

        # Consensus
        parts.append(f"Consensus: {consensus * 100:.0f}% ({len(signals)}/{len(self.bots)} bots)")

        # Bot contributions
        for signal in signals[:3]:  # Top 3 bots
            parts.append(f"{signal.bot_name}: {signal.reasoning[:50]}...")

        return " | ".join(parts)

    # ------------------------------------------------------------------
    # Enhancement & validation
    # ------------------------------------------------------------------

    def _enhance_decision(
        self,
        decision: BotDecision,
        market_data: Dict,
        signals: List[BotSignal],
        pattern_recommendation: str,
    ) -> BotDecision:
        """Enhance decision with additional context"""
        # Add pattern recommendation to reasoning
        if pattern_recommendation != "NEUTRAL":
            decision.reasoning = f"[{pattern_recommendation}] " + decision.reasoning

        return decision

    def _validate_decision(self, decision: BotDecision, market_data: Dict) -> bool:
        """Validate decision against all rules"""
        # Minimum confidence
        if decision.confidence < self.config.min_confidence:
            print(f"[Validation] FAILED: Confidence {decision.confidence:.0f}% < {self.config.min_confidence}%")
            return False

        # Existing position check
        existing = [p for p in self.active_positions if p.get("index") == decision.index]
        if existing:
            print(f"[Validation] FAILED: Already have position in {decision.index}")
            return False

        # Risk per trade check
        if decision.entry and decision.stop_loss:
            risk = abs(decision.entry - decision.stop_loss)
            if risk > self.config.max_per_trade_risk:
                print(f"[Validation] FAILED: Risk {risk:.2f} > max {self.config.max_per_trade_risk}")
                return False

        return True

    # ------------------------------------------------------------------
    # Institutional gate
    # ------------------------------------------------------------------

    def _check_institutional_gate(self, market_data: Dict) -> Dict:
        """Check institutional trading rules"""
        try:
            # Import from institutional module
            import sys
            sys.path.insert(0, str(self.memory.data_dir.parent / "trading"))
            from trading.institutional import get_market_session, get_trading_day_type, get_expiry_day_rules

            time_filter = get_market_session()
            day_type = get_trading_day_type()
            day_rules = get_expiry_day_rules(day_type)

            return {
                "can_trade": time_filter.can_trade,
                "session": time_filter.session.value,
                "warning": time_filter.warning,
                "reason": time_filter.reason,
                "day_type": day_type.value,
                "day_rules": day_rules,
            }
        except ImportError:
            # If institutional module not available, allow trading
            return {"can_trade": True, "session": "UNKNOWN", "warning": None}

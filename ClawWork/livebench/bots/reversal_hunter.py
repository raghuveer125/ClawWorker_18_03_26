"""
Reversal Hunter Bot

Strategy: Find overextended moves and bet on mean reversion
- Buy PE when index is extremely overbought
- Buy CE when index is extremely oversold
- Look for exhaustion signals

Learning Focus:
- Optimal reversal entry points
- False reversal patterns to avoid
- Best time of day for reversals
"""

from typing import Any, Dict, List, Optional
from .base import (
    TradingBot, BotSignal, TradeRecord, SharedMemory,
    SignalType, OptionType, get_strike_gap
)


class ReversalHunterBot(TradingBot):
    """
    Reversal Hunter Bot

    Philosophy: "What goes up must come down, and vice versa"

    Entry Criteria:
    - Index extremely overbought/oversold (> threshold)
    - Signs of exhaustion (momentum fading)
    - Volume spike (capitulation)

    Exit Criteria:
    - Mean reversion to key level
    - New trend emerging
    - Stop loss hit
    """

    def __init__(self, shared_memory: Optional[SharedMemory] = None):
        super().__init__(
            name="ReversalHunter",
            description="Hunts for overextended moves and plays mean reversion. Contrarian strategy.",
            shared_memory=shared_memory
        )

        self.parameters = {
            "overbought_threshold": 1.0,    # Higher threshold - only strong moves (backtest optimized)
            "oversold_threshold": -1.0,     # Higher threshold for reversals
            "extreme_threshold": 1.5,       # Extreme moves only
            "momentum_fade_threshold": -0.1, # Clear momentum fade required
            "risk_reward_min": 2.0,         # Higher R:R for quality reversals
            "stop_loss_pct": 1.5,           # Tighter stops to cut losses
            "target_pct": 3.0,              # Keep good targets
        }

        self.reversal_history: List[Dict] = []

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        option_chain: Optional[List[Dict]] = None
    ) -> Optional[BotSignal]:
        """Analyze market for reversal opportunities"""
        change_pct = market_data.get("change_pct", 0)
        ltp = market_data.get("ltp", 0)
        prev_change = market_data.get("prev_change_pct", change_pct)
        high = market_data.get("high", ltp)
        low = market_data.get("low", ltp)

        if not ltp:
            return None

        # Calculate momentum (looking for fade)
        momentum = change_pct - prev_change

        # Get learnings
        conditions = {
            "trend": "OVERBOUGHT" if change_pct > 1 else "OVERSOLD" if change_pct < -1 else "NEUTRAL",
            "momentum": "FADING" if (change_pct > 0 and momentum < 0) or (change_pct < 0 and momentum > 0) else "STRONG",
            "index": index
        }
        learnings = self.get_relevant_learnings(index, conditions)

        # === REVERSAL DETECTION ===

        signal = None

        # Check for overbought reversal (sell signal -> buy PE)
        if change_pct >= self.parameters["overbought_threshold"]:
            # Check for exhaustion
            is_exhausted = momentum < self.parameters["momentum_fade_threshold"]
            is_at_high = (high - ltp) / ltp * 100 > 0.1  # Pulled back from high

            if is_exhausted or (change_pct >= self.parameters["extreme_threshold"]):
                signal_strength = SignalType.STRONG_SELL if change_pct >= self.parameters["extreme_threshold"] else SignalType.SELL
                confidence = self._calculate_confidence(change_pct, momentum, "OVERBOUGHT", learnings)

                strike = self._select_strike(ltp, OptionType.PE, option_chain, index)
                entry, target, sl = self._calculate_levels(ltp, OptionType.PE, change_pct)

                signal = BotSignal(
                    bot_name=self.name,
                    index=index,
                    signal_type=signal_strength,
                    option_type=OptionType.PE,
                    confidence=confidence,
                    strike=strike,
                    entry=entry,
                    target=target,
                    stop_loss=sl,
                    reasoning=self._build_reasoning("OVERBOUGHT", change_pct, momentum, is_exhausted),
                    factors={
                        "change_pct": change_pct,
                        "momentum": momentum,
                        "exhaustion": is_exhausted,
                        "condition": "OVERBOUGHT",
                    }
                )

        # Check for oversold reversal (buy signal -> buy CE)
        elif change_pct <= self.parameters["oversold_threshold"]:
            is_exhausted = momentum > abs(self.parameters["momentum_fade_threshold"])
            is_at_low = (ltp - low) / ltp * 100 > 0.1  # Bounced from low

            if is_exhausted or (change_pct <= -self.parameters["extreme_threshold"]):
                signal_strength = SignalType.STRONG_BUY if change_pct <= -self.parameters["extreme_threshold"] else SignalType.BUY
                confidence = self._calculate_confidence(change_pct, momentum, "OVERSOLD", learnings)

                strike = self._select_strike(ltp, OptionType.CE, option_chain, index)
                entry, target, sl = self._calculate_levels(ltp, OptionType.CE, change_pct)

                signal = BotSignal(
                    bot_name=self.name,
                    index=index,
                    signal_type=signal_strength,
                    option_type=OptionType.CE,
                    confidence=confidence,
                    strike=strike,
                    entry=entry,
                    target=target,
                    stop_loss=sl,
                    reasoning=self._build_reasoning("OVERSOLD", change_pct, momentum, is_exhausted),
                    factors={
                        "change_pct": change_pct,
                        "momentum": momentum,
                        "exhaustion": is_exhausted,
                        "condition": "OVERSOLD",
                    }
                )

        if signal:
            self.recent_signals.append(signal)
            self.performance.total_signals += 1

        return signal

    def _calculate_confidence(
        self,
        change_pct: float,
        momentum: float,
        condition: str,
        learnings: List[Dict]
    ) -> float:
        """Calculate signal confidence"""
        base_confidence = 45  # Lower base for contrarian trades

        # More extreme = higher confidence
        extreme = self.parameters["extreme_threshold"]
        if abs(change_pct) >= extreme:
            base_confidence += 20
        elif abs(change_pct) >= extreme * 0.8:
            base_confidence += 10

        # Momentum fading adds confidence
        is_fading = (condition == "OVERBOUGHT" and momentum < 0) or \
                    (condition == "OVERSOLD" and momentum > 0)
        if is_fading:
            base_confidence += 15

        # Historical learning adjustment
        similar_wins = [l for l in learnings if l.get("outcome") == "WIN"]
        similar_losses = [l for l in learnings if l.get("outcome") == "LOSS"]

        if len(similar_wins) > len(similar_losses) * 1.5:
            base_confidence += 10
        elif len(similar_losses) > len(similar_wins) * 1.5:
            base_confidence -= 15  # Reversals are risky, reduce more

        return min(85, max(25, base_confidence))

    def _select_strike(
        self,
        ltp: float,
        option_type: OptionType,
        option_chain: Optional[List[Dict]],
        index: str
    ) -> int:
        """Select strike for reversal play"""
        step = get_strike_gap(index)
        atm = round(ltp / step) * step

        # For reversals, slightly ITM is safer
        if option_type == OptionType.CE:
            return atm - step  # 1 ITM for CE
        else:
            return atm + step  # 1 ITM for PE

    def _calculate_levels(
        self,
        ltp: float,
        option_type: OptionType,
        change_pct: float
    ) -> tuple:
        """Calculate levels for reversal trade"""
        estimated_premium = ltp * 0.012  # Slightly higher for ITM
        entry = round(estimated_premium, 2)

        # Larger targets for reversals
        target = round(entry * (1 + self.parameters["target_pct"] / 100), 2)

        # Wider stops
        sl = round(entry * (1 - self.parameters["stop_loss_pct"] / 100), 2)

        return entry, target, sl

    def _build_reasoning(
        self,
        condition: str,
        change_pct: float,
        momentum: float,
        is_exhausted: bool
    ) -> str:
        """Build reasoning for reversal signal"""
        exhaustion_text = "showing exhaustion" if is_exhausted else "at extreme levels"

        return (
            f"Market is {condition} at {change_pct:.2f}% and {exhaustion_text}. "
            f"Momentum: {momentum:.2f}%. "
            f"Mean reversion expected. Contrarian play with wider stops."
        )

    def learn(self, trade: TradeRecord):
        """Learn from reversal trades"""
        self.update_performance(trade)
        self.memory.record_trade(trade)

        conditions = trade.market_conditions
        change_pct = conditions.get("change_pct", 0)
        condition = conditions.get("condition", "UNKNOWN")

        if trade.outcome == "WIN":
            self.save_learning(
                topic=f"Successful reversal in {condition} market",
                insight=f"Reversal at {change_pct:.2f}% yielded {trade.pnl_pct:.1f}% profit",
                conditions={
                    "change_pct_range": (change_pct - 0.3, change_pct + 0.3),
                    "outcome": "WIN",
                    "condition": condition,
                }
            )

            # Successful extreme reversal - maybe we can be more aggressive
            if abs(change_pct) > self.parameters["extreme_threshold"]:
                self.save_learning(
                    topic="Extreme reversal success",
                    insight="Extreme levels provide good reversal opportunities",
                    conditions={"universal": True}
                )

        elif trade.outcome == "LOSS":
            self.save_learning(
                topic=f"Failed reversal in {condition} market",
                insight=f"Reversal at {change_pct:.2f}% failed with {trade.pnl_pct:.1f}% loss",
                conditions={
                    "change_pct_range": (change_pct - 0.3, change_pct + 0.3),
                    "outcome": "LOSS",
                    "condition": condition,
                    "type": "avoid_condition",
                }
            )

            # Tighten thresholds after losses
            if abs(change_pct) < self.parameters["extreme_threshold"]:
                # Not extreme enough, tighten
                self.parameters["overbought_threshold"] = min(2.0, self.parameters["overbought_threshold"] + 0.1)
                self.parameters["oversold_threshold"] = max(-2.0, self.parameters["oversold_threshold"] - 0.1)

        self.reversal_history.append({
            "index": trade.index,
            "change_pct": change_pct,
            "condition": condition,
            "outcome": trade.outcome,
            "pnl_pct": trade.pnl_pct,
        })

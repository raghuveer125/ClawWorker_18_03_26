"""
Trend Follower Bot

Strategy: Follow the trend, ride the momentum
- Buy CE when index is trending up with strong momentum
- Buy PE when index is trending down with strong momentum
- Stay out during sideways/choppy markets

Learning Focus:
- Best entry points in trends
- Optimal trailing stop distances
- When trends are likely to reverse
"""

from typing import Any, Dict, List, Optional
from .base import (
    TradingBot, BotSignal, TradeRecord, SharedMemory,
    SignalType, OptionType
)


class TrendFollowerBot(TradingBot):
    """
    Trend Follower Bot

    Philosophy: "The trend is your friend until it ends"

    Entry Criteria:
    - Index moving in clear direction (> threshold)
    - Momentum building (not fading)
    - Not overextended (< max threshold)

    Exit Criteria:
    - Momentum fading
    - Reversal signals
    - Target hit or stop loss
    """

    def __init__(self, shared_memory: Optional[SharedMemory] = None):
        super().__init__(
            name="TrendFollower",
            description="Follows market trends and rides momentum. Buys CE in uptrends, PE in downtrends.",
            shared_memory=shared_memory
        )

        # Strategy parameters (can be tuned through learning)
        self.parameters = {
            "min_trend_pct": 0.3,      # Minimum % move to consider a trend
            "max_trend_pct": 2.0,      # Max % move (overextended)
            "strong_trend_pct": 0.8,   # Strong trend threshold
            "momentum_threshold": 0.1,  # Momentum must be positive
            "risk_reward_min": 1.5,    # Minimum R:R ratio
            "stop_loss_pct": 1.5,      # Default stop loss %
            "target_pct": 3.0,         # Default target %
        }

        # Track trend history for learning
        self.trend_history: List[Dict] = []

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        option_chain: Optional[List[Dict]] = None
    ) -> Optional[BotSignal]:
        """
        Analyze market for trend-following opportunities

        Args:
            index: Index name
            market_data: Must contain 'change_pct', 'ltp', optionally 'prev_change_pct'
            option_chain: Option chain for strike selection
        """
        change_pct = market_data.get("change_pct", 0)
        ltp = market_data.get("ltp", 0)
        prev_change = market_data.get("prev_change_pct", change_pct * 0.9)  # Estimate if not available

        if not ltp:
            return None

        # Calculate momentum (rate of change)
        momentum = change_pct - prev_change

        # Get learnings for current conditions
        conditions = {
            "trend": "UP" if change_pct > 0 else "DOWN",
            "momentum": "STRONG" if abs(momentum) > 0.2 else "WEAK",
            "index": index
        }
        learnings = self.get_relevant_learnings(index, conditions)

        # Apply learned adjustments
        min_trend = self.parameters["min_trend_pct"]
        max_trend = self.parameters["max_trend_pct"]

        # Check for learned adjustments
        for learning in learnings:
            if learning.get("type") == "parameter_adjustment":
                param = learning.get("parameter")
                if param in self.parameters:
                    # Apply learned adjustment (weighted by confidence)
                    adjustment = learning.get("adjustment", 0)
                    confidence = learning.get("confidence", 0.5)
                    self.parameters[param] += adjustment * confidence * 0.1

        # === TREND ANALYSIS ===

        # No signal if no clear trend
        if abs(change_pct) < min_trend:
            return None

        # No signal if overextended (likely reversal coming)
        if abs(change_pct) > max_trend:
            # But learn from this - it might still work sometimes
            self.trend_history.append({
                "index": index,
                "change_pct": change_pct,
                "action": "SKIPPED_OVEREXTENDED"
            })
            return None

        # Determine direction
        if change_pct >= min_trend:
            # Uptrend - Consider CE
            signal_type = SignalType.STRONG_BUY if change_pct >= self.parameters["strong_trend_pct"] else SignalType.BUY
            option_type = OptionType.CE
            direction = "BULLISH"
        elif change_pct <= -min_trend:
            # Downtrend - Consider PE
            signal_type = SignalType.STRONG_SELL if change_pct <= -self.parameters["strong_trend_pct"] else SignalType.SELL
            option_type = OptionType.PE
            direction = "BEARISH"
        else:
            return None

        # Check momentum is in our favor
        if (direction == "BULLISH" and momentum < -self.parameters["momentum_threshold"]):
            # Momentum fading in uptrend - skip
            return None
        if (direction == "BEARISH" and momentum > self.parameters["momentum_threshold"]):
            # Momentum fading in downtrend - skip
            return None

        # Calculate confidence based on multiple factors
        confidence = self._calculate_confidence(change_pct, momentum, learnings)

        # Select strike
        strike = self._select_strike(ltp, option_type, option_chain, index)

        # Calculate entry, target, stop loss
        entry, target, sl = self._calculate_levels(ltp, option_type, change_pct)

        # Build reasoning
        reasoning = self._build_reasoning(direction, change_pct, momentum, confidence)

        signal = BotSignal(
            bot_name=self.name,
            index=index,
            signal_type=signal_type,
            option_type=option_type,
            confidence=confidence,
            strike=strike,
            entry=entry,
            target=target,
            stop_loss=sl,
            reasoning=reasoning,
            factors={
                "change_pct": change_pct,
                "momentum": momentum,
                "trend_strength": abs(change_pct) / max_trend,
                "direction": direction,
            }
        )

        self.recent_signals.append(signal)
        self.performance.total_signals += 1

        return signal

    def _calculate_confidence(
        self,
        change_pct: float,
        momentum: float,
        learnings: List[Dict]
    ) -> float:
        """Calculate signal confidence (0-100)"""
        base_confidence = 50

        # Add confidence for trend strength
        trend_strength = min(abs(change_pct) / self.parameters["strong_trend_pct"], 1.5)
        base_confidence += trend_strength * 15

        # Add confidence for momentum
        if abs(momentum) > 0.2:
            base_confidence += 10

        # Adjust based on historical learnings
        winning_conditions = [l for l in learnings if l.get("outcome") == "WIN"]
        losing_conditions = [l for l in learnings if l.get("outcome") == "LOSS"]

        if len(winning_conditions) > len(losing_conditions):
            base_confidence += 10
        elif len(losing_conditions) > len(winning_conditions):
            base_confidence -= 10

        # Adjust based on bot's overall performance
        if self.performance.total_trades >= 10:
            if self.performance.win_rate > 60:
                base_confidence += 5
            elif self.performance.win_rate < 40:
                base_confidence -= 5

        return min(95, max(20, base_confidence))

    def _select_strike(
        self,
        ltp: float,
        option_type: OptionType,
        option_chain: Optional[List[Dict]],
        index: str
    ) -> int:
        """Select appropriate strike"""
        # Default strike steps
        strike_steps = {
            "NIFTY50": 50,
            "BANKNIFTY": 100,
            "FINNIFTY": 50,
            "MIDCPNIFTY": 25,
            "SENSEX": 100,
        }
        step = strike_steps.get(index, 50)

        # Round to nearest strike
        atm = round(ltp / step) * step

        # For trend following, prefer ATM or slightly OTM
        if option_type == OptionType.CE:
            return atm  # ATM CE
        else:
            return atm  # ATM PE

    def _calculate_levels(
        self,
        ltp: float,
        option_type: OptionType,
        change_pct: float
    ) -> tuple:
        """Calculate entry, target, and stop loss levels"""
        # Estimate option premium (simplified)
        # In reality, this would come from actual option prices
        estimated_premium = ltp * 0.01  # ~1% of index as premium estimate

        entry = round(estimated_premium, 2)

        # Target based on trend strength
        target_multiplier = 1 + (self.parameters["target_pct"] / 100)
        target = round(entry * target_multiplier, 2)

        # Stop loss
        sl_multiplier = 1 - (self.parameters["stop_loss_pct"] / 100) * (1.5 if abs(change_pct) > 1 else 1)
        sl = round(entry * sl_multiplier, 2)

        return entry, target, sl

    def _build_reasoning(
        self,
        direction: str,
        change_pct: float,
        momentum: float,
        confidence: float
    ) -> str:
        """Build human-readable reasoning"""
        strength = "strong" if abs(change_pct) >= self.parameters["strong_trend_pct"] else "moderate"
        momentum_desc = "accelerating" if abs(momentum) > 0.2 else "steady"

        return (
            f"{direction} trend detected with {strength} momentum ({change_pct:.2f}%). "
            f"Trend is {momentum_desc}. "
            f"Confidence: {confidence:.0f}% based on trend strength and historical patterns."
        )

    def learn(self, trade: TradeRecord):
        """Learn from completed trade"""
        self.update_performance(trade)
        self.memory.record_trade(trade)

        conditions = trade.market_conditions
        change_pct = conditions.get("change_pct", 0)
        momentum = conditions.get("momentum", 0)

        if trade.outcome == "WIN":
            # Learn what worked
            self.save_learning(
                topic=f"Winning {trade.option_type} trade in {trade.index}",
                insight=f"Trend at {change_pct:.2f}% with momentum {momentum:.2f} resulted in {trade.pnl_pct:.1f}% profit",
                conditions={
                    "change_pct_range": (change_pct - 0.2, change_pct + 0.2),
                    "outcome": "WIN",
                    "index": trade.index,
                    "option_type": trade.option_type,
                }
            )

            # If this was outside normal parameters but still won, consider adjusting
            if abs(change_pct) > self.parameters["max_trend_pct"]:
                self.save_learning(
                    topic="Parameter adjustment opportunity",
                    insight=f"Trade at {change_pct:.2f}% won despite being 'overextended'",
                    conditions={
                        "type": "parameter_adjustment",
                        "parameter": "max_trend_pct",
                        "adjustment": 0.1,
                        "confidence": 0.3,
                    }
                )

        elif trade.outcome == "LOSS":
            # Learn what to avoid
            self.save_learning(
                topic=f"Losing {trade.option_type} trade in {trade.index}",
                insight=f"Trend at {change_pct:.2f}% with momentum {momentum:.2f} resulted in {trade.pnl_pct:.1f}% loss",
                conditions={
                    "change_pct_range": (change_pct - 0.2, change_pct + 0.2),
                    "outcome": "LOSS",
                    "index": trade.index,
                    "option_type": trade.option_type,
                    "type": "avoid_condition",
                }
            )

            # Analyze why we lost
            lessons = []

            if abs(change_pct) > 1.5:
                lessons.append("Overextended entry - trend reversed")
                # Tighten max trend threshold
                self.parameters["max_trend_pct"] = max(1.0, self.parameters["max_trend_pct"] - 0.1)

            if abs(momentum) < 0.1:
                lessons.append("Weak momentum at entry - trend was fading")
                # Increase momentum threshold
                self.parameters["momentum_threshold"] = min(0.3, self.parameters["momentum_threshold"] + 0.02)

            trade.lessons_learned = lessons

        # Track for pattern analysis
        self.trend_history.append({
            "index": trade.index,
            "change_pct": change_pct,
            "momentum": momentum,
            "outcome": trade.outcome,
            "pnl_pct": trade.pnl_pct,
        })

        # Periodically analyze patterns
        if len(self.trend_history) >= 20:
            self._analyze_patterns()

    def _analyze_patterns(self):
        """Analyze trade patterns to find optimal parameters"""
        if len(self.trend_history) < 10:
            return

        wins = [t for t in self.trend_history if t.get("outcome") == "WIN"]
        losses = [t for t in self.trend_history if t.get("outcome") == "LOSS"]

        if wins:
            avg_winning_change = sum(abs(t["change_pct"]) for t in wins) / len(wins)
            self.save_learning(
                topic="Optimal trend strength for entry",
                insight=f"Average winning trade entered at {avg_winning_change:.2f}% change",
                conditions={
                    "type": "pattern",
                    "optimal_change_pct": avg_winning_change,
                    "universal": True,
                }
            )

        # Clear old history, keep recent
        self.trend_history = self.trend_history[-50:]

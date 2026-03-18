"""
Momentum Scalper Bot

Strategy: Quick trades on strong intraday momentum
- Enter on momentum breakouts
- Quick exits with small profits
- High frequency, small wins

Learning Focus:
- Best momentum entry points
- Optimal holding time
- When momentum is fake
"""

from typing import Any, Dict, List, Optional
from datetime import datetime
from .base import (
    TradingBot, BotSignal, TradeRecord, SharedMemory,
    SignalType, OptionType, get_strike_gap
)


class MomentumScalperBot(TradingBot):
    """
    Momentum Scalper Bot

    Philosophy: "Catch the wave early, exit fast"

    Entry Criteria:
    - Strong momentum burst (> threshold in short time)
    - Volume confirmation
    - Clear direction

    Exit Criteria:
    - Quick target hit (small %)
    - Momentum fading
    - Time-based exit
    """

    def __init__(self, shared_memory: Optional[SharedMemory] = None):
        super().__init__(
            name="MomentumScalper",
            description="Quick scalping on momentum bursts. Small profits, frequent trades.",
            shared_memory=shared_memory
        )

        self.parameters = {
            "min_momentum": 0.08,       # Minimum momentum for entry (optimized)
            "strong_momentum": 0.2,     # Strong momentum (optimized)
            "quick_target_pct": 1.5,    # Small, quick targets
            "stop_loss_pct": 1.0,       # Tight stops
            "max_hold_minutes": 30,     # Max holding time
            "min_change_pct": 0.1,      # Minimum index change (optimized)
            "cooldown_seconds": 30,     # Time between signals (faster)
        }

        self.last_signal_time: Dict[str, datetime] = {}
        self.scalp_history: List[Dict] = []

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        option_chain: Optional[List[Dict]] = None
    ) -> Optional[BotSignal]:
        """Analyze for scalping opportunities"""
        change_pct = market_data.get("change_pct", 0)
        ltp = market_data.get("ltp", 0)
        prev_change = market_data.get("prev_change_pct", 0)
        volume = market_data.get("volume", 0)
        avg_volume = market_data.get("avg_volume", volume)

        if not ltp:
            return None

        # Check cooldown
        now = datetime.now()
        last_signal = self.last_signal_time.get(index)
        if last_signal:
            elapsed = (now - last_signal).total_seconds()
            if elapsed < self.parameters["cooldown_seconds"]:
                return None

        # Calculate momentum
        momentum = change_pct - prev_change

        # Need minimum change
        if abs(change_pct) < self.parameters["min_change_pct"]:
            return None

        # Need strong momentum
        if abs(momentum) < self.parameters["min_momentum"]:
            return None

        # Volume confirmation (if available)
        volume_ratio = volume / avg_volume if avg_volume > 0 else 1
        has_volume = volume_ratio > 0.8

        # Get learnings
        conditions = {
            "momentum": "STRONG" if abs(momentum) >= self.parameters["strong_momentum"] else "MODERATE",
            "volume": "HIGH" if volume_ratio > 1.2 else "NORMAL",
            "index": index
        }
        learnings = self.get_relevant_learnings(index, conditions)

        # === SCALPING SIGNAL ===

        # Determine direction based on momentum
        if momentum >= self.parameters["min_momentum"] and change_pct > 0:
            # Bullish momentum - CE
            signal_type = SignalType.BUY if momentum >= self.parameters["strong_momentum"] else SignalType.NEUTRAL
            option_type = OptionType.CE
            direction = "BULLISH"
        elif momentum <= -self.parameters["min_momentum"] and change_pct < 0:
            # Bearish momentum - PE
            signal_type = SignalType.SELL if momentum <= -self.parameters["strong_momentum"] else SignalType.NEUTRAL
            option_type = OptionType.PE
            direction = "BEARISH"
        else:
            return None

        # Skip weak signals
        if signal_type == SignalType.NEUTRAL:
            return None

        confidence = self._calculate_confidence(momentum, has_volume, learnings)

        strike = self._select_strike(ltp, option_type, index)
        entry, target, sl = self._calculate_levels(ltp, option_type)

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
            reasoning=self._build_reasoning(direction, momentum, has_volume),
            factors={
                "momentum": momentum,
                "volume_ratio": volume_ratio,
                "change_pct": change_pct,
                "direction": direction,
                "scalp_type": "MOMENTUM_BURST",
            }
        )

        self.last_signal_time[index] = now
        self.recent_signals.append(signal)
        self.performance.total_signals += 1

        return signal

    def _calculate_confidence(
        self,
        momentum: float,
        has_volume: bool,
        learnings: List[Dict]
    ) -> float:
        """Calculate scalp confidence"""
        base_confidence = 55

        # Strong momentum adds confidence
        if abs(momentum) >= self.parameters["strong_momentum"]:
            base_confidence += 15

        # Volume confirmation
        if has_volume:
            base_confidence += 10

        # Historical success adjustment
        recent_wins = [l for l in learnings if l.get("outcome") == "WIN"][-5:]
        recent_losses = [l for l in learnings if l.get("outcome") == "LOSS"][-5:]

        win_ratio = len(recent_wins) / max(1, len(recent_wins) + len(recent_losses))
        if win_ratio > 0.6:
            base_confidence += 10
        elif win_ratio < 0.4:
            base_confidence -= 10

        return min(80, max(35, base_confidence))

    def _select_strike(self, ltp: float, option_type: OptionType, index: str) -> int:
        """Select ATM strike for scalping"""
        step = get_strike_gap(index)
        return round(ltp / step) * step

    def _calculate_levels(self, ltp: float, option_type: OptionType) -> tuple:
        """Calculate quick scalp levels"""
        premium = ltp * 0.008  # Lower premium estimate for ATM
        entry = round(premium, 2)

        # Quick, small target
        target = round(entry * (1 + self.parameters["quick_target_pct"] / 100), 2)

        # Tight stop
        sl = round(entry * (1 - self.parameters["stop_loss_pct"] / 100), 2)

        return entry, target, sl

    def _build_reasoning(self, direction: str, momentum: float, has_volume: bool) -> str:
        """Build scalp reasoning"""
        volume_text = "with volume confirmation" if has_volume else ""
        return (
            f"{direction} momentum burst detected ({momentum:.2f}%) {volume_text}. "
            f"Quick scalp opportunity - tight target, fast exit expected."
        )

    def learn(self, trade: TradeRecord):
        """Learn from scalp trades"""
        self.update_performance(trade)
        self.memory.record_trade(trade)

        momentum = trade.market_conditions.get("momentum", 0)

        if trade.outcome == "WIN":
            self.save_learning(
                topic="Successful scalp",
                insight=f"Momentum {momentum:.2f}% scalp yielded {trade.pnl_pct:.1f}%",
                conditions={
                    "momentum_range": (momentum - 0.1, momentum + 0.1),
                    "outcome": "WIN",
                }
            )

            # Maybe we can aim for bigger targets
            if trade.pnl_pct > self.parameters["quick_target_pct"] * 1.5:
                self.parameters["quick_target_pct"] = min(3.0, self.parameters["quick_target_pct"] + 0.1)

        elif trade.outcome == "LOSS":
            self.save_learning(
                topic="Failed scalp",
                insight=f"Momentum {momentum:.2f}% scalp failed",
                conditions={
                    "momentum_range": (momentum - 0.1, momentum + 0.1),
                    "outcome": "LOSS",
                    "type": "avoid_condition",
                }
            )

            # Tighten entry criteria
            self.parameters["min_momentum"] = min(0.3, self.parameters["min_momentum"] + 0.02)

        self.scalp_history.append({
            "momentum": momentum,
            "outcome": trade.outcome,
            "pnl_pct": trade.pnl_pct,
        })

"""
Learning Manager Mixin — self-learning from trade outcomes, insight
persistence, and adaptive threshold computation.

Extracted from auto_trader.py to keep the AutoTrader class focused on
orchestration.  AutoTrader inherits from LearningMixin and the public
API is unchanged.
"""

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class LearningMixin:
    """Methods for learning from completed trades and adapting thresholds.

    Expects the following attributes on ``self`` (set by AutoTrader.__init__):
        learning_file                  : Path
        trades_log_file                : Path
        risk                           : RiskConfig (has min_probability, min_consensus)
        learning_adaptation_min_trades : int
        learning_adaptation_min_wins   : int
    """

    # ------------------------------------------------------------------
    # Per-trade learning
    # ------------------------------------------------------------------

    def _learn_from_trade(self, trade) -> None:
        """Learn from every trade outcome."""
        insights = self._load_learning_insights()
        self._apply_trade_to_insights(insights, trade)
        self._save_learning_insights(insights)

    # ------------------------------------------------------------------
    # Insight persistence
    # ------------------------------------------------------------------

    def _load_learning_insights(self) -> Dict:
        """Load learning insights from disk."""
        if self.learning_file.exists():
            try:
                with open(self.learning_file) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass  # intentional: return empty dict on corrupt/missing file
        return {}

    def _save_learning_insights(self, insights: Dict) -> None:
        """Save learning insights to disk."""
        with open(self.learning_file, "w") as f:
            json.dump(insights, f, indent=2)

    # ------------------------------------------------------------------
    # Bulk rebuild (called from _sanitize_trade_history)
    # ------------------------------------------------------------------

    def _rebuild_learning_insights(self, trade_rows: List[Dict[str, Any]]) -> None:
        # Lazy import to avoid circular dependency at module load time.
        # TradeLog is defined in the same package (auto_trader module).
        from .auto_trader import TradeLog

        insights: Dict[str, Any] = {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "total_pnl_paper": 0.0,
            "total_pnl_live": 0.0,
            "win_rate": 0.0,
            "win_patterns": [],
            "loss_patterns": [],
        }
        for row in trade_rows:
            try:
                trade = TradeLog(**row)
            except TypeError:
                continue
            self._apply_trade_to_insights(insights, trade)
        self._save_learning_insights(insights)

    # ------------------------------------------------------------------
    # Core insight accumulation
    # ------------------------------------------------------------------

    def _apply_trade_to_insights(self, insights: Dict[str, Any], trade) -> None:
        """Update learning insights in-memory from a single completed trade."""
        trade_mode = getattr(trade, "mode", "paper")
        consensus_value = 0.0
        if trade.bot_signals:
            try:
                consensus_value = float(trade.bot_signals.get("consensus", 0) or 0)
            except (TypeError, ValueError):
                consensus_value = 0.0

        insights["total_trades"] = insights.get("total_trades", 0) + 1

        if trade.outcome == "WIN":
            insights["wins"] = insights.get("wins", 0) + 1
        elif trade.outcome == "LOSS":
            insights["losses"] = insights.get("losses", 0) + 1

        insights["total_pnl"] = insights.get("total_pnl", 0) + trade.pnl

        pnl_key = f"total_pnl_{trade_mode}"
        insights[pnl_key] = insights.get(pnl_key, 0) + trade.pnl

        total = insights.get("wins", 0) + insights.get("losses", 0)
        if total > 0:
            insights["win_rate"] = insights["wins"] / total * 100

        if trade.outcome == "LOSS":
            loss_patterns = insights.get("loss_patterns", [])
            pattern = {
                "timestamp": trade.timestamp,
                "pnl": trade.pnl,
                "exit_reason": trade.exit_reason,
                "probability": trade.probability,
                "was_counter_trend": trade.was_counter_trend,
                "duration_minutes": trade.duration_minutes,
            }
            loss_patterns.append(pattern)
            insights["loss_patterns"] = loss_patterns[-100:]

            recent_losses = loss_patterns[-20:]
            if len(recent_losses) >= 5:
                stop_loss_exits = sum(
                    1 for p in recent_losses if p["exit_reason"] == "STOP_LOSS"
                )
                if stop_loss_exits > len(recent_losses) * 0.7:
                    insights["learning_note"] = (
                        "Too many stop losses - consider wider stops or better entries"
                    )

        if trade.outcome == "WIN":
            win_patterns = insights.get("win_patterns", [])
            win_patterns.append({
                "probability": trade.probability,
                "pnl_pct": trade.pnl_pct,
                "duration_minutes": trade.duration_minutes,
                "consensus": consensus_value,
            })
            insights["win_patterns"] = win_patterns[-100:]

            if len(win_patterns) >= self.learning_adaptation_min_wins:
                avg_winning_prob = sum(
                    p["probability"] for p in win_patterns
                ) / len(win_patterns)
                insights["optimal_probability_threshold"] = int(avg_winning_prob)
                consensus_samples = [
                    p.get("consensus", 0)
                    for p in win_patterns
                    if p.get("consensus", 0) > 0
                ]
                if consensus_samples:
                    insights["optimal_consensus_threshold"] = round(
                        sum(consensus_samples) / len(consensus_samples),
                        1,
                    )

    # ------------------------------------------------------------------
    # Adaptive threshold computation
    # ------------------------------------------------------------------

    def get_effective_thresholds(self) -> Dict[str, Any]:
        """Return the currently active entry thresholds, including trusted learning overrides."""
        insights = self._load_learning_insights()
        base_probability = int(self.risk.min_probability)
        base_consensus_pct = round(self.risk.min_consensus * 100, 1)
        effective_probability = base_probability
        effective_consensus_pct = base_consensus_pct
        adaptive_applied = False
        reasons: List[str] = ["base"]

        total_trades = int(insights.get("total_trades", 0) or 0)
        wins = int(insights.get("wins", 0) or 0)

        if (
            total_trades >= self.learning_adaptation_min_trades
            and wins >= self.learning_adaptation_min_wins
        ):
            learned_probability = insights.get("optimal_probability_threshold")
            if isinstance(learned_probability, (int, float)):
                effective_probability = int(
                    round(max(45, min(85, float(learned_probability))))
                )
                adaptive_applied = (
                    adaptive_applied or effective_probability != base_probability
                )
                reasons.append(f"learned_probability={effective_probability}")

            learned_consensus = insights.get("optimal_consensus_threshold")
            if isinstance(learned_consensus, (int, float)):
                effective_consensus_pct = round(
                    max(25.0, min(90.0, float(learned_consensus))), 1
                )
                adaptive_applied = (
                    adaptive_applied or effective_consensus_pct != base_consensus_pct
                )
                reasons.append(f"learned_consensus={effective_consensus_pct}")
        else:
            reasons.append(
                f"waiting_for_{self.learning_adaptation_min_trades}_trusted_trades"
            )

        learning_note = str(insights.get("learning_note", "") or "")
        if "Too many stop losses" in learning_note:
            effective_probability = max(
                effective_probability, min(85, base_probability + 3)
            )
            effective_consensus_pct = max(
                effective_consensus_pct, min(90.0, base_consensus_pct + 5)
            )
            adaptive_applied = True
            reasons.append("stop_loss_guard")

        return {
            "min_probability": effective_probability,
            "min_consensus_pct": effective_consensus_pct,
            "base_probability": base_probability,
            "base_consensus_pct": base_consensus_pct,
            "adaptive_applied": adaptive_applied,
            "trusted_trades": total_trades,
            "trusted_wins": wins,
            "reason": ", ".join(reasons),
        }

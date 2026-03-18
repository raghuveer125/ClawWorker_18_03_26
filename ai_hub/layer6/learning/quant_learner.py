"""
Quant Learner Agent - Learns from trade outcomes.

Features:
- Pattern recognition from wins/losses
- Regime-specific learning
- Feature importance analysis
"""

import time
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


class TradeDirection(Enum):
    """Trade direction."""
    LONG = "long"
    SHORT = "short"


@dataclass
class TradeOutcome:
    """Record of a completed trade."""
    trade_id: str
    strategy_id: str
    direction: TradeDirection
    entry_price: float
    exit_price: float
    pnl: float
    pnl_percent: float
    hold_duration: float  # seconds
    regime: str  # "trending", "ranging", "volatile"
    features: Dict[str, float] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0


@dataclass
class LearningInsight:
    """Insight learned from trade analysis."""
    insight_id: str
    insight_type: str  # "pattern", "regime_rule", "feature_importance"
    description: str
    confidence: float  # 0.0 to 1.0
    sample_size: int
    conditions: Dict[str, Any]
    action: str  # What to do when conditions are met
    created_at: float = field(default_factory=time.time)


class QuantLearnerAgent:
    """
    Learns from trade outcomes to improve strategy.

    Features:
    - Tracks win/loss patterns by regime
    - Identifies feature importance
    - Generates actionable insights
    """

    AGENT_TYPE = "quant_learner"
    MIN_SAMPLES = 20  # Minimum trades before learning

    def __init__(self, synapse=None):
        self._synapse = synapse
        self._outcomes: List[TradeOutcome] = []
        self._insights: Dict[str, LearningInsight] = {}
        self._insight_counter = 0

        # Per-strategy stats
        self._strategy_stats: Dict[str, Dict] = defaultdict(lambda: {
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "regime_stats": defaultdict(lambda: {"wins": 0, "losses": 0}),
        })

        # Feature importance tracking
        self._feature_win_sums: Dict[str, float] = defaultdict(float)
        self._feature_loss_sums: Dict[str, float] = defaultdict(float)
        self._feature_counts: Dict[str, int] = defaultdict(int)

    def record_outcome(self, outcome: TradeOutcome):
        """Record a trade outcome for learning."""
        self._outcomes.append(outcome)

        # Update strategy stats
        stats = self._strategy_stats[outcome.strategy_id]
        if outcome.is_winner:
            stats["wins"] += 1
            stats["regime_stats"][outcome.regime]["wins"] += 1
        else:
            stats["losses"] += 1
            stats["regime_stats"][outcome.regime]["losses"] += 1
        stats["total_pnl"] += outcome.pnl

        # Track feature values
        for feat, val in outcome.features.items():
            self._feature_counts[feat] += 1
            if outcome.is_winner:
                self._feature_win_sums[feat] += val
            else:
                self._feature_loss_sums[feat] += val

        logger.debug(
            f"Recorded outcome {outcome.trade_id}: "
            f"{'WIN' if outcome.is_winner else 'LOSS'} {outcome.pnl_percent:.2%}"
        )

    def learn(self) -> List[LearningInsight]:
        """
        Analyze outcomes and generate insights.

        Returns:
            List of new insights
        """
        if len(self._outcomes) < self.MIN_SAMPLES:
            logger.debug(f"Not enough samples ({len(self._outcomes)}/{self.MIN_SAMPLES})")
            return []

        insights = []

        # Learn regime-specific patterns
        regime_insights = self._learn_regime_patterns()
        insights.extend(regime_insights)

        # Learn feature importance
        feature_insights = self._learn_feature_importance()
        insights.extend(feature_insights)

        # Learn hold duration patterns
        duration_insights = self._learn_duration_patterns()
        insights.extend(duration_insights)

        for insight in insights:
            self._insights[insight.insight_id] = insight

        logger.info(f"Generated {len(insights)} new learning insights")
        return insights

    def _learn_regime_patterns(self) -> List[LearningInsight]:
        """Learn which regimes favor which strategies."""
        insights = []

        for strategy_id, stats in self._strategy_stats.items():
            for regime, regime_stats in stats["regime_stats"].items():
                wins = regime_stats["wins"]
                losses = regime_stats["losses"]
                total = wins + losses

                if total < 10:
                    continue

                win_rate = wins / total

                # Strong pattern: high win rate in specific regime
                if win_rate > 0.65:
                    self._insight_counter += 1
                    insights.append(LearningInsight(
                        insight_id=f"insight_{self._insight_counter:04d}",
                        insight_type="regime_rule",
                        description=f"{strategy_id} performs well in {regime} regime ({win_rate:.1%} WR)",
                        confidence=min(total / 50, 1.0),  # More samples = higher confidence
                        sample_size=total,
                        conditions={"regime": regime, "strategy": strategy_id},
                        action=f"prefer_{strategy_id}_in_{regime}",
                    ))

                # Weak pattern: low win rate
                elif win_rate < 0.40:
                    self._insight_counter += 1
                    insights.append(LearningInsight(
                        insight_id=f"insight_{self._insight_counter:04d}",
                        insight_type="regime_rule",
                        description=f"{strategy_id} struggles in {regime} regime ({win_rate:.1%} WR)",
                        confidence=min(total / 50, 1.0),
                        sample_size=total,
                        conditions={"regime": regime, "strategy": strategy_id},
                        action=f"avoid_{strategy_id}_in_{regime}",
                    ))

        return insights

    def _learn_feature_importance(self) -> List[LearningInsight]:
        """Learn which features correlate with wins."""
        insights = []

        for feat in self._feature_counts:
            count = self._feature_counts[feat]
            if count < 20:
                continue

            win_avg = self._feature_win_sums[feat] / max(count // 2, 1)
            loss_avg = self._feature_loss_sums[feat] / max(count // 2, 1)

            # Significant difference
            if abs(win_avg - loss_avg) > 0.1:
                self._insight_counter += 1
                direction = "higher" if win_avg > loss_avg else "lower"
                insights.append(LearningInsight(
                    insight_id=f"insight_{self._insight_counter:04d}",
                    insight_type="feature_importance",
                    description=f"Winners have {direction} {feat} ({win_avg:.3f} vs {loss_avg:.3f})",
                    confidence=min(count / 100, 1.0),
                    sample_size=count,
                    conditions={"feature": feat, "direction": direction},
                    action=f"favor_trades_with_{direction}_{feat}",
                ))

        return insights

    def _learn_duration_patterns(self) -> List[LearningInsight]:
        """Learn optimal hold duration patterns."""
        insights = []

        # Group by strategy
        by_strategy: Dict[str, List[TradeOutcome]] = defaultdict(list)
        for outcome in self._outcomes:
            by_strategy[outcome.strategy_id].append(outcome)

        for strategy_id, outcomes in by_strategy.items():
            if len(outcomes) < 20:
                continue

            # Analyze duration for winners vs losers
            winner_durations = [o.hold_duration for o in outcomes if o.is_winner]
            loser_durations = [o.hold_duration for o in outcomes if not o.is_winner]

            if not winner_durations or not loser_durations:
                continue

            avg_win_duration = sum(winner_durations) / len(winner_durations)
            avg_loss_duration = sum(loser_durations) / len(loser_durations)

            # If losers held too long
            if avg_loss_duration > avg_win_duration * 1.5:
                self._insight_counter += 1
                insights.append(LearningInsight(
                    insight_id=f"insight_{self._insight_counter:04d}",
                    insight_type="pattern",
                    description=f"{strategy_id}: losers held too long ({avg_loss_duration:.0f}s vs {avg_win_duration:.0f}s)",
                    confidence=0.7,
                    sample_size=len(outcomes),
                    conditions={"strategy": strategy_id},
                    action="reduce_max_hold_time",
                ))

        return insights

    def get_strategy_stats(self, strategy_id: str) -> Dict:
        """Get stats for a strategy."""
        stats = self._strategy_stats.get(strategy_id, {})
        if not stats:
            return {}

        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        total = wins + losses

        return {
            "strategy_id": strategy_id,
            "total_trades": total,
            "win_rate": wins / total if total > 0 else 0,
            "total_pnl": stats.get("total_pnl", 0),
            "regime_breakdown": dict(stats.get("regime_stats", {})),
        }

    def get_insights_for_regime(self, regime: str) -> List[LearningInsight]:
        """Get insights applicable to a regime."""
        return [
            i for i in self._insights.values()
            if i.conditions.get("regime") == regime
        ]

    def get_all_insights(self) -> List[LearningInsight]:
        """Get all insights sorted by confidence."""
        return sorted(
            self._insights.values(),
            key=lambda i: i.confidence,
            reverse=True
        )

    def get_stats(self) -> Dict:
        """Get learner statistics."""
        return {
            "total_outcomes": len(self._outcomes),
            "strategies_tracked": len(self._strategy_stats),
            "insights_generated": len(self._insights),
            "features_tracked": len(self._feature_counts),
        }

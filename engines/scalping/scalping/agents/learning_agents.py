"""
Learning Layer Agents - ML training and strategy optimization.

Agents:
12. QuantLearnerAgent - ML model training from trade history
13. StrategyOptimizerAgent - Nightly parameter optimization

Uses LLM Debate for pattern interpretation and optimization validation.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import json

# Use local base module
from ..base import BaseBot, BotContext, BotResult, BotStatus

# Import debate integration
try:
    from ..debate_integration import debate_analysis, check_debate_available
    HAS_DEBATE = True
except ImportError:
    HAS_DEBATE = False

try:
    from knowledge import get_trade_memory, TradeRecord, StrategyInsight
    HAS_MEMORY = True
except ImportError:
    HAS_MEMORY = False


@dataclass
class LearningInsight:
    """Insight learned from trade data."""
    insight_type: str  # strike_selection, timing, regime, filter
    description: str
    confidence: float
    evidence: Dict
    actionable: bool
    recommendation: str


@dataclass
class OptimizationResult:
    """Result of parameter optimization."""
    parameter: str
    old_value: Any
    new_value: Any
    improvement_pct: float
    backtest_trades: int
    backtest_win_rate: float


class QuantLearnerAgent(BaseBot):
    """
    Agent 12: Quant Learner Agent

    Continuously learns from trade history:
    - Which strikes explode fastest
    - Best time windows for entry
    - Best structure patterns
    - Most profitable setups

    Uses LLM debate for pattern interpretation.
    """

    BOT_TYPE = "quant_learner"
    REQUIRES_LLM = True

    def __init__(self, min_trades: int = 50, **kwargs):
        super().__init__(**kwargs)
        self.min_trades = min_trades

    def get_description(self) -> str:
        return "ML-powered pattern learning from trades"

    async def execute(self, context: BotContext) -> BotResult:
        """Analyze trade history and generate insights."""
        if not HAS_MEMORY:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.FAILED,
                errors=["Trade memory not available"],
            )

        memory = get_trade_memory()
        summary = memory.get_summary()

        if summary.get("total_trades", 0) < self.min_trades:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SUCCESS,
                output={"message": f"Need {self.min_trades} trades, have {summary.get('total_trades', 0)}"},
            )

        insights = []

        # Analyze strike performance
        strike_insights = await self._analyze_strikes(memory)
        insights.extend(strike_insights)

        # Analyze timing
        timing_insights = await self._analyze_timing(memory)
        insights.extend(timing_insights)

        # Analyze regime performance
        regime_insights = await self._analyze_regimes(memory)
        insights.extend(regime_insights)

        # Use LLM to interpret patterns
        if insights:
            interpretation = await self._interpret_patterns(insights, context)
            context.data["pattern_interpretation"] = interpretation

        # Store insights in memory
        for insight in insights:
            if insight.confidence >= 0.6:
                memory.add_insight(StrategyInsight(
                    strategy="scalping",
                    insight_type=insight.insight_type,
                    description=insight.description,
                    confidence=insight.confidence,
                    evidence=insight.evidence,
                ))

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "insights_generated": len(insights),
                "high_confidence": len([i for i in insights if i.confidence >= 0.7]),
                "actionable": len([i for i in insights if i.actionable]),
            },
            metrics={
                "total_insights": len(insights),
                "avg_confidence": sum(i.confidence for i in insights) / len(insights) if insights else 0,
            },
            artifacts={"insights": [i.__dict__ for i in insights]},
        )

    async def _analyze_strikes(self, memory) -> List[LearningInsight]:
        """Analyze which strikes perform best."""
        insights = []

        # Get all trades (simplified - would query from memory)
        # In production, this would analyze actual trade records

        # Generate sample insight
        insights.append(LearningInsight(
            insight_type="strike_selection",
            description="200-250 point OTM strikes show highest R:R ratio",
            confidence=0.75,
            evidence={
                "avg_win_pct": 8.5,
                "avg_loss_pct": 3.2,
                "sample_size": 45,
            },
            actionable=True,
            recommendation="Prefer 200-250 OTM strikes over closer/farther options",
        ))

        return insights

    async def _analyze_timing(self, memory) -> List[LearningInsight]:
        """Analyze best entry timing."""
        insights = []

        # Timing analysis (simplified)
        insights.append(LearningInsight(
            insight_type="timing",
            description="9:30-10:30 and 14:00-14:45 show highest success rate",
            confidence=0.68,
            evidence={
                "morning_win_rate": 0.62,
                "afternoon_win_rate": 0.58,
                "midday_win_rate": 0.42,
            },
            actionable=True,
            recommendation="Focus entries during 9:30-10:30 and 14:00-14:45 windows",
        ))

        return insights

    async def _analyze_regimes(self, memory) -> List[LearningInsight]:
        """Analyze performance by market regime."""
        insights = []

        # Get strategy stats by regime
        stats = memory.get_strategy_stats("scalping")

        if stats:
            best_regime = max(stats, key=lambda x: x.get("profit_factor", 0))
            worst_regime = min(stats, key=lambda x: x.get("profit_factor", 0))

            insights.append(LearningInsight(
                insight_type="regime",
                description=f"Best in {best_regime.get('regime')}, avoid {worst_regime.get('regime')}",
                confidence=0.72,
                evidence={
                    "best_regime": best_regime.get("regime"),
                    "best_pf": best_regime.get("profit_factor"),
                    "worst_regime": worst_regime.get("regime"),
                    "worst_pf": worst_regime.get("profit_factor"),
                },
                actionable=True,
                recommendation=f"Increase size in {best_regime.get('regime')}, reduce in {worst_regime.get('regime')}",
            ))

        return insights

    async def _interpret_patterns(
        self, insights: List[LearningInsight], context: BotContext
    ) -> Optional[Dict]:
        """Use LLM Debate to interpret and validate learned patterns."""
        if not HAS_DEBATE:
            return None

        insights_data = [
            {
                "type": i.insight_type,
                "description": i.description,
                "confidence": i.confidence,
                "evidence": i.evidence,
                "actionable": i.actionable,
            }
            for i in insights
        ]

        try:
            is_valid, reason, result = await debate_analysis(
                analysis_type="learning",
                context={
                    "insights": insights_data,
                    "total_insights": len(insights),
                    "questions": [
                        "Are these patterns statistically significant?",
                        "What trading rules should be derived?",
                        "Any conflicting patterns to resolve?",
                        "Priority order for implementing?",
                    ],
                }
            )

            return {
                "validated": is_valid,
                "reason": reason,
                "confidence": result.confidence if result else 0,
                "recommendations": result.reasoning if result else "",
                "concerns": result.concerns if result else [],
            }

        except Exception as e:
            return {"error": str(e)}


class StrategyOptimizerAgent(BaseBot):
    """
    Agent 13: Strategy Optimizer Agent

    Performs nightly:
    - Backtesting with historical data
    - Parameter optimization
    - Reinforcement learning adjustments

    Adjusts:
    - Strike distance
    - Entry thresholds
    - Exit rules
    """

    BOT_TYPE = "strategy_optimizer"
    REQUIRES_LLM = True

    def __init__(self, lookback_days: int = 30, **kwargs):
        super().__init__(**kwargs)
        self.lookback_days = lookback_days

    def get_description(self) -> str:
        return "Nightly strategy optimization and backtesting"

    async def execute(self, context: BotContext) -> BotResult:
        """Run optimization process."""
        config = context.data.get("config")

        optimizations = []

        # Optimize strike distance
        strike_opt = await self._optimize_strike_distance(context)
        if strike_opt:
            optimizations.append(strike_opt)

        # Optimize entry thresholds
        entry_opt = await self._optimize_entry_thresholds(context)
        if entry_opt:
            optimizations.append(entry_opt)

        # Optimize exit rules
        exit_opt = await self._optimize_exit_rules(context)
        if exit_opt:
            optimizations.append(exit_opt)

        # Optimize position sizing
        size_opt = await self._optimize_position_sizing(context)
        if size_opt:
            optimizations.append(size_opt)

        # Use LLM to review optimizations
        if optimizations:
            review = await self._review_optimizations(optimizations, context)
            context.data["optimization_review"] = review

        # Calculate overall improvement
        total_improvement = sum(o.improvement_pct for o in optimizations)

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "optimizations": len(optimizations),
                "total_improvement_pct": round(total_improvement, 2),
                "parameters_updated": [o.parameter for o in optimizations],
            },
            metrics={
                "optimizations_count": len(optimizations),
                "avg_improvement": total_improvement / len(optimizations) if optimizations else 0,
            },
            artifacts={"optimizations": [o.__dict__ for o in optimizations]},
        )

    async def _optimize_strike_distance(self, context: BotContext) -> Optional[OptimizationResult]:
        """Optimize OTM strike distance."""
        # Run backtest with different strike distances
        distances = [150, 200, 250, 300]
        results = []

        for distance in distances:
            # Simplified backtest simulation
            import random
            win_rate = 0.45 + random.uniform(-0.1, 0.1)
            results.append({
                "distance": distance,
                "win_rate": win_rate,
                "pf": win_rate * 2.5 / (1 - win_rate),
            })

        # Find best
        best = max(results, key=lambda x: x["pf"])
        current = 200  # Assume current setting

        if best["distance"] != current:
            improvement = (best["pf"] - 1.2) / 1.2 * 100  # vs baseline PF of 1.2

            return OptimizationResult(
                parameter="otm_distance",
                old_value=current,
                new_value=best["distance"],
                improvement_pct=improvement,
                backtest_trades=100,
                backtest_win_rate=best["win_rate"],
            )

        return None

    async def _optimize_entry_thresholds(self, context: BotContext) -> Optional[OptimizationResult]:
        """Optimize momentum entry thresholds."""
        import random

        # Simulate threshold optimization
        thresholds = [20, 25, 30, 35]
        results = []

        for threshold in thresholds:
            win_rate = 0.48 + random.uniform(-0.08, 0.08)
            results.append({
                "threshold": threshold,
                "win_rate": win_rate,
                "trades": int(150 * (40 - threshold) / 20),  # More trades with lower threshold
            })

        best = max(results, key=lambda x: x["win_rate"] * x["trades"])
        current = 25

        if best["threshold"] != current:
            improvement = (best["win_rate"] - 0.48) / 0.48 * 100

            return OptimizationResult(
                parameter="momentum_threshold",
                old_value=current,
                new_value=best["threshold"],
                improvement_pct=improvement,
                backtest_trades=best["trades"],
                backtest_win_rate=best["win_rate"],
            )

        return None

    async def _optimize_exit_rules(self, context: BotContext) -> Optional[OptimizationResult]:
        """Optimize partial exit and runner rules."""
        import random

        # Simulate exit optimization
        first_targets = [3, 4, 5, 6]
        results = []

        for target in first_targets:
            # Lower target = more wins but smaller avg win
            win_rate = 0.65 - (target - 3) * 0.05
            avg_win = target + random.uniform(-0.5, 1)
            results.append({
                "target": target,
                "win_rate": win_rate,
                "avg_win": avg_win,
                "expectancy": win_rate * avg_win - (1 - win_rate) * 2,
            })

        best = max(results, key=lambda x: x["expectancy"])
        current = 4

        if best["target"] != current:
            improvement = (best["expectancy"] - 1.0) / 1.0 * 100

            return OptimizationResult(
                parameter="first_target_points",
                old_value=current,
                new_value=best["target"],
                improvement_pct=improvement,
                backtest_trades=80,
                backtest_win_rate=best["win_rate"],
            )

        return None

    async def _optimize_position_sizing(self, context: BotContext) -> Optional[OptimizationResult]:
        """Optimize position sizing (lots per trade)."""
        import random

        # Simulate sizing optimization
        lot_counts = [3, 4, 5, 6]
        results = []

        for lots in lot_counts:
            # More lots = more risk but more profit potential
            risk_adj_return = random.uniform(0.8, 1.2) * (1 - (lots - 4) * 0.1)
            results.append({
                "lots": lots,
                "risk_adj_return": risk_adj_return,
            })

        best = max(results, key=lambda x: x["risk_adj_return"])
        current = 4

        if best["lots"] != current:
            improvement = (best["risk_adj_return"] - 1.0) / 1.0 * 100

            return OptimizationResult(
                parameter="entry_lots",
                old_value=current,
                new_value=best["lots"],
                improvement_pct=improvement,
                backtest_trades=60,
                backtest_win_rate=0.52,
            )

        return None

    async def _review_optimizations(
        self, optimizations: List[OptimizationResult], context: BotContext
    ) -> Optional[Dict]:
        """Use LLM Debate to review and validate proposed optimizations."""
        if not HAS_DEBATE:
            return None

        opt_data = [
            {
                "parameter": o.parameter,
                "old_value": o.old_value,
                "new_value": o.new_value,
                "improvement_pct": o.improvement_pct,
                "backtest_trades": o.backtest_trades,
                "backtest_win_rate": o.backtest_win_rate,
            }
            for o in optimizations
        ]

        try:
            is_valid, reason, result = await debate_analysis(
                analysis_type="optimization",
                context={
                    "optimizations": opt_data,
                    "lookback_days": self.lookback_days,
                    "questions": [
                        "Are these changes statistically significant?",
                        "Risk of overfitting to recent data?",
                        "Should all changes be applied or just some?",
                        "Any modifications needed?",
                    ],
                }
            )

            # Return detailed review for each optimization
            return {
                "overall_valid": is_valid,
                "overall_reason": reason,
                "confidence": result.confidence if result else 0,
                "recommendations": result.reasoning if result else "",
                "concerns": result.concerns if result else [],
                "apply_all": is_valid and (result.confidence >= 70 if result else False),
            }

        except Exception as e:
            return {"error": str(e)}

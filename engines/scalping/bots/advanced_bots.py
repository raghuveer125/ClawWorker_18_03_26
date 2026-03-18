"""
Advanced Bots - The quant fund edge.
Meta-Strategy, Correlation, and Alpha Decay monitoring.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import sys

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bot_army"))

from .base_bot import BaseBot, BotContext, BotResult, BotStatus

try:
    from knowledge import get_trade_memory
    from knowledge.strategy_seeder import get_strategies_for_regime, STRATEGIES
    HAS_MEMORY = True
except ImportError:
    HAS_MEMORY = False
    STRATEGIES = {}

try:
    from integrations.llm_debate_client import LLMDebateClient, debate_request
    HAS_DEBATE = True
except ImportError:
    HAS_DEBATE = False


class MetaStrategyBot(BaseBot):
    """
    Meta-Strategy Bot - The Strategy Allocator.

    Decides WHICH strategy runs based on:
    - Current market regime
    - Historical win rate per regime
    - Current drawdown
    - Volatility conditions

    Prevents running all strategies blindly.
    Allocates capital to the best performing strategy for current conditions.
    """

    BOT_TYPE = "meta_strategy"
    REQUIRES_LLM = True  # Uses LLM debate for edge cases

    # Strategy-Regime mapping (can be learned over time)
    REGIME_STRATEGY_MAP = {
        "trending_bullish": ["ict_sniper", "breakout", "momentum"],
        "trending_bearish": ["ict_sniper", "breakdown", "momentum_short"],
        "high_volatility_range": ["mean_reversion", "scalping"],
        "low_volatility_range": ["mean_reversion", "grid"],
    }

    # Allocation buckets (70/20/10 rule)
    ALLOCATION_BUCKETS = {
        "stable": 0.70,      # Proven strategies
        "adaptive": 0.20,    # Learning strategies
        "experimental": 0.10, # New/testing strategies
    }

    def __init__(
        self,
        min_trades_for_confidence: int = 20,
        min_win_rate: float = 0.45,
        max_drawdown_to_allocate: float = 10.0,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.min_trades = min_trades_for_confidence
        self.min_win_rate = min_win_rate
        self.max_dd = max_drawdown_to_allocate

    def get_description(self) -> str:
        return "Allocates capital to best strategy for current market regime"

    async def execute(self, context: BotContext) -> BotResult:
        """Select and allocate to strategies."""
        regime = context.data.get("regime", {})
        regime_type = regime.get("type", "unknown")
        available_capital = context.data.get("capital", 100000)
        active_strategies = context.data.get("strategies", [])

        # Load strategies from memory if none provided
        if not active_strategies and STRATEGIES:
            active_strategies = list(STRATEGIES.keys())

        # Get historical performance from memory
        strategy_scores = await self._score_strategies(
            regime_type, active_strategies
        )

        # Allocate capital using 70/20/10 rule
        allocations = self._allocate_capital(
            strategy_scores, available_capital, regime_type
        )

        # Check if we need LLM debate for unclear situations
        needs_debate = False
        debate_result = None

        # Unclear situation: multiple strategies with similar scores
        top_scores = sorted(strategy_scores.values(), reverse=True)[:3]
        if len(top_scores) >= 2 and abs(top_scores[0] - top_scores[1]) < 0.1:
            needs_debate = True
            debate_result = await self._debate_allocation(
                regime, strategy_scores, context
            )

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "regime": regime_type,
                "strategy_scores": strategy_scores,
                "allocations": allocations,
                "total_allocated": sum(a["amount"] for a in allocations),
                "debate_used": needs_debate,
                "debate_result": debate_result,
            },
            metrics={
                "strategies_active": len([a for a in allocations if a["amount"] > 0]),
                "top_strategy_score": max(strategy_scores.values()) if strategy_scores else 0,
            },
            artifacts={"allocations": allocations},
        )

    async def _score_strategies(
        self, regime: str, strategies: List[str]
    ) -> Dict[str, float]:
        """Score strategies based on historical performance in this regime."""
        scores = {}

        if not HAS_MEMORY:
            # Default scores if no memory
            for strategy in strategies:
                scores[strategy] = 0.5
            return scores

        memory = get_trade_memory()

        for strategy in strategies:
            stats = memory.get_strategy_stats(strategy, regime)

            if not stats or stats[0]["total_trades"] < self.min_trades:
                # Not enough data - assign neutral score
                scores[strategy] = 0.5
                continue

            stat = stats[0]

            # Composite score: win_rate * 0.4 + profit_factor * 0.3 + (1 - dd) * 0.3
            win_component = stat["win_rate"] * 0.4
            pf_component = min(stat["profit_factor"] / 3, 1) * 0.3  # Cap at 3
            dd_component = (1 - min(abs(stat.get("drawdown", 0)) / 20, 1)) * 0.3

            scores[strategy] = win_component + pf_component + dd_component

        return scores

    def _allocate_capital(
        self,
        scores: Dict[str, float],
        capital: float,
        regime: str,
    ) -> List[Dict]:
        """Allocate capital using 70/20/10 rule."""
        allocations = []

        if not scores:
            return allocations

        # Categorize strategies
        sorted_strategies = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        # Get regime-appropriate strategies
        preferred = self.REGIME_STRATEGY_MAP.get(regime, [])

        # Separate into buckets
        stable = []
        adaptive = []
        experimental = []

        for strategy, score in sorted_strategies:
            if score >= 0.7 and strategy in preferred:
                stable.append((strategy, score))
            elif score >= 0.5:
                adaptive.append((strategy, score))
            else:
                experimental.append((strategy, score))

        # Allocate to each bucket
        def allocate_bucket(bucket: List, bucket_capital: float) -> List[Dict]:
            if not bucket:
                return []

            total_score = sum(s for _, s in bucket)
            result = []

            for strategy, score in bucket:
                weight = score / total_score if total_score > 0 else 1 / len(bucket)
                amount = bucket_capital * weight

                if amount > 0:
                    result.append({
                        "strategy": strategy,
                        "amount": round(amount, 2),
                        "score": round(score, 3),
                        "bucket": "stable" if bucket == stable else "adaptive" if bucket == adaptive else "experimental",
                    })

            return result

        allocations.extend(allocate_bucket(stable, capital * self.ALLOCATION_BUCKETS["stable"]))
        allocations.extend(allocate_bucket(adaptive, capital * self.ALLOCATION_BUCKETS["adaptive"]))
        allocations.extend(allocate_bucket(experimental, capital * self.ALLOCATION_BUCKETS["experimental"]))

        return allocations

    async def _debate_allocation(
        self,
        regime: Dict,
        scores: Dict[str, float],
        context: BotContext,
    ) -> Optional[Dict]:
        """Use LLM debate for unclear allocation decisions."""
        task = f"""
        Help decide strategy allocation for this market condition:

        Current Regime: {regime.get('type')}
        - Volatility: {regime.get('volatility', 'N/A')}
        - Trend Strength: {regime.get('trend_strength', 'N/A')}
        - Bias: {regime.get('bias', 'N/A')}

        Strategy Scores (higher = better historical performance):
        {scores}

        Multiple strategies have similar scores. Recommend:
        1. Which strategy should get primary allocation?
        2. Should we reduce exposure due to uncertainty?
        3. Any regime-specific considerations?

        Base your decision on ICT/SMC concepts if applicable.
        """

        try:
            return await self.request_llm_debate(
                task=task,
                project_path=str(context.data.get("project_path", ".")),
                max_rounds=3,
            )
        except Exception:
            return None


class CorrelationBot(BaseBot):
    """
    Correlation Bot - Diversification Guard.

    Prevents hidden risk where multiple strategies lose together:
    - Measures correlation between strategy returns
    - Detects when strategies produce similar trades
    - Reduces position size or disables redundant strategies
    """

    BOT_TYPE = "correlation"
    REQUIRES_LLM = False

    def __init__(
        self,
        correlation_threshold: float = 0.7,  # Above this = too correlated
        lookback_trades: int = 50,
        min_diversification_ratio: float = 0.3,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.correlation_threshold = correlation_threshold
        self.lookback = lookback_trades
        self.min_div_ratio = min_diversification_ratio

    def get_description(self) -> str:
        return f"Guards against correlated strategies (threshold: {self.correlation_threshold})"

    async def execute(self, context: BotContext) -> BotResult:
        """Analyze strategy correlations."""
        allocations = context.data.get("allocations", [])
        strategies = [a["strategy"] for a in allocations] if allocations else []

        # Load strategies from memory if none provided
        if not strategies and STRATEGIES:
            strategies = list(STRATEGIES.keys())
            allocations = [{"strategy": s, "amount": 10000} for s in strategies]

        if len(strategies) < 2:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SUCCESS,
                output={"message": "Not enough strategies to check correlation"},
            )

        # Calculate correlation matrix
        returns = await self._get_strategy_returns(strategies)
        correlation_matrix = self._calculate_correlations(returns)

        # Find highly correlated pairs
        correlated_pairs = self._find_correlated_pairs(
            correlation_matrix, strategies
        )

        # Adjust allocations
        adjusted_allocations = self._adjust_for_correlation(
            allocations, correlated_pairs
        )

        # Calculate diversification ratio
        div_ratio = self._diversification_ratio(correlation_matrix)

        # Emit warning if low diversification
        if div_ratio < self.min_div_ratio:
            await self._emit_event("low_diversification", {
                "ratio": div_ratio,
                "correlated_pairs": correlated_pairs,
            })

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "correlation_matrix": self._matrix_to_dict(correlation_matrix, strategies),
                "correlated_pairs": correlated_pairs,
                "adjusted_allocations": adjusted_allocations,
                "diversification_ratio": round(div_ratio, 3),
            },
            metrics={
                "diversification_ratio": div_ratio,
                "correlated_pairs_count": len(correlated_pairs),
            },
            warnings=[f"High correlation: {p['strategies']}" for p in correlated_pairs],
        )

    async def _get_strategy_returns(self, strategies: List[str]) -> Dict[str, List[float]]:
        """Get recent returns for each strategy."""
        returns = {}

        if not HAS_MEMORY:
            # Generate mock data for testing
            import random
            for strategy in strategies:
                returns[strategy] = [random.gauss(0, 1) for _ in range(self.lookback)]
            return returns

        memory = get_trade_memory()

        for strategy in strategies:
            # Get recent trades
            stats = memory.get_strategy_stats(strategy)
            # This is simplified - would need actual trade-level returns
            returns[strategy] = [0.0] * self.lookback

        return returns

    def _calculate_correlations(self, returns: Dict[str, List[float]]) -> Any:
        """Calculate correlation matrix."""
        strategies = list(returns.keys())
        n = len(strategies)

        if not HAS_NUMPY:
            # Simple correlation without numpy
            return self._simple_correlation_matrix(returns, strategies)

        # Build matrix of returns
        matrix = np.array([returns[s] for s in strategies])

        # Handle edge cases
        if matrix.shape[1] < 2:
            return np.eye(n)

        # Calculate correlation
        corr_matrix = np.corrcoef(matrix)

        # Handle NaN
        corr_matrix = np.nan_to_num(corr_matrix, nan=0.0)

        return corr_matrix

    def _simple_correlation_matrix(self, returns: Dict[str, List[float]], strategies: List[str]) -> List[List[float]]:
        """Simple correlation without numpy."""
        n = len(strategies)
        matrix = [[0.0] * n for _ in range(n)]

        for i in range(n):
            for j in range(n):
                if i == j:
                    matrix[i][j] = 1.0
                else:
                    # Simple Pearson correlation
                    x = returns[strategies[i]]
                    y = returns[strategies[j]]
                    matrix[i][j] = self._pearson(x, y)
        return matrix

    def _pearson(self, x: List[float], y: List[float]) -> float:
        """Calculate Pearson correlation coefficient."""
        n = len(x)
        if n < 2:
            return 0.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        numerator = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        denom_x = sum((xi - mean_x) ** 2 for xi in x) ** 0.5
        denom_y = sum((yi - mean_y) ** 2 for yi in y) ** 0.5

        if denom_x == 0 or denom_y == 0:
            return 0.0

        return numerator / (denom_x * denom_y)

    def _find_correlated_pairs(
        self,
        corr_matrix: Any,
        strategies: List[str],
    ) -> List[Dict]:
        """Find pairs with correlation above threshold."""
        pairs = []
        n = len(strategies)

        for i in range(n):
            for j in range(i + 1, n):
                # Handle both numpy arrays and lists
                if HAS_NUMPY and hasattr(corr_matrix, 'shape'):
                    corr = corr_matrix[i, j]
                else:
                    corr = corr_matrix[i][j]

                if abs(corr) >= self.correlation_threshold:
                    pairs.append({
                        "strategies": [strategies[i], strategies[j]],
                        "correlation": round(float(corr), 3),
                        "action": "reduce_second" if corr > 0 else "keep_both_hedge",
                    })

        return pairs

    def _adjust_for_correlation(
        self,
        allocations: List[Dict],
        correlated_pairs: List[Dict],
    ) -> List[Dict]:
        """Reduce allocation for correlated strategies."""
        adjusted = {a["strategy"]: dict(a) for a in allocations}

        for pair in correlated_pairs:
            if pair["action"] == "reduce_second":
                # Reduce the lower-scored strategy
                s1, s2 = pair["strategies"]

                score1 = adjusted.get(s1, {}).get("score", 0)
                score2 = adjusted.get(s2, {}).get("score", 0)

                # Reduce the lower scorer by correlation factor
                reduce_strategy = s2 if score1 >= score2 else s1
                if reduce_strategy in adjusted:
                    reduction = pair["correlation"] * 0.5  # Reduce by up to 50%
                    adjusted[reduce_strategy]["amount"] *= (1 - reduction)
                    adjusted[reduce_strategy]["correlation_adjusted"] = True

        return list(adjusted.values())

    def _diversification_ratio(self, corr_matrix: Any) -> float:
        """
        Calculate diversification ratio.
        DR = sum of individual volatilities / portfolio volatility
        Higher = better diversification
        """
        if HAS_NUMPY and hasattr(corr_matrix, 'shape'):
            n = corr_matrix.shape[0]
        else:
            n = len(corr_matrix)

        if n < 2:
            return 1.0

        if HAS_NUMPY and hasattr(corr_matrix, 'shape'):
            # Assume equal weights and unit volatility for simplicity
            weights = np.ones(n) / n

            # Portfolio variance with correlation
            port_var = weights @ corr_matrix @ weights

            # Sum of individual variances (all 1 for unit vol)
            sum_var = np.sum(weights ** 2)

            # Diversification ratio
            if port_var > 0:
                return float(np.sqrt(sum_var / port_var))
        else:
            # Simple calculation without numpy
            weights = [1.0 / n] * n
            port_var = 0.0
            for i in range(n):
                for j in range(n):
                    port_var += weights[i] * weights[j] * corr_matrix[i][j]

            sum_var = sum(w ** 2 for w in weights)
            if port_var > 0:
                return (sum_var / port_var) ** 0.5

        return 1.0

    def _matrix_to_dict(self, matrix: Any, labels: List[str]) -> Dict:
        """Convert correlation matrix to readable dict."""
        result = {}
        for i, label1 in enumerate(labels):
            result[label1] = {}
            for j, label2 in enumerate(labels):
                if HAS_NUMPY and hasattr(matrix, 'shape'):
                    val = matrix[i, j]
                else:
                    val = matrix[i][j]
                result[label1][label2] = round(float(val), 3)
        return result


class AlphaDecayBot(BaseBot):
    """
    Alpha Decay Bot - Edge Monitor.

    Continuously monitors strategy health:
    - Rolling win rate
    - Sharpe ratio trend
    - Drawdown
    - Expectancy

    Actions when edge degrades:
    - Pause strategy
    - Request retraining/backtest
    - Shift capital to better strategies
    """

    BOT_TYPE = "alpha_decay"
    REQUIRES_LLM = True  # Uses debate for retraining decisions

    # Decay thresholds
    THRESHOLDS = {
        "win_rate_drop": 0.15,      # 15% drop from peak
        "sharpe_min": 0.5,          # Minimum acceptable Sharpe
        "drawdown_max": 0.15,       # 15% max drawdown
        "expectancy_min": 0.001,    # Minimum per-trade expectancy
        "losing_streak": 7,         # Consecutive losses
    }

    def __init__(
        self,
        check_window_days: int = 30,
        alert_on_decay: bool = True,
        auto_pause: bool = True,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.check_window = check_window_days
        self.alert_on_decay = alert_on_decay
        self.auto_pause = auto_pause

    def get_description(self) -> str:
        return "Monitors strategy edge and detects alpha decay"

    async def execute(self, context: BotContext) -> BotResult:
        """Check all strategies for alpha decay."""
        strategies = context.data.get("strategies", [])
        allocations = context.data.get("allocations", [])

        # Load strategies from memory if none provided
        if not strategies and STRATEGIES:
            strategies = list(STRATEGIES.keys())

        decay_report = []
        actions = []

        for strategy in strategies:
            health = await self._check_strategy_health(strategy)
            decay_report.append(health)

            if health["status"] == "decaying":
                action = await self._handle_decay(strategy, health, context)
                actions.append(action)

                # Emit event
                if self.alert_on_decay:
                    await self._emit_event("alpha_decay_detected", {
                        "strategy": strategy,
                        "health": health,
                        "action": action,
                    })

        # Calculate overall portfolio health
        healthy_count = sum(1 for h in decay_report if h["status"] == "healthy")
        portfolio_health = healthy_count / len(decay_report) if decay_report else 1.0

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS if portfolio_health > 0.5 else BotStatus.BLOCKED,
            output={
                "strategy_health": decay_report,
                "actions_taken": actions,
                "portfolio_health": round(portfolio_health, 2),
            },
            metrics={
                "healthy_strategies": healthy_count,
                "decaying_strategies": len(decay_report) - healthy_count,
                "portfolio_health": portfolio_health,
            },
            warnings=[f"{h['strategy']} is decaying: {h['decay_reasons']}"
                     for h in decay_report if h["status"] == "decaying"],
        )

    async def _check_strategy_health(self, strategy: str) -> Dict:
        """Check health metrics for a strategy."""
        health = {
            "strategy": strategy,
            "status": "healthy",
            "decay_reasons": [],
            "metrics": {},
        }

        if not HAS_MEMORY:
            return health

        memory = get_trade_memory()
        stats_list = memory.get_strategy_stats(strategy)

        if not stats_list:
            health["status"] = "no_data"
            return health

        # Aggregate across regimes
        total_trades = sum(s["total_trades"] for s in stats_list)
        total_wins = sum(s["win_count"] for s in stats_list)
        total_pnl = sum(s["total_pnl"] for s in stats_list)

        if total_trades < 10:
            health["status"] = "insufficient_data"
            return health

        # Calculate metrics
        current_win_rate = total_wins / total_trades
        avg_pf = sum(s["profit_factor"] for s in stats_list) / len(stats_list)

        health["metrics"] = {
            "win_rate": round(current_win_rate, 3),
            "profit_factor": round(avg_pf, 2),
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
        }

        # Check for decay signals
        decay_reasons = []

        # Win rate check
        # Would need historical win rate to compare - simplified here
        if current_win_rate < self.THRESHOLDS["expectancy_min"] + 0.4:
            decay_reasons.append(f"Low win rate: {current_win_rate:.1%}")

        # Profit factor check
        if avg_pf < 1.0:
            decay_reasons.append(f"Profit factor below 1: {avg_pf:.2f}")

        # Check insights for known issues
        insights = memory.get_insights(strategy, "lose_condition")
        if len(insights) > 3:
            decay_reasons.append(f"Multiple losing patterns identified: {len(insights)}")

        if decay_reasons:
            health["status"] = "decaying"
            health["decay_reasons"] = decay_reasons

        return health

    async def _handle_decay(
        self, strategy: str, health: Dict, context: BotContext
    ) -> Dict:
        """Handle a decaying strategy."""
        action = {
            "strategy": strategy,
            "type": "none",
            "details": {},
        }

        if self.auto_pause:
            action["type"] = "pause"
            action["details"]["reason"] = health["decay_reasons"]

        # Request LLM debate for retraining decision
        if health["metrics"].get("total_trades", 0) >= 50:
            debate_task = f"""
            Strategy "{strategy}" is showing alpha decay:

            Current Metrics:
            - Win Rate: {health['metrics'].get('win_rate', 'N/A')}
            - Profit Factor: {health['metrics'].get('profit_factor', 'N/A')}
            - Total PnL: {health['metrics'].get('total_pnl', 'N/A')}

            Decay Signals:
            {health['decay_reasons']}

            Recommend:
            1. Should we pause this strategy completely?
            2. Should we reduce allocation?
            3. What parameters should be re-optimized?
            4. Is the edge fundamentally gone or temporary?
            """

            try:
                debate_result = await self.request_llm_debate(
                    task=debate_task,
                    project_path=str(context.data.get("project_path", ".")),
                    max_rounds=3,
                )

                action["debate_result"] = debate_result
                action["type"] = "retrain_requested"
                action["details"]["debate_session"] = debate_result.get("session_id")

            except Exception as e:
                action["details"]["debate_error"] = str(e)

        return action


class ExperimentBot(BaseBot):
    """
    Experiment Bot - Generates strategy variations.

    Creates new strategy variants by:
    - Parameter perturbation
    - Rule modification
    - Combining successful patterns

    Part of the 10% experimental allocation.
    """

    BOT_TYPE = "experiment"
    REQUIRES_LLM = True

    def __init__(
        self,
        max_experiments: int = 3,
        base_strategy: Optional[str] = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.max_experiments = max_experiments
        self.base_strategy = base_strategy

    def get_description(self) -> str:
        return f"Generates {self.max_experiments} strategy variations"

    async def execute(self, context: BotContext) -> BotResult:
        """Generate new strategy experiments."""
        base = self.base_strategy or context.data.get("best_strategy")

        # If no strategy specified, pick the best one from memory
        if not base and HAS_MEMORY:
            memory = get_trade_memory()
            summary = memory.get_summary()
            if summary.get("strategies_tracked", 0) > 0:
                # Pick first ICT strategy as default
                base = "ict_order_block"

        # If still no strategy, use a default from seeded strategies
        if not base and STRATEGIES:
            base = list(STRATEGIES.keys())[0]

        if not base:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.FAILED,
                errors=["No base strategy available"],
            )

        # Get winning patterns from memory
        winning_patterns = await self._get_winning_patterns(base)

        # Use LLM debate to generate variations
        experiments = await self._generate_variations(
            base, winning_patterns, context
        )

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "base_strategy": base,
                "experiments_generated": experiments,
                "winning_patterns_used": len(winning_patterns),
            },
            metrics={
                "experiments_count": len(experiments),
            },
            next_bot="backtest",  # Send to backtest
        )

    async def _get_winning_patterns(self, strategy: str) -> List[Dict]:
        """Get winning patterns from trade memory."""
        if not HAS_MEMORY:
            return []

        memory = get_trade_memory()
        insights = memory.get_insights(strategy, "win_condition")
        return [{"description": i.description, "confidence": i.confidence} for i in insights[:5]]

    async def _generate_variations(
        self,
        base: str,
        patterns: List[Dict],
        context: BotContext,
    ) -> List[Dict]:
        """Use LLM debate to generate strategy variations."""
        task = f"""
        Generate {self.max_experiments} variations of the "{base}" strategy.

        Known winning patterns:
        {patterns}

        For each variation, specify:
        1. Name (e.g., "{base}_v2_faster")
        2. What's changed (parameters, rules, filters)
        3. Hypothesis (why this might work better)

        Focus on:
        - Entry timing refinements
        - Stop loss optimization
        - Exit rule improvements
        - Filter additions (regime, volatility, time)

        Output as structured list.
        """

        try:
            result = await self.request_llm_debate(
                task=task,
                project_path=str(context.data.get("project_path", ".")),
                max_rounds=3,
            )

            # Parse variations from debate result
            # This is simplified - would parse actual output
            return [{
                "name": f"{base}_experiment_{i+1}",
                "hypothesis": "Generated variation",
                "debate_session": result.get("session_id"),
            } for i in range(self.max_experiments)]

        except Exception:
            return []

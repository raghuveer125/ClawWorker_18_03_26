"""
Fitness Evaluator - Evaluates strategy fitness.

Runs backtests and calculates composite fitness scores.
"""

import time
import logging
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field

from .genome import StrategyGenome

logger = logging.getLogger(__name__)


@dataclass
class FitnessResult:
    """Result of fitness evaluation."""
    genome_id: str
    fitness: float
    metrics: Dict[str, float]
    passed_safety: bool
    evaluation_time: float
    details: Dict[str, Any] = field(default_factory=dict)


class FitnessEvaluator:
    """
    Evaluates fitness of strategy genomes.

    Features:
    - Multi-metric scoring
    - Safety constraint checking
    - Backtest integration
    """

    # Fitness weights (aligned with scalping requirements)
    WEIGHTS = {
        "win_rate": 0.20,
        "profit_factor": 0.25,
        "sharpe_ratio": 0.20,
        "max_drawdown": 0.15,
        "trade_frequency_penalty": 0.10,  # Penalize overtrading
        "consistency": 0.10,
    }

    # Trade frequency limits
    MAX_TRADES_PER_HOUR = 10
    OPTIMAL_TRADES_PER_HOUR = 3

    # Safety constraints (from user requirements)
    MIN_WIN_RATE = 0.55  # 55%
    MAX_DRAWDOWN = 0.15  # 15%
    MIN_TRADES = 20

    def __init__(self, backtester: Optional[Callable] = None):
        """
        Initialize evaluator.

        Args:
            backtester: Function(genome_params) -> backtest_result dict
        """
        self._backtester = backtester
        self._results: Dict[str, FitnessResult] = {}

    def set_backtester(self, backtester: Callable):
        """Set backtester function."""
        self._backtester = backtester

    def evaluate(self, genome: StrategyGenome) -> FitnessResult:
        """
        Evaluate fitness of a genome.

        Args:
            genome: Genome to evaluate

        Returns:
            FitnessResult
        """
        start_time = time.time()

        # Run backtest
        params = genome.to_params()
        backtest_result = self._run_backtest(params)

        # Calculate metrics
        metrics = self._calculate_metrics(backtest_result)

        # Check safety constraints
        passed_safety = self._check_safety(metrics)

        # Calculate composite fitness
        fitness = self._calculate_fitness(metrics, passed_safety)

        result = FitnessResult(
            genome_id=genome.genome_id,
            fitness=fitness,
            metrics=metrics,
            passed_safety=passed_safety,
            evaluation_time=time.time() - start_time,
            details={"params": params},
        )

        # Store result
        self._results[genome.genome_id] = result

        # Update genome
        genome.fitness = fitness

        logger.debug(
            f"Evaluated {genome.genome_id}: fitness={fitness:.4f}, "
            f"safety={'PASS' if passed_safety else 'FAIL'}"
        )

        return result

    def evaluate_population(self, genomes: List[StrategyGenome]) -> List[FitnessResult]:
        """Evaluate entire population."""
        results = []
        for genome in genomes:
            result = self.evaluate(genome)
            results.append(result)
        return results

    def _run_backtest(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Run backtest with parameters."""
        if self._backtester:
            return self._backtester(params)

        # Simulated backtest for testing
        import random

        # Parameters affect performance
        vwap_mult = params.get("vwap_band_multiplier", 1.5)
        sl_pct = params.get("stop_loss_pct", 0.003)
        pt_pct = params.get("profit_target_pct", 0.005)

        # Simulate realistic results
        base_wr = 0.50 + random.gauss(0, 0.05)
        # Tighter stops tend to reduce win rate but improve R:R
        wr = base_wr * (1 - sl_pct * 10) + (pt_pct / sl_pct) * 0.02

        trade_count = random.randint(50, 200)
        wins = int(trade_count * wr)
        losses = trade_count - wins

        avg_win = pt_pct + random.gauss(0, 0.001)
        avg_loss = sl_pct + random.gauss(0, 0.0005)

        total_pnl = (wins * avg_win) - (losses * avg_loss)

        return {
            "total_trades": trade_count,
            "winning_trades": wins,
            "losing_trades": losses,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "max_drawdown": random.uniform(0.05, 0.20),
            "sharpe_ratio": random.gauss(1.0, 0.5),
            "profit_factor": (wins * avg_win) / max(losses * avg_loss, 0.0001),
            "daily_returns": [random.gauss(0.001, 0.01) for _ in range(20)],
        }

    def _calculate_metrics(self, backtest: Dict[str, Any]) -> Dict[str, float]:
        """Calculate normalized metrics from backtest."""
        total = backtest.get("total_trades", 1)
        wins = backtest.get("winning_trades", 0)
        hours = backtest.get("backtest_hours", 6)  # Default 6 hours trading day

        win_rate = wins / max(total, 1)

        # Calculate trades per hour
        trades_per_hour = total / max(hours, 1)

        # Trade frequency penalty (penalize both under and over trading)
        # Optimal is around OPTIMAL_TRADES_PER_HOUR
        if trades_per_hour <= self.OPTIMAL_TRADES_PER_HOUR:
            # Under-trading: slight penalty
            freq_score = trades_per_hour / self.OPTIMAL_TRADES_PER_HOUR
        elif trades_per_hour <= self.MAX_TRADES_PER_HOUR:
            # Acceptable range: full score
            freq_score = 1.0
        else:
            # Over-trading: significant penalty
            excess = trades_per_hour - self.MAX_TRADES_PER_HOUR
            freq_score = max(0, 1.0 - (excess / self.MAX_TRADES_PER_HOUR))

        # Normalize metrics to 0-1 scale
        metrics = {
            "win_rate": win_rate,
            "profit_factor": min(backtest.get("profit_factor", 1.0) / 3.0, 1.0),
            "sharpe_ratio": min(max(backtest.get("sharpe_ratio", 0) + 1, 0) / 4.0, 1.0),
            "max_drawdown": 1.0 - min(backtest.get("max_drawdown", 0.5), 1.0),
            "trade_frequency_penalty": freq_score,
            "consistency": self._calculate_consistency(backtest.get("daily_returns", [])),
        }

        return metrics

    def _calculate_consistency(self, daily_returns: List[float]) -> float:
        """Calculate consistency score from daily returns."""
        if len(daily_returns) < 2:
            return 0.5

        # Count positive days
        positive_days = sum(1 for r in daily_returns if r > 0)
        consistency = positive_days / len(daily_returns)

        return consistency

    def _check_safety(self, metrics: Dict[str, float]) -> bool:
        """Check if metrics pass safety constraints."""
        # Win rate check (55% minimum)
        if metrics.get("win_rate", 0) < self.MIN_WIN_RATE:
            return False

        # Drawdown check (15% maximum - inverted metric)
        actual_dd = 1.0 - metrics.get("max_drawdown", 0)
        if actual_dd > self.MAX_DRAWDOWN:
            return False

        return True

    def _calculate_fitness(self, metrics: Dict[str, float], passed_safety: bool) -> float:
        """Calculate composite fitness score."""
        # Weighted sum
        fitness = sum(
            metrics.get(metric, 0) * weight
            for metric, weight in self.WEIGHTS.items()
        )

        # Penalty for failing safety
        if not passed_safety:
            fitness *= 0.5  # 50% penalty

        return fitness

    def get_result(self, genome_id: str) -> Optional[FitnessResult]:
        """Get evaluation result."""
        return self._results.get(genome_id)

    def get_stats(self) -> Dict:
        """Get evaluator statistics."""
        if not self._results:
            return {"evaluations": 0}

        passed = sum(1 for r in self._results.values() if r.passed_safety)
        avg_fitness = sum(r.fitness for r in self._results.values()) / len(self._results)

        return {
            "evaluations": len(self._results),
            "passed_safety": passed,
            "pass_rate": passed / len(self._results),
            "avg_fitness": avg_fitness,
        }

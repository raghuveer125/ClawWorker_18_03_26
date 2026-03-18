"""
Strategy Optimizer Agent - Optimizes strategy parameters.

Features:
- Parameter search (grid, random, bayesian)
- Backtesting integration
- Constraint handling
"""

import time
import random
import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class OptimizationMethod(Enum):
    """Optimization methods."""
    GRID_SEARCH = "grid_search"
    RANDOM_SEARCH = "random_search"
    BAYESIAN = "bayesian"


@dataclass
class ParameterSpec:
    """Specification for a parameter to optimize."""
    name: str
    param_type: str  # "float", "int", "choice"
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    choices: Optional[List[Any]] = None
    step: Optional[float] = None  # For grid search

    def sample(self) -> Any:
        """Sample a random value."""
        if self.param_type == "choice":
            return random.choice(self.choices or [])
        elif self.param_type == "int":
            return random.randint(int(self.min_value or 0), int(self.max_value or 100))
        else:
            return random.uniform(self.min_value or 0, self.max_value or 1)

    def grid_values(self) -> List[Any]:
        """Get grid values for grid search."""
        if self.param_type == "choice":
            return list(self.choices or [])
        elif self.param_type == "int":
            step = int(self.step or 1)
            return list(range(
                int(self.min_value or 0),
                int(self.max_value or 100) + 1,
                step
            ))
        else:
            step = self.step or 0.1
            values = []
            val = self.min_value or 0
            while val <= (self.max_value or 1):
                values.append(round(val, 4))
                val += step
            return values


@dataclass
class OptimizationResult:
    """Result of parameter optimization."""
    strategy_id: str
    method: OptimizationMethod
    best_params: Dict[str, Any]
    best_fitness: float
    all_results: List[Tuple[Dict[str, Any], float]]
    iterations: int
    duration_seconds: float
    improvement_over_baseline: float
    created_at: float = field(default_factory=time.time)


class StrategyOptimizerAgent:
    """
    Optimizes strategy parameters through search.

    Features:
    - Multiple search methods
    - Parallel evaluation
    - Early stopping
    """

    AGENT_TYPE = "strategy_optimizer"
    MAX_ITERATIONS = 100
    EARLY_STOP_PATIENCE = 20  # Stop if no improvement for N iterations

    def __init__(self, synapse=None):
        self._synapse = synapse
        self._results: Dict[str, OptimizationResult] = {}
        self._evaluator: Optional[Callable] = None

    def set_evaluator(self, evaluator: Callable):
        """
        Set fitness evaluator function.

        Args:
            evaluator: Function(strategy_id, params) -> fitness (higher is better)
        """
        self._evaluator = evaluator

    def optimize(
        self,
        strategy_id: str,
        param_specs: List[ParameterSpec],
        method: OptimizationMethod = OptimizationMethod.RANDOM_SEARCH,
        max_iterations: Optional[int] = None,
        baseline_params: Optional[Dict[str, Any]] = None,
    ) -> OptimizationResult:
        """
        Optimize strategy parameters.

        Args:
            strategy_id: Strategy to optimize
            param_specs: Parameter specifications
            method: Optimization method
            max_iterations: Max iterations (default: MAX_ITERATIONS)
            baseline_params: Current parameters for comparison

        Returns:
            OptimizationResult
        """
        start_time = time.time()
        max_iter = max_iterations or self.MAX_ITERATIONS

        logger.info(f"Optimizing {strategy_id} using {method.value}")

        # Get baseline fitness
        baseline_fitness = 0.0
        if baseline_params and self._evaluator:
            baseline_fitness = self._evaluate(strategy_id, baseline_params)

        # Run optimization
        if method == OptimizationMethod.GRID_SEARCH:
            best_params, best_fitness, all_results = self._grid_search(
                strategy_id, param_specs, max_iter
            )
        elif method == OptimizationMethod.RANDOM_SEARCH:
            best_params, best_fitness, all_results = self._random_search(
                strategy_id, param_specs, max_iter
            )
        else:
            # Bayesian falls back to random for now
            best_params, best_fitness, all_results = self._random_search(
                strategy_id, param_specs, max_iter
            )

        improvement = (
            (best_fitness - baseline_fitness) / abs(baseline_fitness)
            if baseline_fitness != 0 else 0
        )

        result = OptimizationResult(
            strategy_id=strategy_id,
            method=method,
            best_params=best_params,
            best_fitness=best_fitness,
            all_results=all_results,
            iterations=len(all_results),
            duration_seconds=time.time() - start_time,
            improvement_over_baseline=improvement,
        )

        self._results[strategy_id] = result

        logger.info(
            f"Optimization complete: fitness={best_fitness:.4f}, "
            f"improvement={improvement:.1%}"
        )

        return result

    def _evaluate(self, strategy_id: str, params: Dict[str, Any]) -> float:
        """Evaluate parameters and return fitness."""
        if self._evaluator:
            return self._evaluator(strategy_id, params)

        # Default: random fitness for testing
        return random.random()

    def _grid_search(
        self,
        strategy_id: str,
        param_specs: List[ParameterSpec],
        max_iterations: int,
    ) -> Tuple[Dict[str, Any], float, List[Tuple[Dict, float]]]:
        """Grid search over parameter space."""
        from itertools import product

        # Build grid
        param_names = [p.name for p in param_specs]
        param_values = [p.grid_values() for p in param_specs]

        all_results = []
        best_params = {}
        best_fitness = float("-inf")

        # Iterate through grid (up to max_iterations)
        for i, values in enumerate(product(*param_values)):
            if i >= max_iterations:
                break

            params = dict(zip(param_names, values))
            fitness = self._evaluate(strategy_id, params)
            all_results.append((params, fitness))

            if fitness > best_fitness:
                best_fitness = fitness
                best_params = params

        return best_params, best_fitness, all_results

    def _random_search(
        self,
        strategy_id: str,
        param_specs: List[ParameterSpec],
        max_iterations: int,
    ) -> Tuple[Dict[str, Any], float, List[Tuple[Dict, float]]]:
        """Random search with early stopping."""
        all_results = []
        best_params = {}
        best_fitness = float("-inf")
        no_improvement_count = 0

        for i in range(max_iterations):
            # Sample random params
            params = {p.name: p.sample() for p in param_specs}
            fitness = self._evaluate(strategy_id, params)
            all_results.append((params, fitness))

            if fitness > best_fitness:
                best_fitness = fitness
                best_params = params
                no_improvement_count = 0
            else:
                no_improvement_count += 1

            # Early stopping
            if no_improvement_count >= self.EARLY_STOP_PATIENCE:
                logger.debug(f"Early stopping at iteration {i}")
                break

        return best_params, best_fitness, all_results

    def get_result(self, strategy_id: str) -> Optional[OptimizationResult]:
        """Get optimization result for a strategy."""
        return self._results.get(strategy_id)

    def get_recommended_params(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        """Get recommended parameters for a strategy."""
        result = self._results.get(strategy_id)
        return result.best_params if result else None

    def get_stats(self) -> Dict:
        """Get optimizer statistics."""
        if not self._results:
            return {"strategies_optimized": 0}

        avg_improvement = sum(
            r.improvement_over_baseline for r in self._results.values()
        ) / len(self._results)

        return {
            "strategies_optimized": len(self._results),
            "avg_improvement": avg_improvement,
            "total_iterations": sum(r.iterations for r in self._results.values()),
        }

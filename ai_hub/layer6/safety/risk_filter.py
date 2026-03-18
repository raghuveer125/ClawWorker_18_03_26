"""
Risk Filter - Validates evolved strategies against safety constraints.

Implements the safety gates required before deployment:
- DD < 15%
- WR > 55%
- Additional risk checks
"""

import logging
from typing import Any, Dict, List
from dataclasses import dataclass, field

from ..genetic.genome import StrategyGenome
from ..genetic.fitness import FitnessResult

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    """Result of risk check."""
    genome_id: str
    passed: bool
    checks_passed: List[str]
    checks_failed: List[str]
    risk_score: float  # 0.0 (safe) to 1.0 (risky)
    details: Dict[str, Any] = field(default_factory=dict)


class RiskFilter:
    """
    Filters strategies based on risk constraints.

    Safety gates (from user requirements):
    - Maximum drawdown: 15%
    - Minimum win rate: 55%
    - Additional checks for position sizing, leverage, etc.
    """

    # Safety thresholds (from user requirements)
    MAX_DRAWDOWN = 0.15  # 15%
    MIN_WIN_RATE = 0.55  # 55%

    # Additional safety limits
    MAX_POSITION_SIZE = 0.05  # 5% of capital per trade
    MAX_DAILY_TRADES = 50
    MIN_RISK_REWARD = 1.0  # Minimum R:R ratio

    def __init__(self):
        self._results: Dict[str, RiskCheckResult] = {}

    def check(
        self,
        genome: StrategyGenome,
        fitness_result: FitnessResult,
    ) -> RiskCheckResult:
        """
        Check if strategy passes risk filters.

        Args:
            genome: Strategy genome
            fitness_result: Fitness evaluation result

        Returns:
            RiskCheckResult
        """
        checks_passed = []
        checks_failed = []
        risk_score = 0.0

        params = genome.to_params()
        metrics = fitness_result.metrics

        # Check 1: Win rate >= 55%
        win_rate = metrics.get("win_rate", 0)
        if win_rate >= self.MIN_WIN_RATE:
            checks_passed.append(f"win_rate={win_rate:.1%} >= {self.MIN_WIN_RATE:.0%}")
        else:
            checks_failed.append(f"win_rate={win_rate:.1%} < {self.MIN_WIN_RATE:.0%}")
            risk_score += 0.3

        # Check 2: Max drawdown <= 15%
        # Note: In metrics, max_drawdown is inverted (higher = better)
        actual_dd = 1.0 - metrics.get("max_drawdown", 0)
        if actual_dd <= self.MAX_DRAWDOWN:
            checks_passed.append(f"drawdown={actual_dd:.1%} <= {self.MAX_DRAWDOWN:.0%}")
        else:
            checks_failed.append(f"drawdown={actual_dd:.1%} > {self.MAX_DRAWDOWN:.0%}")
            risk_score += 0.3

        # Check 3: Position size
        pos_size = params.get("position_size_pct", 0.02)
        if pos_size <= self.MAX_POSITION_SIZE:
            checks_passed.append(f"position_size={pos_size:.1%} <= {self.MAX_POSITION_SIZE:.0%}")
        else:
            checks_failed.append(f"position_size={pos_size:.1%} > {self.MAX_POSITION_SIZE:.0%}")
            risk_score += 0.2

        # Check 4: Risk-reward ratio
        profit_target = params.get("profit_target_pct", 0.005)
        stop_loss = params.get("stop_loss_pct", 0.003)
        rr_ratio = profit_target / stop_loss if stop_loss > 0 else 0
        if rr_ratio >= self.MIN_RISK_REWARD:
            checks_passed.append(f"risk_reward={rr_ratio:.2f} >= {self.MIN_RISK_REWARD}")
        else:
            checks_failed.append(f"risk_reward={rr_ratio:.2f} < {self.MIN_RISK_REWARD}")
            risk_score += 0.2

        # Overall pass/fail
        passed = len(checks_failed) == 0

        result = RiskCheckResult(
            genome_id=genome.genome_id,
            passed=passed,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            risk_score=min(risk_score, 1.0),
            details={
                "win_rate": win_rate,
                "drawdown": actual_dd,
                "position_size": pos_size,
                "risk_reward": rr_ratio,
            },
        )

        self._results[genome.genome_id] = result

        logger.info(
            f"Risk filter {genome.genome_id}: "
            f"{'PASS' if passed else 'FAIL'} "
            f"(score={risk_score:.2f}, "
            f"{len(checks_passed)} passed, {len(checks_failed)} failed)"
        )

        return result

    def batch_check(
        self,
        genomes: List[StrategyGenome],
        fitness_results: Dict[str, FitnessResult],
    ) -> List[RiskCheckResult]:
        """Check multiple genomes."""
        results = []
        for genome in genomes:
            fitness = fitness_results.get(genome.genome_id)
            if fitness:
                results.append(self.check(genome, fitness))
        return results

    def get_approved(self, results: List[RiskCheckResult]) -> List[str]:
        """Get IDs of genomes that passed risk filter."""
        return [r.genome_id for r in results if r.passed]

    def get_stats(self) -> Dict:
        """Get filter statistics."""
        total = len(self._results)
        passed = sum(1 for r in self._results.values() if r.passed)

        return {
            "total_checked": total,
            "passed": passed,
            "pass_rate": passed / total if total > 0 else 0,
            "avg_risk_score": (
                sum(r.risk_score for r in self._results.values()) / total
                if total > 0 else 0
            ),
        }

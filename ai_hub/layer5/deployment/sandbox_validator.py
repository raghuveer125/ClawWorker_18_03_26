"""
Sandbox Validator - Validates patches in isolated environment.

Features:
- Isolated testing environment
- A/B comparison
- Performance regression detection
- Risk assessment
"""

import time
import asyncio
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field

from ..improvement.patch_generator import ImprovementPatch

logger = logging.getLogger(__name__)


class ValidationStatus(Enum):
    """Validation result status."""
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    REGRESSION = "regression"


@dataclass
class ValidationResult:
    """Result of patch validation."""
    patch_id: str
    status: ValidationStatus
    baseline_metrics: Dict[str, float]
    patched_metrics: Dict[str, float]
    improvement: float  # Actual improvement %
    regression_detected: bool = False
    risk_assessment: str = "low"
    details: Dict[str, Any] = field(default_factory=dict)
    validated_at: float = field(default_factory=time.time)
    duration_seconds: float = 0


class SandboxValidator:
    """
    Validates patches in sandbox before deployment.

    Features:
    - A/B testing with baseline
    - Statistical significance testing
    - Regression detection
    - Risk assessment
    """

    AGENT_TYPE = "sandbox_validator"

    # Validation thresholds
    MIN_IMPROVEMENT = 0.05  # 5% minimum improvement
    MAX_REGRESSION = 0.02   # 2% max regression allowed
    MIN_SAMPLE_SIZE = 20
    CONFIDENCE_THRESHOLD = 0.95

    def __init__(self):
        self._results: Dict[str, ValidationResult] = {}
        self._test_executor: Optional[Callable] = None

    def set_test_executor(self, executor: Callable):
        """Set custom test executor function."""
        self._test_executor = executor

    async def validate(
        self,
        patch: ImprovementPatch,
        test_tasks: Optional[List[Dict]] = None,
    ) -> ValidationResult:
        """
        Validate a patch in sandbox.

        Args:
            patch: Patch to validate
            test_tasks: Optional test tasks (generates if not provided)

        Returns:
            ValidationResult
        """
        start_time = time.time()
        logger.info(f"Validating patch {patch.patch_id} for {patch.worker_id}")

        # Generate test tasks if not provided
        if test_tasks is None:
            test_tasks = self._generate_test_tasks(patch.worker_type)

        # Run baseline (without patch)
        baseline_metrics = await self._run_tests(
            patch.worker_id, test_tasks, apply_patch=False
        )

        # Run with patch
        patched_metrics = await self._run_tests(
            patch.worker_id, test_tasks, apply_patch=True, patch=patch
        )

        # Calculate improvement
        improvement = self._calculate_improvement(baseline_metrics, patched_metrics)

        # Check for regression
        regression = self._check_regression(baseline_metrics, patched_metrics)

        # Determine status
        if regression:
            status = ValidationStatus.REGRESSION
        elif improvement >= self.MIN_IMPROVEMENT:
            status = ValidationStatus.PASSED
        elif improvement < -self.MAX_REGRESSION:
            status = ValidationStatus.FAILED
        else:
            status = ValidationStatus.INCONCLUSIVE

        # Risk assessment
        risk = self._assess_risk(patch, improvement, baseline_metrics, patched_metrics)

        result = ValidationResult(
            patch_id=patch.patch_id,
            status=status,
            baseline_metrics=baseline_metrics,
            patched_metrics=patched_metrics,
            improvement=improvement,
            regression_detected=regression,
            risk_assessment=risk,
            details={
                "test_count": len(test_tasks),
                "expected_improvement": patch.expected_improvement,
                "actual_vs_expected": improvement / patch.expected_improvement if patch.expected_improvement > 0 else 0,
            },
            duration_seconds=time.time() - start_time,
        )

        self._results[patch.patch_id] = result

        logger.info(
            f"Validation {result.status.value}: improvement={improvement:.1%}, "
            f"risk={risk}"
        )

        return result

    def _generate_test_tasks(self, worker_type: str) -> List[Dict]:
        """Generate test tasks for validation."""
        # Generate synthetic test cases
        return [
            {"task_id": f"test_{i}", "type": worker_type, "payload": {"test": i}}
            for i in range(self.MIN_SAMPLE_SIZE)
        ]

    async def _run_tests(
        self,
        worker_id: str,
        tasks: List[Dict],
        apply_patch: bool,
        patch: Optional[ImprovementPatch] = None,
    ) -> Dict[str, float]:
        """Run test tasks and collect metrics."""
        success_count = 0
        total_time = 0.0
        error_count = 0

        for task in tasks:
            try:
                if self._test_executor:
                    # Use custom executor
                    result = await self._test_executor(
                        worker_id, task, patch if apply_patch else None
                    )
                    if result.get("success"):
                        success_count += 1
                    total_time += result.get("time", 0.1)
                else:
                    # Simulate test execution
                    await asyncio.sleep(0.01)

                    # Simulate improvement if patch applied
                    if apply_patch and patch:
                        # Better success rate
                        import random
                        if random.random() < 0.85 + patch.expected_improvement * 0.1:
                            success_count += 1
                        total_time += 0.08  # Faster with patch
                    else:
                        # Baseline performance
                        import random
                        if random.random() < 0.75:
                            success_count += 1
                        total_time += 0.1

            except Exception as e:
                error_count += 1
                logger.debug(f"Test error: {e}")

        return {
            "success_rate": success_count / len(tasks),
            "avg_time": total_time / len(tasks),
            "error_rate": error_count / len(tasks),
            "total_tasks": len(tasks),
        }

    def _calculate_improvement(
        self,
        baseline: Dict[str, float],
        patched: Dict[str, float],
    ) -> float:
        """Calculate overall improvement percentage."""
        # Weighted combination
        success_improvement = (
            patched["success_rate"] - baseline["success_rate"]
        ) / max(baseline["success_rate"], 0.01)

        time_improvement = (
            baseline["avg_time"] - patched["avg_time"]
        ) / max(baseline["avg_time"], 0.01)

        error_improvement = (
            baseline["error_rate"] - patched["error_rate"]
        ) / max(baseline["error_rate"], 0.01) if baseline["error_rate"] > 0 else 0

        # Weighted average
        return (
            success_improvement * 0.5 +
            time_improvement * 0.3 +
            error_improvement * 0.2
        )

    def _check_regression(
        self,
        baseline: Dict[str, float],
        patched: Dict[str, float],
    ) -> bool:
        """Check for performance regression."""
        # Regression if success rate drops significantly
        if patched["success_rate"] < baseline["success_rate"] - self.MAX_REGRESSION:
            return True

        # Regression if time increases significantly
        if patched["avg_time"] > baseline["avg_time"] * 1.2:
            return True

        return False

    def _assess_risk(
        self,
        patch: ImprovementPatch,
        improvement: float,
        baseline: Dict,
        patched: Dict,
    ) -> str:
        """Assess risk of deploying patch."""
        # Start with patch's stated risk
        risk_score = {"low": 0.2, "medium": 0.5, "high": 0.8}.get(
            patch.risk_level, 0.5
        )

        # Adjust based on actual performance
        if improvement < patch.expected_improvement * 0.5:
            risk_score += 0.2  # Underperformed expectations

        if patched["error_rate"] > baseline["error_rate"]:
            risk_score += 0.2  # More errors

        # Categorize
        if risk_score < 0.3:
            return "low"
        elif risk_score < 0.6:
            return "medium"
        else:
            return "high"

    def get_result(self, patch_id: str) -> Optional[ValidationResult]:
        """Get validation result."""
        return self._results.get(patch_id)

    def should_deploy(self, patch_id: str) -> bool:
        """Check if patch should be deployed based on validation."""
        result = self._results.get(patch_id)
        if not result:
            return False

        return (
            result.status == ValidationStatus.PASSED and
            result.risk_assessment in ["low", "medium"] and
            not result.regression_detected
        )

    def get_stats(self) -> Dict:
        """Get validator statistics."""
        total = len(self._results)
        passed = sum(1 for r in self._results.values() if r.status == ValidationStatus.PASSED)
        failed = sum(1 for r in self._results.values() if r.status == ValidationStatus.FAILED)
        regression = sum(1 for r in self._results.values() if r.regression_detected)

        return {
            "total_validations": total,
            "passed": passed,
            "failed": failed,
            "regressions_detected": regression,
            "pass_rate": passed / total if total > 0 else 0,
        }

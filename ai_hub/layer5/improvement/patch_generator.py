"""
Patch Generator - Generates improvement patches for workers.

Creates:
- Configuration modifications
- Parameter tuning suggestions
- Code change proposals
"""

import time
import logging
from enum import Enum
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from ..analysis.weak_worker_identifier import WeaknessReport, WeaknessType

logger = logging.getLogger(__name__)


class PatchType(Enum):
    """Types of improvement patches."""
    CONFIG_CHANGE = "config_change"
    PARAMETER_TUNE = "parameter_tune"
    TIMEOUT_ADJUST = "timeout_adjust"
    RETRY_POLICY = "retry_policy"
    RESOURCE_LIMIT = "resource_limit"
    CODE_SUGGESTION = "code_suggestion"


@dataclass
class ImprovementPatch:
    """An improvement patch for a worker."""
    patch_id: str
    worker_id: str
    worker_type: str
    patch_type: PatchType
    weakness_addressed: WeaknessType
    changes: Dict[str, Any]
    expected_improvement: float  # 0.0 to 1.0
    risk_level: str  # "low", "medium", "high"
    description: str
    rollback_config: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    validated: bool = False
    deployed: bool = False


class PatchGenerator:
    """
    Generates improvement patches based on weakness reports.

    Features:
    - Weakness-specific patch templates
    - Parameter optimization
    - Risk assessment
    - Rollback configuration
    """

    AGENT_TYPE = "patch_generator"

    # Patch templates by weakness type
    PATCH_TEMPLATES = {
        WeaknessType.LOW_SUCCESS_RATE: [
            {
                "type": PatchType.RETRY_POLICY,
                "changes": {"max_retries": 3, "retry_delay": 1.0},
                "expected_improvement": 0.15,
                "risk_level": "low",
            },
            {
                "type": PatchType.PARAMETER_TUNE,
                "changes": {"validation_level": "strict", "input_sanitize": True},
                "expected_improvement": 0.1,
                "risk_level": "low",
            },
        ],
        WeaknessType.SLOW_EXECUTION: [
            {
                "type": PatchType.TIMEOUT_ADJUST,
                "changes": {"timeout_multiplier": 1.5},
                "expected_improvement": 0.1,
                "risk_level": "low",
            },
            {
                "type": PatchType.RESOURCE_LIMIT,
                "changes": {"max_memory_mb": 512, "cpu_priority": "high"},
                "expected_improvement": 0.2,
                "risk_level": "medium",
            },
            {
                "type": PatchType.CONFIG_CHANGE,
                "changes": {"batch_size": "reduce_50pct", "parallel_ops": True},
                "expected_improvement": 0.25,
                "risk_level": "medium",
            },
        ],
        WeaknessType.HIGH_ERROR_RATE: [
            {
                "type": PatchType.CONFIG_CHANGE,
                "changes": {"error_recovery": "graceful", "checkpoint_interval": 5},
                "expected_improvement": 0.2,
                "risk_level": "low",
            },
            {
                "type": PatchType.PARAMETER_TUNE,
                "changes": {"tolerance_mode": "lenient", "fallback_enabled": True},
                "expected_improvement": 0.15,
                "risk_level": "medium",
            },
        ],
        WeaknessType.FREQUENT_TIMEOUTS: [
            {
                "type": PatchType.TIMEOUT_ADJUST,
                "changes": {"timeout_seconds": "increase_100pct"},
                "expected_improvement": 0.3,
                "risk_level": "low",
            },
            {
                "type": PatchType.CONFIG_CHANGE,
                "changes": {"async_mode": True, "progress_checkpoints": True},
                "expected_improvement": 0.25,
                "risk_level": "medium",
            },
        ],
    }

    def __init__(self):
        self._patches: Dict[str, ImprovementPatch] = {}
        self._patch_counter = 0

    def generate_patches(self, report: WeaknessReport) -> List[ImprovementPatch]:
        """
        Generate patches for a weakness report.

        Args:
            report: Weakness report

        Returns:
            List of improvement patches
        """
        templates = self.PATCH_TEMPLATES.get(report.weakness_type, [])
        patches = []

        for template in templates:
            self._patch_counter += 1
            patch_id = f"patch_{self._patch_counter:04d}"

            # Customize changes based on severity
            changes = dict(template["changes"])
            expected = template["expected_improvement"]

            # Scale expected improvement by severity
            if report.severity > 0.7:
                expected *= 1.2  # High severity = more aggressive

            # Generate rollback config
            rollback = self._generate_rollback(changes)

            patch = ImprovementPatch(
                patch_id=patch_id,
                worker_id=report.worker_id,
                worker_type=report.worker_type,
                patch_type=template["type"],
                weakness_addressed=report.weakness_type,
                changes=changes,
                expected_improvement=min(expected, 1.0),
                risk_level=template["risk_level"],
                description=self._generate_description(
                    template["type"], report.weakness_type, changes
                ),
                rollback_config=rollback,
            )

            patches.append(patch)
            self._patches[patch_id] = patch

        logger.info(
            f"Generated {len(patches)} patches for {report.worker_id} "
            f"({report.weakness_type.value})"
        )
        return patches

    def _generate_rollback(self, changes: Dict) -> Dict:
        """Generate rollback configuration."""
        # For each change, store the "undo" operation
        rollback = {}
        for key, value in changes.items():
            if isinstance(value, bool):
                rollback[key] = not value
            elif isinstance(value, (int, float)):
                rollback[key] = "restore_original"
            elif isinstance(value, str) and "increase" in value:
                rollback[key] = value.replace("increase", "decrease")
            elif isinstance(value, str) and "reduce" in value:
                rollback[key] = value.replace("reduce", "increase")
            else:
                rollback[key] = "restore_original"
        return rollback

    def _generate_description(
        self,
        patch_type: PatchType,
        weakness: WeaknessType,
        changes: Dict,
    ) -> str:
        """Generate human-readable description."""
        change_list = ", ".join(f"{k}={v}" for k, v in changes.items())
        return (
            f"Address {weakness.value} via {patch_type.value}: {change_list}"
        )

    def get_patch(self, patch_id: str) -> Optional[ImprovementPatch]:
        """Get patch by ID."""
        return self._patches.get(patch_id)

    def mark_validated(self, patch_id: str, success: bool):
        """Mark patch as validated."""
        patch = self._patches.get(patch_id)
        if patch:
            patch.validated = success

    def mark_deployed(self, patch_id: str):
        """Mark patch as deployed."""
        patch = self._patches.get(patch_id)
        if patch:
            patch.deployed = True

    def get_pending_patches(self, worker_id: Optional[str] = None) -> List[ImprovementPatch]:
        """Get patches pending validation/deployment."""
        patches = list(self._patches.values())
        if worker_id:
            patches = [p for p in patches if p.worker_id == worker_id]
        return [p for p in patches if not p.deployed]

    def get_stats(self) -> Dict:
        """Get generator statistics."""
        total = len(self._patches)
        validated = sum(1 for p in self._patches.values() if p.validated)
        deployed = sum(1 for p in self._patches.values() if p.deployed)

        return {
            "total_patches": total,
            "validated": validated,
            "deployed": deployed,
            "pending": total - deployed,
        }

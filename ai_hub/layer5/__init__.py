"""Layer 5 - Self-Improvement Engine.

Identifies weak performers and auto-upgrades through validated patches.
"""
from .analysis.weak_worker_identifier import (
    WeakWorkerIdentifier,
    WeaknessReport,
    WeaknessType,
)
from .improvement.patch_generator import PatchGenerator, ImprovementPatch, PatchType
from .deployment.sandbox_validator import (
    SandboxValidator,
    ValidationResult,
    ValidationStatus,
)

__all__ = [
    "WeakWorkerIdentifier",
    "WeaknessReport",
    "WeaknessType",
    "PatchGenerator",
    "ImprovementPatch",
    "PatchType",
    "SandboxValidator",
    "ValidationResult",
    "ValidationStatus",
]

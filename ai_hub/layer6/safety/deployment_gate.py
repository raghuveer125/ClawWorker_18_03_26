"""
Deployment Gate - Controls when evolved strategies go live.

Features:
- Sandbox validation integration
- Human approval for major changes
- Staged rollout support
"""

import time
import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field

from ..genetic.genome import StrategyGenome
from .risk_filter import RiskCheckResult

logger = logging.getLogger(__name__)


class ApprovalStatus(Enum):
    """Approval status."""
    PENDING = "pending"
    AUTO_APPROVED = "auto_approved"
    HUMAN_APPROVED = "human_approved"
    REJECTED = "rejected"


class DeploymentStage(Enum):
    """Deployment stages."""
    SANDBOX = "sandbox"  # Initial validation
    PAPER = "paper"      # Paper trading
    CANARY = "canary"    # Small live allocation
    FULL = "full"        # Full deployment


@dataclass
class DeploymentDecision:
    """Decision on whether to deploy a strategy."""
    genome_id: str
    approved: bool
    approval_status: ApprovalStatus
    current_stage: DeploymentStage
    next_stage: Optional[DeploymentStage]
    requires_human_approval: bool
    reasons: List[str]
    conditions: Dict[str, Any] = field(default_factory=dict)
    decided_at: float = field(default_factory=time.time)


class DeploymentGate:
    """
    Controls strategy deployment.

    Flow:
    1. Evolved strategy passes fitness evaluation
    2. Passes risk filter
    3. Passes sandbox validation (Layer 5)
    4. Human approval if major change
    5. Staged rollout: sandbox -> paper -> canary -> full
    """

    # Auto-approval thresholds
    AUTO_APPROVE_MIN_FITNESS = 0.7
    AUTO_APPROVE_MAX_RISK = 0.3
    AUTO_APPROVE_MIN_IMPROVEMENT = 0.05  # 5% improvement over baseline

    # Major change detection
    MAJOR_CHANGE_THRESHOLD = 0.2  # 20% parameter change = major

    def __init__(self, synapse=None):
        self._synapse = synapse
        self._decisions: Dict[str, DeploymentDecision] = {}
        self._human_approval_callback: Optional[Callable] = None
        self._staged_deployments: Dict[str, DeploymentStage] = {}

    def set_approval_callback(self, callback: Callable[[StrategyGenome], bool]):
        """Set human approval callback."""
        self._human_approval_callback = callback

    def evaluate(
        self,
        genome: StrategyGenome,
        risk_result: RiskCheckResult,
        sandbox_passed: bool,
        baseline_genome: Optional[StrategyGenome] = None,
    ) -> DeploymentDecision:
        """
        Evaluate whether to deploy a strategy.

        Args:
            genome: Strategy genome
            risk_result: Risk filter result
            sandbox_passed: Whether sandbox validation passed
            baseline_genome: Current production genome for comparison

        Returns:
            DeploymentDecision
        """
        reasons = []
        approved = True
        requires_human = False

        # Check 1: Risk filter
        if not risk_result.passed:
            approved = False
            reasons.append(f"Failed risk filter: {risk_result.checks_failed}")

        # Check 2: Sandbox validation
        if not sandbox_passed:
            approved = False
            reasons.append("Failed sandbox validation")

        # Check 3: Fitness threshold
        fitness = genome.fitness or 0
        if fitness < self.AUTO_APPROVE_MIN_FITNESS:
            approved = False
            reasons.append(f"Fitness {fitness:.2f} below threshold {self.AUTO_APPROVE_MIN_FITNESS}")

        # Check 4: Risk score
        if risk_result.risk_score > self.AUTO_APPROVE_MAX_RISK:
            approved = False
            reasons.append(f"Risk score {risk_result.risk_score:.2f} above threshold")

        # Check 5: Major change detection
        if baseline_genome:
            is_major = self._is_major_change(genome, baseline_genome)
            if is_major:
                requires_human = True
                reasons.append("Major parameter changes detected - requires human approval")

        # Determine stage
        current_stage = self._staged_deployments.get(
            genome.genome_id, DeploymentStage.SANDBOX
        )

        if approved:
            next_stage = self._get_next_stage(current_stage)
            reasons.append(f"Ready to advance from {current_stage.value} to {next_stage.value if next_stage else 'none'}")
        else:
            next_stage = None

        # Determine approval status
        if not approved:
            status = ApprovalStatus.REJECTED
        elif requires_human:
            status = ApprovalStatus.PENDING
        else:
            status = ApprovalStatus.AUTO_APPROVED

        decision = DeploymentDecision(
            genome_id=genome.genome_id,
            approved=approved and not requires_human,
            approval_status=status,
            current_stage=current_stage,
            next_stage=next_stage,
            requires_human_approval=requires_human,
            reasons=reasons,
            conditions={
                "fitness": fitness,
                "risk_score": risk_result.risk_score,
                "sandbox_passed": sandbox_passed,
            },
        )

        self._decisions[genome.genome_id] = decision

        logger.info(
            f"Deployment gate {genome.genome_id}: "
            f"{'APPROVED' if decision.approved else 'BLOCKED'} "
            f"(status={status.value}, stage={current_stage.value})"
        )

        return decision

    def _is_major_change(
        self,
        new_genome: StrategyGenome,
        baseline: StrategyGenome,
    ) -> bool:
        """Detect if changes are major."""
        new_params = new_genome.to_params()
        base_params = baseline.to_params()

        # Count significant parameter changes
        major_changes = 0
        for key in new_params:
            if key not in base_params:
                major_changes += 1
                continue

            new_val = new_params[key]
            base_val = base_params[key]

            # Skip non-numeric
            if not isinstance(new_val, (int, float)):
                if new_val != base_val:
                    major_changes += 1
                continue

            # Check percentage change
            if base_val != 0:
                change = abs(new_val - base_val) / abs(base_val)
                if change > self.MAJOR_CHANGE_THRESHOLD:
                    major_changes += 1

        # Major if more than 30% of parameters changed significantly
        return major_changes > len(new_params) * 0.3

    def _get_next_stage(self, current: DeploymentStage) -> Optional[DeploymentStage]:
        """Get next deployment stage."""
        stages = [
            DeploymentStage.SANDBOX,
            DeploymentStage.PAPER,
            DeploymentStage.CANARY,
            DeploymentStage.FULL,
        ]
        try:
            idx = stages.index(current)
            if idx < len(stages) - 1:
                return stages[idx + 1]
        except ValueError:
            pass
        return None

    def advance_stage(self, genome_id: str) -> Optional[DeploymentStage]:
        """Advance deployment stage for a genome."""
        current = self._staged_deployments.get(genome_id, DeploymentStage.SANDBOX)
        next_stage = self._get_next_stage(current)

        if next_stage:
            self._staged_deployments[genome_id] = next_stage
            logger.info(f"Advanced {genome_id}: {current.value} -> {next_stage.value}")
            return next_stage

        return None

    def human_approve(self, genome_id: str, approved: bool):
        """Record human approval decision."""
        decision = self._decisions.get(genome_id)
        if decision:
            if approved:
                decision.approval_status = ApprovalStatus.HUMAN_APPROVED
                decision.approved = True
            else:
                decision.approval_status = ApprovalStatus.REJECTED
                decision.approved = False
            decision.requires_human_approval = False

            logger.info(
                f"Human {'approved' if approved else 'rejected'} {genome_id}"
            )

    def get_pending_approvals(self) -> List[DeploymentDecision]:
        """Get decisions pending human approval."""
        return [
            d for d in self._decisions.values()
            if d.requires_human_approval and d.approval_status == ApprovalStatus.PENDING
        ]

    def get_deployed(self, stage: Optional[DeploymentStage] = None) -> List[str]:
        """Get deployed genome IDs."""
        if stage:
            return [
                gid for gid, s in self._staged_deployments.items()
                if s == stage
            ]
        return list(self._staged_deployments.keys())

    def get_stats(self) -> Dict:
        """Get gate statistics."""
        total = len(self._decisions)
        approved = sum(1 for d in self._decisions.values() if d.approved)
        pending = sum(1 for d in self._decisions.values() if d.requires_human_approval)

        return {
            "total_evaluated": total,
            "approved": approved,
            "pending_human_approval": pending,
            "deployed_by_stage": {
                stage.value: len(self.get_deployed(stage))
                for stage in DeploymentStage
            },
        }

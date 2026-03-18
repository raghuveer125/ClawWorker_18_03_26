"""
Error Handler Agent - Handles errors and determines recovery actions.

Features:
- Error classification
- Recovery strategy selection
- Escalation logic
- Error pattern tracking
"""

import time
import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import threading

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    """Error severity levels."""
    LOW = "low"           # Recoverable, no user impact
    MEDIUM = "medium"     # Recoverable, minor impact
    HIGH = "high"         # Requires attention
    CRITICAL = "critical" # Immediate action needed


class ErrorCategory(Enum):
    """Error categories for classification."""
    TIMEOUT = "timeout"
    NETWORK = "network"
    DATA = "data"
    WORKER = "worker"
    VALIDATION = "validation"
    RESOURCE = "resource"
    UNKNOWN = "unknown"


class RecoveryAction(Enum):
    """Possible recovery actions."""
    RETRY = "retry"
    RETRY_DIFFERENT_WORKER = "retry_different_worker"
    SKIP = "skip"
    ABORT_GOAL = "abort_goal"
    ESCALATE = "escalate"
    WAIT_AND_RETRY = "wait_and_retry"
    FALLBACK = "fallback"


@dataclass
class ExecutionError:
    """An execution error."""
    error_id: str
    task_id: str
    goal_id: str
    error_type: str
    message: str
    category: ErrorCategory = ErrorCategory.UNKNOWN
    severity: ErrorSeverity = ErrorSeverity.MEDIUM
    timestamp: float = field(default_factory=time.time)
    worker_id: Optional[str] = None
    retry_count: int = 0
    stack_trace: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RecoveryPlan:
    """Plan for error recovery."""
    error_id: str
    action: RecoveryAction
    parameters: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    max_retries: int = 3
    delay_seconds: float = 0


class ErrorHandlerAgent:
    """
    Handles execution errors and determines recovery.

    Features:
    - Error classification
    - Pattern-based recovery
    - Escalation thresholds
    - Error history tracking
    """

    AGENT_TYPE = "error_handler"

    # Error patterns for classification
    ERROR_PATTERNS = {
        ErrorCategory.TIMEOUT: ["timeout", "timed out", "deadline exceeded"],
        ErrorCategory.NETWORK: ["connection", "network", "socket", "refused", "reset"],
        ErrorCategory.DATA: ["invalid data", "missing field", "parse error", "json"],
        ErrorCategory.WORKER: ["worker", "busy", "unavailable", "offline"],
        ErrorCategory.VALIDATION: ["validation", "invalid", "constraint", "required"],
        ErrorCategory.RESOURCE: ["memory", "cpu", "disk", "resource", "limit"],
    }

    # Default recovery strategies by category
    DEFAULT_RECOVERY = {
        ErrorCategory.TIMEOUT: RecoveryAction.RETRY,
        ErrorCategory.NETWORK: RecoveryAction.WAIT_AND_RETRY,
        ErrorCategory.DATA: RecoveryAction.SKIP,
        ErrorCategory.WORKER: RecoveryAction.RETRY_DIFFERENT_WORKER,
        ErrorCategory.VALIDATION: RecoveryAction.SKIP,
        ErrorCategory.RESOURCE: RecoveryAction.WAIT_AND_RETRY,
        ErrorCategory.UNKNOWN: RecoveryAction.RETRY,
    }

    def __init__(
        self,
        max_retries: int = 3,
        escalation_threshold: int = 5,
        synapse=None,
    ):
        """
        Initialize error handler.

        Args:
            max_retries: Default max retries
            escalation_threshold: Errors before escalation
            synapse: Synapse for alerts
        """
        self._max_retries = max_retries
        self._escalation_threshold = escalation_threshold
        self._synapse = synapse

        self._errors: Dict[str, ExecutionError] = {}
        self._error_counts: Dict[str, int] = defaultdict(int)  # task_id -> count
        self._pattern_counts: Dict[ErrorCategory, int] = defaultdict(int)
        self._callbacks: List[Callable] = []
        self._lock = threading.Lock()

    def _get_synapse(self):
        """Lazy load synapse."""
        if self._synapse is None:
            try:
                from ...synapse import get_synapse
                self._synapse = get_synapse()
            except ImportError:
                pass
        return self._synapse

    def on_error(self, callback: Callable[[ExecutionError, RecoveryPlan], None]):
        """Register error callback."""
        self._callbacks.append(callback)

    def _emit_error(self, error: ExecutionError, plan: RecoveryPlan):
        """Emit error event."""
        for callback in self._callbacks:
            try:
                callback(error, plan)
            except Exception as e:
                logger.error(f"Error callback failed: {e}")

    def handle_error(
        self,
        task_id: str,
        goal_id: str,
        error_type: str,
        message: str,
        worker_id: Optional[str] = None,
        context: Optional[Dict] = None,
    ) -> RecoveryPlan:
        """
        Handle an execution error.

        Args:
            task_id: Task that failed
            goal_id: Parent goal
            error_type: Type/class of error
            message: Error message
            worker_id: Worker that failed
            context: Additional context

        Returns:
            RecoveryPlan with recommended action
        """
        import hashlib

        error_id = hashlib.sha256(
            f"{task_id}{time.time()}".encode()
        ).hexdigest()[:8]

        # Classify error
        category = self._classify_error(error_type, message)
        severity = self._determine_severity(category, task_id)

        # Get retry count
        with self._lock:
            self._error_counts[task_id] += 1
            retry_count = self._error_counts[task_id]
            self._pattern_counts[category] += 1

        error = ExecutionError(
            error_id=error_id,
            task_id=task_id,
            goal_id=goal_id,
            error_type=error_type,
            message=message,
            category=category,
            severity=severity,
            worker_id=worker_id,
            retry_count=retry_count,
            context=context or {},
        )

        with self._lock:
            self._errors[error_id] = error

        # Determine recovery
        plan = self._create_recovery_plan(error)

        # Log and alert
        self._log_error(error, plan)
        self._emit_error(error, plan)

        # Send alert for high severity
        if severity in [ErrorSeverity.HIGH, ErrorSeverity.CRITICAL]:
            self._send_alert(error, plan)

        return plan

    def _classify_error(self, error_type: str, message: str) -> ErrorCategory:
        """Classify error into a category."""
        combined = f"{error_type} {message}".lower()

        for category, patterns in self.ERROR_PATTERNS.items():
            for pattern in patterns:
                if pattern in combined:
                    return category

        return ErrorCategory.UNKNOWN

    def _determine_severity(self, category: ErrorCategory, task_id: str) -> ErrorSeverity:
        """Determine error severity."""
        retry_count = self._error_counts.get(task_id, 0)

        # Critical if multiple retries already
        if retry_count >= self._max_retries:
            return ErrorSeverity.CRITICAL

        # High for resource/worker issues
        if category in [ErrorCategory.RESOURCE, ErrorCategory.WORKER]:
            return ErrorSeverity.HIGH

        # Medium for network/timeout
        if category in [ErrorCategory.NETWORK, ErrorCategory.TIMEOUT]:
            return ErrorSeverity.MEDIUM

        return ErrorSeverity.LOW

    def _create_recovery_plan(self, error: ExecutionError) -> RecoveryPlan:
        """Create recovery plan for error."""
        # Check if max retries exceeded
        if error.retry_count >= self._max_retries:
            return RecoveryPlan(
                error_id=error.error_id,
                action=RecoveryAction.ESCALATE,
                reason=f"Max retries ({self._max_retries}) exceeded",
            )

        # Check escalation threshold
        if self._pattern_counts[error.category] >= self._escalation_threshold:
            return RecoveryPlan(
                error_id=error.error_id,
                action=RecoveryAction.ESCALATE,
                reason=f"Pattern threshold exceeded for {error.category.value}",
            )

        # Get default action for category
        action = self.DEFAULT_RECOVERY.get(error.category, RecoveryAction.RETRY)

        # Build plan
        plan = RecoveryPlan(
            error_id=error.error_id,
            action=action,
            max_retries=self._max_retries,
            reason=f"Default recovery for {error.category.value}",
        )

        # Add parameters based on action
        if action == RecoveryAction.WAIT_AND_RETRY:
            plan.delay_seconds = min(2 ** error.retry_count, 30)  # Exponential backoff
            plan.parameters["delay"] = plan.delay_seconds

        if action == RecoveryAction.RETRY_DIFFERENT_WORKER:
            plan.parameters["exclude_worker"] = error.worker_id

        return plan

    def _log_error(self, error: ExecutionError, plan: RecoveryPlan):
        """Log error with appropriate level."""
        log_fn = {
            ErrorSeverity.LOW: logger.debug,
            ErrorSeverity.MEDIUM: logger.warning,
            ErrorSeverity.HIGH: logger.error,
            ErrorSeverity.CRITICAL: logger.critical,
        }.get(error.severity, logger.error)

        log_fn(
            f"Error [{error.category.value}] {error.task_id}: {error.message} "
            f"-> {plan.action.value}"
        )

    def _send_alert(self, error: ExecutionError, plan: RecoveryPlan):
        """Send alert via Synapse."""
        synapse = self._get_synapse()
        if synapse:
            synapse.send_alert(
                source="error_handler",
                alert_type=f"execution_error_{error.severity.value}",
                message=f"Task {error.task_id}: {error.message}",
                severity="critical" if error.severity == ErrorSeverity.CRITICAL else "warning",
            )

    def get_error(self, error_id: str) -> Optional[ExecutionError]:
        """Get error by ID."""
        return self._errors.get(error_id)

    def get_task_errors(self, task_id: str) -> List[ExecutionError]:
        """Get all errors for a task."""
        return [e for e in self._errors.values() if e.task_id == task_id]

    def get_goal_errors(self, goal_id: str) -> List[ExecutionError]:
        """Get all errors for a goal."""
        return [e for e in self._errors.values() if e.goal_id == goal_id]

    def should_abort_goal(self, goal_id: str) -> bool:
        """Check if goal should be aborted due to errors."""
        errors = self.get_goal_errors(goal_id)
        critical = sum(1 for e in errors if e.severity == ErrorSeverity.CRITICAL)
        return critical >= 3

    def clear_task_errors(self, task_id: str):
        """Clear errors for a task (after successful retry)."""
        with self._lock:
            self._error_counts.pop(task_id, None)
            self._errors = {
                eid: e for eid, e in self._errors.items()
                if e.task_id != task_id
            }

    def get_stats(self) -> Dict:
        """Get error statistics."""
        with self._lock:
            by_category = dict(self._pattern_counts)
            by_severity = defaultdict(int)
            for error in self._errors.values():
                by_severity[error.severity.value] += 1

            return {
                "total_errors": len(self._errors),
                "by_category": by_category,
                "by_severity": dict(by_severity),
                "tasks_with_errors": len(self._error_counts),
            }

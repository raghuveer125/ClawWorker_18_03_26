"""
Goal Manager - Central goal state management.

Tracks goals through their lifecycle:
Input -> Parsed -> Planned -> Prioritized -> Executing -> Complete/Failed
"""

import hashlib
import time
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class GoalType(Enum):
    """Types of goals the system can handle."""
    TRADE = "trade"           # Execute a trade strategy
    OPTIMIZE = "optimize"     # Optimize parameters
    ANALYZE = "analyze"       # Analyze market/data
    LEARN = "learn"           # Learn patterns
    MONITOR = "monitor"       # Monitor conditions
    IMPROVE = "improve"       # Self-improvement task


class GoalStatus(Enum):
    """Status of a goal."""
    PENDING = "pending"       # Just received
    PARSING = "parsing"       # Being parsed
    PARSED = "parsed"         # Parsed successfully
    PLANNING = "planning"     # Creating task plan
    PLANNED = "planned"       # Tasks created
    CHECKING = "checking"     # Checking data requirements
    READY = "ready"           # Ready for execution
    EXECUTING = "executing"   # Being executed
    COMPLETE = "complete"     # Successfully completed
    FAILED = "failed"         # Failed
    BLOCKED = "blocked"       # Blocked (missing data, etc.)


@dataclass
class Task:
    """A single task derived from a goal."""
    task_id: str
    name: str
    description: str
    dependencies: List[str] = field(default_factory=list)  # Task IDs
    data_requirements: List[str] = field(default_factory=list)  # Field names
    worker_type: Optional[str] = None  # Which army/worker handles this
    priority: int = 5  # 1-10, higher = more urgent
    status: str = "pending"
    result: Optional[Dict] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "dependencies": self.dependencies,
            "data_requirements": self.data_requirements,
            "worker_type": self.worker_type,
            "priority": self.priority,
            "status": self.status,
            "result": self.result,
            "created_at": self.created_at,
        }


@dataclass
class Goal:
    """A goal submitted to the system."""
    goal_id: str
    raw_input: str
    goal_type: GoalType = GoalType.ANALYZE
    status: GoalStatus = GoalStatus.PENDING
    parsed: Optional[Dict] = None
    tasks: List[Task] = field(default_factory=list)
    data_requirements: List[str] = field(default_factory=list)
    missing_data: List[str] = field(default_factory=list)
    priority: int = 5
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    result: Optional[Dict] = None
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "goal_id": self.goal_id,
            "raw_input": self.raw_input,
            "goal_type": self.goal_type.value,
            "status": self.status.value,
            "parsed": self.parsed,
            "tasks": [t.to_dict() for t in self.tasks],
            "data_requirements": self.data_requirements,
            "missing_data": self.missing_data,
            "priority": self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
        }


class GoalManager:
    """
    Central manager for goals.

    Tracks all goals through their lifecycle.
    Provides queries for active, pending, blocked goals.
    """

    def __init__(self):
        self._goals: Dict[str, Goal] = {}
        self._stats = {
            "total_goals": 0,
            "completed_goals": 0,
            "failed_goals": 0,
            "blocked_goals": 0,
        }

    def create_goal(self, raw_input: str, metadata: Optional[Dict] = None) -> Goal:
        """Create a new goal from raw input."""
        goal_id = hashlib.sha256(
            f"{raw_input}{time.time()}".encode()
        ).hexdigest()[:12]

        goal = Goal(
            goal_id=goal_id,
            raw_input=raw_input,
            metadata=metadata or {},
        )

        self._goals[goal_id] = goal
        self._stats["total_goals"] += 1

        logger.info(f"Goal created: {goal_id}")
        return goal

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """Get a goal by ID."""
        return self._goals.get(goal_id)

    def update_goal(self, goal_id: str, **kwargs) -> bool:
        """Update goal attributes."""
        goal = self._goals.get(goal_id)
        if not goal:
            return False

        for key, value in kwargs.items():
            if hasattr(goal, key):
                setattr(goal, key, value)

        goal.updated_at = time.time()
        return True

    def set_status(self, goal_id: str, status: GoalStatus) -> bool:
        """Update goal status."""
        goal = self._goals.get(goal_id)
        if not goal:
            return False

        old_status = goal.status
        goal.status = status
        goal.updated_at = time.time()

        # Update stats
        if status == GoalStatus.COMPLETE:
            goal.completed_at = time.time()
            self._stats["completed_goals"] += 1
        elif status == GoalStatus.FAILED:
            self._stats["failed_goals"] += 1
        elif status == GoalStatus.BLOCKED:
            self._stats["blocked_goals"] += 1

        logger.info(f"Goal {goal_id} status: {old_status.value} -> {status.value}")
        return True

    def add_task(self, goal_id: str, task: Task) -> bool:
        """Add a task to a goal."""
        goal = self._goals.get(goal_id)
        if not goal:
            return False

        goal.tasks.append(task)
        goal.updated_at = time.time()
        return True

    def get_pending_goals(self) -> List[Goal]:
        """Get goals awaiting processing."""
        return [
            g for g in self._goals.values()
            if g.status in [GoalStatus.PENDING, GoalStatus.PARSED, GoalStatus.PLANNED]
        ]

    def get_ready_goals(self) -> List[Goal]:
        """Get goals ready for execution."""
        return [g for g in self._goals.values() if g.status == GoalStatus.READY]

    def get_blocked_goals(self) -> List[Goal]:
        """Get blocked goals (missing data)."""
        return [g for g in self._goals.values() if g.status == GoalStatus.BLOCKED]

    def get_active_goals(self) -> List[Goal]:
        """Get currently executing goals."""
        return [g for g in self._goals.values() if g.status == GoalStatus.EXECUTING]

    def get_recent_goals(self, limit: int = 20) -> List[Goal]:
        """Get recent goals."""
        goals = sorted(self._goals.values(), key=lambda g: g.created_at, reverse=True)
        return goals[:limit]

    def get_stats(self) -> Dict:
        """Get goal statistics."""
        status_counts = {}
        for status in GoalStatus:
            status_counts[status.value] = len([
                g for g in self._goals.values() if g.status == status
            ])

        return {
            **self._stats,
            "by_status": status_counts,
            "active_count": len(self._goals),
        }

    def cleanup_old_goals(self, max_age_hours: float = 24) -> int:
        """Remove old completed/failed goals."""
        cutoff = time.time() - (max_age_hours * 3600)
        to_remove = [
            gid for gid, g in self._goals.items()
            if g.status in [GoalStatus.COMPLETE, GoalStatus.FAILED]
            and g.updated_at < cutoff
        ]

        for gid in to_remove:
            del self._goals[gid]

        return len(to_remove)


# Global singleton
_goal_manager: Optional[GoalManager] = None


def get_goal_manager() -> GoalManager:
    """Get the global GoalManager instance."""
    global _goal_manager
    if _goal_manager is None:
        _goal_manager = GoalManager()
    return _goal_manager

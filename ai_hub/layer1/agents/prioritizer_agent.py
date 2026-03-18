"""
Prioritizer Agent - Scores urgency and allocates resources.

Factors:
- Time sensitivity (market hours, expiry)
- Potential impact (profit/loss)
- Resource requirements
- Dependencies on other goals
"""

import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..goal.goal_manager import Goal, GoalType, GoalStatus, Task, GoalManager, get_goal_manager

logger = logging.getLogger(__name__)


class PrioritizerAgent:
    """
    Prioritizes goals and tasks based on multiple factors.
    """

    AGENT_TYPE = "prioritizer"

    # Priority weights by goal type
    TYPE_WEIGHTS = {
        GoalType.TRADE: 1.5,      # Trading is highest priority
        GoalType.MONITOR: 1.3,    # Monitoring for alerts
        GoalType.ANALYZE: 1.0,    # Standard analysis
        GoalType.OPTIMIZE: 0.8,   # Can be deferred
        GoalType.LEARN: 0.7,      # Background learning
        GoalType.IMPROVE: 0.6,    # Self-improvement lowest urgency
    }

    # Market hours (IST)
    MARKET_OPEN = 9 * 60 + 15   # 9:15 AM in minutes
    MARKET_CLOSE = 15 * 60 + 30  # 3:30 PM in minutes

    def __init__(self, goal_manager: Optional[GoalManager] = None):
        self.goal_manager = goal_manager or get_goal_manager()

    async def prioritize(self, goal: Goal) -> int:
        """
        Calculate and set priority for a goal.

        Returns:
            Priority score (1-10)
        """
        scores = {
            "type": self._score_by_type(goal),
            "time": self._score_by_time(goal),
            "impact": self._score_by_impact(goal),
            "resources": self._score_by_resources(goal),
            "age": self._score_by_age(goal),
        }

        # Weighted combination
        weights = {
            "type": 0.25,
            "time": 0.30,
            "impact": 0.25,
            "resources": 0.10,
            "age": 0.10,
        }

        total = sum(scores[k] * weights[k] for k in scores)
        priority = min(max(int(total), 1), 10)

        # Update goal
        self.goal_manager.update_goal(goal.goal_id, priority=priority)

        # Update task priorities based on goal priority
        self._update_task_priorities(goal, priority)

        logger.info(f"Goal {goal.goal_id} priority: {priority} (scores: {scores})")
        return priority

    async def prioritize_all(self, goals: List[Goal]) -> List[Goal]:
        """
        Prioritize multiple goals and return sorted list.

        Returns:
            Goals sorted by priority (highest first)
        """
        for goal in goals:
            await self.prioritize(goal)

        return sorted(goals, key=lambda g: g.priority, reverse=True)

    def _score_by_type(self, goal: Goal) -> float:
        """Score based on goal type."""
        weight = self.TYPE_WEIGHTS.get(goal.goal_type, 1.0)
        return weight * 6.67  # Scale to ~10 max

    def _score_by_time(self, goal: Goal) -> float:
        """Score based on time sensitivity."""
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute

        # Check if market hours
        if self.MARKET_OPEN <= current_minutes <= self.MARKET_CLOSE:
            is_market_hours = True
            # Higher priority near market close
            minutes_to_close = self.MARKET_CLOSE - current_minutes
            if minutes_to_close < 30:
                return 10.0  # Very urgent
            elif minutes_to_close < 60:
                return 8.0
            else:
                return 6.0
        else:
            is_market_hours = False
            # Lower priority outside market hours
            return 3.0

    def _score_by_impact(self, goal: Goal) -> float:
        """Score based on potential impact."""
        # Trade goals have direct P&L impact
        if goal.goal_type == GoalType.TRADE:
            params = goal.parsed.get("parameters", {}) if goal.parsed else {}

            # Check for risk parameters
            if params.get("percentage"):
                return min(params["percentage"] * 2, 10)
            if params.get("points"):
                return min(params["points"] / 50 * 10, 10)

            return 7.0  # Default high for trades

        # Optimize/improve have indirect impact
        elif goal.goal_type in [GoalType.OPTIMIZE, GoalType.IMPROVE]:
            return 5.0

        # Analysis/monitoring/learning
        return 4.0

    def _score_by_resources(self, goal: Goal) -> float:
        """Score based on resource requirements (inverse - less resources = higher)."""
        # Count tasks
        task_count = len(goal.tasks)

        if task_count <= 2:
            return 10.0  # Quick goals
        elif task_count <= 4:
            return 7.0
        elif task_count <= 6:
            return 5.0
        else:
            return 3.0

    def _score_by_age(self, goal: Goal) -> float:
        """Score based on how long goal has been waiting."""
        age_seconds = time.time() - goal.created_at

        if age_seconds < 60:
            return 5.0  # New goals
        elif age_seconds < 300:  # 5 minutes
            return 6.0
        elif age_seconds < 900:  # 15 minutes
            return 8.0
        else:
            return 10.0  # Old goals get priority boost

    def _update_task_priorities(self, goal: Goal, goal_priority: int):
        """Update task priorities based on goal priority."""
        for task in goal.tasks:
            # Tasks inherit goal priority with adjustments
            task.priority = goal_priority

            # Boost priority for risk-related tasks
            if "risk" in task.name.lower():
                task.priority = min(task.priority + 1, 10)

            # Boost priority for execution tasks
            if "execute" in task.name.lower() or "entry" in task.name.lower():
                task.priority = min(task.priority + 1, 10)

    def get_priority_queue(self, goals: Optional[List[Goal]] = None) -> List[Goal]:
        """Get goals sorted by priority for execution."""
        if goals is None:
            goals = self.goal_manager.get_ready_goals()

        return sorted(goals, key=lambda g: (g.priority, -g.created_at), reverse=True)

    def estimate_resources(self, goal: Goal) -> Dict[str, Any]:
        """Estimate resources needed for a goal."""
        worker_counts = {}
        data_fields = set()

        for task in goal.tasks:
            worker_type = task.worker_type or "default"
            worker_counts[worker_type] = worker_counts.get(worker_type, 0) + 1
            data_fields.update(task.data_requirements)

        return {
            "task_count": len(goal.tasks),
            "worker_counts": worker_counts,
            "data_fields": list(data_fields),
            "estimated_duration_seconds": len(goal.tasks) * 10,  # Rough estimate
        }

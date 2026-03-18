"""
AI Engineering Hub - Layer 1: Goal Decomposition

Breaks high-level goals into actionable tasks.
Verifies data requirements with Layer 0.

Components:
- GoalParserAgent: Parse natural language goals
- TaskPlannerAgent: Create dependency graphs
- PrioritizerAgent: Urgency and resource scoring
- DataCheckerAgent: Verify Layer 0 data availability
"""

from .agents.goal_parser_agent import GoalParserAgent
from .agents.task_planner_agent import TaskPlannerAgent
from .agents.prioritizer_agent import PrioritizerAgent
from .agents.data_checker_agent import DataCheckerAgent
from .goal.goal_manager import GoalManager

__all__ = [
    "GoalParserAgent",
    "TaskPlannerAgent",
    "PrioritizerAgent",
    "DataCheckerAgent",
    "GoalManager",
]

__version__ = "0.1.0"

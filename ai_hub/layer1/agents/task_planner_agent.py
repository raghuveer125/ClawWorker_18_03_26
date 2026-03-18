"""
Task Planner Agent - Creates task dependency graphs.

Takes a parsed goal and breaks it into executable tasks with dependencies.
Maps tasks to worker armies.
"""

import hashlib
import time
import logging
from typing import Any, Dict, List, Optional

from ..goal.goal_manager import (
    Goal, GoalType, GoalStatus, Task, GoalManager, get_goal_manager
)

logger = logging.getLogger(__name__)


class TaskPlannerAgent:
    """
    Creates task execution plans from parsed goals.

    Maps goal types to task templates and worker armies.
    """

    AGENT_TYPE = "task_planner"

    # Task templates by goal type
    TASK_TEMPLATES = {
        GoalType.TRADE: [
            {
                "name": "fetch_market_data",
                "worker_type": "data_army",
                "data_requirements": ["ltp", "volume", "oi"],
                "dependencies": [],
            },
            {
                "name": "analyze_regime",
                "worker_type": "analysis_army",
                "data_requirements": ["history", "vwap"],
                "dependencies": ["fetch_market_data"],
            },
            {
                "name": "check_risk",
                "worker_type": "risk_army",
                "data_requirements": ["positions", "exposure"],
                "dependencies": ["analyze_regime"],
            },
            {
                "name": "select_strike",
                "worker_type": "scalping_army",
                "data_requirements": ["option_chain", "greeks"],
                "dependencies": ["analyze_regime"],
            },
            {
                "name": "execute_entry",
                "worker_type": "execution_army",
                "data_requirements": ["ltp", "bid", "ask"],
                "dependencies": ["check_risk", "select_strike"],
            },
            {
                "name": "manage_position",
                "worker_type": "execution_army",
                "data_requirements": ["ltp", "pnl"],
                "dependencies": ["execute_entry"],
            },
        ],
        GoalType.OPTIMIZE: [
            {
                "name": "fetch_historical_data",
                "worker_type": "data_army",
                "data_requirements": ["history"],
                "dependencies": [],
            },
            {
                "name": "run_backtest",
                "worker_type": "learning_army",
                "data_requirements": ["history", "trades"],
                "dependencies": ["fetch_historical_data"],
            },
            {
                "name": "analyze_results",
                "worker_type": "learning_army",
                "data_requirements": ["backtest_results"],
                "dependencies": ["run_backtest"],
            },
            {
                "name": "generate_recommendations",
                "worker_type": "learning_army",
                "data_requirements": [],
                "dependencies": ["analyze_results"],
            },
        ],
        GoalType.ANALYZE: [
            {
                "name": "fetch_market_data",
                "worker_type": "data_army",
                "data_requirements": ["ltp", "volume", "history"],
                "dependencies": [],
            },
            {
                "name": "compute_indicators",
                "worker_type": "analysis_army",
                "data_requirements": ["vwap", "atr", "fvg_zones"],
                "dependencies": ["fetch_market_data"],
            },
            {
                "name": "detect_patterns",
                "worker_type": "analysis_army",
                "data_requirements": ["history"],
                "dependencies": ["compute_indicators"],
            },
            {
                "name": "generate_report",
                "worker_type": "analysis_army",
                "data_requirements": [],
                "dependencies": ["detect_patterns"],
            },
        ],
        GoalType.LEARN: [
            {
                "name": "fetch_trade_history",
                "worker_type": "data_army",
                "data_requirements": ["trades", "history"],
                "dependencies": [],
            },
            {
                "name": "analyze_patterns",
                "worker_type": "learning_army",
                "data_requirements": [],
                "dependencies": ["fetch_trade_history"],
            },
            {
                "name": "identify_improvements",
                "worker_type": "learning_army",
                "data_requirements": [],
                "dependencies": ["analyze_patterns"],
            },
            {
                "name": "request_data_updates",
                "worker_type": "learning_army",
                "data_requirements": [],
                "dependencies": ["identify_improvements"],
            },
        ],
        GoalType.MONITOR: [
            {
                "name": "setup_monitoring",
                "worker_type": "data_army",
                "data_requirements": ["ltp"],
                "dependencies": [],
            },
            {
                "name": "check_conditions",
                "worker_type": "analysis_army",
                "data_requirements": ["ltp"],
                "dependencies": ["setup_monitoring"],
            },
            {
                "name": "send_alert",
                "worker_type": "execution_army",
                "data_requirements": [],
                "dependencies": ["check_conditions"],
            },
        ],
        GoalType.IMPROVE: [
            {
                "name": "analyze_performance",
                "worker_type": "learning_army",
                "data_requirements": ["performance_metrics"],
                "dependencies": [],
            },
            {
                "name": "identify_weak_workers",
                "worker_type": "learning_army",
                "data_requirements": [],
                "dependencies": ["analyze_performance"],
            },
            {
                "name": "generate_patches",
                "worker_type": "learning_army",
                "data_requirements": [],
                "dependencies": ["identify_weak_workers"],
            },
            {
                "name": "validate_improvements",
                "worker_type": "learning_army",
                "data_requirements": [],
                "dependencies": ["generate_patches"],
            },
        ],
    }

    def __init__(self, goal_manager: Optional[GoalManager] = None):
        self.goal_manager = goal_manager or get_goal_manager()

    async def plan(self, goal: Goal) -> List[Task]:
        """
        Create task plan for a goal.

        Args:
            goal: Goal with parsed data

        Returns:
            List of Task objects with dependencies
        """
        self.goal_manager.set_status(goal.goal_id, GoalStatus.PLANNING)

        # Get task template for goal type
        templates = self.TASK_TEMPLATES.get(goal.goal_type, [])

        # Customize tasks based on parsed data
        tasks = []
        task_id_map = {}  # name -> task_id

        for template in templates:
            task_id = self._generate_task_id(goal.goal_id, template["name"])
            task_id_map[template["name"]] = task_id

            # Resolve dependency task IDs
            dep_ids = [
                task_id_map[dep] for dep in template["dependencies"]
                if dep in task_id_map
            ]

            # Customize data requirements based on goal targets
            data_reqs = list(template["data_requirements"])
            if goal.parsed:
                targets = goal.parsed.get("targets", [])
                # Add target-specific requirements
                for target in targets:
                    data_reqs.append(f"{target.lower()}_data")

            task = Task(
                task_id=task_id,
                name=template["name"],
                description=self._create_task_description(
                    template["name"], goal
                ),
                dependencies=dep_ids,
                data_requirements=data_reqs,
                worker_type=template["worker_type"],
                priority=self._calculate_task_priority(template, goal),
            )

            tasks.append(task)
            self.goal_manager.add_task(goal.goal_id, task)

        # Collect all data requirements
        all_data_reqs = set()
        for task in tasks:
            all_data_reqs.update(task.data_requirements)

        self.goal_manager.update_goal(
            goal.goal_id,
            data_requirements=list(all_data_reqs)
        )
        self.goal_manager.set_status(goal.goal_id, GoalStatus.PLANNED)

        logger.info(f"Planned {len(tasks)} tasks for goal {goal.goal_id}")
        return tasks

    def _generate_task_id(self, goal_id: str, task_name: str) -> str:
        """Generate unique task ID."""
        return hashlib.sha256(
            f"{goal_id}{task_name}{time.time()}".encode()
        ).hexdigest()[:10]

    def _create_task_description(self, task_name: str, goal: Goal) -> str:
        """Create task description based on goal context."""
        targets = ""
        if goal.parsed:
            targets = ", ".join(goal.parsed.get("targets", []))

        descriptions = {
            "fetch_market_data": f"Fetch real-time market data for {targets or 'indices'}",
            "fetch_historical_data": f"Fetch historical candles for {targets or 'indices'}",
            "analyze_regime": "Detect current market regime (trending/ranging)",
            "check_risk": "Verify risk parameters and exposure limits",
            "select_strike": "Select optimal strike based on greeks and structure",
            "execute_entry": "Execute entry order with proper sizing",
            "manage_position": "Monitor and manage open position",
            "run_backtest": "Run backtest on historical data",
            "analyze_results": "Analyze backtest results for patterns",
            "generate_recommendations": "Generate optimization recommendations",
            "compute_indicators": "Compute technical indicators",
            "detect_patterns": "Detect price action patterns",
            "generate_report": "Generate analysis report",
            "fetch_trade_history": "Fetch historical trade data",
            "analyze_patterns": "Analyze patterns in trade history",
            "identify_improvements": "Identify areas for improvement",
            "request_data_updates": "Request new data fields from Layer 0",
            "setup_monitoring": "Setup price monitoring",
            "check_conditions": "Check if monitoring conditions are met",
            "send_alert": "Send alert notification",
            "analyze_performance": "Analyze system performance metrics",
            "identify_weak_workers": "Identify underperforming workers",
            "generate_patches": "Generate code/config patches",
            "validate_improvements": "Validate improvements in sandbox",
        }

        return descriptions.get(task_name, f"Execute {task_name}")

    def _calculate_task_priority(self, template: Dict, goal: Goal) -> int:
        """Calculate task priority (1-10)."""
        base_priority = goal.priority if goal.priority else 5

        # Risk tasks are higher priority
        if "risk" in template["name"]:
            base_priority = min(base_priority + 2, 10)

        # Entry/execution tasks are high priority
        if "execute" in template["name"] or "entry" in template["name"]:
            base_priority = min(base_priority + 1, 10)

        return base_priority

    def get_execution_order(self, tasks: List[Task]) -> List[List[Task]]:
        """
        Get tasks in execution order (parallel groups).

        Returns:
            List of task groups that can run in parallel
        """
        executed = set()
        order = []

        while len(executed) < len(tasks):
            # Find tasks with all dependencies satisfied
            ready = [
                t for t in tasks
                if t.task_id not in executed
                and all(d in executed for d in t.dependencies)
            ]

            if not ready:
                # Circular dependency or error
                remaining = [t for t in tasks if t.task_id not in executed]
                logger.warning(f"Cannot resolve dependencies for: {[t.name for t in remaining]}")
                break

            order.append(ready)
            for t in ready:
                executed.add(t.task_id)

        return order

    def visualize_plan(self, tasks: List[Task]) -> str:
        """Create ASCII visualization of task plan."""
        order = self.get_execution_order(tasks)

        lines = ["Task Execution Plan:", "=" * 40]

        for i, group in enumerate(order):
            lines.append(f"\nPhase {i + 1}:")
            for task in group:
                deps = ", ".join(task.dependencies[:2]) if task.dependencies else "none"
                lines.append(f"  [{task.worker_type}] {task.name}")
                lines.append(f"    deps: {deps}")

        lines.append("=" * 40)
        return "\n".join(lines)

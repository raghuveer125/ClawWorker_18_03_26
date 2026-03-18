"""
Progress Tracker Agent - Tracks execution progress in real-time.

Features:
- Task-level progress tracking
- Goal-level aggregation
- ETA calculation
- Bottleneck detection
"""

import time
import logging
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import threading

logger = logging.getLogger(__name__)


@dataclass
class TaskProgress:
    """Progress for a single task."""
    task_id: str
    goal_id: str
    task_type: str
    progress: float = 0.0  # 0.0 to 1.0
    status: str = "pending"  # pending, running, completed, failed
    message: str = ""
    started_at: Optional[float] = None
    updated_at: float = field(default_factory=time.time)
    estimated_remaining: Optional[float] = None
    worker_id: Optional[str] = None


@dataclass
class GoalProgress:
    """Aggregate progress for a goal."""
    goal_id: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    running_tasks: int
    overall_progress: float
    started_at: Optional[float] = None
    estimated_completion: Optional[float] = None
    bottleneck_task: Optional[str] = None


class ProgressTrackerAgent:
    """
    Tracks and reports execution progress.

    Features:
    - Real-time progress updates
    - Goal-level aggregation
    - ETA calculation
    - Bottleneck detection
    - Progress history
    """

    AGENT_TYPE = "progress_tracker"

    def __init__(self, synapse=None):
        """
        Initialize progress tracker.

        Args:
            synapse: Synapse for broadcasting updates
        """
        self._synapse = synapse
        self._task_progress: Dict[str, TaskProgress] = {}
        self._goal_tasks: Dict[str, List[str]] = defaultdict(list)
        self._callbacks: List[Callable] = []
        self._lock = threading.Lock()

        # Historical data for ETA
        self._task_history: Dict[str, List[float]] = defaultdict(list)  # task_type -> durations

    def _get_synapse(self):
        """Lazy load synapse."""
        if self._synapse is None:
            try:
                from ...synapse import get_synapse
                self._synapse = get_synapse()
            except ImportError:
                pass
        return self._synapse

    def on_update(self, callback: Callable[[TaskProgress], None]):
        """Register progress update callback."""
        self._callbacks.append(callback)

    def _emit_update(self, progress: TaskProgress):
        """Emit progress update."""
        for callback in self._callbacks:
            try:
                callback(progress)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

        # Broadcast via Synapse
        synapse = self._get_synapse()
        if synapse:
            synapse.send(
                channel="status",
                source="progress_tracker",
                payload={
                    "task_id": progress.task_id,
                    "goal_id": progress.goal_id,
                    "progress": progress.progress,
                    "status": progress.status,
                    "message": progress.message,
                },
                message_type="progress_update",
            )

    def start_task(
        self,
        task_id: str,
        goal_id: str,
        task_type: str,
        worker_id: Optional[str] = None,
    ):
        """Mark task as started."""
        with self._lock:
            progress = TaskProgress(
                task_id=task_id,
                goal_id=goal_id,
                task_type=task_type,
                progress=0.0,
                status="running",
                message="Task started",
                started_at=time.time(),
                worker_id=worker_id,
            )
            self._task_progress[task_id] = progress
            self._goal_tasks[goal_id].append(task_id)

        self._emit_update(progress)
        logger.debug(f"Task started: {task_id}")

    def update_progress(
        self,
        task_id: str,
        progress: float,
        message: str = "",
    ):
        """Update task progress."""
        with self._lock:
            if task_id not in self._task_progress:
                return

            task_prog = self._task_progress[task_id]
            task_prog.progress = min(max(progress, 0.0), 1.0)
            task_prog.message = message
            task_prog.updated_at = time.time()

            # Calculate ETA based on progress rate
            if task_prog.started_at and progress > 0:
                elapsed = time.time() - task_prog.started_at
                total_estimated = elapsed / progress
                task_prog.estimated_remaining = total_estimated - elapsed

        self._emit_update(task_prog)

    def complete_task(
        self,
        task_id: str,
        success: bool = True,
        message: str = "",
    ):
        """Mark task as completed."""
        with self._lock:
            if task_id not in self._task_progress:
                return

            task_prog = self._task_progress[task_id]
            task_prog.progress = 1.0
            task_prog.status = "completed" if success else "failed"
            task_prog.message = message or ("Completed" if success else "Failed")
            task_prog.updated_at = time.time()
            task_prog.estimated_remaining = 0

            # Record duration for future ETA
            if task_prog.started_at:
                duration = time.time() - task_prog.started_at
                self._task_history[task_prog.task_type].append(duration)
                # Keep last 100
                if len(self._task_history[task_prog.task_type]) > 100:
                    self._task_history[task_prog.task_type] = \
                        self._task_history[task_prog.task_type][-100:]

        self._emit_update(task_prog)
        logger.debug(f"Task completed: {task_id} (success={success})")

    def get_task_progress(self, task_id: str) -> Optional[TaskProgress]:
        """Get progress for a task."""
        with self._lock:
            return self._task_progress.get(task_id)

    def get_goal_progress(self, goal_id: str) -> GoalProgress:
        """Get aggregate progress for a goal."""
        with self._lock:
            task_ids = self._goal_tasks.get(goal_id, [])
            tasks = [self._task_progress.get(tid) for tid in task_ids]
            tasks = [t for t in tasks if t]

            if not tasks:
                return GoalProgress(
                    goal_id=goal_id,
                    total_tasks=0,
                    completed_tasks=0,
                    failed_tasks=0,
                    running_tasks=0,
                    overall_progress=0.0,
                )

            completed = sum(1 for t in tasks if t.status == "completed")
            failed = sum(1 for t in tasks if t.status == "failed")
            running = sum(1 for t in tasks if t.status == "running")

            # Calculate overall progress
            total_progress = sum(t.progress for t in tasks)
            overall = total_progress / len(tasks) if tasks else 0

            # Find bottleneck (slowest running task)
            running_tasks = [t for t in tasks if t.status == "running"]
            bottleneck = None
            if running_tasks:
                # Task with longest running time
                slowest = min(running_tasks, key=lambda t: t.started_at or time.time())
                bottleneck = slowest.task_id

            # Estimate completion
            started_at = min((t.started_at for t in tasks if t.started_at), default=None)
            estimated_completion = None
            if overall > 0 and started_at:
                elapsed = time.time() - started_at
                total_estimated = elapsed / overall
                estimated_completion = started_at + total_estimated

            return GoalProgress(
                goal_id=goal_id,
                total_tasks=len(tasks),
                completed_tasks=completed,
                failed_tasks=failed,
                running_tasks=running,
                overall_progress=overall,
                started_at=started_at,
                estimated_completion=estimated_completion,
                bottleneck_task=bottleneck,
            )

    def get_estimated_duration(self, task_type: str) -> Optional[float]:
        """Get estimated duration for a task type based on history."""
        with self._lock:
            history = self._task_history.get(task_type, [])
            if history:
                return sum(history) / len(history)
        return None

    def detect_bottlenecks(self, goal_id: str) -> List[Dict]:
        """
        Detect bottlenecks in goal execution.

        Returns:
            List of bottleneck info dicts
        """
        bottlenecks = []

        with self._lock:
            task_ids = self._goal_tasks.get(goal_id, [])
            tasks = [self._task_progress.get(tid) for tid in task_ids]
            running = [t for t in tasks if t and t.status == "running"]

            for task in running:
                if not task.started_at:
                    continue

                elapsed = time.time() - task.started_at
                expected = self.get_estimated_duration(task.task_type)

                if expected and elapsed > expected * 2:
                    bottlenecks.append({
                        "task_id": task.task_id,
                        "task_type": task.task_type,
                        "elapsed": elapsed,
                        "expected": expected,
                        "ratio": elapsed / expected,
                        "worker_id": task.worker_id,
                    })

        # Sort by ratio (worst first)
        bottlenecks.sort(key=lambda b: b["ratio"], reverse=True)
        return bottlenecks

    def get_summary(self) -> Dict:
        """Get overall execution summary."""
        with self._lock:
            total = len(self._task_progress)
            completed = sum(1 for t in self._task_progress.values() if t.status == "completed")
            failed = sum(1 for t in self._task_progress.values() if t.status == "failed")
            running = sum(1 for t in self._task_progress.values() if t.status == "running")
            pending = sum(1 for t in self._task_progress.values() if t.status == "pending")

            return {
                "total_tasks": total,
                "completed": completed,
                "failed": failed,
                "running": running,
                "pending": pending,
                "goals": len(self._goal_tasks),
                "completion_rate": completed / total if total > 0 else 0,
            }

    def clear_goal(self, goal_id: str):
        """Clear progress data for a goal."""
        with self._lock:
            task_ids = self._goal_tasks.pop(goal_id, [])
            for tid in task_ids:
                self._task_progress.pop(tid, None)

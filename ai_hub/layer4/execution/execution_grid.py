"""
Parallel Execution Grid - Executes tasks across worker armies.

Features:
- Async parallel execution
- Result aggregation
- Timeout handling
- Dependency-aware scheduling
"""

import asyncio
import time
import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field
from collections import defaultdict
import threading

logger = logging.getLogger(__name__)


class ExecutionStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class ExecutionTask:
    """A task to execute."""
    task_id: str
    goal_id: str
    task_type: str
    worker_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    timeout: float = 60.0
    priority: int = 5
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    executor: Optional[Callable] = None  # Function to execute


@dataclass
class ExecutionResult:
    """Result of task execution."""
    task_id: str
    goal_id: str
    status: ExecutionStatus
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: float = 0
    completed_at: float = 0
    execution_time: float = 0
    worker_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ParallelExecutionGrid:
    """
    Executes tasks in parallel across workers.

    Features:
    - Dependency-aware scheduling
    - Concurrent execution with limits
    - Timeout handling
    - Result aggregation
    - Progress callbacks
    """

    def __init__(
        self,
        max_concurrent: int = 10,
        default_timeout: float = 60.0,
        synapse=None,
    ):
        """
        Initialize execution grid.

        Args:
            max_concurrent: Maximum concurrent tasks
            default_timeout: Default task timeout
            synapse: Synapse for messaging
        """
        self._max_concurrent = max_concurrent
        self._default_timeout = default_timeout
        self._synapse = synapse

        self._tasks: Dict[str, ExecutionTask] = {}
        self._results: Dict[str, ExecutionResult] = {}
        self._running: Set[str] = set()
        self._completed: Set[str] = set()

        self._progress_callbacks: List[Callable] = []
        self._completion_callbacks: List[Callable] = []

        self._lock = threading.Lock()
        self._semaphore: Optional[asyncio.Semaphore] = None

        # Stats
        self._stats = {
            "total_executed": 0,
            "successful": 0,
            "failed": 0,
            "timeouts": 0,
            "total_time": 0.0,
        }

    def _get_synapse(self):
        """Lazy load synapse."""
        if self._synapse is None:
            try:
                from ...synapse import get_synapse
                self._synapse = get_synapse()
            except ImportError:
                pass
        return self._synapse

    def on_progress(self, callback: Callable[[str, float, str], None]):
        """Register progress callback (task_id, progress, message)."""
        self._progress_callbacks.append(callback)

    def on_completion(self, callback: Callable[[ExecutionResult], None]):
        """Register completion callback."""
        self._completion_callbacks.append(callback)

    def _emit_progress(self, task_id: str, progress: float, message: str):
        """Emit progress update."""
        for callback in self._progress_callbacks:
            try:
                callback(task_id, progress, message)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

    def _emit_completion(self, result: ExecutionResult):
        """Emit completion event."""
        for callback in self._completion_callbacks:
            try:
                callback(result)
            except Exception as e:
                logger.error(f"Completion callback error: {e}")

    async def execute_single(
        self,
        task: ExecutionTask,
        executor: Optional[Callable] = None,
    ) -> ExecutionResult:
        """
        Execute a single task.

        Args:
            task: Task to execute
            executor: Optional custom executor function

        Returns:
            ExecutionResult
        """
        task.started_at = time.time()
        self._emit_progress(task.task_id, 0.0, "Starting execution")

        with self._lock:
            self._tasks[task.task_id] = task
            self._running.add(task.task_id)

        try:
            # Use provided executor or default
            exec_fn = executor or task.executor or self._default_executor

            # Execute with timeout
            result_data = await asyncio.wait_for(
                self._run_task(task, exec_fn),
                timeout=task.timeout or self._default_timeout
            )

            completed_at = time.time()
            result = ExecutionResult(
                task_id=task.task_id,
                goal_id=task.goal_id,
                status=ExecutionStatus.COMPLETED,
                result=result_data,
                started_at=task.started_at,
                completed_at=completed_at,
                execution_time=completed_at - task.started_at,
                worker_id=task.worker_id,
            )

            self._stats["successful"] += 1

        except asyncio.TimeoutError:
            completed_at = time.time()
            result = ExecutionResult(
                task_id=task.task_id,
                goal_id=task.goal_id,
                status=ExecutionStatus.TIMEOUT,
                error=f"Task timed out after {task.timeout}s",
                started_at=task.started_at,
                completed_at=completed_at,
                execution_time=completed_at - task.started_at,
                worker_id=task.worker_id,
            )
            self._stats["timeouts"] += 1

        except Exception as e:
            completed_at = time.time()
            result = ExecutionResult(
                task_id=task.task_id,
                goal_id=task.goal_id,
                status=ExecutionStatus.FAILED,
                error=str(e),
                started_at=task.started_at,
                completed_at=completed_at,
                execution_time=completed_at - task.started_at,
                worker_id=task.worker_id,
            )
            self._stats["failed"] += 1
            logger.error(f"Task {task.task_id} failed: {e}")

        with self._lock:
            self._running.discard(task.task_id)
            self._completed.add(task.task_id)
            self._results[task.task_id] = result
            self._stats["total_executed"] += 1
            self._stats["total_time"] += result.execution_time

        self._emit_progress(task.task_id, 1.0, f"Completed: {result.status.value}")
        self._emit_completion(result)

        return result

    async def _run_task(self, task: ExecutionTask, executor: Callable) -> Any:
        """Run task with executor."""
        self._emit_progress(task.task_id, 0.1, "Executing...")

        # If executor is async
        if asyncio.iscoroutinefunction(executor):
            result = await executor(task)
        else:
            # Run sync function in thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, executor, task)

        self._emit_progress(task.task_id, 0.9, "Processing result...")
        return result

    async def _default_executor(self, task: ExecutionTask) -> Dict:
        """Default executor - sends to worker via Synapse."""
        synapse = self._get_synapse()

        if synapse and task.worker_id:
            # Send to worker and wait for response
            response = await synapse.request(
                channel="cmd",
                source="execution_grid",
                payload={
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                    "payload": task.payload,
                },
                target=task.worker_id,
                timeout=task.timeout,
            )
            return response or {"status": "no_response"}
        else:
            # Simulate execution
            await asyncio.sleep(0.1)
            return {"status": "simulated", "task_type": task.task_type}

    async def execute_parallel(
        self,
        tasks: List[ExecutionTask],
        respect_dependencies: bool = True,
    ) -> Dict[str, ExecutionResult]:
        """
        Execute multiple tasks in parallel.

        Args:
            tasks: Tasks to execute
            respect_dependencies: Wait for dependencies

        Returns:
            Dict of task_id -> ExecutionResult
        """
        if not tasks:
            return {}

        self._semaphore = asyncio.Semaphore(self._max_concurrent)

        # Group by dependencies
        if respect_dependencies:
            execution_order = self._topological_sort(tasks)
        else:
            execution_order = [tasks]

        results = {}

        for batch in execution_order:
            # Execute batch in parallel
            batch_tasks = [
                self._execute_with_semaphore(task)
                for task in batch
            ]

            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

            for task, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    results[task.task_id] = ExecutionResult(
                        task_id=task.task_id,
                        goal_id=task.goal_id,
                        status=ExecutionStatus.FAILED,
                        error=str(result),
                    )
                else:
                    results[task.task_id] = result

        return results

    async def _execute_with_semaphore(self, task: ExecutionTask) -> ExecutionResult:
        """Execute task with concurrency limit."""
        async with self._semaphore:
            return await self.execute_single(task)

    def _topological_sort(self, tasks: List[ExecutionTask]) -> List[List[ExecutionTask]]:
        """Sort tasks by dependencies into execution batches."""
        task_map = {t.task_id: t for t in tasks}
        completed = set()
        batches = []

        remaining = set(task_map.keys())

        while remaining:
            # Find tasks with all dependencies satisfied
            ready = []
            for task_id in remaining:
                task = task_map[task_id]
                deps_satisfied = all(
                    d in completed or d not in task_map
                    for d in task.dependencies
                )
                if deps_satisfied:
                    ready.append(task)

            if not ready:
                # Circular dependency - just add remaining
                ready = [task_map[tid] for tid in remaining]
                logger.warning(f"Circular dependency detected, forcing execution")

            batches.append(ready)
            for task in ready:
                completed.add(task.task_id)
                remaining.discard(task.task_id)

        return batches

    async def execute_goal(
        self,
        goal_id: str,
        tasks: List[ExecutionTask],
    ) -> Dict[str, ExecutionResult]:
        """
        Execute all tasks for a goal.

        Args:
            goal_id: Goal identifier
            tasks: Tasks to execute

        Returns:
            Results for all tasks
        """
        logger.info(f"Executing goal {goal_id} with {len(tasks)} tasks")

        # Ensure all tasks have goal_id
        for task in tasks:
            task.goal_id = goal_id

        results = await self.execute_parallel(tasks, respect_dependencies=True)

        # Summarize
        success = sum(1 for r in results.values() if r.status == ExecutionStatus.COMPLETED)
        failed = len(results) - success

        logger.info(f"Goal {goal_id} complete: {success} success, {failed} failed")

        return results

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        with self._lock:
            if task_id in self._running:
                # Mark as cancelled
                self._running.discard(task_id)
                self._results[task_id] = ExecutionResult(
                    task_id=task_id,
                    goal_id=self._tasks.get(task_id, ExecutionTask(task_id, "", "")).goal_id,
                    status=ExecutionStatus.CANCELLED,
                )
                return True
        return False

    def get_result(self, task_id: str) -> Optional[ExecutionResult]:
        """Get result for a task."""
        return self._results.get(task_id)

    def get_running(self) -> List[str]:
        """Get list of running task IDs."""
        with self._lock:
            return list(self._running)

    def get_stats(self) -> Dict:
        """Get execution statistics."""
        with self._lock:
            avg_time = (
                self._stats["total_time"] / self._stats["total_executed"]
                if self._stats["total_executed"] > 0 else 0
            )
            return {
                **self._stats,
                "running": len(self._running),
                "completed": len(self._completed),
                "avg_execution_time": avg_time,
            }

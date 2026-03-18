"""
Dispatcher Agent - Routes tasks to workers via Synapse.

Handles:
- Task-to-worker matching
- Load-aware dispatching
- Priority queue management
- Retry logic
"""

import asyncio
import time
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from queue import PriorityQueue
from collections import defaultdict
import threading

from ..registry.worker_registry import (
    WorkerRegistry, WorkerInfo, WorkerStatus, ArmyType
)

logger = logging.getLogger(__name__)


@dataclass(order=True)
class DispatchTask:
    """A task to be dispatched."""
    priority: int = field(compare=True)
    task_id: str = field(compare=False)
    task_type: str = field(compare=False)
    goal_id: str = field(compare=False)
    payload: Dict[str, Any] = field(compare=False, default_factory=dict)
    required_capabilities: List[str] = field(compare=False, default_factory=list)
    preferred_army: Optional[ArmyType] = field(compare=False, default=None)
    timeout: float = field(compare=False, default=60.0)
    retries: int = field(compare=False, default=0)
    max_retries: int = field(compare=False, default=3)
    created_at: float = field(compare=False, default_factory=time.time)
    callback: Optional[Callable] = field(compare=False, default=None)


@dataclass
class DispatchResult:
    """Result of task dispatch."""
    task_id: str
    worker_id: Optional[str]
    success: bool
    error: Optional[str] = None
    dispatch_time: float = 0.0
    queued_time: float = 0.0


class DispatcherAgent:
    """
    Dispatches tasks to worker armies.

    Features:
    - Priority-based task queue
    - Capability matching
    - Load balancing
    - Retry with backoff
    - Synapse integration
    """

    AGENT_TYPE = "dispatcher"

    def __init__(
        self,
        registry: Optional[WorkerRegistry] = None,
        synapse=None,
        max_queue_size: int = 1000,
    ):
        """
        Initialize dispatcher.

        Args:
            registry: Worker registry
            synapse: Synapse message bus
            max_queue_size: Maximum pending tasks
        """
        self._registry = registry or WorkerRegistry()
        self._synapse = synapse
        self._queue: PriorityQueue = PriorityQueue(maxsize=max_queue_size)
        self._pending: Dict[str, DispatchTask] = {}
        self._results: Dict[str, DispatchResult] = {}
        self._lock = threading.Lock()

        # Stats
        self._stats = {
            "dispatched": 0,
            "completed": 0,
            "failed": 0,
            "retried": 0,
            "by_army": defaultdict(int),
        }

        self._running = False

    def _get_synapse(self):
        """Lazy load synapse."""
        if self._synapse is None:
            try:
                from ...synapse import get_synapse
                self._synapse = get_synapse()
            except ImportError:
                pass
        return self._synapse

    def start(self):
        """Start dispatcher."""
        self._running = True
        logger.info("Dispatcher started")

    def stop(self):
        """Stop dispatcher."""
        self._running = False
        logger.info("Dispatcher stopped")

    def submit(
        self,
        task_id: str,
        task_type: str,
        goal_id: str,
        payload: Dict[str, Any],
        priority: int = 5,
        required_capabilities: Optional[List[str]] = None,
        preferred_army: Optional[ArmyType] = None,
        timeout: float = 60.0,
        callback: Optional[Callable] = None,
    ) -> bool:
        """
        Submit a task for dispatch.

        Args:
            task_id: Unique task identifier
            task_type: Type of task
            goal_id: Parent goal ID
            payload: Task data
            priority: Priority (1=highest, 10=lowest)
            required_capabilities: Required worker capabilities
            preferred_army: Preferred army type
            timeout: Task timeout
            callback: Completion callback

        Returns:
            True if queued successfully
        """
        task = DispatchTask(
            priority=priority,
            task_id=task_id,
            task_type=task_type,
            goal_id=goal_id,
            payload=payload,
            required_capabilities=required_capabilities or [],
            preferred_army=preferred_army,
            timeout=timeout,
            callback=callback,
        )

        try:
            self._queue.put_nowait(task)
            with self._lock:
                self._pending[task_id] = task
            logger.debug(f"Task queued: {task_id} (type={task_type}, priority={priority})")
            return True
        except Exception as e:
            logger.error(f"Failed to queue task {task_id}: {e}")
            return False

    async def dispatch_next(self) -> Optional[DispatchResult]:
        """
        Dispatch the next task from queue.

        Returns:
            DispatchResult or None if queue empty
        """
        if self._queue.empty():
            return None

        try:
            task = self._queue.get_nowait()
        except Exception:
            return None

        start_time = time.time()
        queued_time = start_time - task.created_at

        # Find suitable worker
        worker = self._find_worker(task)

        if not worker:
            # No worker available - retry or fail
            if task.retries < task.max_retries:
                task.retries += 1
                task.priority = min(task.priority + 1, 10)  # Lower priority
                self._queue.put(task)
                self._stats["retried"] += 1
                logger.warning(f"Task {task.task_id} retry {task.retries}/{task.max_retries}")
                return DispatchResult(
                    task_id=task.task_id,
                    worker_id=None,
                    success=False,
                    error="No worker available - retrying",
                    queued_time=queued_time,
                )
            else:
                self._stats["failed"] += 1
                result = DispatchResult(
                    task_id=task.task_id,
                    worker_id=None,
                    success=False,
                    error="No worker available after max retries",
                    queued_time=queued_time,
                )
                self._results[task.task_id] = result
                return result

        # Dispatch to worker
        success = await self._dispatch_to_worker(task, worker)
        dispatch_time = time.time() - start_time

        if success:
            self._stats["dispatched"] += 1
            if task.preferred_army:
                self._stats["by_army"][task.preferred_army.value] += 1

            result = DispatchResult(
                task_id=task.task_id,
                worker_id=worker.worker_id,
                success=True,
                dispatch_time=dispatch_time,
                queued_time=queued_time,
            )
        else:
            result = DispatchResult(
                task_id=task.task_id,
                worker_id=worker.worker_id,
                success=False,
                error="Dispatch failed",
                dispatch_time=dispatch_time,
                queued_time=queued_time,
            )

        self._results[task.task_id] = result
        return result

    def _find_worker(self, task: DispatchTask) -> Optional[WorkerInfo]:
        """Find best worker for task."""
        # Try by task type first
        candidates = self._registry.find_by_task_type(task.task_type)

        # Filter by required capabilities
        if task.required_capabilities:
            candidates = [
                w for w in candidates
                if all(
                    any(cap.name == rc for cap in w.capabilities)
                    for rc in task.required_capabilities
                )
            ]

        # Filter by preferred army
        if task.preferred_army and candidates:
            army_candidates = [w for w in candidates if w.army == task.preferred_army]
            if army_candidates:
                candidates = army_candidates

        if not candidates:
            return None

        # Select best by success rate
        return max(candidates, key=lambda w: w.success_rate())

    async def _dispatch_to_worker(self, task: DispatchTask, worker: WorkerInfo) -> bool:
        """Send task to worker via Synapse."""
        synapse = self._get_synapse()

        # Update worker status
        self._registry.update_status(worker.worker_id, WorkerStatus.BUSY, task.task_id)

        if synapse:
            # Send via Synapse
            synapse.send_command(
                source="dispatcher",
                command="execute_task",
                params={
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                    "goal_id": task.goal_id,
                    "payload": task.payload,
                    "timeout": task.timeout,
                },
                target=worker.worker_id,
            )
            logger.info(f"Task {task.task_id} dispatched to {worker.worker_id}")
            return True
        else:
            # Direct dispatch (for testing)
            logger.info(f"Task {task.task_id} assigned to {worker.worker_id} (direct)")
            return True

    def complete_task(
        self,
        task_id: str,
        worker_id: str,
        success: bool,
        execution_time: float,
        result: Optional[Dict] = None,
    ):
        """
        Mark task as completed.

        Args:
            task_id: Task ID
            worker_id: Worker that completed it
            success: Whether successful
            execution_time: Time taken
            result: Task result data
        """
        with self._lock:
            task = self._pending.pop(task_id, None)

        # Update registry
        self._registry.record_task_completion(worker_id, success, execution_time)

        if success:
            self._stats["completed"] += 1
        else:
            self._stats["failed"] += 1

        # Call callback
        if task and task.callback:
            try:
                task.callback(task_id, success, result)
            except Exception as e:
                logger.error(f"Task callback error: {e}")

        logger.info(f"Task {task_id} completed: success={success}, time={execution_time:.2f}s")

    async def process_queue(self, batch_size: int = 10):
        """Process pending tasks in queue."""
        processed = 0
        while processed < batch_size and not self._queue.empty():
            await self.dispatch_next()
            processed += 1
            await asyncio.sleep(0.001)  # Small delay between dispatches

    def get_pending_count(self) -> int:
        """Get count of pending tasks."""
        return self._queue.qsize()

    def get_result(self, task_id: str) -> Optional[DispatchResult]:
        """Get result for a task."""
        return self._results.get(task_id)

    def get_stats(self) -> Dict:
        """Get dispatcher statistics."""
        return {
            "queued": self._queue.qsize(),
            "pending": len(self._pending),
            "dispatched": self._stats["dispatched"],
            "completed": self._stats["completed"],
            "failed": self._stats["failed"],
            "retried": self._stats["retried"],
            "by_army": dict(self._stats["by_army"]),
        }

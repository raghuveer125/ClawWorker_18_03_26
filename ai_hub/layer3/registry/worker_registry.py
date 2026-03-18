"""
Worker Registry - Manages all worker armies and their capabilities.

Central registry for:
- Scalping Army (18 bots)
- Signal Army
- Risk Army
- Analysis Army
- Execution Army
"""

import time
import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field
from collections import defaultdict
import threading

logger = logging.getLogger(__name__)


class WorkerStatus(Enum):
    """Worker status."""
    IDLE = "idle"
    BUSY = "busy"
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    OFFLINE = "offline"


class ArmyType(Enum):
    """Types of worker armies."""
    SCALPING = "scalping"
    SIGNAL = "signal"
    RISK = "risk"
    ANALYSIS = "analysis"
    EXECUTION = "execution"
    LEARNING = "learning"
    DATA = "data"


@dataclass
class WorkerCapability:
    """A capability that a worker provides."""
    name: str
    description: str
    task_types: List[str]  # Task types this capability can handle
    performance_score: float = 1.0  # Historical performance
    avg_execution_time: float = 0.0  # seconds


@dataclass
class WorkerInfo:
    """Information about a worker."""
    worker_id: str
    worker_type: str
    army: ArmyType
    capabilities: List[WorkerCapability]
    status: WorkerStatus = WorkerStatus.IDLE
    current_task: Optional[str] = None
    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    tasks_completed: int = 0
    tasks_failed: int = 0
    avg_task_time: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def success_rate(self) -> float:
        total = self.tasks_completed + self.tasks_failed
        return self.tasks_completed / total if total > 0 else 1.0

    def is_available(self) -> bool:
        return self.status in [WorkerStatus.IDLE, WorkerStatus.ACTIVE]


@dataclass
class WorkerArmy:
    """A group of workers with shared purpose."""
    army_type: ArmyType
    name: str
    description: str
    workers: Dict[str, WorkerInfo] = field(default_factory=dict)
    max_workers: int = 20
    min_workers: int = 1
    created_at: float = field(default_factory=time.time)

    def active_count(self) -> int:
        return sum(1 for w in self.workers.values() if w.is_available())

    def busy_count(self) -> int:
        return sum(1 for w in self.workers.values() if w.status == WorkerStatus.BUSY)


class WorkerRegistry:
    """
    Central registry for all worker armies.

    Features:
    - Army registration and management
    - Worker capability indexing
    - Health status tracking
    - Performance metrics
    - Synapse integration
    """

    def __init__(self, synapse=None):
        """
        Initialize registry.

        Args:
            synapse: Synapse instance for messaging
        """
        self._synapse = synapse
        self._armies: Dict[ArmyType, WorkerArmy] = {}
        self._workers: Dict[str, WorkerInfo] = {}
        self._by_capability: Dict[str, List[str]] = defaultdict(list)
        self._by_task_type: Dict[str, List[str]] = defaultdict(list)
        self._lock = threading.Lock()

        # Initialize default armies
        self._init_default_armies()

    def _init_default_armies(self):
        """Initialize default army structures."""
        defaults = [
            (ArmyType.SCALPING, "Scalping Army", "18-bot scalping execution", 18),
            (ArmyType.SIGNAL, "Signal Army", "Signal generation and validation", 10),
            (ArmyType.RISK, "Risk Army", "Risk assessment and management", 5),
            (ArmyType.ANALYSIS, "Analysis Army", "Market analysis and pattern detection", 10),
            (ArmyType.EXECUTION, "Execution Army", "Order execution and management", 10),
            (ArmyType.LEARNING, "Learning Army", "Pattern learning and optimization", 5),
            (ArmyType.DATA, "Data Army", "Data fetching and processing", 5),
        ]

        for army_type, name, desc, max_workers in defaults:
            self._armies[army_type] = WorkerArmy(
                army_type=army_type,
                name=name,
                description=desc,
                max_workers=max_workers,
            )

    def _get_synapse(self):
        """Lazy load synapse."""
        if self._synapse is None:
            try:
                from ...synapse import get_synapse
                self._synapse = get_synapse()
            except ImportError:
                pass
        return self._synapse

    def register_worker(
        self,
        worker_id: str,
        worker_type: str,
        army_type: ArmyType,
        capabilities: List[Dict[str, Any]],
        metadata: Optional[Dict] = None,
    ) -> bool:
        """
        Register a worker with an army.

        Args:
            worker_id: Unique worker identifier
            worker_type: Type of worker
            army_type: Army to join
            capabilities: List of capability dicts
            metadata: Additional metadata

        Returns:
            True if registered successfully
        """
        with self._lock:
            army = self._armies.get(army_type)
            if not army:
                logger.error(f"Unknown army type: {army_type}")
                return False

            if len(army.workers) >= army.max_workers:
                logger.warning(f"Army {army_type.value} at max capacity")
                return False

            # Parse capabilities
            caps = []
            for cap_dict in capabilities:
                cap = WorkerCapability(
                    name=cap_dict.get("name", "unknown"),
                    description=cap_dict.get("description", ""),
                    task_types=cap_dict.get("task_types", []),
                    performance_score=cap_dict.get("performance_score", 1.0),
                )
                caps.append(cap)

                # Index by capability and task types
                self._by_capability[cap.name].append(worker_id)
                for task_type in cap.task_types:
                    self._by_task_type[task_type].append(worker_id)

            worker = WorkerInfo(
                worker_id=worker_id,
                worker_type=worker_type,
                army=army_type,
                capabilities=caps,
                metadata=metadata or {},
            )

            army.workers[worker_id] = worker
            self._workers[worker_id] = worker

        # Register with Synapse coordinator
        synapse = self._get_synapse()
        if synapse:
            from ...synapse.coordination.agent_coordinator import AgentCoordinator
            coordinator = AgentCoordinator(synapse)
            coordinator.register(
                agent_id=worker_id,
                agent_type=worker_type,
                layer=3,
                capabilities=[c.name for c in caps],
            )

        logger.info(f"Worker registered: {worker_id} -> {army_type.value}")
        return True

    def unregister_worker(self, worker_id: str):
        """Unregister a worker."""
        with self._lock:
            worker = self._workers.pop(worker_id, None)
            if worker:
                army = self._armies.get(worker.army)
                if army:
                    army.workers.pop(worker_id, None)

                # Remove from indexes
                for cap in worker.capabilities:
                    if worker_id in self._by_capability[cap.name]:
                        self._by_capability[cap.name].remove(worker_id)
                    for task_type in cap.task_types:
                        if worker_id in self._by_task_type[task_type]:
                            self._by_task_type[task_type].remove(worker_id)

        logger.info(f"Worker unregistered: {worker_id}")

    def get_worker(self, worker_id: str) -> Optional[WorkerInfo]:
        """Get worker info."""
        with self._lock:
            return self._workers.get(worker_id)

    def get_army(self, army_type: ArmyType) -> Optional[WorkerArmy]:
        """Get army info."""
        return self._armies.get(army_type)

    def find_by_capability(
        self,
        capability: str,
        available_only: bool = True
    ) -> List[WorkerInfo]:
        """Find workers with a capability."""
        with self._lock:
            worker_ids = self._by_capability.get(capability, [])
            workers = [self._workers[wid] for wid in worker_ids if wid in self._workers]
            if available_only:
                workers = [w for w in workers if w.is_available()]
            return workers

    def find_by_task_type(
        self,
        task_type: str,
        available_only: bool = True
    ) -> List[WorkerInfo]:
        """Find workers that can handle a task type."""
        with self._lock:
            worker_ids = self._by_task_type.get(task_type, [])
            workers = [self._workers[wid] for wid in worker_ids if wid in self._workers]
            if available_only:
                workers = [w for w in workers if w.is_available()]
            return workers

    def find_in_army(
        self,
        army_type: ArmyType,
        available_only: bool = True
    ) -> List[WorkerInfo]:
        """Find workers in an army."""
        with self._lock:
            army = self._armies.get(army_type)
            if not army:
                return []
            workers = list(army.workers.values())
            if available_only:
                workers = [w for w in workers if w.is_available()]
            return workers

    def select_best_worker(
        self,
        task_type: str,
        army_type: Optional[ArmyType] = None,
    ) -> Optional[WorkerInfo]:
        """
        Select best available worker for a task.

        Uses success rate and performance score.
        """
        candidates = self.find_by_task_type(task_type, available_only=True)

        if army_type:
            candidates = [w for w in candidates if w.army == army_type]

        if not candidates:
            return None

        # Score workers
        def score(w: WorkerInfo) -> float:
            # Find capability performance for this task type
            cap_score = 1.0
            for cap in w.capabilities:
                if task_type in cap.task_types:
                    cap_score = cap.performance_score
                    break
            return w.success_rate() * cap_score

        return max(candidates, key=score)

    def update_status(self, worker_id: str, status: WorkerStatus, task_id: Optional[str] = None):
        """Update worker status."""
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker:
                worker.status = status
                worker.current_task = task_id
                worker.last_heartbeat = time.time()

    def record_task_completion(
        self,
        worker_id: str,
        success: bool,
        execution_time: float,
    ):
        """Record task completion."""
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker:
                if success:
                    worker.tasks_completed += 1
                else:
                    worker.tasks_failed += 1

                # Update average task time
                total = worker.tasks_completed + worker.tasks_failed
                worker.avg_task_time = (
                    (worker.avg_task_time * (total - 1) + execution_time) / total
                )
                worker.status = WorkerStatus.IDLE
                worker.current_task = None

    def heartbeat(self, worker_id: str):
        """Update worker heartbeat."""
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker:
                worker.last_heartbeat = time.time()

    def check_health(self, timeout: float = 60.0) -> List[str]:
        """Check worker health, return unhealthy worker IDs."""
        now = time.time()
        unhealthy = []

        with self._lock:
            for worker_id, worker in self._workers.items():
                if now - worker.last_heartbeat > timeout:
                    worker.status = WorkerStatus.OFFLINE
                    unhealthy.append(worker_id)

        return unhealthy

    def get_army_stats(self, army_type: ArmyType) -> Dict:
        """Get statistics for an army."""
        with self._lock:
            army = self._armies.get(army_type)
            if not army:
                return {}

            workers = list(army.workers.values())
            total = len(workers)
            idle = sum(1 for w in workers if w.status == WorkerStatus.IDLE)
            busy = sum(1 for w in workers if w.status == WorkerStatus.BUSY)
            offline = sum(1 for w in workers if w.status == WorkerStatus.OFFLINE)

            total_completed = sum(w.tasks_completed for w in workers)
            total_failed = sum(w.tasks_failed for w in workers)

            return {
                "army": army_type.value,
                "name": army.name,
                "workers": total,
                "idle": idle,
                "busy": busy,
                "offline": offline,
                "tasks_completed": total_completed,
                "tasks_failed": total_failed,
                "success_rate": total_completed / (total_completed + total_failed) if (total_completed + total_failed) > 0 else 1.0,
            }

    def get_registry_stats(self) -> Dict:
        """Get overall registry statistics."""
        with self._lock:
            return {
                "total_workers": len(self._workers),
                "armies": {
                    at.value: self.get_army_stats(at)
                    for at in ArmyType
                },
                "capabilities": list(self._by_capability.keys()),
                "task_types": list(self._by_task_type.keys()),
            }

    def list_workers(self) -> List[Dict]:
        """List all workers."""
        with self._lock:
            return [
                {
                    "worker_id": w.worker_id,
                    "type": w.worker_type,
                    "army": w.army.value,
                    "status": w.status.value,
                    "capabilities": [c.name for c in w.capabilities],
                    "success_rate": f"{w.success_rate():.1%}",
                    "tasks": w.tasks_completed + w.tasks_failed,
                }
                for w in self._workers.values()
            ]

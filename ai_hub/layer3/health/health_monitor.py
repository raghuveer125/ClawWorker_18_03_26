"""
Health Monitor Agent - Monitors worker health and handles failures.

Features:
- Heartbeat monitoring
- Failure detection
- Auto-restart recommendations
- Performance degradation alerts
"""

import asyncio
import time
import logging
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from collections import defaultdict
import threading

from ..registry.worker_registry import WorkerRegistry, WorkerStatus, ArmyType

logger = logging.getLogger(__name__)


@dataclass
class HealthEvent:
    """A health-related event."""
    event_type: str  # heartbeat_miss, error, recovery, degradation
    worker_id: str
    timestamp: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerHealth:
    """Health state for a worker."""
    worker_id: str
    status: str = "healthy"  # healthy, degraded, unhealthy, offline
    last_heartbeat: float = 0
    consecutive_failures: int = 0
    error_rate: float = 0.0
    avg_response_time: float = 0.0
    recent_events: List[HealthEvent] = field(default_factory=list)


class HealthMonitorAgent:
    """
    Monitors health of all workers.

    Features:
    - Heartbeat tracking
    - Failure pattern detection
    - Automatic alerting
    - Recovery monitoring
    """

    AGENT_TYPE = "health_monitor"

    # Thresholds
    HEARTBEAT_TIMEOUT = 30.0  # seconds
    HEARTBEAT_WARNING = 15.0
    MAX_CONSECUTIVE_FAILURES = 3
    ERROR_RATE_THRESHOLD = 0.3
    RESPONSE_TIME_THRESHOLD = 10.0  # seconds

    def __init__(
        self,
        registry: Optional[WorkerRegistry] = None,
        synapse=None,
    ):
        """
        Initialize health monitor.

        Args:
            registry: Worker registry
            synapse: Synapse for alerts
        """
        self._registry = registry or WorkerRegistry()
        self._synapse = synapse
        self._health: Dict[str, WorkerHealth] = {}
        self._callbacks: List[Callable[[HealthEvent], None]] = []
        self._lock = threading.Lock()
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

    def on_health_event(self, callback: Callable[[HealthEvent], None]):
        """Register health event callback."""
        self._callbacks.append(callback)

    def _emit_event(self, event: HealthEvent):
        """Emit health event to callbacks."""
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Health callback error: {e}")

        # Also send via Synapse
        synapse = self._get_synapse()
        if synapse:
            synapse.send_alert(
                source="health_monitor",
                alert_type=event.event_type,
                message=f"Worker {event.worker_id}: {event.event_type}",
                severity="warning" if event.event_type != "offline" else "critical"
            )

    def record_heartbeat(self, worker_id: str):
        """Record worker heartbeat."""
        now = time.time()

        with self._lock:
            if worker_id not in self._health:
                self._health[worker_id] = WorkerHealth(worker_id=worker_id)

            health = self._health[worker_id]
            health.last_heartbeat = now

            # Recovery from unhealthy
            if health.status in ["unhealthy", "offline"]:
                health.status = "healthy"
                health.consecutive_failures = 0
                event = HealthEvent(
                    event_type="recovery",
                    worker_id=worker_id,
                )
                health.recent_events.append(event)
                self._emit_event(event)

        # Update registry
        self._registry.heartbeat(worker_id)

    def record_task_result(
        self,
        worker_id: str,
        success: bool,
        response_time: float,
    ):
        """Record task completion for health tracking."""
        with self._lock:
            if worker_id not in self._health:
                self._health[worker_id] = WorkerHealth(worker_id=worker_id)

            health = self._health[worker_id]

            if success:
                health.consecutive_failures = 0
            else:
                health.consecutive_failures += 1
                if health.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    health.status = "unhealthy"
                    event = HealthEvent(
                        event_type="consecutive_failures",
                        worker_id=worker_id,
                        details={"count": health.consecutive_failures}
                    )
                    health.recent_events.append(event)
                    self._emit_event(event)

            # Update average response time
            health.avg_response_time = (
                health.avg_response_time * 0.9 + response_time * 0.1
            )

            # Check response time threshold
            if health.avg_response_time > self.RESPONSE_TIME_THRESHOLD:
                if health.status == "healthy":
                    health.status = "degraded"
                    event = HealthEvent(
                        event_type="slow_response",
                        worker_id=worker_id,
                        details={"avg_time": health.avg_response_time}
                    )
                    health.recent_events.append(event)
                    self._emit_event(event)

    def check_all_workers(self) -> Dict[str, List[str]]:
        """
        Check health of all workers.

        Returns:
            Dict with lists of workers by status
        """
        now = time.time()
        results = {
            "healthy": [],
            "degraded": [],
            "unhealthy": [],
            "offline": [],
        }

        # Check registry for all workers
        unhealthy_ids = self._registry.check_health(self.HEARTBEAT_TIMEOUT)

        with self._lock:
            for worker_id in unhealthy_ids:
                if worker_id not in self._health:
                    self._health[worker_id] = WorkerHealth(worker_id=worker_id)

                health = self._health[worker_id]

                if health.status != "offline":
                    health.status = "offline"
                    event = HealthEvent(
                        event_type="offline",
                        worker_id=worker_id,
                    )
                    health.recent_events.append(event)
                    self._emit_event(event)

            # Categorize all known workers
            for worker_id, health in self._health.items():
                time_since_heartbeat = now - health.last_heartbeat

                if health.status == "offline" or time_since_heartbeat > self.HEARTBEAT_TIMEOUT:
                    results["offline"].append(worker_id)
                elif health.status == "unhealthy":
                    results["unhealthy"].append(worker_id)
                elif health.status == "degraded" or time_since_heartbeat > self.HEARTBEAT_WARNING:
                    results["degraded"].append(worker_id)
                else:
                    results["healthy"].append(worker_id)

        return results

    def get_worker_health(self, worker_id: str) -> Optional[WorkerHealth]:
        """Get health info for a worker."""
        with self._lock:
            return self._health.get(worker_id)

    def get_army_health(self, army_type: ArmyType) -> Dict:
        """Get aggregate health for an army."""
        workers = self._registry.find_in_army(army_type, available_only=False)

        healthy = 0
        degraded = 0
        unhealthy = 0
        offline = 0

        with self._lock:
            for worker in workers:
                health = self._health.get(worker.worker_id)
                if not health or health.status == "healthy":
                    healthy += 1
                elif health.status == "degraded":
                    degraded += 1
                elif health.status == "unhealthy":
                    unhealthy += 1
                else:
                    offline += 1

        total = len(workers)
        return {
            "army": army_type.value,
            "total": total,
            "healthy": healthy,
            "degraded": degraded,
            "unhealthy": unhealthy,
            "offline": offline,
            "health_score": healthy / total if total > 0 else 0,
        }

    def get_recommendations(self) -> List[Dict]:
        """
        Get health-based recommendations.

        Returns:
            List of recommendation dicts
        """
        recommendations = []
        results = self.check_all_workers()

        # Offline workers
        if results["offline"]:
            recommendations.append({
                "type": "restart",
                "priority": "critical",
                "workers": results["offline"],
                "action": "Restart offline workers",
            })

        # Unhealthy workers
        if results["unhealthy"]:
            recommendations.append({
                "type": "investigate",
                "priority": "high",
                "workers": results["unhealthy"],
                "action": "Investigate unhealthy workers - multiple failures",
            })

        # Degraded workers
        if results["degraded"]:
            recommendations.append({
                "type": "monitor",
                "priority": "medium",
                "workers": results["degraded"],
                "action": "Monitor degraded workers - slow response",
            })

        # Army-level recommendations
        for army_type in ArmyType:
            army_health = self.get_army_health(army_type)
            if army_health["health_score"] < 0.5:
                recommendations.append({
                    "type": "scale",
                    "priority": "high",
                    "army": army_type.value,
                    "action": f"Scale up {army_type.value} army - low health score",
                })

        return recommendations

    def get_stats(self) -> Dict:
        """Get health monitor statistics."""
        results = self.check_all_workers()
        return {
            "monitored_workers": len(self._health),
            "healthy": len(results["healthy"]),
            "degraded": len(results["degraded"]),
            "unhealthy": len(results["unhealthy"]),
            "offline": len(results["offline"]),
            "recommendations": len(self.get_recommendations()),
        }

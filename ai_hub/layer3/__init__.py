"""Layer 3 - Worker Army Orchestrator.

Dispatches tasks to specialized bot armies with load balancing and health monitoring.
"""
from .registry.worker_registry import (
    WorkerRegistry, WorkerArmy, WorkerCapability, WorkerInfo, WorkerStatus, ArmyType
)
from .dispatch.dispatcher_agent import DispatcherAgent, DispatchTask, DispatchResult
from .health.health_monitor import HealthMonitorAgent, HealthEvent, WorkerHealth

__all__ = [
    "WorkerRegistry",
    "WorkerArmy",
    "WorkerCapability",
    "WorkerInfo",
    "WorkerStatus",
    "ArmyType",
    "DispatcherAgent",
    "DispatchTask",
    "DispatchResult",
    "HealthMonitorAgent",
    "HealthEvent",
    "WorkerHealth",
]

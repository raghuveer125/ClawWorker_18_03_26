"""
Agent Coordinator - Coordinates agents across all layers.

Handles:
- Agent discovery and registration
- Capability matching
- Load-aware routing
- Health monitoring
- Failover handling
"""

import time
import logging
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import threading

logger = logging.getLogger(__name__)


@dataclass
class AgentInfo:
    """Information about a registered agent."""
    agent_id: str
    agent_type: str
    layer: int
    capabilities: List[str]
    handler: Optional[Callable] = None
    status: str = "active"  # active, busy, idle, unhealthy
    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    current_load: int = 0  # Active tasks
    max_load: int = 10
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_available(self) -> bool:
        return self.status == "active" and self.current_load < self.max_load

    def health_score(self) -> float:
        """Calculate health score (0-1)."""
        # Check heartbeat freshness
        time_since_heartbeat = time.time() - self.last_heartbeat
        if time_since_heartbeat > 60:
            return 0.0
        heartbeat_score = max(0, 1 - time_since_heartbeat / 60)

        # Check load
        load_score = 1 - (self.current_load / self.max_load)

        return (heartbeat_score * 0.5) + (load_score * 0.5)


class AgentCoordinator:
    """
    Coordinates agents across the AI Engineering Hub.

    Features:
    - Agent registration and discovery
    - Capability-based routing
    - Load balancing
    - Health monitoring
    - Automatic failover
    """

    HEARTBEAT_TIMEOUT = 60  # seconds

    def __init__(self, synapse=None):
        """
        Initialize coordinator.

        Args:
            synapse: Synapse instance (optional, will use singleton)
        """
        self._synapse = synapse
        self._agents: Dict[str, AgentInfo] = {}
        self._by_type: Dict[str, List[str]] = defaultdict(list)
        self._by_layer: Dict[int, List[str]] = defaultdict(list)
        self._by_capability: Dict[str, List[str]] = defaultdict(list)
        self._lock = threading.Lock()

    def _get_synapse(self):
        """Lazy load synapse."""
        if self._synapse is None:
            from ..synapse_core import get_synapse
            self._synapse = get_synapse()
        return self._synapse

    def register(
        self,
        agent_id: str,
        agent_type: str,
        layer: int,
        capabilities: List[str],
        handler: Optional[Callable] = None,
        max_load: int = 10,
        metadata: Optional[Dict] = None,
    ) -> bool:
        """
        Register an agent.

        Args:
            agent_id: Unique agent identifier
            agent_type: Type of agent
            layer: Layer number (0-6)
            capabilities: List of capabilities
            handler: Message handler function
            max_load: Maximum concurrent tasks
            metadata: Additional metadata

        Returns:
            True if registered successfully
        """
        with self._lock:
            if agent_id in self._agents:
                logger.warning(f"Agent {agent_id} already registered")
                return False

            agent = AgentInfo(
                agent_id=agent_id,
                agent_type=agent_type,
                layer=layer,
                capabilities=capabilities,
                handler=handler,
                max_load=max_load,
                metadata=metadata or {},
            )

            self._agents[agent_id] = agent
            self._by_type[agent_type].append(agent_id)
            self._by_layer[layer].append(agent_id)
            for cap in capabilities:
                self._by_capability[cap].append(agent_id)

        # Register with Synapse
        synapse = self._get_synapse()
        synapse.register_agent(agent_id, agent_type, capabilities, layer, handler)

        logger.info(f"Agent registered: {agent_id} (type={agent_type}, layer={layer})")
        return True

    def unregister(self, agent_id: str):
        """Unregister an agent."""
        with self._lock:
            agent = self._agents.pop(agent_id, None)
            if agent:
                if agent_id in self._by_type[agent.agent_type]:
                    self._by_type[agent.agent_type].remove(agent_id)
                if agent_id in self._by_layer[agent.layer]:
                    self._by_layer[agent.layer].remove(agent_id)
                for cap in agent.capabilities:
                    if agent_id in self._by_capability[cap]:
                        self._by_capability[cap].remove(agent_id)

        # Unregister from Synapse
        synapse = self._get_synapse()
        synapse.unregister_agent(agent_id)

        logger.info(f"Agent unregistered: {agent_id}")

    def heartbeat(self, agent_id: str, load: Optional[int] = None):
        """Update agent heartbeat."""
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent:
                agent.last_heartbeat = time.time()
                if load is not None:
                    agent.current_load = load
                if agent.status == "unhealthy":
                    agent.status = "active"

        # Forward to Synapse
        synapse = self._get_synapse()
        synapse.heartbeat(agent_id)

    def set_status(self, agent_id: str, status: str):
        """Set agent status."""
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent:
                agent.status = status

    def find_by_type(self, agent_type: str, available_only: bool = True) -> List[AgentInfo]:
        """Find agents by type."""
        with self._lock:
            agent_ids = self._by_type.get(agent_type, [])
            agents = [self._agents[aid] for aid in agent_ids if aid in self._agents]
            if available_only:
                agents = [a for a in agents if a.is_available()]
            return agents

    def find_by_capability(self, capability: str, available_only: bool = True) -> List[AgentInfo]:
        """Find agents by capability."""
        with self._lock:
            agent_ids = self._by_capability.get(capability, [])
            agents = [self._agents[aid] for aid in agent_ids if aid in self._agents]
            if available_only:
                agents = [a for a in agents if a.is_available()]
            return agents

    def find_by_layer(self, layer: int, available_only: bool = True) -> List[AgentInfo]:
        """Find agents by layer."""
        with self._lock:
            agent_ids = self._by_layer.get(layer, [])
            agents = [self._agents[aid] for aid in agent_ids if aid in self._agents]
            if available_only:
                agents = [a for a in agents if a.is_available()]
            return agents

    def select_best(
        self,
        agent_type: Optional[str] = None,
        capability: Optional[str] = None,
        layer: Optional[int] = None,
    ) -> Optional[AgentInfo]:
        """
        Select best available agent based on criteria.

        Uses health score for selection.
        """
        candidates = []

        with self._lock:
            for agent in self._agents.values():
                if not agent.is_available():
                    continue
                if agent_type and agent.agent_type != agent_type:
                    continue
                if capability and capability not in agent.capabilities:
                    continue
                if layer is not None and agent.layer != layer:
                    continue
                candidates.append(agent)

        if not candidates:
            return None

        # Select by highest health score
        return max(candidates, key=lambda a: a.health_score())

    def route_task(
        self,
        task_type: str,
        required_capabilities: List[str],
    ) -> Optional[str]:
        """
        Route task to best available agent.

        Returns:
            agent_id of selected agent, or None
        """
        candidates = []

        with self._lock:
            for agent in self._agents.values():
                if not agent.is_available():
                    continue
                # Check has all required capabilities
                if all(cap in agent.capabilities for cap in required_capabilities):
                    candidates.append(agent)

        if not candidates:
            logger.warning(f"No agent available for task {task_type}")
            return None

        # Select best
        best = max(candidates, key=lambda a: a.health_score())

        # Update load
        with self._lock:
            best.current_load += 1

        return best.agent_id

    def complete_task(self, agent_id: str):
        """Mark task completion (reduces load)."""
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent and agent.current_load > 0:
                agent.current_load -= 1

    def check_health(self) -> List[str]:
        """Check health of all agents, return unhealthy IDs."""
        now = time.time()
        unhealthy = []

        with self._lock:
            for agent_id, agent in self._agents.items():
                if now - agent.last_heartbeat > self.HEARTBEAT_TIMEOUT:
                    agent.status = "unhealthy"
                    unhealthy.append(agent_id)

        if unhealthy:
            logger.warning(f"Unhealthy agents: {unhealthy}")

        return unhealthy

    def get_stats(self) -> Dict:
        """Get coordinator statistics."""
        with self._lock:
            active = sum(1 for a in self._agents.values() if a.status == "active")
            busy = sum(1 for a in self._agents.values() if a.status == "busy")
            unhealthy = sum(1 for a in self._agents.values() if a.status == "unhealthy")
            total_load = sum(a.current_load for a in self._agents.values())

            return {
                "total_agents": len(self._agents),
                "active": active,
                "busy": busy,
                "unhealthy": unhealthy,
                "total_load": total_load,
                "by_layer": {
                    layer: len(ids) for layer, ids in self._by_layer.items()
                },
                "by_type": {
                    t: len(ids) for t, ids in self._by_type.items()
                },
            }

    def list_agents(self) -> List[Dict]:
        """List all agents with their info."""
        with self._lock:
            return [
                {
                    "agent_id": a.agent_id,
                    "agent_type": a.agent_type,
                    "layer": a.layer,
                    "status": a.status,
                    "capabilities": a.capabilities,
                    "load": f"{a.current_load}/{a.max_load}",
                    "health": f"{a.health_score():.2f}",
                }
                for a in self._agents.values()
            ]

"""
Synapse Core - Central Neural Message Bus.

The brain of the AI Engineering Hub communication system.
Routes messages between agents, manages channels, and coordinates state.
"""

import asyncio
import time
import logging
import threading
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field
from collections import defaultdict
from queue import PriorityQueue
import uuid

logger = logging.getLogger(__name__)


class MessagePriority(Enum):
    """Message priority levels."""
    CRITICAL = 0  # Risk alerts, stop losses
    HIGH = 1      # Trade executions
    NORMAL = 2    # Regular data flow
    LOW = 3       # Background tasks
    BACKGROUND = 4  # Learning, optimization


@dataclass(order=True)
class SynapseMessage:
    """A message flowing through Synapse."""
    priority: int = field(compare=True)
    message_id: str = field(compare=False)
    channel: str = field(compare=False)
    source: str = field(compare=False)  # Sender agent/layer
    target: Optional[str] = field(compare=False, default=None)  # None = broadcast
    message_type: str = field(compare=False, default="event")
    payload: Dict[str, Any] = field(compare=False, default_factory=dict)
    timestamp: float = field(compare=False, default_factory=time.time)
    correlation_id: Optional[str] = field(compare=False, default=None)  # For request-response
    ttl: float = field(compare=False, default=60.0)  # Time-to-live seconds
    metadata: Dict[str, Any] = field(compare=False, default_factory=dict)

    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl

    def to_dict(self) -> Dict:
        return {
            "message_id": self.message_id,
            "channel": self.channel,
            "source": self.source,
            "target": self.target,
            "message_type": self.message_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "priority": self.priority,
        }


class Synapse:
    """
    Neural Message Bus - Central communication layer.

    Features:
    - Priority-based message queue
    - Named channels for different concerns
    - Topic-based publish/subscribe
    - Request-response patterns
    - Agent discovery and routing
    - Context sharing across layers
    """

    # Standard channels
    CHANNEL_DATA = "data"       # Market data, quotes
    CHANNEL_COMMAND = "cmd"     # Commands, task dispatches
    CHANNEL_STATUS = "status"   # Health, progress updates
    CHANNEL_MEMORY = "memory"   # Memory queries/updates
    CHANNEL_ALERT = "alert"     # Risk alerts, notifications
    CHANNEL_LEARN = "learn"     # Learning signals, feedback

    def __init__(self, max_queue_size: int = 10000):
        """Initialize Synapse message bus."""
        # Priority message queue
        self._queue: PriorityQueue = PriorityQueue(maxsize=max_queue_size)

        # Subscribers by channel
        self._channel_subscribers: Dict[str, List[Callable]] = defaultdict(list)

        # Subscribers by message type
        self._type_subscribers: Dict[str, List[Callable]] = defaultdict(list)

        # Registered agents
        self._agents: Dict[str, Dict] = {}

        # Pending request-response
        self._pending_responses: Dict[str, asyncio.Future] = {}

        # Context store
        self._context: Dict[str, Any] = {}

        # Statistics
        self._stats = {
            "messages_sent": 0,
            "messages_delivered": 0,
            "messages_expired": 0,
            "by_channel": defaultdict(int),
            "by_priority": defaultdict(int),
        }

        # Control
        self._running = False
        self._lock = threading.Lock()
        self._process_task: Optional[asyncio.Task] = None

        logger.info("Synapse neural message bus initialized")

    def start(self):
        """Start message processing."""
        self._running = True
        logger.info("Synapse started")

    def stop(self):
        """Stop message processing."""
        self._running = False
        if self._process_task:
            self._process_task.cancel()
        logger.info("Synapse stopped")

    def register_agent(
        self,
        agent_id: str,
        agent_type: str,
        capabilities: List[str],
        layer: int,
        handler: Optional[Callable] = None,
    ):
        """
        Register an agent with Synapse.

        Args:
            agent_id: Unique agent identifier
            agent_type: Type of agent (e.g., "context", "dispatcher")
            capabilities: List of capabilities
            layer: Layer number (0-6)
            handler: Optional message handler
        """
        with self._lock:
            self._agents[agent_id] = {
                "agent_id": agent_id,
                "agent_type": agent_type,
                "capabilities": capabilities,
                "layer": layer,
                "handler": handler,
                "registered_at": time.time(),
                "last_seen": time.time(),
                "status": "active",
            }

        logger.info(f"Agent registered: {agent_id} (layer {layer})")

    def unregister_agent(self, agent_id: str):
        """Unregister an agent."""
        with self._lock:
            self._agents.pop(agent_id, None)
        logger.info(f"Agent unregistered: {agent_id}")

    def subscribe(
        self,
        callback: Callable[[SynapseMessage], None],
        channels: Optional[List[str]] = None,
        message_types: Optional[List[str]] = None,
    ):
        """
        Subscribe to messages.

        Args:
            callback: Function to call with messages
            channels: Channels to subscribe to (None = all)
            message_types: Message types to filter (None = all)
        """
        with self._lock:
            if channels:
                for channel in channels:
                    self._channel_subscribers[channel].append(callback)
            if message_types:
                for msg_type in message_types:
                    self._type_subscribers[msg_type].append(callback)
            if not channels and not message_types:
                # Subscribe to default channel
                self._channel_subscribers["*"].append(callback)

    def unsubscribe(self, callback: Callable):
        """Unsubscribe from messages."""
        with self._lock:
            for channel_subs in self._channel_subscribers.values():
                if callback in channel_subs:
                    channel_subs.remove(callback)
            for type_subs in self._type_subscribers.values():
                if callback in type_subs:
                    type_subs.remove(callback)

    def send(
        self,
        channel: str,
        source: str,
        payload: Dict[str, Any],
        target: Optional[str] = None,
        message_type: str = "event",
        priority: MessagePriority = MessagePriority.NORMAL,
        correlation_id: Optional[str] = None,
        ttl: float = 60.0,
    ) -> str:
        """
        Send a message through Synapse.

        Args:
            channel: Channel to send on
            source: Sending agent/component
            payload: Message data
            target: Target agent (None = broadcast)
            message_type: Type of message
            priority: Message priority
            correlation_id: For request-response pairing
            ttl: Time-to-live in seconds

        Returns:
            Message ID
        """
        message_id = str(uuid.uuid4())[:8]

        message = SynapseMessage(
            priority=priority.value,
            message_id=message_id,
            channel=channel,
            source=source,
            target=target,
            message_type=message_type,
            payload=payload,
            correlation_id=correlation_id,
            ttl=ttl,
        )

        try:
            self._queue.put_nowait(message)
            self._stats["messages_sent"] += 1
            self._stats["by_channel"][channel] += 1
            self._stats["by_priority"][priority.name] += 1
        except Exception as e:
            logger.error(f"Failed to queue message: {e}")
            return ""

        # Immediate delivery for high priority
        if priority.value <= MessagePriority.HIGH.value:
            self._deliver_message(message)

        return message_id

    def send_data(
        self,
        source: str,
        payload: Dict[str, Any],
        target: Optional[str] = None,
    ) -> str:
        """Convenience: Send data channel message."""
        return self.send(
            self.CHANNEL_DATA, source, payload, target,
            message_type="data", priority=MessagePriority.NORMAL
        )

    def send_command(
        self,
        source: str,
        command: str,
        params: Dict[str, Any],
        target: str,
    ) -> str:
        """Convenience: Send command to specific agent."""
        return self.send(
            self.CHANNEL_COMMAND, source,
            {"command": command, "params": params},
            target, message_type="command",
            priority=MessagePriority.HIGH
        )

    def send_alert(
        self,
        source: str,
        alert_type: str,
        message: str,
        severity: str = "warning",
    ) -> str:
        """Convenience: Send alert message."""
        return self.send(
            self.CHANNEL_ALERT, source,
            {"alert_type": alert_type, "message": message, "severity": severity},
            message_type="alert",
            priority=MessagePriority.CRITICAL if severity == "critical" else MessagePriority.HIGH
        )

    async def request(
        self,
        channel: str,
        source: str,
        payload: Dict[str, Any],
        target: str,
        timeout: float = 30.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Send request and wait for response.

        Args:
            channel: Channel to send on
            source: Requesting agent
            payload: Request data
            target: Target agent
            timeout: Response timeout

        Returns:
            Response payload or None on timeout
        """
        correlation_id = str(uuid.uuid4())[:8]

        # Create future for response
        future: asyncio.Future = asyncio.Future()
        self._pending_responses[correlation_id] = future

        # Send request
        self.send(
            channel, source, payload, target,
            message_type="request",
            priority=MessagePriority.HIGH,
            correlation_id=correlation_id,
        )

        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Request timeout: {correlation_id}")
            return None
        finally:
            self._pending_responses.pop(correlation_id, None)

    def respond(
        self,
        original_message: SynapseMessage,
        payload: Dict[str, Any],
    ):
        """Respond to a request message."""
        if original_message.correlation_id:
            self.send(
                original_message.channel,
                original_message.target or "synapse",
                payload,
                original_message.source,
                message_type="response",
                priority=MessagePriority.HIGH,
                correlation_id=original_message.correlation_id,
            )

    def _deliver_message(self, message: SynapseMessage):
        """Deliver message to subscribers."""
        if message.is_expired():
            self._stats["messages_expired"] += 1
            return

        delivered = False

        # Check for response to pending request
        if message.message_type == "response" and message.correlation_id:
            future = self._pending_responses.get(message.correlation_id)
            if future and not future.done():
                future.set_result(message.payload)
                delivered = True

        # Deliver to target agent if specified
        if message.target:
            agent = self._agents.get(message.target)
            if agent and agent.get("handler"):
                try:
                    agent["handler"](message)
                    delivered = True
                except Exception as e:
                    logger.error(f"Agent handler error: {e}")

        # Deliver to channel subscribers
        for callback in self._channel_subscribers.get(message.channel, []):
            try:
                callback(message)
                delivered = True
            except Exception as e:
                logger.error(f"Channel subscriber error: {e}")

        # Deliver to wildcard subscribers
        for callback in self._channel_subscribers.get("*", []):
            try:
                callback(message)
                delivered = True
            except Exception as e:
                logger.error(f"Wildcard subscriber error: {e}")

        # Deliver to type subscribers
        for callback in self._type_subscribers.get(message.message_type, []):
            try:
                callback(message)
                delivered = True
            except Exception as e:
                logger.error(f"Type subscriber error: {e}")

        if delivered:
            self._stats["messages_delivered"] += 1

    async def process_queue(self):
        """Process message queue (run in background)."""
        while self._running:
            try:
                if not self._queue.empty():
                    message = self._queue.get_nowait()
                    self._deliver_message(message)
                else:
                    await asyncio.sleep(0.001)  # 1ms sleep when idle
            except Exception as e:
                logger.error(f"Queue processing error: {e}")
                await asyncio.sleep(0.1)

    # Context management
    def set_context(self, key: str, value: Any, ttl: Optional[float] = None):
        """Set shared context value."""
        with self._lock:
            self._context[key] = {
                "value": value,
                "set_at": time.time(),
                "ttl": ttl,
            }

    def get_context(self, key: str) -> Optional[Any]:
        """Get shared context value."""
        with self._lock:
            ctx = self._context.get(key)
            if ctx:
                if ctx["ttl"] and time.time() - ctx["set_at"] > ctx["ttl"]:
                    del self._context[key]
                    return None
                return ctx["value"]
        return None

    def clear_context(self, key: Optional[str] = None):
        """Clear context."""
        with self._lock:
            if key:
                self._context.pop(key, None)
            else:
                self._context.clear()

    # Agent discovery
    def find_agents(
        self,
        agent_type: Optional[str] = None,
        capability: Optional[str] = None,
        layer: Optional[int] = None,
    ) -> List[Dict]:
        """Find agents matching criteria."""
        results = []
        with self._lock:
            for agent in self._agents.values():
                if agent_type and agent["agent_type"] != agent_type:
                    continue
                if capability and capability not in agent["capabilities"]:
                    continue
                if layer is not None and agent["layer"] != layer:
                    continue
                results.append(dict(agent))
        return results

    def get_agent(self, agent_id: str) -> Optional[Dict]:
        """Get agent info."""
        with self._lock:
            return dict(self._agents.get(agent_id, {})) or None

    def heartbeat(self, agent_id: str):
        """Update agent last seen time."""
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id]["last_seen"] = time.time()

    def get_stats(self) -> Dict:
        """Get Synapse statistics."""
        with self._lock:
            return {
                "messages_sent": self._stats["messages_sent"],
                "messages_delivered": self._stats["messages_delivered"],
                "messages_expired": self._stats["messages_expired"],
                "queue_size": self._queue.qsize(),
                "registered_agents": len(self._agents),
                "by_channel": dict(self._stats["by_channel"]),
                "by_priority": dict(self._stats["by_priority"]),
                "context_keys": list(self._context.keys()),
            }


# Global singleton
_synapse_instance: Optional[Synapse] = None


def get_synapse() -> Synapse:
    """Get the global Synapse instance."""
    global _synapse_instance
    if _synapse_instance is None:
        _synapse_instance = Synapse()
        _synapse_instance.start()
    return _synapse_instance

"""
Event Bus - Async event-driven communication between bots.
Supports Redis for distributed deployments or in-memory for local.
"""

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """An event in the system."""
    event_type: str
    data: Dict[str, Any]
    source: str  # Bot ID or system component
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    event_id: str = field(default_factory=lambda: f"evt_{datetime.now().strftime('%Y%m%d%H%M%S%f')}")

    def to_dict(self) -> Dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "data": self.data,
            "source": self.source,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Event":
        return cls(
            event_id=d.get("event_id", ""),
            event_type=d["event_type"],
            data=d["data"],
            source=d["source"],
            timestamp=d.get("timestamp", datetime.now().isoformat()),
        )


class EventBus(ABC):
    """Abstract event bus interface."""

    @abstractmethod
    async def emit(self, event_type: str, data: Dict[str, Any], source: str = "system"):
        """Emit an event."""
        pass

    @abstractmethod
    async def subscribe(self, event_type: str, callback: Callable[[Event], Any]):
        """Subscribe to an event type."""
        pass

    @abstractmethod
    async def unsubscribe(self, event_type: str, callback: Callable):
        """Unsubscribe from an event type."""
        pass

    @abstractmethod
    async def start(self):
        """Start the event bus."""
        pass

    @abstractmethod
    async def stop(self):
        """Stop the event bus."""
        pass


class InMemoryEventBus(EventBus):
    """
    In-memory event bus for local/single-process deployments.
    Good for development and testing.
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._event_history: List[Event] = []
        self._max_history = 1000
        self._running = False
        self._queue: asyncio.Queue = asyncio.Queue()
        self._processor_task: Optional[asyncio.Task] = None

    async def emit(self, event_type: str, data: Dict[str, Any], source: str = "system"):
        """Emit an event to all subscribers."""
        event = Event(event_type=event_type, data=data, source=source)

        # Store in history
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history:]

        # Queue for processing
        await self._queue.put(event)

        logger.debug(f"Event emitted: {event_type} from {source}")

    async def subscribe(self, event_type: str, callback: Callable[[Event], Any]):
        """Subscribe to an event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)
        logger.debug(f"Subscribed to {event_type}")

    async def unsubscribe(self, event_type: str, callback: Callable):
        """Unsubscribe from an event type."""
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                cb for cb in self._subscribers[event_type] if cb != callback
            ]

    async def start(self):
        """Start processing events."""
        self._running = True
        self._processor_task = asyncio.create_task(self._process_events())
        logger.info("Event bus started")

    async def stop(self):
        """Stop processing events."""
        self._running = False
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        logger.info("Event bus stopped")

    async def _process_events(self):
        """Process events from the queue."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)

                # Notify all subscribers
                callbacks = self._subscribers.get(event.event_type, [])
                callbacks += self._subscribers.get("*", [])  # Wildcard subscribers

                for callback in callbacks:
                    try:
                        result = callback(event)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(f"Error in event callback: {e}")

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_history(self, event_type: Optional[str] = None, limit: int = 100) -> List[Event]:
        """Get event history."""
        events = self._event_history
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events[-limit:]


class RedisEventBus(EventBus):
    """
    Redis-based event bus for distributed deployments.
    Supports pub/sub across multiple processes/machines.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self._redis = None
        self._pubsub = None
        self._subscribers: Dict[str, List[Callable]] = {}
        self._running = False
        self._listener_task: Optional[asyncio.Task] = None

    async def start(self):
        """Connect to Redis and start listening."""
        try:
            import aioredis
            self._redis = await aioredis.from_url(self.redis_url)
            self._pubsub = self._redis.pubsub()
            self._running = True
            self._listener_task = asyncio.create_task(self._listen())
            logger.info(f"Redis event bus connected to {self.redis_url}")
        except ImportError:
            logger.warning("aioredis not installed, falling back to in-memory")
            raise

    async def stop(self):
        """Disconnect from Redis."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
        if self._pubsub:
            await self._pubsub.close()
        if self._redis:
            await self._redis.close()
        logger.info("Redis event bus disconnected")

    async def emit(self, event_type: str, data: Dict[str, Any], source: str = "system"):
        """Publish event to Redis channel."""
        event = Event(event_type=event_type, data=data, source=source)
        channel = f"bot_army:{event_type}"
        await self._redis.publish(channel, json.dumps(event.to_dict()))

        # Also publish to wildcard channel
        await self._redis.publish("bot_army:*", json.dumps(event.to_dict()))

        logger.debug(f"Event published to Redis: {event_type}")

    async def subscribe(self, event_type: str, callback: Callable[[Event], Any]):
        """Subscribe to a Redis channel."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
            channel = f"bot_army:{event_type}"
            await self._pubsub.subscribe(channel)
        self._subscribers[event_type].append(callback)

    async def unsubscribe(self, event_type: str, callback: Callable):
        """Unsubscribe from a Redis channel."""
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                cb for cb in self._subscribers[event_type] if cb != callback
            ]
            if not self._subscribers[event_type]:
                channel = f"bot_army:{event_type}"
                await self._pubsub.unsubscribe(channel)

    async def _listen(self):
        """Listen for messages on subscribed channels."""
        while self._running:
            try:
                message = await self._pubsub.get_message(ignore_subscribe_messages=True)
                if message:
                    data = json.loads(message["data"])
                    event = Event.from_dict(data)

                    callbacks = self._subscribers.get(event.event_type, [])
                    for callback in callbacks:
                        try:
                            result = callback(event)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.error(f"Error in Redis event callback: {e}")

                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Redis listener error: {e}")
                await asyncio.sleep(1)


def create_event_bus(backend: str = "memory", **kwargs) -> EventBus:
    """Factory function to create an event bus."""
    if backend == "redis":
        return RedisEventBus(**kwargs)
    return InMemoryEventBus()


# Predefined event types for consistency
class EventTypes:
    # Bot lifecycle
    BOT_STARTED = "bot_started"
    BOT_COMPLETED = "bot_completed"
    BOT_FAILED = "bot_failed"

    # Pipeline events
    PIPELINE_STARTED = "pipeline_started"
    PIPELINE_COMPLETED = "pipeline_completed"
    PIPELINE_FAILED = "pipeline_failed"

    # Code events
    CODE_CHANGED = "code_changed"
    TESTS_PASSED = "tests_passed"
    TESTS_FAILED = "tests_failed"
    LINT_ERROR = "lint_error"

    # Trading events
    SIGNAL_GENERATED = "signal_generated"
    BACKTEST_COMPLETE = "backtest_complete"
    REGIME_CHANGED = "regime_changed"
    RISK_BREACH = "risk_breach"
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"

    # Knowledge events
    STRATEGY_LEARNED = "strategy_learned"
    PATTERN_DETECTED = "pattern_detected"

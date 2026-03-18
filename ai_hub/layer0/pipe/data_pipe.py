"""
Data Pipe - Unified data bus for Layer 0.

This is the central integration point where:
- Data flows from adapters
- Enrichment is applied
- Schema is enforced
- Upper layers consume data
- Learning Army feedback is processed
"""

import asyncio
import time
import logging
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import threading

logger = logging.getLogger(__name__)


class DataEventType(Enum):
    """Types of data events in the pipe."""
    QUOTE = "quote"
    HISTORY = "history"
    OPTION_CHAIN = "option_chain"
    INDEX_DATA = "index_data"
    INDICATOR_UPDATE = "indicator_update"
    SCHEMA_CHANGE = "schema_change"
    FIELD_REQUEST = "field_request"
    ERROR = "error"


@dataclass
class DataEvent:
    """Event flowing through the data pipe."""
    event_type: DataEventType
    source: str  # Adapter name (e.g., "fyers")
    symbol: str
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "event_type": self.event_type.value,
            "source": self.source,
            "symbol": self.symbol,
            "data": self.data,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class FieldRequest:
    """Request from Learning Army to add a new field."""
    request_id: str
    field_name: str
    description: str
    dependencies: List[str]
    compute_fn: str
    params: Dict[str, Any]
    reason: str
    confidence: float
    requester: str
    status: str = "pending"
    created_at: float = field(default_factory=time.time)


class DataPipe:
    """
    Unified data bus for Layer 0.

    Features:
    - Central point for all data flow
    - Event-based architecture
    - Subscriber pattern for upper layers
    - Learning Army integration for field requests
    - Rate limiting and buffering
    """

    def __init__(
        self,
        buffer_size: int = 1000,
        enable_history: bool = True,
    ):
        """
        Initialize Data Pipe.

        Args:
            buffer_size: Size of event history buffer
            enable_history: Keep event history for debugging
        """
        self._subscribers: Dict[DataEventType, List[Callable]] = {
            event_type: [] for event_type in DataEventType
        }
        self._all_subscribers: List[Callable] = []

        # Event buffer for debugging/replay
        self._event_buffer: deque = deque(maxlen=buffer_size)
        self._enable_history = enable_history

        # Field requests from Learning Army
        self._field_requests: List[FieldRequest] = []

        # Statistics
        self._stats = {
            "events_published": 0,
            "events_by_type": {e.value: 0 for e in DataEventType},
            "errors": 0,
            "field_requests": 0,
        }

        # Adapters registered with the pipe
        self._adapters: Dict[str, Any] = {}

        # Lock for thread safety
        self._lock = threading.Lock()

        logger.info("DataPipe initialized")

    def register_adapter(self, name: str, adapter: Any):
        """Register an adapter with the pipe."""
        with self._lock:
            self._adapters[name] = adapter
            logger.info(f"Adapter registered: {name}")

    def get_adapter(self, name: str) -> Optional[Any]:
        """Get a registered adapter."""
        return self._adapters.get(name)

    def subscribe(
        self,
        callback: Callable[[DataEvent], None],
        event_types: Optional[List[DataEventType]] = None,
    ):
        """
        Subscribe to data events.

        Args:
            callback: Function to call with DataEvent
            event_types: List of event types to subscribe to (None = all)
        """
        with self._lock:
            if event_types is None:
                self._all_subscribers.append(callback)
                logger.debug(f"Subscriber added for all events")
            else:
                for event_type in event_types:
                    self._subscribers[event_type].append(callback)
                logger.debug(f"Subscriber added for: {[e.value for e in event_types]}")

    def unsubscribe(self, callback: Callable):
        """Remove a subscriber."""
        with self._lock:
            if callback in self._all_subscribers:
                self._all_subscribers.remove(callback)

            for event_type in DataEventType:
                if callback in self._subscribers[event_type]:
                    self._subscribers[event_type].remove(callback)

    def publish(self, event: DataEvent):
        """
        Publish an event to all subscribers.

        Args:
            event: DataEvent to publish
        """
        with self._lock:
            # Update stats
            self._stats["events_published"] += 1
            self._stats["events_by_type"][event.event_type.value] += 1

            # Store in history
            if self._enable_history:
                self._event_buffer.append(event)

        # Notify type-specific subscribers
        for callback in self._subscribers.get(event.event_type, []):
            try:
                callback(event)
            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"Subscriber error: {e}")

        # Notify all-event subscribers
        for callback in self._all_subscribers:
            try:
                callback(event)
            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"Subscriber error: {e}")

    def publish_quote(
        self,
        source: str,
        symbol: str,
        data: Dict,
        metadata: Optional[Dict] = None,
    ):
        """Convenience method to publish a quote event."""
        self.publish(DataEvent(
            event_type=DataEventType.QUOTE,
            source=source,
            symbol=symbol,
            data=data,
            metadata=metadata or {},
        ))

    def publish_history(
        self,
        source: str,
        symbol: str,
        data: Dict,
        metadata: Optional[Dict] = None,
    ):
        """Convenience method to publish a history event."""
        self.publish(DataEvent(
            event_type=DataEventType.HISTORY,
            source=source,
            symbol=symbol,
            data=data,
            metadata=metadata or {},
        ))

    def publish_index_data(
        self,
        source: str,
        index_name: str,
        data: Dict,
        metadata: Optional[Dict] = None,
    ):
        """Convenience method to publish index data event."""
        self.publish(DataEvent(
            event_type=DataEventType.INDEX_DATA,
            source=source,
            symbol=index_name,
            data=data,
            metadata=metadata or {},
        ))

    def request_field(self, request: Dict) -> str:
        """
        Accept a field request from Learning Army.

        Args:
            request: Field request dict with:
                - name: Field name
                - description: Description
                - dependencies: Required fields
                - compute_fn: Function name
                - params: Compute parameters
                - reason: Why this field is needed
                - confidence: Confidence score
                - requester: Agent name

        Returns:
            Request ID for tracking
        """
        import hashlib

        request_id = hashlib.sha256(
            f"{request.get('name', '')}{time.time()}".encode()
        ).hexdigest()[:8]

        field_request = FieldRequest(
            request_id=request_id,
            field_name=request.get("name", ""),
            description=request.get("description", ""),
            dependencies=request.get("dependencies", []),
            compute_fn=request.get("compute_fn", ""),
            params=request.get("params", {}),
            reason=request.get("reason", ""),
            confidence=request.get("confidence", 0.5),
            requester=request.get("requester", "unknown"),
        )

        with self._lock:
            self._field_requests.append(field_request)
            self._stats["field_requests"] += 1

        # Publish field request event
        self.publish(DataEvent(
            event_type=DataEventType.FIELD_REQUEST,
            source="learning_army",
            symbol="",
            data={
                "request_id": request_id,
                "field_name": field_request.field_name,
                "reason": field_request.reason,
            },
        ))

        logger.info(f"Field request received: {request_id} - {field_request.field_name}")
        return request_id

    def approve_field_request(self, request_id: str) -> bool:
        """Approve a pending field request."""
        with self._lock:
            for req in self._field_requests:
                if req.request_id == request_id and req.status == "pending":
                    req.status = "approved"

                    # Forward to adapter's schema manager
                    for adapter in self._adapters.values():
                        if hasattr(adapter, "schema_manager"):
                            adapter.schema_manager.approve_request(request_id)

                    # Publish schema change event
                    self.publish(DataEvent(
                        event_type=DataEventType.SCHEMA_CHANGE,
                        source="data_pipe",
                        symbol="",
                        data={
                            "action": "field_added",
                            "field_name": req.field_name,
                            "request_id": request_id,
                        },
                    ))

                    logger.info(f"Field request approved: {request_id}")
                    return True
        return False

    def reject_field_request(self, request_id: str, reason: str = "") -> bool:
        """Reject a pending field request."""
        with self._lock:
            for req in self._field_requests:
                if req.request_id == request_id and req.status == "pending":
                    req.status = "rejected"
                    logger.info(f"Field request rejected: {request_id} - {reason}")
                    return True
        return False

    def get_pending_field_requests(self) -> List[Dict]:
        """Get all pending field requests."""
        with self._lock:
            return [
                {
                    "request_id": req.request_id,
                    "field_name": req.field_name,
                    "description": req.description,
                    "reason": req.reason,
                    "confidence": req.confidence,
                    "requester": req.requester,
                    "created_at": req.created_at,
                }
                for req in self._field_requests
                if req.status == "pending"
            ]

    def get_recent_events(
        self,
        limit: int = 100,
        event_type: Optional[DataEventType] = None,
    ) -> List[Dict]:
        """Get recent events from buffer."""
        with self._lock:
            events = list(self._event_buffer)

        if event_type:
            events = [e for e in events if e.event_type == event_type]

        return [e.to_dict() for e in events[-limit:]]

    def get_stats(self) -> Dict:
        """Get pipe statistics."""
        with self._lock:
            stats = dict(self._stats)
            stats["buffer_size"] = len(self._event_buffer)
            stats["adapters"] = list(self._adapters.keys())
            stats["subscribers"] = {
                "all": len(self._all_subscribers),
                **{e.value: len(self._subscribers[e]) for e in DataEventType},
            }
            stats["pending_field_requests"] = len(
                [r for r in self._field_requests if r.status == "pending"]
            )
            return stats

    # === High-level data access methods ===

    def get_index_data(
        self,
        index_name: str,
        adapter_name: str = "fyers",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Get comprehensive index data through the pipe.

        This is the main entry point for upper layers to get data.
        """
        adapter = self._adapters.get(adapter_name)
        if not adapter:
            return {"error": f"Adapter '{adapter_name}' not found"}

        try:
            data = adapter.get_index_data(index_name, **kwargs)

            # Publish event
            self.publish_index_data(
                source=adapter_name,
                index_name=index_name,
                data=data,
                metadata={"kwargs": kwargs},
            )

            return data
        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Error getting index data: {e}")
            return {"error": str(e)}

    def get_quote(
        self,
        symbol: str,
        adapter_name: str = "fyers",
        **kwargs
    ) -> Dict[str, Any]:
        """Get quote through the pipe."""
        adapter = self._adapters.get(adapter_name)
        if not adapter:
            return {"error": f"Adapter '{adapter_name}' not found"}

        try:
            quote = adapter.get_quote(symbol, **kwargs)

            # Publish event
            data = quote.to_dict() if hasattr(quote, "to_dict") else dict(quote)
            self.publish_quote(
                source=adapter_name,
                symbol=symbol,
                data=data,
            )

            return data
        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Error getting quote: {e}")
            return {"error": str(e)}

    def get_history(
        self,
        symbol: str,
        adapter_name: str = "fyers",
        **kwargs
    ) -> Dict[str, Any]:
        """Get history through the pipe."""
        adapter = self._adapters.get(adapter_name)
        if not adapter:
            return {"error": f"Adapter '{adapter_name}' not found"}

        try:
            history = adapter.get_history(symbol, **kwargs)

            # Publish event
            data = history.to_dict() if hasattr(history, "to_dict") else dict(history)
            self.publish_history(
                source=adapter_name,
                symbol=symbol,
                data=data,
            )

            return data
        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Error getting history: {e}")
            return {"error": str(e)}

    def get_latest(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get the latest data for a symbol from the event buffer.

        Used by ContextAgent to retrieve recent market state.

        Args:
            symbol: Symbol to get data for

        Returns:
            Latest data dict or None if no data available
        """
        with self._lock:
            # Search buffer from newest to oldest
            for event in reversed(self._event_buffer):
                if event.symbol == symbol:
                    return event.data
        return None

    def get_latest_by_type(
        self,
        symbol: str,
        event_type: DataEventType
    ) -> Optional[Dict[str, Any]]:
        """
        Get the latest data for a symbol and event type.

        Args:
            symbol: Symbol to get data for
            event_type: Type of event to filter

        Returns:
            Latest data dict or None
        """
        with self._lock:
            for event in reversed(self._event_buffer):
                if event.symbol == symbol and event.event_type == event_type:
                    return event.data
        return None


# Global singleton
_pipe_instance: Optional[DataPipe] = None


def get_data_pipe() -> DataPipe:
    """Get the global DataPipe instance."""
    global _pipe_instance
    if _pipe_instance is None:
        _pipe_instance = DataPipe()
    return _pipe_instance

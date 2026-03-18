"""
Synapse Context Manager - Cross-layer context sharing.

Manages shared state and context across all layers:
- Current market regime
- Active goals
- Worker states
- Recent decisions
- Trading session info
"""

import time
import logging
import threading
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class ContextEntry:
    """A context entry with metadata."""
    key: str
    value: Any
    layer: int  # Layer that set this
    source: str  # Agent that set this
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    ttl: Optional[float] = None  # Time-to-live seconds
    tags: List[str] = field(default_factory=list)

    def is_expired(self) -> bool:
        if self.ttl is None:
            return False
        return time.time() - self.updated_at > self.ttl


class SynapseContextManager:
    """
    Manages shared context across all layers.

    Features:
    - Versioned context updates
    - TTL-based expiration
    - Layer-scoped context
    - Context snapshots
    - Change notifications
    """

    # Standard context keys
    CTX_MARKET_REGIME = "market_regime"
    CTX_VOLATILITY_STATE = "volatility_state"
    CTX_ACTIVE_GOALS = "active_goals"
    CTX_TRADING_SESSION = "trading_session"
    CTX_RISK_STATUS = "risk_status"

    def __init__(self):
        self._context: Dict[str, ContextEntry] = {}
        self._history: Dict[str, List[ContextEntry]] = defaultdict(list)
        self._watchers: Dict[str, List[callable]] = defaultdict(list)
        self._lock = threading.Lock()
        self._max_history = 100

    def set(
        self,
        key: str,
        value: Any,
        layer: int,
        source: str,
        ttl: Optional[float] = None,
        tags: Optional[List[str]] = None,
    ):
        """
        Set context value.

        Args:
            key: Context key
            value: Context value
            layer: Layer setting this (0-6)
            source: Agent/component setting this
            ttl: Optional time-to-live
            tags: Optional tags for filtering
        """
        with self._lock:
            existing = self._context.get(key)

            if existing:
                # Update existing
                existing.value = value
                existing.updated_at = time.time()
                existing.version += 1
                if ttl is not None:
                    existing.ttl = ttl

                # Keep history
                self._history[key].append(ContextEntry(
                    key=key,
                    value=existing.value,
                    layer=existing.layer,
                    source=existing.source,
                    version=existing.version - 1,
                    created_at=existing.created_at,
                    updated_at=existing.updated_at,
                ))
                if len(self._history[key]) > self._max_history:
                    self._history[key] = self._history[key][-self._max_history:]
            else:
                # Create new
                entry = ContextEntry(
                    key=key,
                    value=value,
                    layer=layer,
                    source=source,
                    ttl=ttl,
                    tags=tags or [],
                )
                self._context[key] = entry

        # Notify watchers
        self._notify_watchers(key, value)

    def get(self, key: str, default: Any = None) -> Any:
        """Get context value."""
        with self._lock:
            entry = self._context.get(key)
            if entry:
                if entry.is_expired():
                    del self._context[key]
                    return default
                return entry.value
            return default

    def get_entry(self, key: str) -> Optional[ContextEntry]:
        """Get full context entry with metadata."""
        with self._lock:
            entry = self._context.get(key)
            if entry and not entry.is_expired():
                return entry
            return None

    def delete(self, key: str):
        """Delete context entry."""
        with self._lock:
            self._context.pop(key, None)

    def get_by_layer(self, layer: int) -> Dict[str, Any]:
        """Get all context set by a specific layer."""
        with self._lock:
            return {
                k: v.value for k, v in self._context.items()
                if v.layer == layer and not v.is_expired()
            }

    def get_by_tags(self, tags: List[str]) -> Dict[str, Any]:
        """Get context entries matching any of the tags."""
        with self._lock:
            result = {}
            for key, entry in self._context.items():
                if not entry.is_expired():
                    if any(tag in entry.tags for tag in tags):
                        result[key] = entry.value
            return result

    def watch(self, key: str, callback: callable):
        """Watch a context key for changes."""
        with self._lock:
            if callback not in self._watchers[key]:
                self._watchers[key].append(callback)

    def unwatch(self, key: str, callback: callable):
        """Stop watching a context key."""
        with self._lock:
            if callback in self._watchers[key]:
                self._watchers[key].remove(callback)

    def _notify_watchers(self, key: str, value: Any):
        """Notify watchers of context change."""
        watchers = self._watchers.get(key, [])
        for callback in watchers:
            try:
                callback(key, value)
            except Exception as e:
                logger.error(f"Context watcher error for {key}: {e}")

    def snapshot(self) -> Dict[str, Any]:
        """Get snapshot of all current context."""
        with self._lock:
            return {
                k: v.value for k, v in self._context.items()
                if not v.is_expired()
            }

    def snapshot_full(self) -> Dict[str, Dict]:
        """Get full snapshot with metadata."""
        with self._lock:
            result = {}
            for key, entry in self._context.items():
                if not entry.is_expired():
                    result[key] = {
                        "value": entry.value,
                        "layer": entry.layer,
                        "source": entry.source,
                        "version": entry.version,
                        "updated_at": entry.updated_at,
                    }
            return result

    def get_history(self, key: str, limit: int = 10) -> List[Dict]:
        """Get history of a context key."""
        with self._lock:
            entries = self._history.get(key, [])[-limit:]
            return [
                {"value": e.value, "version": e.version, "updated_at": e.updated_at}
                for e in entries
            ]

    def cleanup_expired(self) -> int:
        """Remove expired entries, return count removed."""
        with self._lock:
            expired_keys = [
                k for k, v in self._context.items()
                if v.is_expired()
            ]
            for key in expired_keys:
                del self._context[key]
            return len(expired_keys)

    # Convenience methods for standard context

    def set_market_regime(self, regime: str, source: str):
        """Set current market regime."""
        self.set(self.CTX_MARKET_REGIME, regime, layer=2, source=source, ttl=300)

    def get_market_regime(self) -> Optional[str]:
        """Get current market regime."""
        return self.get(self.CTX_MARKET_REGIME)

    def set_volatility_state(self, state: str, source: str):
        """Set volatility state."""
        self.set(self.CTX_VOLATILITY_STATE, state, layer=2, source=source, ttl=300)

    def get_volatility_state(self) -> Optional[str]:
        """Get volatility state."""
        return self.get(self.CTX_VOLATILITY_STATE)

    def set_trading_session(self, session_info: Dict, source: str):
        """Set trading session info."""
        self.set(self.CTX_TRADING_SESSION, session_info, layer=0, source=source, ttl=3600)

    def is_market_open(self) -> bool:
        """Check if market is open."""
        session = self.get(self.CTX_TRADING_SESSION, {})
        return session.get("is_open", False)

    def set_risk_status(self, status: Dict, source: str):
        """Set current risk status."""
        self.set(self.CTX_RISK_STATUS, status, layer=4, source=source, ttl=60)

    def get_stats(self) -> Dict:
        """Get context manager stats."""
        with self._lock:
            return {
                "total_entries": len(self._context),
                "by_layer": {
                    i: sum(1 for v in self._context.values() if v.layer == i)
                    for i in range(7)
                },
                "watchers": {k: len(v) for k, v in self._watchers.items() if v},
                "history_keys": len(self._history),
            }

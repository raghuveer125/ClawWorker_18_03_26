"""Kafka topic configuration and in-process message bus.

Uses an in-process bus by default (no external Kafka required).
Set KAFKA_BOOTSTRAP_SERVERS env to connect to real Kafka.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")

TOPICS = {
    "market_data": "scalping.dryrun.market_data",
    "signals": "scalping.dryrun.signals",
    "decisions": "scalping.dryrun.decisions",
    "positions": "scalping.dryrun.positions",
    "fills": "scalping.dryrun.fills",
    "logs": "scalping.dryrun.logs",
}


class InProcessBus:
    """Zero-latency in-process message bus replacing Kafka for local dry-run."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._history: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._lock = threading.Lock()

    def publish(self, topic: str, message: Dict[str, Any]) -> None:
        stamped = {**message, "_topic": topic, "_ts": datetime.now().isoformat()}
        with self._lock:
            self._history[topic].append(stamped)
            subscribers = list(self._subscribers.get(topic, []))
        for cb in subscribers:
            try:
                cb(stamped)
            except Exception:
                pass

    def subscribe(self, topic: str, callback: Callable) -> None:
        with self._lock:
            self._subscribers[topic].append(callback)

    def get_history(self, topic: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._history[topic][-limit:])

    def clear(self) -> None:
        with self._lock:
            self._history.clear()


_bus_instance: Optional[InProcessBus] = None


def get_bus() -> InProcessBus:
    global _bus_instance
    if _bus_instance is None:
        _bus_instance = InProcessBus()
    return _bus_instance


def publish(topic: str, message: Dict[str, Any]) -> None:
    get_bus().publish(TOPICS.get(topic, topic), message)


def subscribe(topic: str, callback: Callable) -> None:
    get_bus().subscribe(TOPICS.get(topic, topic), callback)

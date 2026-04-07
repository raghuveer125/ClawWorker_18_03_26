"""
Real-time metrics collector for the scalping validation pipeline.

Tracks per-topic throughput, per-stage latency, Kafka consumer lag
estimates, and signal/trade rates using rolling time windows backed
by ``collections.deque``.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Tuple

from config.settings import SCALPING_TOPICS, Settings


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------

@dataclass
class _TimestampedValue:
    """A value tagged with the wall-clock time it was recorded."""

    value: float
    recorded_at: float


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Collects rolling-window metrics for topics, pipeline stages,
    Kafka lag, and signal/trade rates.

    All ``deque`` instances are bounded by the configured window so
    memory stays constant regardless of run duration.
    """

    _MAX_WINDOW_ENTRIES = 50_000  # hard cap per deque

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._window_sec: float = settings.metrics_window_sec

        # Per-topic message tracking
        self._topic_messages: Dict[str, Deque[_TimestampedValue]] = defaultdict(
            lambda: deque(maxlen=self._MAX_WINDOW_ENTRIES)
        )
        self._topic_bytes: Dict[str, int] = defaultdict(int)
        self._topic_count: Dict[str, int] = defaultdict(int)
        self._topic_last_time: Dict[str, float] = {}

        # Per-stage latency tracking
        self._stage_latencies: Dict[str, Deque[_TimestampedValue]] = defaultdict(
            lambda: deque(maxlen=self._MAX_WINDOW_ENTRIES)
        )

        # Kafka lag estimates (msg timestamp vs now)
        self._topic_lag_samples: Dict[str, Deque[_TimestampedValue]] = defaultdict(
            lambda: deque(maxlen=self._MAX_WINDOW_ENTRIES)
        )

        # Signal and trade rate tracking
        self._signal_times: Deque[float] = deque(maxlen=self._MAX_WINDOW_ENTRIES)
        self._trade_times: Deque[float] = deque(maxlen=self._MAX_WINDOW_ENTRIES)

    # -- Recording methods ---------------------------------------------------

    def record_message(self, topic: str, size_bytes: int) -> None:
        """Record a received message on *topic* with its payload size."""
        now = time.time()
        self._topic_messages[topic].append(
            _TimestampedValue(value=size_bytes, recorded_at=now)
        )
        self._topic_bytes[topic] += size_bytes
        self._topic_count[topic] += 1
        self._topic_last_time[topic] = now

        # Automatically track signal / trade rates
        signals_topic = SCALPING_TOPICS.get("signals", "")
        trades_topic = SCALPING_TOPICS.get("trades", "")
        if topic == signals_topic:
            self._signal_times.append(now)
        elif topic == trades_topic:
            self._trade_times.append(now)

    def record_latency(self, stage: str, latency_ms: float) -> None:
        """Record processing latency for a pipeline *stage*."""
        now = time.time()
        self._stage_latencies[stage].append(
            _TimestampedValue(value=latency_ms, recorded_at=now)
        )

    def record_kafka_lag(self, topic: str, lag_ms: float) -> None:
        """Record an estimated consumer lag sample for *topic*.

        Typically computed as ``now_ms - message_timestamp_ms``.
        """
        now = time.time()
        self._topic_lag_samples[topic].append(
            _TimestampedValue(value=lag_ms, recorded_at=now)
        )

    # -- Query methods -------------------------------------------------------

    def get_metrics(self) -> Dict[str, Any]:
        """Return a complete snapshot of all tracked metrics."""
        now = time.time()
        cutoff = now - self._window_sec

        return {
            "topics": self._topic_metrics(cutoff, now),
            "stages": self._stage_metrics(cutoff),
            "kafka": self._kafka_metrics(cutoff),
            "signals_per_minute": self._rate_per_minute(self._signal_times, cutoff),
            "trades_per_minute": self._rate_per_minute(self._trade_times, cutoff),
            "window_sec": self._window_sec,
            "collected_at": now,
        }

    # -- Internal helpers ----------------------------------------------------

    def _topic_metrics(
        self, cutoff: float, now: float
    ) -> Dict[str, Dict[str, Any]]:
        all_topics = set(SCALPING_TOPICS.values())
        result: Dict[str, Dict[str, Any]] = {}

        for topic in all_topics:
            window = self._topic_messages.get(topic, deque())
            recent = [e for e in window if e.recorded_at >= cutoff]
            recent_count = len(recent)
            elapsed = now - cutoff if now > cutoff else 1.0

            result[topic] = {
                "message_count": self._topic_count.get(topic, 0),
                "bytes_total": self._topic_bytes.get(topic, 0),
                "messages_per_sec": round(recent_count / elapsed, 2),
                "last_message_time": self._topic_last_time.get(topic),
            }

        return result

    def _stage_metrics(self, cutoff: float) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}

        for stage, window in self._stage_latencies.items():
            recent = [e.value for e in window if e.recorded_at >= cutoff]
            if not recent:
                result[stage] = {
                    "avg_latency_ms": None,
                    "p99_latency_ms": None,
                    "sample_count": 0,
                }
                continue

            sorted_vals = sorted(recent)
            p99_idx = max(0, int(len(sorted_vals) * 0.99) - 1)

            result[stage] = {
                "avg_latency_ms": round(sum(sorted_vals) / len(sorted_vals), 2),
                "p99_latency_ms": round(sorted_vals[p99_idx], 2),
                "sample_count": len(sorted_vals),
            }

        return result

    def _kafka_metrics(self, cutoff: float) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}

        for topic in SCALPING_TOPICS.values():
            window = self._topic_lag_samples.get(topic, deque())
            recent = [e.value for e in window if e.recorded_at >= cutoff]
            if not recent:
                result[topic] = {"estimated_lag_ms": None, "sample_count": 0}
                continue

            result[topic] = {
                "estimated_lag_ms": round(sum(recent) / len(recent), 2),
                "sample_count": len(recent),
            }

        return result

    @staticmethod
    def _rate_per_minute(times: Deque[float], cutoff: float) -> float:
        """Count events after *cutoff* and extrapolate to per-minute rate."""
        recent = [t for t in times if t >= cutoff]
        count = len(recent)
        if count < 2:
            return round(count * 60.0, 2)

        span = recent[-1] - recent[0]
        if span <= 0:
            return round(count * 60.0, 2)

        return round((count / span) * 60.0, 2)

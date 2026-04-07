"""
Canonical Layer 4c: non-blocking DB writer with deterministic routing.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import threading
import time
from typing import Any


DB_TARGET_TIMESCALE = "timescale"
DB_TARGET_POSTGRES = "postgres"
DB_TARGET_REDIS = "redis"


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True)
class DBWriteRecord:
    event_id: str
    topic: str
    key: str
    target: str
    table: str
    received_at: datetime
    payload: dict[str, Any]


@dataclass(frozen=True)
class EnqueueResult:
    accepted: bool
    reason: str | None = None
    target: str | None = None
    table: str | None = None


@dataclass(frozen=True)
class DrainSummary:
    written: int
    batches: int
    failures: int


class DBSink:
    def write_batch(self, table: str, rows: list[DBWriteRecord]) -> int:
        raise NotImplementedError


class InMemoryDBSink(DBSink):
    def __init__(self) -> None:
        self.writes: list[tuple[str, list[DBWriteRecord]]] = []

    def write_batch(self, table: str, rows: list[DBWriteRecord]) -> int:
        self.writes.append((table, list(rows)))
        return len(rows)


@dataclass(frozen=True)
class DBRoute:
    target: str
    table: str


class DBTopicRouter:
    """
    Layer 4c routing contract:
    - market feature topics -> TimescaleDB
    - transactional/system topics -> PostgreSQL
    - mutable latest-state topics -> Redis
    """

    def route(self, topic: str, payload: dict[str, Any]) -> DBRoute | None:
        _ = payload
        parts = topic.split(".")
        if len(parts) < 2:
            return None

        if topic.startswith("market.") and topic.endswith(".oi_features"):
            return DBRoute(target=DB_TARGET_TIMESCALE, table="market_oi_features_1m")
        if topic.startswith("market.") and ".features." in topic:
            resolution = parts[-1]
            if resolution == "1m":
                return DBRoute(target=DB_TARGET_TIMESCALE, table="market_features_1m")
            if resolution == "5m":
                return DBRoute(target=DB_TARGET_TIMESCALE, table="market_features_5m")
            return DBRoute(target=DB_TARGET_TIMESCALE, table=f"market_features_{resolution}")
        if topic.startswith("market.") and ".candles." in topic:
            resolution = parts[-1]
            return DBRoute(target=DB_TARGET_TIMESCALE, table=f"market_candles_{resolution}")

        if topic.startswith("signals."):
            return DBRoute(target=DB_TARGET_POSTGRES, table="strategy_signals")
        if topic.startswith("orders."):
            return DBRoute(target=DB_TARGET_POSTGRES, table="orders_events")
        if topic == "audit.log":
            return DBRoute(target=DB_TARGET_POSTGRES, table="audit_log")
        if topic == "risk.breach":
            return DBRoute(target=DB_TARGET_POSTGRES, table="risk_breaches")

        if topic.startswith("positions."):
            return DBRoute(target=DB_TARGET_REDIS, table="positions_latest")
        if topic == "system.heartbeat":
            return DBRoute(target=DB_TARGET_REDIS, table="service_heartbeats")

        return None


class NonBlockingDBWriter:
    """
    Non-blocking ingest path:
    - enqueue() only buffers and returns quickly
    - drain_once()/drain_all() performs batched writes
    """

    def __init__(
        self,
        timescale_sink: DBSink,
        postgres_sink: DBSink,
        redis_sink: DBSink,
        router: DBTopicRouter | None = None,
        max_buffer: int = 10000,
        flush_batch_size: int = 200,
        dedupe_cache_size: int = 5000,
    ) -> None:
        self._router = router or DBTopicRouter()
        self._sinks: dict[str, DBSink] = {
            DB_TARGET_TIMESCALE: timescale_sink,
            DB_TARGET_POSTGRES: postgres_sink,
            DB_TARGET_REDIS: redis_sink,
        }
        self._max_buffer = max(1, max_buffer)
        self._flush_batch_size = max(1, flush_batch_size)
        self._dedupe_cache_size = max(1, dedupe_cache_size)
        self._buffer: deque[DBWriteRecord] = deque()
        self._buffer_lock = threading.Lock()
        self._recent_event_ids: dict[str, float] = {}

    @property
    def buffered_count(self) -> int:
        with self._buffer_lock:
            return len(self._buffer)

    def enqueue(
        self,
        topic: str,
        key: str,
        payload: dict[str, Any],
        received_at: datetime | None = None,
    ) -> EnqueueResult:
        route = self._router.route(topic=topic, payload=payload)
        if route is None:
            return EnqueueResult(accepted=False, reason="unsupported_topic")

        event_id = self._event_id(topic=topic, key=key, payload=payload)

        rec = DBWriteRecord(
            event_id=event_id,
            topic=topic,
            key=key,
            target=route.target,
            table=route.table,
            received_at=received_at or _utc_now(),
            payload=dict(payload),
        )

        with self._buffer_lock:
            if event_id in self._recent_event_ids:
                return EnqueueResult(accepted=False, reason="duplicate", target=route.target, table=route.table)

            if len(self._buffer) >= self._max_buffer:
                return EnqueueResult(accepted=False, reason="buffer_full", target=route.target, table=route.table)

            self._buffer.append(rec)
            self._remember_event_id(event_id)

        return EnqueueResult(accepted=True, target=route.target, table=route.table)

    def drain_once(self, max_items: int | None = None) -> DrainSummary:
        with self._buffer_lock:
            if not self._buffer:
                return DrainSummary(written=0, batches=0, failures=0)

            limit = max_items if max_items is not None else self._flush_batch_size
            limit = max(1, min(limit, len(self._buffer)))

            selected: list[DBWriteRecord] = []
            for _ in range(limit):
                selected.append(self._buffer.popleft())

        grouped: dict[tuple[str, str], list[DBWriteRecord]] = {}
        for rec in selected:
            grouped.setdefault((rec.target, rec.table), []).append(rec)

        written = 0
        batches = 0
        failures = 0
        for (target, table), rows in grouped.items():
            sink = self._sinks[target]
            try:
                written += sink.write_batch(table=table, rows=rows)
                batches += 1
            except Exception:
                failures += len(rows)
        return DrainSummary(written=written, batches=batches, failures=failures)

    def drain_all(self) -> DrainSummary:
        total_written = 0
        total_batches = 0
        total_failures = 0
        while True:
            with self._buffer_lock:
                has_items = bool(self._buffer)
            if not has_items:
                break
            summary = self.drain_once()
            total_written += summary.written
            total_batches += summary.batches
            total_failures += summary.failures
            if summary.written == 0 and summary.failures == 0:
                break
        return DrainSummary(written=total_written, batches=total_batches, failures=total_failures)

    def _event_id(self, topic: str, key: str, payload: dict[str, Any]) -> str:
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        text = f"{topic}|{key}|{normalized}"
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _remember_event_id(self, event_id: str) -> None:
        self._recent_event_ids[event_id] = time.monotonic()
        if len(self._recent_event_ids) <= self._dedupe_cache_size:
            return
        oldest = min(self._recent_event_ids, key=self._recent_event_ids.get)
        self._recent_event_ids.pop(oldest, None)


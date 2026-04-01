"""
Async Kafka consumer for the scalping validation pipeline.

Subscribes to all scalping topics, deserializes JSON messages, and routes
them to registered validator callbacks.  The blocking KafkaConsumer.poll()
call runs inside a thread-pool executor so the asyncio event loop stays
responsive.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

from kafka import KafkaConsumer
from kafka.errors import KafkaError, NoBrokersAvailable

from config.settings import SCALPING_TOPICS, Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MAX_CONNECT_RETRIES = 5
_RETRY_BACKOFF_SEC = 2.0


@dataclass
class _TopicStats:
    """Per-topic consumption statistics."""

    message_count: int = 0
    last_message_time: float = 0.0
    bytes_received: int = 0

    def snapshot(self) -> Dict[str, Any]:
        return {
            "message_count": self.message_count,
            "last_message_time": self.last_message_time,
            "bytes_received": self.bytes_received,
        }


# ---------------------------------------------------------------------------
# Public consumer
# ---------------------------------------------------------------------------


class ScalpingKafkaConsumer:
    """Async wrapper around *kafka-python* ``KafkaConsumer``.

    Usage::

        consumer = ScalpingKafkaConsumer(settings)
        consumer.register_handler("scalping.market_data", my_callback)
        await consumer.start()
        # ... later ...
        await consumer.stop()
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._topics: List[str] = list(SCALPING_TOPICS.values())
        self._handlers: Dict[str, List[Callable[..., Coroutine[Any, Any, None]]]] = {}
        self._stats: Dict[str, _TopicStats] = {
            topic: _TopicStats() for topic in self._topics
        }
        self._consumer: Optional[KafkaConsumer] = None
        self._running = False
        self._task: Optional[asyncio.Task[None]] = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kafka")

    # -- Registration -------------------------------------------------------

    def register_handler(
        self,
        topic: str,
        callback: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        """Register an async callback for *topic*.

        Multiple handlers per topic are supported; they execute concurrently.
        """
        self._handlers.setdefault(topic, []).append(callback)

    # -- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Connect to Kafka and begin consuming in a background task."""
        if self._running:
            logger.warning("Consumer already running")
            return

        self._consumer = await self._connect_with_retry()
        self._running = True
        self._task = asyncio.create_task(self._consume_loop(), name="kafka-consumer")
        logger.info(
            "Kafka consumer started on topics %s", ", ".join(self._topics)
        )

    async def stop(self) -> None:
        """Signal the consumer loop to stop and wait for clean shutdown."""
        if not self._running:
            return

        self._running = False

        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Consumer task did not finish in time; cancelling")
                self._task.cancel()

        if self._consumer is not None:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self._executor, self._consumer.close)
            except Exception:
                logger.exception("Error closing Kafka consumer")

        self._executor.shutdown(wait=False)
        logger.info("Kafka consumer stopped")

    # -- Stats --------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Return per-topic message counts, last-seen timestamps, throughput."""
        return {topic: stats.snapshot() for topic, stats in self._stats.items()}

    # -- Internal -----------------------------------------------------------

    async def _connect_with_retry(self) -> KafkaConsumer:
        """Create a ``KafkaConsumer``, retrying on connection failure."""
        loop = asyncio.get_running_loop()
        last_err: Optional[Exception] = None

        for attempt in range(1, _MAX_CONNECT_RETRIES + 1):
            try:
                consumer = await loop.run_in_executor(
                    self._executor,
                    self._create_consumer,
                )
                logger.info("Connected to Kafka (attempt %d)", attempt)
                return consumer
            except (NoBrokersAvailable, KafkaError) as exc:
                last_err = exc
                logger.warning(
                    "Kafka connection attempt %d/%d failed: %s",
                    attempt,
                    _MAX_CONNECT_RETRIES,
                    exc,
                )
                await asyncio.sleep(_RETRY_BACKOFF_SEC * attempt)

        raise ConnectionError(
            f"Failed to connect to Kafka after {_MAX_CONNECT_RETRIES} attempts"
        ) from last_err

    def _create_consumer(self) -> KafkaConsumer:
        return KafkaConsumer(
            *self._topics,
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            group_id=self._settings.kafka_consumer_group,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="latest",
            enable_auto_commit=True,
            consumer_timeout_ms=self._settings.kafka_consumer_timeout_ms,
        )

    async def _consume_loop(self) -> None:
        """Poll Kafka in a thread executor and dispatch to handlers."""
        loop = asyncio.get_running_loop()
        assert self._consumer is not None  # noqa: S101

        while self._running:
            try:
                records = await loop.run_in_executor(
                    self._executor,
                    lambda: self._consumer.poll(  # type: ignore[union-attr]
                        timeout_ms=self._settings.kafka_consumer_timeout_ms,
                        max_records=500,
                    ),
                )
            except KafkaError as exc:
                logger.error("Kafka poll error: %s", exc)
                await asyncio.sleep(1.0)
                continue
            except Exception:
                logger.exception("Unexpected error during Kafka poll")
                await asyncio.sleep(1.0)
                continue

            for tp, messages in records.items():
                topic = tp.topic
                for record in messages:
                    await self._dispatch(topic, record)

    async def _dispatch(self, topic: str, record: Any) -> None:
        """Update stats and invoke registered handlers."""
        now = time.time()
        message = record.value
        raw_size = len(json.dumps(message).encode("utf-8")) if message else 0

        stats = self._stats.get(topic)
        if stats is not None:
            stats.message_count += 1
            stats.last_message_time = now
            stats.bytes_received += raw_size

        handlers = self._handlers.get(topic, [])
        if not handlers:
            return

        tasks = [handler(topic, message) for handler in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error("Handler error on topic %s: %s", topic, result)

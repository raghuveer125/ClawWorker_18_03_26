"""
Layer 3 Kafka producer contracts and in-memory test implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProducerRecord:
    topic: str
    key: str
    value: dict[str, Any]


class MessageProducer(Protocol):
    def publish(self, topic: str, key: str, value: dict[str, Any]) -> None: ...


class PublishError(Exception):
    """Raised when a message publish attempt fails."""


class InMemoryProducer:
    """Simple producer used by tests and local dry-run flows."""

    def __init__(self) -> None:
        self._records: list[ProducerRecord] = []

    def publish(self, topic: str, key: str, value: dict[str, Any]) -> None:
        self._records.append(ProducerRecord(topic=topic, key=key, value=value))

    @property
    def records(self) -> tuple[ProducerRecord, ...]:
        return tuple(self._records)

    def clear(self) -> None:
        self._records.clear()


class ConfluentKafkaProducer:
    """
    Thin adapter for confluent-kafka Producer.

    This keeps Layer 3 contract stable while allowing local tests to rely
    on InMemoryProducer.
    """

    def __init__(self, config: dict[str, Any], flush_timeout: float = 2.0) -> None:
        try:
            from confluent_kafka import Producer  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on runtime env
            raise RuntimeError("confluent-kafka is not available") from exc
        self._producer = Producer(config)
        self._flush_timeout = flush_timeout

    def publish(self, topic: str, key: str, value: dict[str, Any]) -> None:
        import json

        try:
            payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self._producer.produce(topic=topic, key=key.encode("utf-8"), value=payload)
            self._producer.poll(0)
        except Exception as exc:
            raise PublishError(str(exc)) from exc

    def flush(self, timeout: float | None = None) -> None:
        """Flush all in-flight messages. Call on graceful shutdown."""
        self._producer.flush(timeout if timeout is not None else self._flush_timeout)

"""
Publish validated market bundles from ingestion to Kafka topics.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
import hashlib
import json
import time
from typing import Any

from data_platform.ingestion.pipeline import ValidatedMarketBundle, ValidatedPayload
from data_platform.kafka.key_builder import KafkaKeyBuilder
from data_platform.kafka.producer import MessageProducer
from data_platform.kafka.topic_mapper import KafkaTopicMapper


@dataclass(frozen=True)
class PublishSummary:
    raw_published: int
    validated_published: int
    rejected_published: int
    retries: int = 0
    failures: int = 0
    dedup_skipped: int = 0


class ValidatedMarketPublisher:
    def __init__(
        self,
        producer: MessageProducer,
        topics: KafkaTopicMapper | None = None,
        key_builder: KafkaKeyBuilder | None = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.05,
        dedupe_cache_size: int = 5000,
    ) -> None:
        self._producer = producer
        self._topics = topics or KafkaTopicMapper()
        self._key_builder = key_builder or KafkaKeyBuilder()
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._dedupe_cache_size = max(1, dedupe_cache_size)
        self._recent_event_ids: dict[str, float] = {}

    def publish_bundle(self, bundle: ValidatedMarketBundle) -> PublishSummary:
        raw_count = 0
        validated_count = 0
        rejected_count = 0
        retries = 0
        failures = 0
        dedup_skipped = 0

        for item in bundle.items:
            if item.passed and item.payload is not None:
                payload_dict = self._to_jsonable(item.payload)
                key = self._key_builder.for_payload(bundle.index, item.stream, payload_dict)
                envelope = {
                    "index": bundle.index,
                    "stream": item.stream,
                    "collected_at": bundle.collected_at.isoformat(),
                    "payload": payload_dict,
                }
                published_raw, r, f, d = self._publish_with_retry(self._topics.raw_topic(item.stream), key, envelope)
                retries += r
                failures += f
                dedup_skipped += d
                if published_raw:
                    raw_count += 1

                validated_envelope = {
                    **envelope,
                    "validated_at": (item.report.validated_at.isoformat() if item.report.validated_at else None),
                    "validation": {"passed": True, "issues": []},
                }
                published_validated, r, f, d = self._publish_with_retry(
                    self._topics.validated_topic(item.stream),
                    key,
                    validated_envelope,
                )
                retries += r
                failures += f
                dedup_skipped += d
                if published_validated:
                    validated_count += 1
            else:
                reject_event = self._build_reject_event(bundle, item)
                reject_key = self._key_builder.for_rejected(bundle.index, item.stream, reject_event["reason"])
                published_rejected, r, f, d = self._publish_with_retry(self._topics.rejected, reject_key, reject_event)
                retries += r
                failures += f
                dedup_skipped += d
                if published_rejected:
                    rejected_count += 1

        return PublishSummary(
            raw_published=raw_count,
            validated_published=validated_count,
            rejected_published=rejected_count,
            retries=retries,
            failures=failures,
            dedup_skipped=dedup_skipped,
        )

    def _build_reject_event(self, bundle: ValidatedMarketBundle, item: ValidatedPayload) -> dict[str, Any]:
        issues = [
            {"code": i.code, "field": i.field, "message": i.message}
            for i in item.report.issues
        ]
        return {
            "index": bundle.index,
            "stream": item.stream,
            "collected_at": bundle.collected_at.isoformat(),
            "validated_at": (item.report.validated_at.isoformat() if item.report.validated_at else None),
            "reason": item.report.primary_reason or "validation.failed",
            "issues": issues,
        }

    def _to_jsonable(self, value: Any) -> Any:
        if is_dataclass(value):
            return self._to_jsonable(asdict(value))
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): self._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._to_jsonable(v) for v in value]
        return value

    def _publish_with_retry(self, topic: str, key: str, value: dict[str, Any]) -> tuple[bool, int, int, int]:
        event_id = self._event_id(topic, key, value)
        if event_id in self._recent_event_ids:
            return False, 0, 0, 1

        retries = 0
        for attempt in range(self._max_retries + 1):
            try:
                self._producer.publish(topic=topic, key=key, value=value)
                self._remember_event_id(event_id)
                return True, retries, 0, 0
            except Exception as exc:
                if attempt >= self._max_retries:
                    _ = exc
                    return False, retries, 1, 0
                retries += 1
                if self._retry_backoff_seconds > 0:
                    time.sleep(self._retry_backoff_seconds)
        return False, retries, 1, 0

    def _event_id(self, topic: str, key: str, value: dict[str, Any]) -> str:
        normalized = json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(f"{topic}|{key}|{normalized}".encode("utf-8")).hexdigest()
        return digest

    def _remember_event_id(self, event_id: str) -> None:
        self._recent_event_ids[event_id] = time.monotonic()
        if len(self._recent_event_ids) <= self._dedupe_cache_size:
            return
        oldest_id = min(self._recent_event_ids, key=self._recent_event_ids.get)
        self._recent_event_ids.pop(oldest_id, None)

"""
Layer 2 schema validation routing to validated and dead-letter topics.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from data_platform.kafka.producer import MessageProducer
from data_platform.kafka.topic_mapper import KafkaTopicMapper
from data_platform.validation.models import ValidationReport
from data_platform.validation.service import ValidationService


class SchemaValidationRouter:
    def __init__(
        self,
        producer: MessageProducer,
        validator: ValidationService | None = None,
        topics: KafkaTopicMapper | None = None,
    ) -> None:
        self._producer = producer
        self._validator = validator or ValidationService()
        self._topics = topics or KafkaTopicMapper()

    def route(
        self,
        payload_type: str,
        payload: Mapping[str, Any],
        source: str = "ingestion",
        now: datetime | None = None,
    ) -> ValidationReport:
        report = self._validator.validate_payload_schema(payload_type=payload_type, payload=payload, now=now)
        normalized_type = payload_type.strip().lower()
        key = str(payload.get("symbol", payload.get("index", normalized_type)))

        if report.passed:
            self._producer.publish(
                topic=self._topics.validated_contract_topic(normalized_type),
                key=key,
                value=dict(payload),
            )
            if normalized_type == "tick":
                index = str(payload.get("index", "")).strip()
                symbol = str(payload.get("symbol", "")).strip()
                if index:
                    per_sym_topic = KafkaTopicMapper.symbol_ticks_topic(index, symbol or None)
                    self._producer.publish(topic=per_sym_topic, key=key, value=dict(payload))
            return report

        reject_event = self._validator.build_reject_event(
            payload_type=normalized_type,
            payload=payload,
            report=report,
            source=source,
        )
        self._producer.publish(
            topic=self._topics.rejected_contract_topic(normalized_type),
            key=key,
            value=reject_event,
        )
        return report

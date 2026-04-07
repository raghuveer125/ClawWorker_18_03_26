"""
Canonical Layer 3: Message Bus (Kafka).
"""

from data_platform.kafka.consumer_contracts import (
    ContractViolation,
    validate_market_event,
    validate_rejected_event,
)
from data_platform.kafka.key_builder import KafkaKeyBuilder
from data_platform.kafka.producer import (
    ConfluentKafkaProducer,
    InMemoryProducer,
    MessageProducer,
    ProducerRecord,
    PublishError,
)
from data_platform.kafka.topic_mapper import KafkaTopicMapper

__all__ = [
    "MessageProducer",
    "ProducerRecord",
    "PublishError",
    "InMemoryProducer",
    "ConfluentKafkaProducer",
    "KafkaKeyBuilder",
    "ContractViolation",
    "validate_market_event",
    "validate_rejected_event",
    "KafkaTopicMapper",
    "ValidatedMarketPublisher",
    "PublishSummary",
]

from data_platform.kafka.publisher import PublishSummary, ValidatedMarketPublisher

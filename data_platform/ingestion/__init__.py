"""
Canonical Layer 1: Data Ingestion Service.

Provides typed models, broker-facing fetchers, and payload normalization.
"""

from data_platform.ingestion.fetchers import (
    FuturesFetcher,
    HistoryFetcher,
    OptionChainFetcher,
    QuoteFetcher,
    VIXFetcher,
)
from data_platform.ingestion.fyers_live import (
    AuthManager,
    FyersConnectorSettings,
    HeartbeatPublisher,
    LiveFyersConnector,
    RetryManager,
    SelfHealingFyersIngestionEngine,
    TickNormalizer,
    TickPublisher,
    map_provider_error,
)
from data_platform.ingestion.models import (
    FuturesSnapshot,
    OptionChainSnapshot,
    OptionContractSnapshot,
    QuoteSnapshot,
    VIXSnapshot,
)
from data_platform.ingestion.normalizer import DataNormalizer
from data_platform.ingestion.pipeline import (
    IngestionPipeline,
    ValidatedMarketBundle,
    ValidatedPayload,
)

__all__ = [
    "DataNormalizer",
    "QuoteFetcher",
    "OptionChainFetcher",
    "VIXFetcher",
    "FuturesFetcher",
    "HistoryFetcher",
    "FyersConnectorSettings",
    "AuthManager",
    "RetryManager",
    "LiveFyersConnector",
    "TickNormalizer",
    "TickPublisher",
    "HeartbeatPublisher",
    "SelfHealingFyersIngestionEngine",
    "map_provider_error",
    "IngestionPipeline",
    "ValidatedPayload",
    "ValidatedMarketBundle",
    "QuoteSnapshot",
    "OptionContractSnapshot",
    "OptionChainSnapshot",
    "VIXSnapshot",
    "FuturesSnapshot",
]

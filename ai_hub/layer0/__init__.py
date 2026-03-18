"""
AI Engineering Hub - Layer 0: Data Foundation

The foundation layer that provides all data to upper layers.
Nothing works without this layer.

Components:
- AdaptiveSchemaManager: Dynamic field registration and computed fields
- FyersAdapter: Wraps existing MarketDataAdapter with enrichment
- IndicatorRegistry: Manages computed indicators (VWAP, FVG, etc.)
- DataPipe: Unified data bus for real-time streaming
- DataAgents: DataFeed, OptionChain, Futures, LatencyGuardian

Learning Integration:
- Layer 6 can request new indicators via AdapterModificationRequest
- Schema changes are version controlled
- Hot-reload without restart
"""

from .schema.adaptive_schema_manager import AdaptiveSchemaManager
from .adapters.fyers_adapter import Layer0FyersAdapter
from .enrichment.indicator_registry import IndicatorRegistry
from .pipe.data_pipe import DataPipe

__all__ = [
    "AdaptiveSchemaManager",
    "Layer0FyersAdapter",
    "IndicatorRegistry",
    "DataPipe",
]

__version__ = "0.1.0"

"""
Layer 0 Data Feed Agent - Central data fetching for the AI Hub.

Orchestrates data fetching from adapters and publishes to the DataPipe.
"""

import asyncio
import time
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from ..adapters.fyers_adapter import Layer0FyersAdapter
from ..pipe.data_pipe import DataPipe, get_data_pipe, DataEventType

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Result of a data fetch operation."""
    success: bool
    data: Dict[str, Any]
    latency_ms: float
    errors: List[str]


class Layer0DataFeedAgent:
    """
    Layer 0 Data Feed Agent.

    Responsibilities:
    - Fetch data from configured adapters
    - Publish to DataPipe for upper layers
    - Handle multiple indices
    - Monitor data freshness
    """

    AGENT_TYPE = "layer0_data_feed"

    def __init__(
        self,
        adapters: Optional[Dict[str, Any]] = None,
        data_pipe: Optional[DataPipe] = None,
        indices: Optional[List[str]] = None,
    ):
        """
        Initialize Layer 0 Data Feed Agent.

        Args:
            adapters: Dict of adapter name to adapter instance
            data_pipe: DataPipe instance (uses global if None)
            indices: List of indices to fetch (uses default if None)
        """
        self.data_pipe = data_pipe or get_data_pipe()
        self.indices = indices or ["NIFTY50", "BANKNIFTY", "SENSEX"]

        # Initialize adapters
        self._adapters: Dict[str, Any] = {}
        if adapters:
            self._adapters = adapters
        else:
            # Default: create Fyers adapter
            try:
                fyers_adapter = Layer0FyersAdapter()
                self._adapters["fyers"] = fyers_adapter
            except Exception as e:
                logger.warning(f"Could not initialize Fyers adapter: {e}")

        # Register adapters with pipe
        for name, adapter in self._adapters.items():
            self.data_pipe.register_adapter(name, adapter)

        # Stats
        self._stats = {
            "fetches": 0,
            "errors": 0,
            "last_fetch": None,
            "avg_latency_ms": 0.0,
        }

    async def fetch_all(
        self,
        include_history: bool = True,
        include_options: bool = True,
        resolution: str = "5",
        lookback_days: int = 5,
        indicators: Optional[List[str]] = None,
    ) -> Dict[str, FetchResult]:
        """
        Fetch data for all configured indices.

        Args:
            include_history: Include historical candles
            include_options: Include option chain
            resolution: Candle resolution
            lookback_days: History lookback
            indicators: List of indicators to compute

        Returns:
            Dict of index name to FetchResult
        """
        results = {}
        start = time.time()

        for index in self.indices:
            result = await self.fetch_index(
                index,
                include_history=include_history,
                include_options=include_options,
                resolution=resolution,
                lookback_days=lookback_days,
                indicators=indicators,
            )
            results[index] = result

        total_latency = (time.time() - start) * 1000
        self._stats["fetches"] += 1
        self._stats["last_fetch"] = time.time()

        # Update average latency
        alpha = 0.2
        self._stats["avg_latency_ms"] = (
            alpha * total_latency +
            (1 - alpha) * self._stats["avg_latency_ms"]
        )

        return results

    async def fetch_index(
        self,
        index_name: str,
        adapter_name: str = "fyers",
        include_history: bool = True,
        include_options: bool = True,
        resolution: str = "5",
        lookback_days: int = 5,
        indicators: Optional[List[str]] = None,
    ) -> FetchResult:
        """
        Fetch comprehensive data for a single index.

        Args:
            index_name: Index to fetch (NIFTY50, BANKNIFTY, etc.)
            adapter_name: Which adapter to use
            include_history: Include historical candles
            include_options: Include option chain
            resolution: Candle resolution
            lookback_days: History lookback
            indicators: List of indicators to compute

        Returns:
            FetchResult with data
        """
        start = time.time()
        errors = []

        adapter = self._adapters.get(adapter_name)
        if not adapter:
            return FetchResult(
                success=False,
                data={},
                latency_ms=0,
                errors=[f"Adapter '{adapter_name}' not found"],
            )

        try:
            # Fetch from adapter
            data = adapter.get_index_data(
                index_name=index_name,
                include_history=include_history,
                include_options=include_options,
                resolution=resolution,
                lookback_days=lookback_days,
                indicators=indicators,
            )

            latency = (time.time() - start) * 1000

            # Publish to pipe
            self.data_pipe.publish_index_data(
                source=adapter_name,
                index_name=index_name,
                data=data,
                metadata={
                    "resolution": resolution,
                    "lookback_days": lookback_days,
                    "indicators": indicators,
                },
            )

            return FetchResult(
                success=True,
                data=data,
                latency_ms=latency,
                errors=[],
            )

        except Exception as e:
            self._stats["errors"] += 1
            latency = (time.time() - start) * 1000
            logger.error(f"Error fetching {index_name}: {e}")

            return FetchResult(
                success=False,
                data={},
                latency_ms=latency,
                errors=[str(e)],
            )

    async def fetch_quote(
        self,
        symbol: str,
        adapter_name: str = "fyers",
    ) -> FetchResult:
        """Fetch quote for a symbol."""
        start = time.time()

        adapter = self._adapters.get(adapter_name)
        if not adapter:
            return FetchResult(
                success=False,
                data={},
                latency_ms=0,
                errors=[f"Adapter '{adapter_name}' not found"],
            )

        try:
            quote = adapter.get_quote(symbol)
            data = quote.to_dict() if hasattr(quote, "to_dict") else dict(quote)
            latency = (time.time() - start) * 1000

            self.data_pipe.publish_quote(
                source=adapter_name,
                symbol=symbol,
                data=data,
            )

            return FetchResult(success=True, data=data, latency_ms=latency, errors=[])

        except Exception as e:
            self._stats["errors"] += 1
            return FetchResult(
                success=False,
                data={},
                latency_ms=(time.time() - start) * 1000,
                errors=[str(e)],
            )

    async def fetch_history(
        self,
        symbol: str,
        adapter_name: str = "fyers",
        resolution: str = "5",
        lookback_days: int = 5,
        indicators: Optional[List[str]] = None,
    ) -> FetchResult:
        """Fetch historical candles for a symbol."""
        start = time.time()

        adapter = self._adapters.get(adapter_name)
        if not adapter:
            return FetchResult(
                success=False,
                data={},
                latency_ms=0,
                errors=[f"Adapter '{adapter_name}' not found"],
            )

        try:
            history = adapter.get_history(
                symbol=symbol,
                resolution=resolution,
                lookback_days=lookback_days,
                indicators=indicators,
            )
            data = history.to_dict() if hasattr(history, "to_dict") else dict(history)
            latency = (time.time() - start) * 1000

            self.data_pipe.publish_history(
                source=adapter_name,
                symbol=symbol,
                data=data,
            )

            return FetchResult(success=True, data=data, latency_ms=latency, errors=[])

        except Exception as e:
            self._stats["errors"] += 1
            return FetchResult(
                success=False,
                data={},
                latency_ms=(time.time() - start) * 1000,
                errors=[str(e)],
            )

    def get_stats(self) -> Dict:
        """Get agent statistics."""
        return {
            **self._stats,
            "adapters": list(self._adapters.keys()),
            "indices": self.indices,
            "pipe_stats": self.data_pipe.get_stats(),
        }

    def subscribe(self, callback, event_types: Optional[List[DataEventType]] = None):
        """Subscribe to data events."""
        self.data_pipe.subscribe(callback, event_types)

    def request_indicator(self, request: Dict) -> str:
        """Forward indicator request to pipe."""
        return self.data_pipe.request_field(request)


# Convenience function
async def run_data_feed_cycle(
    agent: Layer0DataFeedAgent,
    interval_seconds: float = 5.0,
    max_cycles: int = 0,
):
    """
    Run continuous data feed cycle.

    Args:
        agent: Layer0DataFeedAgent instance
        interval_seconds: Seconds between fetches
        max_cycles: Max cycles (0 = infinite)
    """
    cycle = 0
    while max_cycles == 0 or cycle < max_cycles:
        cycle += 1
        logger.info(f"Data feed cycle {cycle}")

        try:
            results = await agent.fetch_all()
            success_count = sum(1 for r in results.values() if r.success)
            logger.info(f"Fetched {success_count}/{len(results)} indices")
        except Exception as e:
            logger.error(f"Cycle {cycle} error: {e}")

        if max_cycles == 0 or cycle < max_cycles:
            await asyncio.sleep(interval_seconds)

    logger.info(f"Data feed completed after {cycle} cycles")

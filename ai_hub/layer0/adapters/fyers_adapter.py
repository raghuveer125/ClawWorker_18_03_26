"""
Layer 0 Fyers Adapter - Wraps existing MarketDataAdapter with enrichment.

Uses the existing shared_project_engine.market.adapter.MarketDataAdapter
and adds:
- Dynamic field enrichment via IndicatorRegistry
- Schema-aware data contracts
- Learning Army integration
"""

import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from .base_adapter import BaseAdapter, AdapterStatus
from ..schema.adaptive_schema_manager import AdaptiveSchemaManager
from ..enrichment.indicator_registry import get_indicator_registry, IndicatorResult

logger = logging.getLogger(__name__)

# Import existing adapter
try:
    from shared_project_engine.market.adapter import MarketDataAdapter
    HAS_MARKET_ADAPTER = True
except ImportError:
    HAS_MARKET_ADAPTER = False
    logger.warning("MarketDataAdapter not available - using mock mode")


@dataclass
class EnrichedQuote:
    """Quote data enriched with computed fields."""
    symbol: str
    base: Dict[str, Any]
    computed: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    cache_hit: bool = False

    def to_dict(self) -> Dict:
        """Flatten to single dict."""
        result = dict(self.base)
        result.update(self.computed)
        result["_timestamp"] = self.timestamp
        result["_cache_hit"] = self.cache_hit
        return result


@dataclass
class EnrichedHistory:
    """Historical data enriched with computed fields."""
    symbol: str
    candles: List[Dict]
    computed: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    cache_hit: bool = False

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "candles": self.candles,
            "computed": self.computed,
            "_timestamp": self.timestamp,
            "_cache_hit": self.cache_hit,
        }


class Layer0FyersAdapter(BaseAdapter):
    """
    Fyers adapter with dynamic enrichment.

    Features:
    - Wraps existing MarketDataAdapter
    - Adds computed indicators (VWAP, FVG, etc.)
    - Schema-aware data contracts
    - Supports Learning Army field requests
    """

    SUPPORTED_INDICES = ["NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]

    def __init__(
        self,
        schema_manager: Optional[AdaptiveSchemaManager] = None,
        access_token: Optional[str] = None,
        client_id: Optional[str] = None,
        env_file: Optional[str] = None,
        auto_enrich: bool = True,
    ):
        """
        Initialize Layer 0 Fyers Adapter.

        Args:
            schema_manager: AdaptiveSchemaManager for field definitions
            access_token: Fyers access token (optional, uses env)
            client_id: Fyers client ID (optional, uses env)
            env_file: Path to .env file
            auto_enrich: Automatically compute indicators on data fetch
        """
        self.schema_manager = schema_manager or AdaptiveSchemaManager()
        self.indicator_registry = get_indicator_registry()
        self.auto_enrich = auto_enrich
        self._last_update: Optional[float] = None
        self._latency_ms: float = 0.0

        # Initialize underlying adapter
        if HAS_MARKET_ADAPTER:
            self._adapter = MarketDataAdapter(
                access_token=access_token,
                client_id=client_id,
                env_file=env_file,
            )
            logger.info("Layer0FyersAdapter initialized with MarketDataAdapter")
        else:
            self._adapter = None
            logger.warning("Layer0FyersAdapter running in mock mode")

    @property
    def name(self) -> str:
        return "fyers"

    @property
    def supported_indices(self) -> List[str]:
        return self.SUPPORTED_INDICES

    def get_status(self) -> AdapterStatus:
        """Get adapter status."""
        return AdapterStatus(
            connected=self._adapter is not None,
            last_update=self._last_update,
            latency_ms=self._latency_ms,
            error=None if self._adapter else "Adapter not initialized",
        )

    def _measure_latency(self, start_time: float):
        """Update latency measurement."""
        self._latency_ms = (time.time() - start_time) * 1000
        self._last_update = time.time()

    def get_quote(
        self,
        symbol: str,
        enrich: Optional[bool] = None,
        ttl_seconds: Optional[float] = None,
    ) -> EnrichedQuote:
        """
        Get enriched quote for a symbol.

        Args:
            symbol: Trading symbol
            enrich: Override auto_enrich setting
            ttl_seconds: Cache TTL override

        Returns:
            EnrichedQuote with base and computed fields
        """
        start = time.time()
        should_enrich = enrich if enrich is not None else self.auto_enrich

        if not self._adapter:
            return EnrichedQuote(
                symbol=symbol,
                base={"symbol": symbol, "ltp": 0.0},
                timestamp=time.time(),
            )

        # Get base quote from underlying adapter
        raw = self._adapter.get_quote(symbol, ttl_seconds=ttl_seconds)
        self._measure_latency(start)

        base_data = {
            "symbol": raw.get("symbol", symbol),
            "ltp": float(raw.get("ltp", 0) or 0),
            "open": float(raw.get("open", 0) or 0),
            "high": float(raw.get("high", 0) or 0),
            "low": float(raw.get("low", 0) or 0),
            "close": float(raw.get("ltp", 0) or 0),  # Use LTP as current close
            "prev_close": float(raw.get("prev_close", 0) or 0),
            "volume": float(raw.get("volume", 0) or 0),
        }

        computed = {}
        if should_enrich:
            # Compute spread if bid/ask available
            if "bid" in raw or "ask" in raw:
                spread_data = {"bid": raw.get("bid", 0), "ask": raw.get("ask", 0)}
                result = self.indicator_registry.compute("compute_spread", spread_data)
                if result.value is not None:
                    computed["spread"] = result.value
                    computed["spread_pct"] = result.metadata.get("spread_pct", 0)

        return EnrichedQuote(
            symbol=symbol,
            base=base_data,
            computed=computed,
            timestamp=time.time(),
            cache_hit=raw.get("cache_hit", False),
        )

    def get_quotes(
        self,
        symbols: List[str],
        enrich: Optional[bool] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, EnrichedQuote]:
        """Get enriched quotes for multiple symbols."""
        return {
            symbol: self.get_quote(symbol, enrich=enrich, ttl_seconds=ttl_seconds)
            for symbol in symbols
        }

    def get_history(
        self,
        symbol: str,
        resolution: str = "5",
        lookback_days: int = 5,
        enrich: Optional[bool] = None,
        indicators: Optional[List[str]] = None,
        ttl_seconds: Optional[float] = None,
    ) -> EnrichedHistory:
        """
        Get enriched historical data.

        Args:
            symbol: Trading symbol
            resolution: Candle resolution (1, 5, 15, etc.)
            lookback_days: Days of history
            enrich: Override auto_enrich setting
            indicators: List of indicators to compute
            ttl_seconds: Cache TTL override

        Returns:
            EnrichedHistory with candles and computed indicators
        """
        start = time.time()
        should_enrich = enrich if enrich is not None else self.auto_enrich

        if not self._adapter:
            return EnrichedHistory(
                symbol=symbol,
                candles=[],
                timestamp=time.time(),
            )

        # Get raw history from underlying adapter
        raw = self._adapter.get_history_snapshot(
            symbol=symbol,
            resolution=resolution,
            lookback_days=lookback_days,
            ttl_seconds=ttl_seconds,
        )
        self._measure_latency(start)

        candles = raw.get("candles", [])

        computed = {}
        if should_enrich and candles:
            # Default indicators to compute
            default_indicators = indicators or ["compute_vwap", "compute_atr"]

            for ind_name in default_indicators:
                if self.indicator_registry.has(ind_name):
                    result = self.indicator_registry.compute(ind_name, candles)
                    if result.value is not None:
                        # Extract field name from function name
                        field_name = ind_name.replace("compute_", "")
                        computed[field_name] = result.value
                        if result.metadata:
                            computed[f"{field_name}_meta"] = result.metadata

        return EnrichedHistory(
            symbol=symbol,
            candles=candles,
            computed=computed,
            timestamp=time.time(),
            cache_hit=raw.get("_cache_hit", False),
        )

    def get_option_chain(
        self,
        symbol: str,
        strike_count: int = 10,
        enrich: Optional[bool] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Get option chain with enrichment."""
        start = time.time()

        if not self._adapter:
            return {"symbol": symbol, "data": [], "computed": {}}

        raw = self._adapter.get_option_chain_snapshot(
            symbol=symbol,
            strike_count=strike_count,
            ttl_seconds=ttl_seconds,
        )
        self._measure_latency(start)

        result = {
            "symbol": symbol,
            "data": raw,
            "timestamp": time.time(),
            "cache_hit": raw.get("_cache_hit", False),
        }

        # Add computed fields if enrichment enabled
        should_enrich = enrich if enrich is not None else self.auto_enrich
        if should_enrich:
            result["computed"] = self._enrich_option_chain(raw)

        return result

    def _enrich_option_chain(self, chain_data: Dict) -> Dict:
        """Compute enrichments for option chain."""
        computed = {}

        # Extract strikes for analysis
        expiry_data = chain_data.get("expiryData", [])
        if not expiry_data:
            return computed

        # Calculate max pain, PCR, etc.
        # This is a simplified version - full implementation would be more complex
        total_ce_oi = 0
        total_pe_oi = 0

        for strike_data in expiry_data:
            ce = strike_data.get("CE", {})
            pe = strike_data.get("PE", {})
            total_ce_oi += float(ce.get("oi", 0) or 0)
            total_pe_oi += float(pe.get("oi", 0) or 0)

        if total_ce_oi > 0:
            computed["pcr"] = round(total_pe_oi / total_ce_oi, 2)
        else:
            computed["pcr"] = 0.0

        computed["total_ce_oi"] = total_ce_oi
        computed["total_pe_oi"] = total_pe_oi

        return computed

    def get_index_data(
        self,
        index_name: str,
        include_history: bool = True,
        include_options: bool = True,
        resolution: str = "5",
        lookback_days: int = 5,
        strike_count: int = 10,
        indicators: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get comprehensive index data with all enrichments.

        This is the main method for getting complete data for an index.
        """
        start = time.time()

        if not self._adapter:
            return {
                "index": index_name,
                "error": "Adapter not available",
            }

        # Get raw data from underlying adapter
        raw = self._adapter.get_index_market_data(
            index_name=index_name,
            resolution=resolution,
            lookback_days=lookback_days,
            strike_count=strike_count,
        )
        self._measure_latency(start)

        # Build enriched response
        result = {
            "index": index_name,
            "timestamp": time.time(),
            "latency_ms": self._latency_ms,
        }

        # Quote data
        quote_data = raw.get("quote", {})
        result["quote"] = {
            "ltp": float(quote_data.get("ltp", 0) or 0),
            "open": float(quote_data.get("open", 0) or 0),
            "high": float(quote_data.get("high", 0) or 0),
            "low": float(quote_data.get("low", 0) or 0),
            "prev_close": float(quote_data.get("prev_close", 0) or 0),
            "volume": float(quote_data.get("volume", 0) or 0),
            "change": float(quote_data.get("ltp", 0) or 0) - float(quote_data.get("prev_close", 0) or 0),
        }

        # VIX
        vix_data = raw.get("vix_quote", {})
        result["vix"] = float(vix_data.get("ltp", 0) or 0)

        # Futures
        result["future"] = {
            "symbol": raw.get("future_symbol", ""),
            "ltp": float(raw.get("future_ltp", 0) or 0),
        }

        # History with enrichment
        if include_history:
            history = raw.get("history", {})
            candles = history.get("candles", [])
            result["history"] = {
                "candles": candles,
                "candle_count": len(candles),
            }

            # Compute indicators
            if candles:
                indicators_to_compute = indicators or [
                    "compute_vwap",
                    "compute_atr",
                    "compute_fvg",
                ]
                for ind_name in indicators_to_compute:
                    if self.indicator_registry.has(ind_name):
                        ind_result = self.indicator_registry.compute(ind_name, candles)
                        if ind_result.value is not None:
                            field_name = ind_name.replace("compute_", "")
                            result["history"][field_name] = ind_result.value

        # Option chain with enrichment
        if include_options:
            chain = raw.get("option_chain", {})
            result["option_chain"] = {
                "raw": chain,
                "computed": self._enrich_option_chain(chain),
            }

        return result

    def enrich_data(
        self,
        data: Dict,
        indicators: List[str],
        data_type: str = "candles",
    ) -> Dict:
        """
        Enrich existing data with computed indicators.

        Args:
            data: Data to enrich (candles, quote, etc.)
            indicators: List of indicator function names
            data_type: Type of data ("candles", "quote", "chain")

        Returns:
            Data with computed fields added
        """
        result = dict(data)
        computed = {}

        for ind_name in indicators:
            if self.indicator_registry.has(ind_name):
                # Get appropriate input data
                if data_type == "candles":
                    input_data = data.get("candles", data)
                else:
                    input_data = data

                ind_result = self.indicator_registry.compute(ind_name, input_data)
                if ind_result.value is not None:
                    field_name = ind_name.replace("compute_", "")
                    computed[field_name] = ind_result.value

        result["computed"] = computed
        return result

    def request_indicator(self, request: Dict) -> str:
        """
        Request a new indicator from Learning Army.

        This method is called by Layer 6 to request new data fields.
        """
        return self.schema_manager.request_field(request)

    def get_available_indicators(self) -> List[Dict]:
        """Get list of available indicators."""
        return self.indicator_registry.list_indicators()

    def get_schema(self) -> Dict:
        """Get current data schema."""
        return self.schema_manager.export_schema()

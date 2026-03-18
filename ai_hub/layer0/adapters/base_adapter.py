"""
Base Adapter Interface - Abstract base for all data adapters.

All adapters (Fyers, NSE, etc.) implement this interface.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class AdapterStatus:
    """Status of an adapter."""
    connected: bool
    last_update: Optional[float] = None
    latency_ms: float = 0.0
    error: Optional[str] = None


class BaseAdapter(ABC):
    """
    Abstract base class for data adapters.

    All data source adapters must implement this interface.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter name (e.g., 'fyers', 'nse')."""
        pass

    @property
    @abstractmethod
    def supported_indices(self) -> List[str]:
        """List of supported indices."""
        pass

    @abstractmethod
    def get_status(self) -> AdapterStatus:
        """Get adapter connection status."""
        pass

    @abstractmethod
    def get_quote(self, symbol: str, **kwargs) -> Dict[str, Any]:
        """Get real-time quote for a symbol."""
        pass

    @abstractmethod
    def get_quotes(self, symbols: List[str], **kwargs) -> Dict[str, Any]:
        """Get quotes for multiple symbols."""
        pass

    @abstractmethod
    def get_history(
        self,
        symbol: str,
        resolution: str = "5",
        lookback_days: int = 5,
        **kwargs
    ) -> Dict[str, Any]:
        """Get historical candles."""
        pass

    @abstractmethod
    def get_option_chain(
        self,
        symbol: str,
        strike_count: int = 10,
        **kwargs
    ) -> Dict[str, Any]:
        """Get option chain data."""
        pass

    @abstractmethod
    def get_index_data(
        self,
        index_name: str,
        include_history: bool = True,
        include_options: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """Get comprehensive index data."""
        pass

    def enrich_data(self, data: Dict, indicators: List[str]) -> Dict:
        """
        Enrich data with computed indicators.

        Default implementation returns data unchanged.
        Override in subclasses to add enrichment.
        """
        return data

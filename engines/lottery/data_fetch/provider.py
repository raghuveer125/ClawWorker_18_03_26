"""Abstract data provider interface — for future broker/vendor swap.

All data fetch implementations must conform to this interface.
The lottery engine only depends on this abstraction, never on a specific broker.
"""

from abc import ABC, abstractmethod
from typing import Optional

from ..models import ChainSnapshot, UnderlyingTick, ExpiryInfo


class DataProvider(ABC):
    """Abstract interface for market data providers.

    Implementations: FyersAdapter, CsvReplayAdapter (future), etc.
    """

    @abstractmethod
    def fetch_spot(self, symbol: str, exchange: str) -> Optional[UnderlyingTick]:
        """Fetch latest spot price for the underlying.

        Args:
            symbol: Instrument name (e.g., "NIFTY", "BANKNIFTY")
            exchange: Exchange code (e.g., "NSE", "BSE")

        Returns:
            UnderlyingTick or None on failure.
        """

    @abstractmethod
    def fetch_option_chain(
        self,
        symbol: str,
        exchange: str,
        expiry: str,
        strike_count: int = 50,
    ) -> Optional[ChainSnapshot]:
        """Fetch full option chain for one symbol + expiry.

        Args:
            symbol: Instrument name
            exchange: Exchange code
            expiry: Target expiry date string (YYYY-MM-DD)
            strike_count: Number of strikes around ATM to fetch

        Returns:
            ChainSnapshot or None on failure.
        """

    @abstractmethod
    def fetch_expiries(self, symbol: str, exchange: str) -> list[ExpiryInfo]:
        """Fetch available expiry dates for the instrument.

        Args:
            symbol: Instrument name
            exchange: Exchange code

        Returns:
            List of ExpiryInfo sorted by date (nearest first).
        """

    @abstractmethod
    def get_lot_size(self, symbol: str) -> int:
        """Get lot size for the instrument.

        Args:
            symbol: Instrument name

        Returns:
            Lot size (e.g., 75 for NIFTY, 30 for BANKNIFTY).
        """

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if provider has valid connection/auth."""

"""
Fetcher layer for canonical Layer 1 (Data Ingestion Service).
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from data_platform.ingestion.models import (
    FuturesSnapshot,
    OptionChainSnapshot,
    QuoteSnapshot,
    VIXSnapshot,
)
from data_platform.ingestion.normalizer import DataNormalizer


class FyersConnector(Protocol):
    """Broker adapter contract required by ingestion fetchers."""

    def get_quote(self, symbol: str) -> Mapping[str, Any]: ...

    def get_option_chain(self, index: str) -> Mapping[str, Any]: ...

    def get_vix(self) -> Mapping[str, Any]: ...

    def get_futures(self, index: str) -> Mapping[str, Any]: ...

    def get_history(self, symbol: str, resolution: str, count: int) -> Sequence[Mapping[str, Any]]: ...


class QuoteFetcher:
    def __init__(self, connector: FyersConnector, normalizer: DataNormalizer | None = None) -> None:
        self._connector = connector
        self._normalizer = normalizer or DataNormalizer()

    def fetch(self, index: str, symbol: str) -> QuoteSnapshot:
        return self._normalizer.normalize_quote(index=index, raw=self._connector.get_quote(symbol))


class OptionChainFetcher:
    def __init__(
        self,
        connector: FyersConnector,
        normalizer: DataNormalizer | None = None,
        atm_depth: int | None = None,
    ) -> None:
        self._connector = connector
        self._normalizer = normalizer or DataNormalizer()
        self._atm_depth = atm_depth

    def fetch(self, index: str) -> OptionChainSnapshot:
        payload = self._connector.get_option_chain(index)
        contracts = payload.get("contracts", [])
        as_of = payload.get("timestamp") or payload.get("as_of")
        underlying_ltp = float(payload.get("underlying_ltp", payload.get("spot_ltp", 0.0)))
        atm_strike = float(payload.get("atm_strike", 0.0))
        is_expiry_day = bool(payload.get("is_expiry_day", False))
        session_state = str(payload.get("session_state", "regular"))
        return self._normalizer.normalize_option_chain(
            index=index,
            raw_contracts=contracts,
            as_of=as_of,
            underlying_ltp=underlying_ltp,
            atm_strike=atm_strike,
            atm_depth=self._atm_depth,
            is_expiry_day=is_expiry_day,
            session_state=session_state,
        )


class VIXFetcher:
    def __init__(self, connector: FyersConnector, normalizer: DataNormalizer | None = None) -> None:
        self._connector = connector
        self._normalizer = normalizer or DataNormalizer()

    def fetch(self) -> VIXSnapshot:
        return self._normalizer.normalize_vix(self._connector.get_vix())


class FuturesFetcher:
    def __init__(self, connector: FyersConnector, normalizer: DataNormalizer | None = None) -> None:
        self._connector = connector
        self._normalizer = normalizer or DataNormalizer()

    def fetch(self, index: str) -> FuturesSnapshot:
        return self._normalizer.normalize_futures(index=index, raw=self._connector.get_futures(index))


class HistoryFetcher:
    """
    Batch historical fetcher for Layer 1 gap-fill and replay use cases.

    Returns normalized quote-like snapshots from provider history rows.
    """

    def __init__(self, connector: FyersConnector, normalizer: DataNormalizer | None = None) -> None:
        self._connector = connector
        self._normalizer = normalizer or DataNormalizer()

    def fetch_quotes(
        self,
        index: str,
        symbol: str,
        resolution: str = "1",
        count: int = 100,
    ) -> tuple[QuoteSnapshot, ...]:
        rows = self._connector.get_history(symbol=symbol, resolution=resolution, count=count)
        snapshots: list[QuoteSnapshot] = []
        for row in rows:
            payload: dict[str, Any] = dict(row)
            payload.setdefault("symbol", symbol)
            snapshots.append(self._normalizer.normalize_quote(index=index, raw=payload))
        return tuple(snapshots)

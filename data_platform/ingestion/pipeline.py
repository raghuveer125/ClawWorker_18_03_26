"""
Layer 1 -> Layer 2 integration pipeline.

Fetches ingested snapshots and validates each payload before downstream publish.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from data_platform.ingestion.fetchers import (
    FyersConnector,
    FuturesFetcher,
    OptionChainFetcher,
    QuoteFetcher,
    VIXFetcher,
)
from data_platform.validation import ValidationIssue, ValidationReport, ValidationService


@dataclass(frozen=True)
class ValidatedPayload:
    stream: str
    payload: Any
    report: ValidationReport

    @property
    def passed(self) -> bool:
        return self.report.passed


@dataclass(frozen=True)
class ValidatedMarketBundle:
    index: str
    collected_at: datetime
    items: tuple[ValidatedPayload, ...]

    @property
    def accepted(self) -> tuple[ValidatedPayload, ...]:
        return tuple(item for item in self.items if item.passed)

    @property
    def rejected(self) -> tuple[ValidatedPayload, ...]:
        return tuple(item for item in self.items if not item.passed)

    _REQUIRED_STREAMS: frozenset[str] = frozenset({"quote", "option_chain"})

    @property
    def ready_for_publish(self) -> bool:
        failed_required = {
            item.stream for item in self.rejected if item.stream in self._REQUIRED_STREAMS
        }
        return len(failed_required) == 0


class IngestionPipeline:
    """Orchestrates Layer 1 fetchers and applies Layer 2 validation."""

    def __init__(
        self,
        quote_fetcher: QuoteFetcher,
        option_chain_fetcher: OptionChainFetcher,
        vix_fetcher: VIXFetcher,
        futures_fetcher: FuturesFetcher,
        validator: ValidationService,
    ) -> None:
        self._quote_fetcher = quote_fetcher
        self._option_chain_fetcher = option_chain_fetcher
        self._vix_fetcher = vix_fetcher
        self._futures_fetcher = futures_fetcher
        self._validator = validator

    @classmethod
    def from_connector(
        cls,
        connector: FyersConnector,
        validator: ValidationService | None = None,
    ) -> "IngestionPipeline":
        return cls(
            quote_fetcher=QuoteFetcher(connector),
            option_chain_fetcher=OptionChainFetcher(connector),
            vix_fetcher=VIXFetcher(connector),
            futures_fetcher=FuturesFetcher(connector),
            validator=validator or ValidationService(),
        )

    def collect_index(
        self,
        index: str,
        quote_symbol: str,
        now: datetime | None = None,
    ) -> ValidatedMarketBundle:
        collected_at = now or datetime.now(timezone.utc)
        items = (
            self._collect_item("quote", lambda: self._quote_fetcher.fetch(index=index, symbol=quote_symbol), collected_at),
            self._collect_item("option_chain", lambda: self._option_chain_fetcher.fetch(index=index), collected_at),
            self._collect_item("vix", self._vix_fetcher.fetch, collected_at),
            self._collect_item("futures", lambda: self._futures_fetcher.fetch(index=index), collected_at),
        )
        return ValidatedMarketBundle(index=index, collected_at=collected_at, items=items)

    def _collect_item(
        self,
        stream: str,
        producer: Callable[[], Any],
        now: datetime,
    ) -> ValidatedPayload:
        try:
            payload = producer()
            report = self._validator.validate(payload, now=now)
        except ValueError as exc:
            message = str(exc)
            issue_code = "validation.input_error"
            lowered = message.lower()
            if any(token in lowered for token in ("price", "ltp", "strike", "vix")):
                issue_code = "price.zero_or_negative"
            report = ValidationReport(
                passed=False,
                issues=(
                    ValidationIssue(
                        code=issue_code,
                        field=stream,
                        message=message,
                    ),
                ),
                validated_at=now,
                payload_type=stream,
                payload=None,
            )
            payload = None
        except Exception as exc:
            report = ValidationReport(
                passed=False,
                issues=(
                    ValidationIssue(
                        code="ingestion.fetch_error",
                        field=stream,
                        message=str(exc),
                    ),
                ),
                validated_at=now,
                payload_type=stream,
                payload=None,
            )
            payload = None
        return ValidatedPayload(stream=stream, payload=payload, report=report)

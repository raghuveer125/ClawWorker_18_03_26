"""
Trade-execution quality validator for the scalping pipeline.

Validates every trade message for correctness, uniqueness, and
execution quality against the signals that triggered them.

Checks:
  1. Duplicate trades  -- same trade_id seen twice
  2. Incorrect price execution  -- slippage > 1 % vs signal's expected entry
  3. Execution delay  -- time between signal and trade > max_execution_delay_ms
  4. Orphan trades  -- trade references a signal_id never seen
  5. Missing fields  -- required trade fields present
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

from config.settings import TOPIC_SCHEMAS, Settings

logger = logging.getLogger(__name__)

_SLIPPAGE_THRESHOLD_PCT = 1.0  # flag if slippage exceeds 1 %
_TRADES_TOPIC = "scalping.trades"
_SIGNALS_TOPIC = "scalping.signals"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ValidationIssue:
    """Single validation finding (mirrors data_validator.ValidationIssue)."""

    severity: str  # "CRITICAL" | "WARNING" | "INFO"
    category: str
    topic: str
    message: str
    timestamp: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class _SignalRecord:
    """Lightweight snapshot of a signal for later cross-referencing."""

    signal_id: str
    entry_price: float
    timestamp: float  # epoch ms


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class TradeValidator:
    """Validates trade messages for execution quality.

    Also ingests signal messages so it can cross-reference trade-vs-signal.

    Usage::

        validator = TradeValidator(settings)
        # Feed signals first (or concurrently)
        validator.ingest_signal(signal_msg)
        # Validate trade
        issues = await validator.validate(trade_msg)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

        # State
        self._seen_trade_ids: Set[str] = set()
        self._signals: Dict[str, _SignalRecord] = {}  # signal_id -> record

        # Counters
        self._total_trades: int = 0
        self._duplicates: int = 0
        self._orphan_count: int = 0
        self._slippage_sum: float = 0.0
        self._slippage_count: int = 0
        self._delay_sum_ms: float = 0.0
        self._delay_count: int = 0
        self._issues_by_category: Dict[str, int] = defaultdict(int)

    # -- Signal ingestion ---------------------------------------------------

    def ingest_signal(self, message: dict) -> None:
        """Record a signal for future trade cross-referencing.

        Should be called for every message on the *scalping.signals* topic.
        """
        signal_id = message.get("signal_id")
        if not signal_id:
            return

        entry_price = _safe_float(message.get("entry_price"))
        ts = _safe_float(message.get("timestamp"))

        if entry_price is not None and ts is not None:
            self._signals[signal_id] = _SignalRecord(
                signal_id=signal_id,
                entry_price=entry_price,
                timestamp=ts,
            )

    # -- Public API ---------------------------------------------------------

    async def validate(self, message: dict) -> List[ValidationIssue]:
        """Run all trade-quality checks on *message*.

        Returns a list of issues (may be empty).
        """
        self._total_trades += 1
        ts_raw = str(message.get("timestamp", ""))
        issues: List[ValidationIssue] = []

        issues.extend(self._check_missing_fields(message, ts_raw))
        issues.extend(self._check_duplicate(message, ts_raw))
        issues.extend(self._check_slippage(message, ts_raw))
        issues.extend(self._check_execution_delay(message, ts_raw))
        issues.extend(self._check_orphan(message, ts_raw))

        for issue in issues:
            self._issues_by_category[issue.category] += 1

        return issues

    def get_report(self) -> Dict[str, Any]:
        """Trade validation summary."""
        avg_slippage = (
            round(self._slippage_sum / self._slippage_count, 4)
            if self._slippage_count
            else 0.0
        )
        avg_delay = (
            round(self._delay_sum_ms / self._delay_count, 2)
            if self._delay_count
            else 0.0
        )

        return {
            "total_trades": self._total_trades,
            "duplicates": self._duplicates,
            "orphan_trades": self._orphan_count,
            "avg_slippage_pct": avg_slippage,
            "avg_execution_delay_ms": avg_delay,
            "signals_tracked": len(self._signals),
            "issues_by_category": dict(self._issues_by_category),
        }

    # -- Internal checks ----------------------------------------------------

    def _check_missing_fields(
        self, message: dict, ts_raw: str
    ) -> List[ValidationIssue]:
        """Required trade fields must be present and non-null."""
        required = TOPIC_SCHEMAS.get(_TRADES_TOPIC, [])
        missing = [f for f in required if f not in message]
        null_fields = [
            f for f in required if f in message and message[f] is None
        ]

        issues: List[ValidationIssue] = []
        if missing:
            issues.append(
                ValidationIssue(
                    severity="CRITICAL",
                    category="missing_field",
                    topic=_TRADES_TOPIC,
                    message=f"Missing required trade fields: {', '.join(missing)}",
                    timestamp=ts_raw,
                    details={"missing_fields": missing},
                )
            )
        if null_fields:
            issues.append(
                ValidationIssue(
                    severity="WARNING",
                    category="missing_field",
                    topic=_TRADES_TOPIC,
                    message=f"Null required trade fields: {', '.join(null_fields)}",
                    timestamp=ts_raw,
                    details={"null_fields": null_fields},
                )
            )
        return issues

    def _check_duplicate(
        self, message: dict, ts_raw: str
    ) -> List[ValidationIssue]:
        """Flag if the same trade_id has been seen before."""
        trade_id = message.get("trade_id")
        if not trade_id:
            return []

        if trade_id in self._seen_trade_ids:
            self._duplicates += 1
            return [
                ValidationIssue(
                    severity="CRITICAL",
                    category="duplicate_trade",
                    topic=_TRADES_TOPIC,
                    message=f"Duplicate trade_id: {trade_id}",
                    timestamp=ts_raw,
                    details={"trade_id": trade_id},
                )
            ]

        self._seen_trade_ids.add(trade_id)
        return []

    def _check_slippage(
        self, message: dict, ts_raw: str
    ) -> List[ValidationIssue]:
        """Compare trade entry_price to signal's expected entry."""
        signal_id = message.get("signal_id")
        trade_price = _safe_float(message.get("entry_price"))

        if not signal_id or trade_price is None:
            return []

        signal = self._signals.get(signal_id)
        if signal is None:
            return []  # orphan check handles this case

        if signal.entry_price == 0:
            return []

        slippage_pct = abs(trade_price - signal.entry_price) / signal.entry_price * 100
        self._slippage_sum += slippage_pct
        self._slippage_count += 1

        if slippage_pct > _SLIPPAGE_THRESHOLD_PCT:
            return [
                ValidationIssue(
                    severity="WARNING",
                    category="price_slippage",
                    topic=_TRADES_TOPIC,
                    message=(
                        f"Slippage {slippage_pct:.2f}% on trade "
                        f"(expected {signal.entry_price}, got {trade_price})"
                    ),
                    timestamp=ts_raw,
                    details={
                        "trade_id": message.get("trade_id"),
                        "signal_id": signal_id,
                        "expected_price": signal.entry_price,
                        "actual_price": trade_price,
                        "slippage_pct": round(slippage_pct, 4),
                    },
                )
            ]
        return []

    def _check_execution_delay(
        self, message: dict, ts_raw: str
    ) -> List[ValidationIssue]:
        """Time between signal timestamp and trade timestamp."""
        signal_id = message.get("signal_id")
        trade_ts = _safe_float(message.get("timestamp"))

        if not signal_id or trade_ts is None:
            return []

        signal = self._signals.get(signal_id)
        if signal is None:
            return []

        delay_ms = trade_ts - signal.timestamp
        if delay_ms < 0:
            delay_ms = abs(delay_ms)

        self._delay_sum_ms += delay_ms
        self._delay_count += 1

        if delay_ms > self._settings.max_execution_delay_ms:
            return [
                ValidationIssue(
                    severity="WARNING",
                    category="execution_delay",
                    topic=_TRADES_TOPIC,
                    message=f"Execution delay {delay_ms:.1f} ms exceeds threshold",
                    timestamp=ts_raw,
                    details={
                        "trade_id": message.get("trade_id"),
                        "signal_id": signal_id,
                        "delay_ms": round(delay_ms, 2),
                        "threshold_ms": self._settings.max_execution_delay_ms,
                    },
                )
            ]
        return []

    def _check_orphan(
        self, message: dict, ts_raw: str
    ) -> List[ValidationIssue]:
        """Trade with a signal_id that was never seen on the signals topic."""
        signal_id = message.get("signal_id")
        if not signal_id:
            return []

        if signal_id not in self._signals:
            self._orphan_count += 1
            return [
                ValidationIssue(
                    severity="WARNING",
                    category="orphan_trade",
                    topic=_TRADES_TOPIC,
                    message=f"Trade references unknown signal_id: {signal_id}",
                    timestamp=ts_raw,
                    details={
                        "trade_id": message.get("trade_id"),
                        "signal_id": signal_id,
                    },
                )
            ]
        return []


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any) -> Optional[float]:
    """Convert *value* to float, returning ``None`` on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

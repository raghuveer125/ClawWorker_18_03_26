"""
Data-integrity validator for all incoming market-data messages.

Runs five checks on every message:
  1. Missing ticks  (gap > max_tick_gap_ms)
  2. Duplicate messages  (SHA-256 of key fields)
  3. Timestamp drift  (message vs system clock)
  4. Out-of-order  (current ts < previous ts for same topic+symbol)
  5. Schema validation  (required fields present and non-null)
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Any, Deque, Dict, List, Optional, Set

from config.settings import TOPIC_SCHEMAS, Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

_DUPLICATE_WINDOW = 1000  # hashes to remember per topic


@dataclass
class ValidationIssue:
    """Single validation finding."""

    severity: str  # "CRITICAL" | "WARNING" | "INFO"
    category: str  # "missing_tick" | "duplicate" | "timestamp_drift" | "out_of_order" | "schema"
    topic: str
    message: str
    timestamp: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class DataValidator:
    """Validates every incoming Kafka message against data-integrity rules.

    Usage::

        validator = DataValidator(settings)
        issues = await validator.validate("scalping.market_data", msg)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

        # Tracking state -- all keyed by (topic, symbol) or topic
        self._last_ts: Dict[str, float] = {}           # "topic|symbol" -> epoch ms
        self._hash_windows: Dict[str, Deque[str]] = defaultdict(
            lambda: deque(maxlen=_DUPLICATE_WINDOW)
        )

        # Counters
        self._total_messages: int = 0
        self._issues_by_category: Dict[str, int] = defaultdict(int)
        self._issues_by_severity: Dict[str, int] = defaultdict(int)

    # -- Public API ---------------------------------------------------------

    async def validate(self, topic: str, message: dict) -> List[ValidationIssue]:
        """Run all data checks on *message* from *topic*.

        Returns a (possibly empty) list of issues found.
        """
        self._total_messages += 1
        issues: List[ValidationIssue] = []

        msg_ts_raw = message.get("timestamp", "")
        msg_ts = self._parse_ts(msg_ts_raw)
        symbol = message.get("symbol", "unknown")

        issues.extend(self._check_schema(topic, message, msg_ts_raw))
        issues.extend(self._check_duplicate(topic, message, msg_ts_raw, symbol))
        issues.extend(self._check_timestamp_drift(topic, msg_ts, msg_ts_raw))
        issues.extend(
            self._check_order_and_gap(topic, symbol, msg_ts, msg_ts_raw)
        )

        for issue in issues:
            self._issues_by_category[issue.category] += 1
            self._issues_by_severity[issue.severity] += 1

        return issues

    def get_report(self) -> Dict[str, Any]:
        """Cumulative validation report."""
        return {
            "total_messages": self._total_messages,
            "issues_by_category": dict(self._issues_by_category),
            "issues_by_severity": dict(self._issues_by_severity),
        }

    # -- Checks -------------------------------------------------------------

    def _check_schema(
        self, topic: str, message: dict, ts_raw: str
    ) -> List[ValidationIssue]:
        """Verify required fields exist and are non-null."""
        required = TOPIC_SCHEMAS.get(topic)
        if required is None:
            return []

        missing: List[str] = []
        null_fields: List[str] = []
        for fld in required:
            if fld not in message:
                missing.append(fld)
            elif message[fld] is None:
                null_fields.append(fld)

        issues: List[ValidationIssue] = []
        if missing:
            issues.append(
                ValidationIssue(
                    severity="CRITICAL",
                    category="schema",
                    topic=topic,
                    message=f"Missing required fields: {', '.join(missing)}",
                    timestamp=ts_raw,
                    details={"missing_fields": missing},
                )
            )
        if null_fields:
            issues.append(
                ValidationIssue(
                    severity="WARNING",
                    category="schema",
                    topic=topic,
                    message=f"Null required fields: {', '.join(null_fields)}",
                    timestamp=ts_raw,
                    details={"null_fields": null_fields},
                )
            )
        return issues

    def _check_duplicate(
        self, topic: str, message: dict, ts_raw: str, symbol: str
    ) -> List[ValidationIssue]:
        """SHA-256 of (symbol, timestamp, ltp); flag if seen before."""
        ltp = message.get("ltp", "")
        digest = hashlib.sha256(
            f"{symbol}|{ts_raw}|{ltp}".encode()
        ).hexdigest()

        window = self._hash_windows[topic]
        if digest in window:
            return [
                ValidationIssue(
                    severity="WARNING",
                    category="duplicate",
                    topic=topic,
                    message=f"Duplicate message detected for {symbol}",
                    timestamp=ts_raw,
                    details={"symbol": symbol, "hash": digest},
                )
            ]

        window.append(digest)
        return []

    def _check_timestamp_drift(
        self, topic: str, msg_ts: Optional[float], ts_raw: str
    ) -> List[ValidationIssue]:
        """Compare message timestamp to system clock."""
        if msg_ts is None:
            return []

        now_ms = time.time() * 1000
        drift = abs(now_ms - msg_ts)

        if drift > self._settings.max_timestamp_drift_ms:
            severity = "CRITICAL" if drift > self._settings.max_timestamp_drift_ms * 5 else "WARNING"
            return [
                ValidationIssue(
                    severity=severity,
                    category="timestamp_drift",
                    topic=topic,
                    message=f"Timestamp drift {drift:.1f} ms exceeds threshold",
                    timestamp=ts_raw,
                    details={"drift_ms": drift, "threshold_ms": self._settings.max_timestamp_drift_ms},
                )
            ]
        return []

    def _check_order_and_gap(
        self,
        topic: str,
        symbol: str,
        msg_ts: Optional[float],
        ts_raw: str,
    ) -> List[ValidationIssue]:
        """Detect out-of-order messages and missing ticks."""
        if msg_ts is None:
            return []

        key = f"{topic}|{symbol}"
        prev_ts = self._last_ts.get(key)
        self._last_ts[key] = msg_ts

        if prev_ts is None:
            return []

        issues: List[ValidationIssue] = []

        # Out-of-order
        if msg_ts < prev_ts:
            issues.append(
                ValidationIssue(
                    severity="WARNING",
                    category="out_of_order",
                    topic=topic,
                    message=f"Out-of-order message for {symbol}: {msg_ts} < {prev_ts}",
                    timestamp=ts_raw,
                    details={"symbol": symbol, "current_ts": msg_ts, "previous_ts": prev_ts},
                )
            )

        # Missing tick (gap)
        gap = msg_ts - prev_ts
        if gap > self._settings.max_tick_gap_ms:
            issues.append(
                ValidationIssue(
                    severity="WARNING",
                    category="missing_tick",
                    topic=topic,
                    message=f"Tick gap {gap:.1f} ms for {symbol} exceeds threshold",
                    timestamp=ts_raw,
                    details={
                        "symbol": symbol,
                        "gap_ms": gap,
                        "threshold_ms": self._settings.max_tick_gap_ms,
                    },
                )
            )

        return issues

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _parse_ts(raw: Any) -> Optional[float]:
        """Best-effort epoch-millisecond extraction.

        Accepts:
          - numeric (int/float) already in ms
          - ISO-format string
        Returns ``None`` when parsing fails.
        """
        if isinstance(raw, (int, float)):
            return float(raw)

        if isinstance(raw, str) and raw:
            try:
                return float(raw)
            except ValueError:
                pass

            # ISO format fallback
            try:
                from datetime import datetime, timezone

                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return dt.timestamp() * 1000
            except (ValueError, TypeError):
                pass

        return None

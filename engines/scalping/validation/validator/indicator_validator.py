"""
Indicator validator for scalping pipeline components.

Ensures every component registered in ``SCALPING_COMPONENTS`` produces
correct, timely output on its designated Kafka topic.

Checks performed per message:
  1. Data existence  -- track which components have produced output
  2. Schema correctness  -- required fields per TOPIC_SCHEMAS
  3. Missing / null fields  -- required fields must be non-empty
  4. Value ranges  -- confidence 0-100, prices > 0, parseable timestamps
  5. Freshness  -- flag components silent longer than stale_data_threshold_sec
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from config.settings import SCALPING_COMPONENTS, TOPIC_SCHEMAS, Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Re-use the same issue type as data_validator for consistency.
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


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class IndicatorValidator:
    """Validates indicator / component output messages.

    Usage::

        validator = IndicatorValidator(settings)
        issues = await validator.validate("scalping.analysis", msg)
        report = validator.get_coverage_report()
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

        # Track which components have been seen
        self._seen_components: Set[str] = set()
        self._component_last_seen: Dict[str, float] = {}

        # Per-component counters
        self._component_message_count: Dict[str, int] = defaultdict(int)
        self._total_messages: int = 0
        self._issues_by_category: Dict[str, int] = defaultdict(int)

    # -- Public API ---------------------------------------------------------

    async def validate(
        self, topic: str, message: dict
    ) -> List[ValidationIssue]:
        """Validate an indicator / component message.

        The component is identified by the ``component`` field in the message
        or inferred from the topic via ``SCALPING_COMPONENTS``.
        """
        self._total_messages += 1
        ts_raw = str(message.get("timestamp", ""))
        component = self._resolve_component(topic, message)

        if component:
            self._seen_components.add(component)
            self._component_last_seen[component] = time.time()
            self._component_message_count[component] += 1

        issues: List[ValidationIssue] = []
        issues.extend(self._check_schema(topic, message, ts_raw))
        issues.extend(self._check_missing_fields(topic, message, ts_raw))
        issues.extend(self._check_value_ranges(topic, message, ts_raw))
        issues.extend(self._check_freshness(ts_raw))

        for issue in issues:
            self._issues_by_category[issue.category] += 1

        return issues

    def get_coverage_report(self) -> Dict[str, Any]:
        """Which components have produced data and which have not."""
        all_components = set(SCALPING_COMPONENTS.keys())
        missing = all_components - self._seen_components

        component_details: Dict[str, Dict[str, Any]] = {}
        for comp in all_components:
            meta = SCALPING_COMPONENTS[comp]
            component_details[comp] = {
                "stage": meta["stage"],
                "seen": comp in self._seen_components,
                "message_count": self._component_message_count.get(comp, 0),
                "last_seen": self._component_last_seen.get(comp),
                "output_topic": meta["output_topic"],
            }

        return {
            "total_messages": self._total_messages,
            "total_components": len(all_components),
            "seen_components": sorted(self._seen_components),
            "missing_components": sorted(missing),
            "coverage_pct": (
                round(len(self._seen_components) / len(all_components) * 100, 1)
                if all_components
                else 0.0
            ),
            "components": component_details,
            "issues_by_category": dict(self._issues_by_category),
        }

    # -- Internal checks ----------------------------------------------------

    def _check_schema(
        self, topic: str, message: dict, ts_raw: str
    ) -> List[ValidationIssue]:
        """Validate required fields from TOPIC_SCHEMAS."""
        required = TOPIC_SCHEMAS.get(topic)
        if required is None:
            return []

        missing = [f for f in required if f not in message]
        if missing:
            return [
                ValidationIssue(
                    severity="CRITICAL",
                    category="schema",
                    topic=topic,
                    message=f"Missing schema fields: {', '.join(missing)}",
                    timestamp=ts_raw,
                    details={"missing_fields": missing},
                )
            ]
        return []

    def _check_missing_fields(
        self, topic: str, message: dict, ts_raw: str
    ) -> List[ValidationIssue]:
        """Check for null or empty required fields."""
        required = TOPIC_SCHEMAS.get(topic, [])
        empty_fields = [
            f for f in required if f in message and _is_empty(message[f])
        ]
        if empty_fields:
            return [
                ValidationIssue(
                    severity="WARNING",
                    category="missing_field_value",
                    topic=topic,
                    message=f"Null/empty required fields: {', '.join(empty_fields)}",
                    timestamp=ts_raw,
                    details={"empty_fields": empty_fields},
                )
            ]
        return []

    def _check_value_ranges(
        self, topic: str, message: dict, ts_raw: str
    ) -> List[ValidationIssue]:
        """Validate domain-specific value constraints."""
        issues: List[ValidationIssue] = []

        # Confidence 0-100
        confidence = message.get("confidence")
        if confidence is not None:
            try:
                conf_val = float(confidence)
                if not 0 <= conf_val <= 100:
                    issues.append(
                        ValidationIssue(
                            severity="WARNING",
                            category="value_range",
                            topic=topic,
                            message=f"Confidence {conf_val} out of range [0, 100]",
                            timestamp=ts_raw,
                            details={"field": "confidence", "value": conf_val},
                        )
                    )
            except (TypeError, ValueError):
                issues.append(
                    ValidationIssue(
                        severity="WARNING",
                        category="value_range",
                        topic=topic,
                        message=f"Confidence is not numeric: {confidence!r}",
                        timestamp=ts_raw,
                        details={"field": "confidence", "value": str(confidence)},
                    )
                )

        # Prices > 0
        for price_field in ("ltp", "entry_price", "stop_loss", "target", "bid", "ask"):
            price = message.get(price_field)
            if price is not None:
                try:
                    if float(price) <= 0:
                        issues.append(
                            ValidationIssue(
                                severity="WARNING",
                                category="value_range",
                                topic=topic,
                                message=f"{price_field} must be > 0, got {price}",
                                timestamp=ts_raw,
                                details={"field": price_field, "value": price},
                            )
                        )
                except (TypeError, ValueError):
                    pass

        # Timestamp parseable
        raw_ts = message.get("timestamp")
        if raw_ts is not None and not _is_parseable_ts(raw_ts):
            issues.append(
                ValidationIssue(
                    severity="WARNING",
                    category="value_range",
                    topic=topic,
                    message=f"Timestamp not parseable: {raw_ts!r}",
                    timestamp=ts_raw,
                    details={"field": "timestamp", "value": str(raw_ts)},
                )
            )

        return issues

    def _check_freshness(self, ts_raw: str) -> List[ValidationIssue]:
        """Flag components that have not produced data recently."""
        now = time.time()
        threshold = self._settings.stale_data_threshold_sec
        issues: List[ValidationIssue] = []

        for comp, meta in SCALPING_COMPONENTS.items():
            last = self._component_last_seen.get(comp)
            if last is not None and (now - last) > threshold:
                issues.append(
                    ValidationIssue(
                        severity="WARNING",
                        category="freshness",
                        topic=meta["output_topic"],
                        message=(
                            f"Component {comp} stale: no data for "
                            f"{now - last:.1f}s (threshold {threshold}s)"
                        ),
                        timestamp=ts_raw,
                        details={
                            "component": comp,
                            "silent_sec": round(now - last, 2),
                            "threshold_sec": threshold,
                        },
                    )
                )

        return issues

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _resolve_component(topic: str, message: dict) -> Optional[str]:
        """Identify the component from the message or topic mapping."""
        # Prefer explicit component field
        comp = message.get("component")
        if comp and comp in SCALPING_COMPONENTS:
            return comp

        # Fall back: find any component whose output_topic matches
        for name, meta in SCALPING_COMPONENTS.items():
            if meta["output_topic"] == topic:
                return name

        return None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _is_empty(value: Any) -> bool:
    """Return True if *value* is None or an empty string."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _is_parseable_ts(raw: Any) -> bool:
    """Return True if *raw* can be interpreted as a timestamp."""
    if isinstance(raw, (int, float)):
        return True
    if isinstance(raw, str):
        try:
            float(raw)
            return True
        except ValueError:
            pass
        try:
            datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return True
        except (ValueError, TypeError):
            pass
    return False

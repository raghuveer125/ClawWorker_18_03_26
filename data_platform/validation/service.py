"""
Canonical Layer 2 service: validates ingested market snapshots before downstream use.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from data_platform.ingestion.models import (
    FuturesSnapshot,
    OptionChainSnapshot,
    OptionContractSnapshot,
    QuoteSnapshot,
    VIXSnapshot,
)
from data_platform.validation.models import ValidationIssue, ValidationReport


class ValidationService:
    """
    Layer 2 validation rules:
    - Schema check
    - Zero-price reject
    - Stale timestamp reject

    Schema (required fields + types) can be loaded from the market_field_schema
    DB table via load_schema_from_db().  Until that is called the service falls
    back to the hardcoded defaults defined in _schema_for_payload_type().
    """

    def __init__(
        self,
        max_staleness_seconds: int = 30,
        max_spread_pct: float = 5.0,
        min_iv: float = 0.01,
        max_iv: float = 500.0,
        max_oi_change_ratio: float = 5.0,
        max_delta_abs: float = 1.2,
        max_gamma: float = 10.0,
        max_theta_abs: float = 10000.0,
        max_vega: float = 10000.0,
    ) -> None:
        self._db_schema: dict[str, dict] | None = None
        if max_staleness_seconds < 1:
            raise ValueError("max_staleness_seconds must be >= 1")
        if min_iv < 0 or max_iv <= min_iv:
            raise ValueError("invalid IV range configuration")
        if max_oi_change_ratio <= 0:
            raise ValueError("max_oi_change_ratio must be > 0")
        if max_delta_abs <= 0 or max_gamma <= 0 or max_theta_abs <= 0 or max_vega <= 0:
            raise ValueError("greek sanity thresholds must be > 0")
        self._max_staleness = timedelta(seconds=max_staleness_seconds)
        self._max_spread_pct = max_spread_pct
        self._min_iv = min_iv
        self._max_iv = max_iv
        self._max_oi_change_ratio = max_oi_change_ratio
        self._max_delta_abs = max_delta_abs
        self._max_gamma = max_gamma
        self._max_theta_abs = max_theta_abs
        self._max_vega = max_vega

    def load_schema_from_db(self, config: Any) -> None:
        """
        Load field schema (required fields + types) from the market_field_schema
        DB table.  Once called, validate_payload_schema() uses the DB schema
        instead of the hardcoded fallbacks.

        config: FieldSchemaConfig instance (or any object accepted by
                sync_list_active_fields from bdts.db.field_schema)
        """
        from data_platform.db.field_schema import build_validation_schema, sync_list_active_fields
        fields = sync_list_active_fields(config)
        self._db_schema = build_validation_schema(fields)

    def validate(self, payload: Any, now: datetime | None = None) -> ValidationReport:
        timestamp_now = now or datetime.now(timezone.utc)
        issues: list[ValidationIssue] = []

        issues.extend(self._validate_schema(payload))
        issues.extend(self._validate_zero_price(payload))
        issues.extend(self._validate_market_sanity(payload))
        issues.extend(self._validate_stale_timestamp(payload, timestamp_now))

        return ValidationReport(
            passed=len(issues) == 0,
            issues=tuple(issues),
            validated_at=timestamp_now,
            payload_type=type(payload).__name__,
            payload=payload,
        )

    def validate_payload_schema(
        self,
        payload_type: str,
        payload: Mapping[str, Any],
        now: datetime | None = None,
    ) -> ValidationReport:
        """
        Strict schema validation for external ingestion payload contracts.

        Supported payload types:
        - tick
        - candle
        - option_chain
        """
        timestamp_now = now or datetime.now(timezone.utc)
        issues: list[ValidationIssue] = []
        normalized_type = payload_type.strip().lower()

        if not isinstance(payload, Mapping):
            issues.append(
                ValidationIssue(
                    code="INVALID_TYPE",
                    field="payload",
                    message="payload must be a mapping/object",
                )
            )
            return ValidationReport(
                passed=False,
                issues=tuple(issues),
                validated_at=timestamp_now,
                payload_type=normalized_type,
                payload=payload,
            )

        schema = self._schema_for_payload_type(normalized_type)
        if schema is None:
            issues.append(
                ValidationIssue(
                    code="INVALID_VALUE",
                    field="payload_type",
                    message=f"unsupported payload type '{payload_type}'",
                )
            )
            return ValidationReport(
                passed=False,
                issues=tuple(issues),
                validated_at=timestamp_now,
                payload_type=normalized_type,
                payload=payload,
            )

        issues.extend(self._validate_required_fields(payload, schema["required"]))
        issues.extend(self._validate_field_types(payload, schema["types"]))
        issues.extend(self._validate_field_values(normalized_type, payload))
        issues.extend(self._validate_payload_freshness(payload, timestamp_now))

        return ValidationReport(
            passed=len(issues) == 0,
            issues=tuple(issues),
            validated_at=timestamp_now,
            payload_type=normalized_type,
            payload=dict(payload),
        )

    def build_reject_event(
        self,
        payload_type: str,
        payload: Mapping[str, Any],
        report: ValidationReport,
        source: str = "unknown",
    ) -> dict[str, Any]:
        issue = report.issues[0] if report.issues else ValidationIssue(
            code="INVALID_VALUE",
            field="payload",
            message="validation failed",
        )
        return {
            "timestamp": int((report.validated_at or datetime.now(timezone.utc)).timestamp()),
            "payload_type": payload_type,
            "original_payload": dict(payload),
            "error_code": issue.code,
            "error_message": issue.message,
            "source": source,
        }

    def _schema_for_payload_type(self, payload_type: str) -> dict[str, Any] | None:
        if self._db_schema is not None:
            return self._db_schema.get(payload_type)
        if payload_type == "tick":
            return {
                "required": ("timestamp", "symbol", "ltp", "volume", "source"),
                "types": {
                    "timestamp": (int, float),
                    "symbol": (str,),
                    "ltp": (int, float),
                    "volume": (int, float),
                    "bid": (int, float),
                    "ask": (int, float),
                    "source": (str,),
                },
            }
        if payload_type == "candle":
            return {
                "required": ("timestamp", "symbol", "interval", "open", "high", "low", "close", "volume", "source"),
                "types": {
                    "timestamp": (int, float),
                    "symbol": (str,),
                    "interval": (str,),
                    "open": (int, float),
                    "high": (int, float),
                    "low": (int, float),
                    "close": (int, float),
                    "volume": (int, float),
                    "source": (str,),
                },
            }
        if payload_type == "option_chain":
            return {
                "required": ("timestamp", "index", "expiry", "strike", "option_type", "ltp", "oi", "oi_change", "volume"),
                "types": {
                    "timestamp": (int, float),
                    "index": (str,),
                    "expiry": (str,),
                    "strike": (int, float),
                    "option_type": (str,),
                    "ltp": (int, float),
                    "oi": (int,),
                    "oi_change": (int,),
                    "volume": (int,),
                },
            }
        return None

    def _validate_required_fields(
        self,
        payload: Mapping[str, Any],
        required_fields: tuple[str, ...],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field_name in required_fields:
            if field_name not in payload:
                issues.append(
                    ValidationIssue(
                        code="MISSING_FIELD",
                        field=field_name,
                        message=f"required field '{field_name}' is missing",
                    )
                )
                continue
            value = payload.get(field_name)
            if value is None:
                issues.append(
                    ValidationIssue(
                        code="MISSING_FIELD",
                        field=field_name,
                        message=f"required field '{field_name}' is null",
                    )
                )
                continue
            if isinstance(value, str) and not value.strip():
                issues.append(
                    ValidationIssue(
                        code="MISSING_FIELD",
                        field=field_name,
                        message=f"required field '{field_name}' is blank",
                    )
                )
        return issues

    def _validate_field_types(
        self,
        payload: Mapping[str, Any],
        expected_types: dict[str, tuple[type, ...]],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field_name, valid_types in expected_types.items():
            if field_name not in payload:
                continue
            value = payload[field_name]
            if value is None:
                continue
            if not isinstance(value, valid_types):
                expected = ", ".join(t.__name__ for t in valid_types)
                issues.append(
                    ValidationIssue(
                        code="INVALID_TYPE",
                        field=field_name,
                        message=f"field '{field_name}' must be of type: {expected}",
                    )
                )
        return issues

    def _validate_field_values(
        self,
        payload_type: str,
        payload: Mapping[str, Any],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if payload_type == "tick":
            if "ltp" in payload and float(payload["ltp"]) <= 0:
                issues.append(ValidationIssue(code="INVALID_VALUE", field="ltp", message="ltp must be > 0"))
            if "volume" in payload and float(payload["volume"]) < 0:
                issues.append(ValidationIssue(code="INVALID_VALUE", field="volume", message="volume must be >= 0"))
            if "bid" in payload and float(payload["bid"]) < 0:
                issues.append(ValidationIssue(code="INVALID_VALUE", field="bid", message="bid must be >= 0"))
            if "ask" in payload and float(payload["ask"]) < 0:
                issues.append(ValidationIssue(code="INVALID_VALUE", field="ask", message="ask must be >= 0"))
        elif payload_type == "candle":
            for field_name in ("open", "high", "low", "close"):
                if field_name in payload and float(payload[field_name]) <= 0:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_VALUE",
                            field=field_name,
                            message=f"{field_name} must be > 0",
                        )
                    )
            if "volume" in payload and float(payload["volume"]) < 0:
                issues.append(ValidationIssue(code="INVALID_VALUE", field="volume", message="volume must be >= 0"))
            allowed_intervals = {"1m", "3m", "5m", "15m"}
            interval = str(payload.get("interval", "")).lower()
            if interval and interval not in allowed_intervals:
                issues.append(
                    ValidationIssue(
                        code="INVALID_VALUE",
                        field="interval",
                        message=f"interval must be one of {sorted(allowed_intervals)}",
                    )
                )
        elif payload_type == "option_chain":
            if "strike" in payload and float(payload["strike"]) <= 0:
                issues.append(ValidationIssue(code="INVALID_VALUE", field="strike", message="strike must be > 0"))
            if "ltp" in payload and float(payload["ltp"]) <= 0:
                issues.append(ValidationIssue(code="INVALID_VALUE", field="ltp", message="ltp must be > 0"))
            if "oi" in payload and int(payload["oi"]) < 0:
                issues.append(ValidationIssue(code="INVALID_VALUE", field="oi", message="oi must be >= 0"))
            if "volume" in payload and int(payload["volume"]) < 0:
                issues.append(ValidationIssue(code="INVALID_VALUE", field="volume", message="volume must be >= 0"))
            option_type = str(payload.get("option_type", "")).upper()
            if option_type and option_type not in {"CE", "PE"}:
                issues.append(
                    ValidationIssue(
                        code="INVALID_VALUE",
                        field="option_type",
                        message="option_type must be CE or PE",
                    )
                )
        return issues

    def _validate_payload_freshness(self, payload: Mapping[str, Any], now: datetime) -> list[ValidationIssue]:
        raw_ts = payload.get("timestamp")
        if raw_ts is None:
            return []
        try:
            if isinstance(raw_ts, (int, float)):
                ts_val = float(raw_ts)
                ts_val = ts_val / 1000.0 if ts_val > 10_000_000_000 else ts_val
                ts = datetime.fromtimestamp(ts_val, tz=timezone.utc)
            elif isinstance(raw_ts, str):
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            elif isinstance(raw_ts, datetime):
                ts = raw_ts if raw_ts.tzinfo else raw_ts.replace(tzinfo=timezone.utc)
            else:
                return [
                    ValidationIssue(
                        code="INVALID_TYPE",
                        field="timestamp",
                        message="timestamp must be epoch seconds/ms, ISO string, or datetime",
                    )
                ]
        except Exception:
            return [
                ValidationIssue(
                    code="INVALID_VALUE",
                    field="timestamp",
                    message="timestamp parsing failed",
                )
            ]

        if now - ts > self._max_staleness:
            return [
                ValidationIssue(
                    code="STALE_DATA",
                    field="timestamp",
                    message=f"payload older than {int(self._max_staleness.total_seconds())}s",
                )
            ]
        return []

    def _validate_schema(self, payload: Any) -> list[ValidationIssue]:
        if not is_dataclass(payload):
            return [
                ValidationIssue(
                    code="schema.invalid_type",
                    field="payload",
                    message="payload must be a dataclass snapshot",
                )
            ]

        issues: list[ValidationIssue] = []
        for f in fields(payload):
            value = getattr(payload, f.name)
            if value is None:
                issues.append(
                    ValidationIssue(
                        code="schema.missing_field",
                        field=f.name,
                        message=f"required field '{f.name}' is missing",
                    )
                )
                continue

            if isinstance(value, str) and not value.strip():
                issues.append(
                    ValidationIssue(
                        code="schema.blank_field",
                        field=f.name,
                        message=f"required string field '{f.name}' is blank",
                    )
                )
        return issues

    def _validate_zero_price(self, payload: Any) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if isinstance(payload, QuoteSnapshot):
            if payload.ltp <= 0:
                issues.append(ValidationIssue(code="price.zero_or_negative", field="ltp", message="ltp must be > 0"))
            if payload.bid <= 0:
                issues.append(ValidationIssue(code="price.zero_or_negative", field="bid", message="bid must be > 0"))
            if payload.ask <= 0:
                issues.append(ValidationIssue(code="price.zero_or_negative", field="ask", message="ask must be > 0"))

        elif isinstance(payload, VIXSnapshot):
            if payload.value <= 0:
                issues.append(ValidationIssue(code="price.zero_or_negative", field="value", message="vix must be > 0"))

        elif isinstance(payload, FuturesSnapshot):
            if payload.ltp <= 0:
                issues.append(ValidationIssue(code="price.zero_or_negative", field="ltp", message="futures ltp must be > 0"))

        elif isinstance(payload, OptionContractSnapshot):
            if payload.ltp <= 0:
                issues.append(ValidationIssue(code="price.zero_or_negative", field="ltp", message="option ltp must be > 0"))
            if payload.strike <= 0:
                issues.append(ValidationIssue(code="price.zero_or_negative", field="strike", message="strike must be > 0"))

        elif isinstance(payload, OptionChainSnapshot):
            if len(payload.contracts) == 0:
                issues.append(
                    ValidationIssue(
                        code="schema.empty_contracts",
                        field="contracts",
                        message="option chain must contain at least one contract",
                    )
                )
            for idx, contract in enumerate(payload.contracts):
                contract_issues = self._validate_zero_price(contract)
                issues.extend(
                    ValidationIssue(
                        code=i.code,
                        field=f"contracts[{idx}].{i.field}" if i.field else f"contracts[{idx}]",
                        message=i.message,
                    )
                    for i in contract_issues
                )
        return issues

    def _validate_market_sanity(self, payload: Any) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if isinstance(payload, QuoteSnapshot):
            if payload.ask < payload.bid:
                issues.append(
                    ValidationIssue(
                        code="sanity.crossed_market",
                        field="ask",
                        message="ask must be >= bid",
                    )
                )
            if payload.ltp > 0:
                spread_pct = ((payload.ask - payload.bid) / payload.ltp) * 100.0
                if spread_pct > self._max_spread_pct:
                    issues.append(
                        ValidationIssue(
                            code="sanity.wide_spread",
                            field="spread",
                            message=f"spread {spread_pct:.2f}% exceeds max {self._max_spread_pct:.2f}%",
                        )
                    )

        elif isinstance(payload, OptionContractSnapshot):
            if payload.iv < self._min_iv or payload.iv > self._max_iv:
                issues.append(
                    ValidationIssue(
                        code="sanity.iv_out_of_range",
                        field="iv",
                        message=f"iv must be in [{self._min_iv}, {self._max_iv}]",
                    )
                )
            if payload.oi < 0 or payload.volume < 0:
                issues.append(
                    ValidationIssue(
                        code="sanity.negative_oi_or_volume",
                        field="oi",
                        message="oi and volume must be >= 0",
                    )
                )
            dynamic_limit = max(int(payload.oi * self._max_oi_change_ratio), 1000)
            if abs(payload.oi_change) > dynamic_limit:
                issues.append(
                    ValidationIssue(
                        code="sanity.oi_change_outlier",
                        field="oi_change",
                        message=f"abs(oi_change) exceeds dynamic limit {dynamic_limit}",
                    )
                )
            if abs(payload.delta) > self._max_delta_abs:
                issues.append(
                    ValidationIssue(
                        code="sanity.delta_out_of_range",
                        field="delta",
                        message=f"abs(delta) must be <= {self._max_delta_abs}",
                    )
                )
            if payload.gamma < 0 or payload.gamma > self._max_gamma:
                issues.append(
                    ValidationIssue(
                        code="sanity.gamma_out_of_range",
                        field="gamma",
                        message=f"gamma must be in [0, {self._max_gamma}]",
                    )
                )
            if abs(payload.theta) > self._max_theta_abs:
                issues.append(
                    ValidationIssue(
                        code="sanity.theta_out_of_range",
                        field="theta",
                        message=f"abs(theta) must be <= {self._max_theta_abs}",
                    )
                )
            if payload.vega < 0 or payload.vega > self._max_vega:
                issues.append(
                    ValidationIssue(
                        code="sanity.vega_out_of_range",
                        field="vega",
                        message=f"vega must be in [0, {self._max_vega}]",
                    )
                )

        elif isinstance(payload, OptionChainSnapshot):
            for idx, contract in enumerate(payload.contracts):
                contract_issues = self._validate_market_sanity(contract)
                issues.extend(
                    ValidationIssue(
                        code=i.code,
                        field=f"contracts[{idx}].{i.field}" if i.field else f"contracts[{idx}]",
                        message=i.message,
                    )
                    for i in contract_issues
                )
        return issues

    def _validate_stale_timestamp(self, payload: Any, now: datetime) -> list[ValidationIssue]:
        ts = getattr(payload, "timestamp", None)
        if not isinstance(ts, datetime):
            return [
                ValidationIssue(
                    code="schema.invalid_timestamp",
                    field="timestamp",
                    message="timestamp must be a datetime",
                )
            ]

        if ts.tzinfo is None:
            return [
                ValidationIssue(
                    code="schema.invalid_timestamp",
                    field="timestamp",
                    message="timestamp must be timezone-aware",
                )
            ]

        if now - ts > self._max_staleness:
            return [
                ValidationIssue(
                    code="timestamp.stale",
                    field="timestamp",
                    message=f"payload older than {int(self._max_staleness.total_seconds())}s",
                )
            ]
        return []

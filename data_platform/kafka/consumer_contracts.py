"""
Consumer-side contract checks for Layer 3 events.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_VALID_STREAMS = {"quote", "option_chain", "vix", "futures"}


@dataclass(frozen=True)
class ContractViolation:
    code: str
    field: str
    message: str


def validate_market_event(event: dict[str, Any]) -> list[ContractViolation]:
    violations: list[ContractViolation] = []

    for required in ("index", "stream", "collected_at", "payload"):
        if required not in event:
            violations.append(
                ContractViolation(
                    code="contract.missing_field",
                    field=required,
                    message=f"missing required field '{required}'",
                )
            )
    stream = str(event.get("stream", ""))
    if stream and stream not in _VALID_STREAMS:
        violations.append(
            ContractViolation(
                code="contract.invalid_stream",
                field="stream",
                message=f"invalid stream '{stream}'",
            )
        )

    payload = event.get("payload")
    if payload is not None and not isinstance(payload, dict):
        violations.append(
            ContractViolation(
                code="contract.invalid_payload",
                field="payload",
                message="payload must be an object",
            )
        )

    if isinstance(payload, dict):
        if stream == "quote":
            for field in ("symbol", "ltp", "bid", "ask", "timestamp"):
                if field not in payload:
                    violations.append(ContractViolation("contract.missing_payload_field", f"payload.{field}", "required"))
        elif stream == "option_chain":
            for field in ("index", "timestamp", "contracts"):
                if field not in payload:
                    violations.append(ContractViolation("contract.missing_payload_field", f"payload.{field}", "required"))
        elif stream == "vix":
            for field in ("value", "timestamp"):
                if field not in payload:
                    violations.append(ContractViolation("contract.missing_payload_field", f"payload.{field}", "required"))
        elif stream == "futures":
            for field in ("symbol", "ltp", "timestamp"):
                if field not in payload:
                    violations.append(ContractViolation("contract.missing_payload_field", f"payload.{field}", "required"))

    return violations


def validate_rejected_event(event: dict[str, Any]) -> list[ContractViolation]:
    violations: list[ContractViolation] = []

    for required in ("index", "stream", "collected_at", "reason", "issues"):
        if required not in event:
            violations.append(
                ContractViolation(
                    code="contract.missing_field",
                    field=required,
                    message=f"missing required field '{required}'",
                )
            )

    issues = event.get("issues")
    if issues is not None and not isinstance(issues, list):
        violations.append(
            ContractViolation(
                code="contract.invalid_issues",
                field="issues",
                message="issues must be a list",
            )
        )
    return violations

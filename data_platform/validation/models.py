"""
Validation result models for canonical Layer 2 (Data Validation Service).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    field: str = ""


@dataclass(frozen=True)
class ValidationReport:
    passed: bool
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)
    validated_at: datetime | None = None
    payload_type: str = ""
    payload: Any = None

    @property
    def primary_reason(self) -> str:
        if self.passed or not self.issues:
            return ""
        return self.issues[0].code


@dataclass(frozen=True)
class RejectEvent:
    timestamp: int
    payload_type: str
    original_payload: dict[str, Any]
    error_code: str
    error_message: str
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "payload_type": self.payload_type,
            "original_payload": self.original_payload,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "source": self.source,
        }

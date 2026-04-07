"""
Shared data models for the scalping validation subsystem.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationIssue:
    """A single validation finding with severity, context, and metadata."""

    severity: str   # "CRITICAL" | "WARNING" | "INFO"
    category: str
    topic: str
    message: str
    timestamp: str
    details: dict = field(default_factory=dict)

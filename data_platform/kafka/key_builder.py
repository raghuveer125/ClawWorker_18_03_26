"""
Partition key strategy for Layer 3 topics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class KafkaKeyBuilder:
    """Build deterministic keys to preserve per-instrument ordering."""

    def for_payload(self, index: str, stream: str, payload: dict[str, Any]) -> str:
        symbol = str(payload.get("symbol", "")).strip()
        strike = payload.get("strike")
        side = str(payload.get("side", "")).strip()
        expiry = str(payload.get("expiry", "")).strip()

        if symbol:
            return f"{index}:{stream}:{symbol}"
        if strike is not None and side:
            return f"{index}:{stream}:{strike}:{side}:{expiry or 'na'}"
        return f"{index}:{stream}"

    def for_rejected(self, index: str, stream: str, reason: str) -> str:
        return f"{index}:{stream}:rejected:{reason or 'unknown'}"

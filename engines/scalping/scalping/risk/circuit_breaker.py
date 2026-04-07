"""Production circuit breaker — progressive loss cluster response.

Three levels:
  L1: Reduce position size to 50%
  L2: Pause new entries for cooldown_minutes
  L3: Full halt (requires manual reset)

Deterministic, replay-safe, idempotent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("scalping.circuit_breaker")


class CircuitLevel(IntEnum):
    NORMAL = 0
    L1_REDUCE = 1
    L2_PAUSE = 2
    L3_HALT = 3


@dataclass
class LossEvent:
    timestamp: datetime
    pnl: float
    position_id: str
    exit_reason: str


class CircuitBreaker:
    """Progressive circuit breaker with loss cluster detection.

    L1 (1 loss in window):     size_scale = 0.5
    L2 (2 losses in window):   pause entries for cooldown
    L3 (3+ losses in window):  full halt, manual reset required

    Window = 30 minutes (configurable).
    Cooldown = 15 minutes for L2, 30 minutes for L3.
    """

    def __init__(
        self,
        window_minutes: int = 30,
        l1_losses: int = 1,
        l2_losses: int = 2,
        l3_losses: int = 3,
        l1_size_scale: float = 0.5,
        l2_cooldown_minutes: int = 15,
        l3_cooldown_minutes: int = 30,
    ) -> None:
        self.window_minutes = window_minutes
        self.l1_losses = l1_losses
        self.l2_losses = l2_losses
        self.l3_losses = l3_losses
        self.l1_size_scale = l1_size_scale
        self.l2_cooldown_minutes = l2_cooldown_minutes
        self.l3_cooldown_minutes = l3_cooldown_minutes

        self._loss_events: List[LossEvent] = []
        self._level: CircuitLevel = CircuitLevel.NORMAL
        self._level_set_at: Optional[datetime] = None
        self._trigger_count: int = 0
        self._l3_requires_manual_reset: bool = False

    @property
    def level(self) -> CircuitLevel:
        return self._level

    @property
    def size_scale(self) -> float:
        if self._level >= CircuitLevel.L1_REDUCE:
            return self.l1_size_scale
        return 1.0

    @property
    def entries_blocked(self) -> bool:
        return self._level >= CircuitLevel.L2_PAUSE

    @property
    def fully_halted(self) -> bool:
        return self._level >= CircuitLevel.L3_HALT

    def record_loss(self, pnl: float, position_id: str, exit_reason: str,
                    now: Optional[datetime] = None) -> CircuitLevel:
        """Record a losing trade and evaluate circuit level."""
        now = now or datetime.now()
        self._loss_events.append(LossEvent(
            timestamp=now, pnl=pnl, position_id=position_id, exit_reason=exit_reason,
        ))
        return self._evaluate(now)

    def record_win(self, now: Optional[datetime] = None) -> CircuitLevel:
        """Record a winning trade — can de-escalate from L1."""
        now = now or datetime.now()
        if self._level == CircuitLevel.L1_REDUCE:
            recent = self._recent_losses(now)
            if len(recent) == 0:
                self._set_level(CircuitLevel.NORMAL, now)
        return self._level

    def check(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Check current circuit state. Call every cycle."""
        now = now or datetime.now()

        # Auto-reset L2 after cooldown
        if self._level == CircuitLevel.L2_PAUSE and self._level_set_at:
            elapsed = (now - self._level_set_at).total_seconds() / 60
            if elapsed >= self.l2_cooldown_minutes:
                self._set_level(CircuitLevel.L1_REDUCE, now)

        return {
            "level": self._level.name,
            "level_int": int(self._level),
            "size_scale": self.size_scale,
            "entries_blocked": self.entries_blocked,
            "fully_halted": self.fully_halted,
            "recent_losses": len(self._recent_losses(now)),
            "trigger_count": self._trigger_count,
            "cooldown_remaining_min": self._cooldown_remaining(now),
        }

    def force_reset(self) -> None:
        """Manual reset from L3. Use only after human review."""
        self._level = CircuitLevel.NORMAL
        self._level_set_at = None
        self._l3_requires_manual_reset = False
        self._loss_events.clear()

    def inject_into_context(self, context: Dict[str, Any], now: Optional[datetime] = None) -> None:
        """Inject circuit breaker state into engine context for validate_entry/compute_position_size."""
        state = self.check(now)
        context["_circuit_breaker"] = state
        context["_circuit_size_scale"] = state["size_scale"]
        context["_circuit_entries_blocked"] = state["entries_blocked"]

    # ── internal ──

    def _recent_losses(self, now: datetime) -> List[LossEvent]:
        cutoff = now - timedelta(minutes=self.window_minutes)
        return [e for e in self._loss_events if e.timestamp >= cutoff]

    def _evaluate(self, now: datetime) -> CircuitLevel:
        recent = self._recent_losses(now)
        count = len(recent)
        old_level = self._level

        if count >= self.l3_losses:
            self._set_level(CircuitLevel.L3_HALT, now)
        elif count >= self.l2_losses:
            if self._level < CircuitLevel.L2_PAUSE:
                self._set_level(CircuitLevel.L2_PAUSE, now)
        elif count >= self.l1_losses:
            if self._level < CircuitLevel.L1_REDUCE:
                self._set_level(CircuitLevel.L1_REDUCE, now)

        if self._level > old_level:
            self._trigger_count += 1
            self._log_trigger(now, count)

        return self._level

    def _set_level(self, level: CircuitLevel, now: datetime) -> None:
        self._level = level
        self._level_set_at = now
        if level == CircuitLevel.L3_HALT:
            self._l3_requires_manual_reset = True

    def _cooldown_remaining(self, now: datetime) -> float:
        if not self._level_set_at:
            return 0
        elapsed = (now - self._level_set_at).total_seconds() / 60
        if self._level == CircuitLevel.L2_PAUSE:
            return max(0, self.l2_cooldown_minutes - elapsed)
        if self._level == CircuitLevel.L3_HALT:
            return max(0, self.l3_cooldown_minutes - elapsed)
        return 0

    def _log_trigger(self, now: datetime, loss_count: int) -> None:
        record = {
            "event": "circuit_breaker_trigger",
            "level": self._level.name,
            "loss_count_in_window": loss_count,
            "window_minutes": self.window_minutes,
            "trigger_count": self._trigger_count,
            "size_scale": self.size_scale,
            "entries_blocked": self.entries_blocked,
            "timestamp": now.isoformat(),
        }
        logger.warning(json.dumps(record, default=str))
        try:
            from ..risk_engine import trade_logger
            trade_logger.log_decision(record)
        except Exception:
            pass

"""EOE state machine: OFF → WATCH → ARMED → ACTIVE → COOLDOWN → OFF."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class EOEState:
    current: str = "OFF"
    entered_at: Optional[datetime] = None
    session_high: float = 0.0
    session_low: float = float("inf")
    morning_direction: str = ""
    reversal_pct: float = 0.0
    bos_bullish_30min: int = 0
    bos_bearish_30min: int = 0
    active_duration_min: float = 0.0
    vwap_cross_time: Optional[datetime] = None


class EOEStateMachine:
    """Deterministic state machine for expiry opportunity detection."""

    WATCH_START = dtime(10, 30)
    LATE_CUTOFF = dtime(14, 50)
    SESSION_EXIT = dtime(15, 10)
    ARMED_TIMEOUT_MIN = 60
    ACTIVE_TIMEOUT_MIN = 90
    COOLDOWN_MIN = 30
    REVERSAL_THRESHOLD = 0.015  # 1.5%
    BOS_REQUIRED = 2
    VWAP_HOLD_SEC = 300  # 5 minutes

    def __init__(self, is_expiry: bool = False) -> None:
        self.is_expiry = is_expiry
        self.state = EOEState()
        self._bos_history: List[Dict[str, Any]] = []
        self._vwap_above_since: Optional[datetime] = None
        self._transitions: List[Dict[str, Any]] = []

    @property
    def current(self) -> str:
        return self.state.current

    def tick(
        self,
        now: datetime,
        ltp: float,
        vwap: float,
        open_price: float,
        prev_close: float,
        bos_events: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Process one cycle. Returns transition dict if state changed, else None."""
        bos_events = bos_events or []
        self.state.session_high = max(self.state.session_high, ltp)
        if ltp > 0:
            self.state.session_low = min(self.state.session_low, ltp)

        # Track BOS history (rolling 30-min window)
        for b in bos_events:
            b_copy = dict(b)
            b_copy["_tick_time"] = now
            self._bos_history.append(b_copy)
        cutoff_30 = now - timedelta(minutes=30)
        self._bos_history = [b for b in self._bos_history if b.get("_tick_time", now) >= cutoff_30]

        reversal_dir = "bullish" if self.state.morning_direction == "bearish" else "bearish"
        self.state.bos_bullish_30min = sum(
            1 for b in self._bos_history if "bullish" in str(b.get("break_type", "")).lower()
        )
        self.state.bos_bearish_30min = sum(
            1 for b in self._bos_history if "bearish" in str(b.get("break_type", "")).lower()
        )

        # Compute reversal from extreme
        if self.state.morning_direction == "bearish" and self.state.session_low > 0:
            self.state.reversal_pct = (ltp - self.state.session_low) / self.state.session_low
        elif self.state.morning_direction == "bullish" and self.state.session_high > 0:
            self.state.reversal_pct = (self.state.session_high - ltp) / self.state.session_high
        else:
            self.state.reversal_pct = 0.0

        # VWAP cross tracking
        above_vwap = (self.state.morning_direction == "bearish" and ltp > vwap) or \
                     (self.state.morning_direction == "bullish" and ltp < vwap)
        if above_vwap:
            if self._vwap_above_since is None:
                self._vwap_above_since = now
        else:
            self._vwap_above_since = None
        vwap_held = (
            self._vwap_above_since is not None
            and (now - self._vwap_above_since).total_seconds() >= self.VWAP_HOLD_SEC
        )

        # BOS count in reversal direction
        if reversal_dir == "bullish":
            reversal_bos = self.state.bos_bullish_30min
        else:
            reversal_bos = self.state.bos_bearish_30min

        # ── State transitions ──
        old = self.state.current

        if old == "OFF":
            if self.is_expiry and now.time() >= self.WATCH_START:
                self._set("WATCH", now)

        elif old == "WATCH":
            if not self.state.morning_direction and now.time() >= self.WATCH_START:
                self.state.morning_direction = "bearish" if ltp < open_price else "bullish"

            if self.state.reversal_pct >= self.REVERSAL_THRESHOLD or vwap_held:
                self._set("ARMED", now)

        elif old == "ARMED":
            armed_min = (now - (self.state.entered_at or now)).total_seconds() / 60
            if armed_min > self.ARMED_TIMEOUT_MIN:
                self._set("WATCH", now)
            elif reversal_bos >= self.BOS_REQUIRED:
                self._set("ACTIVE", now)

        elif old == "ACTIVE":
            self.state.active_duration_min = (now - (self.state.entered_at or now)).total_seconds() / 60
            if self.state.active_duration_min > self.ACTIVE_TIMEOUT_MIN:
                self._set("COOLDOWN", now)

        elif old == "COOLDOWN":
            cd_min = (now - (self.state.entered_at or now)).total_seconds() / 60
            if cd_min > self.COOLDOWN_MIN or now.time() >= self.LATE_CUTOFF:
                self._set("OFF", now)

        if now.time() >= self.LATE_CUTOFF and self.state.current not in ("OFF", "COOLDOWN"):
            self._set("OFF", now)

        if self.state.current != old:
            transition = {
                "timestamp": now.isoformat(),
                "from_state": old,
                "to_state": self.state.current,
                "sensex_ltp": ltp,
                "session_high": self.state.session_high,
                "session_low": self.state.session_low,
                "vwap": vwap,
                "pct_from_extreme": round(self.state.reversal_pct * 100, 2),
                "bos_count_30min": reversal_bos,
                "bos_direction": reversal_dir,
            }
            self._transitions.append(transition)
            return transition
        return None

    def _set(self, state: str, now: datetime) -> None:
        self.state.current = state
        self.state.entered_at = now

    @property
    def transitions(self) -> List[Dict[str, Any]]:
        return list(self._transitions)

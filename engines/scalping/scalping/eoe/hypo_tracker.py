"""Hypothetical trade tracker for EOE shadow testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .tradability import spread_trap


@dataclass
class HypoTrade:
    entry_time: datetime
    strike: int
    option_type: str
    entry_premium: float
    entry_bid: float
    entry_ask: float
    entry_spread_pct: float
    entry_bid_qty: int = 0
    entry_ask_qty: int = 0
    tradable_at_entry: bool = True

    # Live tracking
    peak_premium: float = 0.0
    peak_time: Optional[datetime] = None
    mae_premium: float = 0.0  # Will be set to entry on creation
    current_premium: float = 0.0
    _peak_3x_start: Optional[datetime] = None
    peak_sustained_60s: bool = False

    # Exit
    exit_premium: float = 0.0
    exit_time: Optional[datetime] = None
    exit_reason: str = ""
    exit_spread_pct: float = 0.0

    # Computed
    payoff_multiple: float = 0.0
    mfe_multiple: float = 0.0
    mae_multiple: float = 0.0
    hold_time_min: float = 0.0
    result: str = ""
    round_trip_spread_cost: float = 0.0
    is_spread_trap: bool = False

    # Scaled exit tracking
    _qty_remaining_pct: float = 100.0
    _partial_exits: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.peak_premium = self.entry_premium
        self.mae_premium = self.entry_premium
        self.current_premium = self.entry_premium

    def update(self, current: float, now: datetime) -> Optional[str]:
        """Update with current premium. Returns exit_reason if exit triggered."""
        if self.exit_time is not None:
            return None

        self.current_premium = current

        # MFE update
        if current > self.peak_premium:
            self.peak_premium = current
            self.peak_time = now

        # MAE update
        if current < self.mae_premium:
            self.mae_premium = current

        # Sustained 3x check
        if current >= self.entry_premium * 3:
            if self._peak_3x_start is None:
                self._peak_3x_start = now
            elif (now - self._peak_3x_start).total_seconds() >= 60:
                self.peak_sustained_60s = True
        else:
            self._peak_3x_start = None

        # ── Exit checks ──

        # Hard SL: premium drops 50%
        if current <= self.entry_premium * 0.5:
            return self._close(current, now, "hard_sl")

        # Time SL: 30 min and losing
        hold = (now - self.entry_time).total_seconds() / 60
        if hold > 30 and current < self.entry_premium:
            return self._close(current, now, "time_sl")

        # Scaled exit simulation
        multiple = current / self.entry_premium if self.entry_premium > 0 else 0
        if multiple >= 3 and self._qty_remaining_pct > 70:
            self._partial_exit(30, current, now, "scaled_3x")
        if multiple >= 5 and self._qty_remaining_pct > 40:
            self._partial_exit(30, current, now, "scaled_5x")
        if multiple >= 10 and self._qty_remaining_pct > 20:
            self._partial_exit(20, current, now, "scaled_10x")

        # Trail stop for remaining (after 5x: trail at -20%)
        if self._qty_remaining_pct < 100 and self.peak_premium > 0:
            trail_pct = 0.20 if multiple < 10 else 0.15 if multiple < 20 else 0.10
            trail_stop = self.peak_premium * (1 - trail_pct)
            if current < trail_stop:
                return self._close(current, now, "trail_stop")

        return None

    def force_close(self, current: float, now: datetime) -> None:
        """Force close at session end."""
        if self.exit_time is None:
            self._close(current, now, "session_close")

    def _partial_exit(self, pct: float, premium: float, now: datetime, reason: str) -> None:
        self._qty_remaining_pct -= pct
        self._partial_exits.append({
            "time": now.isoformat(), "pct": pct, "premium": premium, "reason": reason
        })

    def _close(self, premium: float, now: datetime, reason: str) -> str:
        self.exit_premium = premium
        self.exit_time = now
        self.exit_reason = reason
        self.exit_spread_pct = self.entry_spread_pct  # Assume similar spread
        self.hold_time_min = (now - self.entry_time).total_seconds() / 60
        self.payoff_multiple = premium / self.entry_premium if self.entry_premium > 0 else 0
        self.mfe_multiple = self.peak_premium / self.entry_premium if self.entry_premium > 0 else 0
        self.mae_multiple = self.mae_premium / self.entry_premium if self.entry_premium > 0 else 0
        self.result = "WIN" if premium > self.entry_premium else "LOSS"

        entry_cost = self.entry_spread_pct / 100 * self.entry_premium / 2
        exit_cost = self.exit_spread_pct / 100 * self.exit_premium / 2
        self.round_trip_spread_cost = entry_cost + exit_cost
        profit = premium - self.entry_premium
        self.is_spread_trap = spread_trap(
            self.entry_spread_pct, self.entry_premium,
            self.exit_spread_pct, self.exit_premium,
        )
        return reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_time": self.entry_time.isoformat(),
            "strike": self.strike,
            "option_type": self.option_type,
            "entry_premium": self.entry_premium,
            "entry_bid": self.entry_bid,
            "entry_ask": self.entry_ask,
            "entry_spread_pct": self.entry_spread_pct,
            "entry_bid_qty": self.entry_bid_qty,
            "entry_ask_qty": self.entry_ask_qty,
            "tradable_at_entry": self.tradable_at_entry,
            "peak_premium": self.peak_premium,
            "peak_time": self.peak_time.isoformat() if self.peak_time else "",
            "peak_sustained_60s": self.peak_sustained_60s,
            "exit_premium": self.exit_premium,
            "exit_time": self.exit_time.isoformat() if self.exit_time else "",
            "exit_reason": self.exit_reason,
            "payoff_multiple": round(self.payoff_multiple, 2),
            "mfe_multiple": round(self.mfe_multiple, 2),
            "mae_multiple": round(self.mae_multiple, 2),
            "hold_time_min": round(self.hold_time_min, 1),
            "result": self.result,
            "round_trip_spread_cost": round(self.round_trip_spread_cost, 2),
            "spread_trap": self.is_spread_trap,
        }

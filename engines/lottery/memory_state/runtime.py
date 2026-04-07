"""In-memory runtime state — ring buffers, deques, and live state tracking.

Holds all transient state needed during a trading session.
Not persisted — rebuilt on startup from DB if needed.
Symbol-agnostic: one RuntimeState per instrument instance.
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..config import LotteryConfig
from ..models import (
    CalculatedSnapshot,
    ChainSnapshot,
    DebugTrace,
    MachineState,
    PaperTrade,
    QualityReport,
    Side,
    SignalEvent,
    TradeStatus,
)

logger = logging.getLogger(__name__)

# Default buffer sizes
_MAX_SPOT_HISTORY = 300       # 5 minutes at 1s polling
_MAX_SNAPSHOT_HASHES = 20
_MAX_SIGNALS = 100
_MAX_DEBUG_EVENTS = 50
_MAX_REJECTIONS = 100
_MAX_TRADES = 500


@dataclass
class RuntimeState:
    """Complete in-memory state for one instrument's lottery pipeline.

    All collections use bounded deques to prevent memory growth.
    """
    symbol: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ── Latest snapshots ───────────────────────────────────────────
    last_spot_ltp: Optional[float] = None
    last_spot_timestamp: Optional[datetime] = None
    last_chain_snapshot: Optional[ChainSnapshot] = None
    last_calculated: Optional[CalculatedSnapshot] = None
    last_quality_report: Optional[QualityReport] = None

    # ── Last signal / selection ────────────────────────────────────
    last_signal: Optional[SignalEvent] = None
    last_selected_strike: Optional[float] = None
    last_side_bias: Optional[Side] = None
    last_bias_score: Optional[float] = None

    # ── Active trade ───────────────────────────────────────────────
    active_trade: Optional[PaperTrade] = None
    active_trade_peak_ltp: float = 0.0  # for trailing stop

    # ── State machine ──────────────────────────────────────────────
    machine_state: MachineState = MachineState.IDLE

    # ── Rolling buffers ────────────────────────────────────────────
    spot_history: deque = field(default_factory=lambda: deque(maxlen=_MAX_SPOT_HISTORY))
    snapshot_hashes: deque = field(default_factory=lambda: deque(maxlen=_MAX_SNAPSHOT_HASHES))
    recent_signals: deque = field(default_factory=lambda: deque(maxlen=_MAX_SIGNALS))
    recent_debug: deque = field(default_factory=lambda: deque(maxlen=_MAX_DEBUG_EVENTS))
    recent_rejections: deque = field(default_factory=lambda: deque(maxlen=_MAX_REJECTIONS))
    trade_history: deque = field(default_factory=lambda: deque(maxlen=_MAX_TRADES))

    # ── Cycle counter ──────────────────────────────────────────────
    cycle_count: int = 0
    last_cycle_time: Optional[datetime] = None
    last_cycle_latency_ms: float = 0.0


class RuntimeStateManager:
    """Manages the in-memory runtime state for the lottery pipeline.

    One instance per instrument. Provides methods to update state
    atomically and query current status.
    """

    def __init__(self, config: LotteryConfig, symbol: str) -> None:
        self._config = config
        self._state = RuntimeState(symbol=symbol)

    @property
    def state(self) -> RuntimeState:
        return self._state

    # ── Spot Updates ───────────────────────────────────────────────

    def update_spot(self, ltp: float, timestamp: Optional[datetime] = None) -> None:
        """Record a new spot price."""
        now = timestamp or datetime.now(timezone.utc)
        self._state.last_spot_ltp = ltp
        self._state.last_spot_timestamp = now
        self._state.spot_history.append({
            "ltp": ltp,
            "timestamp": now.isoformat(),
        })

    # ── Snapshot Updates ───────────────────────────────────────────

    def update_chain_snapshot(self, snapshot: ChainSnapshot) -> None:
        """Store the latest chain snapshot."""
        self._state.last_chain_snapshot = snapshot
        self._state.last_spot_ltp = snapshot.spot_ltp
        self._state.last_spot_timestamp = snapshot.snapshot_timestamp

    def update_calculated(self, calculated: CalculatedSnapshot) -> None:
        """Store the latest calculated snapshot."""
        self._state.last_calculated = calculated
        self._state.last_side_bias = calculated.preferred_side
        self._state.last_bias_score = calculated.bias_score

    def update_quality(self, report: QualityReport) -> None:
        """Store the latest quality report."""
        self._state.last_quality_report = report

    # ── Signal Updates ─────────────────────────────────────────────

    def update_signal(self, signal: SignalEvent) -> None:
        """Record a new signal event."""
        self._state.last_signal = signal
        self._state.recent_signals.append(signal)

        if signal.selected_strike is not None:
            self._state.last_selected_strike = signal.selected_strike

        if signal.rejection_reason is not None:
            self._state.recent_rejections.append({
                "timestamp": signal.timestamp.isoformat(),
                "reason": signal.rejection_reason.value,
                "detail": signal.rejection_detail,
                "state": signal.machine_state.value,
            })

    # ── Trade Updates ──────────────────────────────────────────────

    def set_active_trade(self, trade: PaperTrade) -> None:
        """Set the currently active paper trade."""
        self._state.active_trade = trade
        self._state.active_trade_peak_ltp = trade.entry_price

    def update_trade_ltp(self, current_ltp: float) -> None:
        """Update peak LTP for trailing stop tracking."""
        if current_ltp > self._state.active_trade_peak_ltp:
            self._state.active_trade_peak_ltp = current_ltp

    def close_active_trade(self, closed_trade: PaperTrade) -> None:
        """Move active trade to history and clear."""
        self._state.trade_history.append(closed_trade)
        self._state.active_trade = None
        self._state.active_trade_peak_ltp = 0.0

    # ── State Machine ──────────────────────────────────────────────

    def update_machine_state(self, state: MachineState) -> None:
        """Update the current state machine state."""
        self._state.machine_state = state

    # ── Debug ──────────────────────────────────────────────────────

    def add_debug_trace(self, trace: DebugTrace) -> None:
        """Record a debug trace for the current cycle."""
        self._state.recent_debug.append(trace)

    # ── Cycle Tracking ─────────────────────────────────────────────

    def start_cycle(self) -> None:
        """Mark the start of a new calculation cycle."""
        self._state.cycle_count += 1
        self._state.last_cycle_time = datetime.now(timezone.utc)

    def end_cycle(self, latency_ms: float) -> None:
        """Mark the end of a cycle with latency."""
        self._state.last_cycle_latency_ms = latency_ms

    # ── Snapshot Hash Tracking ─────────────────────────────────────

    def record_snapshot_hash(self, hash_val: str) -> None:
        """Record a snapshot hash for staleness detection."""
        self._state.snapshot_hashes.append(hash_val)

    # ── Queries ────────────────────────────────────────────────────

    def get_status_summary(self) -> dict:
        """Get a compact status summary for dashboard display."""
        s = self._state
        return {
            "symbol": s.symbol,
            "state": s.machine_state.value,
            "spot": s.last_spot_ltp,
            "spot_timestamp": s.last_spot_timestamp.isoformat() if s.last_spot_timestamp else None,
            "side_bias": s.last_side_bias.value if s.last_side_bias else None,
            "bias_score": s.last_bias_score,
            "selected_strike": s.last_selected_strike,
            "active_trade": {
                "trade_id": s.active_trade.trade_id,
                "strike": s.active_trade.strike,
                "side": s.active_trade.side.value,
                "entry": s.active_trade.entry_price,
                "peak_ltp": s.active_trade_peak_ltp,
            } if s.active_trade else None,
            "quality": s.last_quality_report.overall_status.value if s.last_quality_report else None,
            "quality_score": s.last_quality_report.quality_score if s.last_quality_report else None,
            "cycle_count": s.cycle_count,
            "last_cycle_latency_ms": s.last_cycle_latency_ms,
            "recent_signals": len(s.recent_signals),
            "recent_rejections": len(s.recent_rejections),
            "trade_history_count": len(s.trade_history),
            "spot_history_count": len(s.spot_history),
            "uptime_seconds": (datetime.now(timezone.utc) - s.started_at).total_seconds(),
        }

    def get_recent_rejections(self, limit: int = 10) -> list[dict]:
        """Get recent rejection reasons for debugging."""
        return list(self._state.recent_rejections)[-limit:]

    def get_spot_history(self, limit: int = 60) -> list[dict]:
        """Get recent spot price history."""
        return list(self._state.spot_history)[-limit:]

    def get_trade_history(self, limit: int = 50) -> list[PaperTrade]:
        """Get recent trade history."""
        return list(self._state.trade_history)[-limit:]

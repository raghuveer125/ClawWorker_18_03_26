"""
Strategy validator — measures signal quality against actual market movement.

Tracks every signal, compares predicted direction / targets against real price
action, and flags delayed or missed opportunities.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config.settings import SETTINGS, SCALPING_TOPICS, SCALPING_COMPONENTS, Settings
from validator.models import ValidationIssue

# ── Signal lifecycle states ─────────────────────────────────────────────────

PENDING = "PENDING"
TRUE_SIGNAL = "TRUE_SIGNAL"
FALSE_SIGNAL = "FALSE_SIGNAL"
MISSED = "MISSED"
DELAYED_SIGNAL = "DELAYED_SIGNAL"


@dataclass
class TrackedSignal:
    """Immutable snapshot of a signal being evaluated."""

    signal_id: str
    side: str           # CE / PE / BUY / SELL
    entry_price: float
    target: float
    stop_loss: float
    index: str
    received_at: float  # monotonic seconds
    timestamp: str      # original ISO timestamp from message
    status: str = PENDING
    resolved_at: Optional[float] = None


@dataclass
class MarketSnapshot:
    """Latest known price for a symbol / index."""

    symbol: str
    price: float
    updated_at: float   # monotonic seconds


class StrategyValidator:
    """Validates signal quality by comparing predictions to market reality."""

    # ── Tuning constants ────────────────────────────────────────────────────
    _EVAL_AGE_SEC: float = 60.0          # minimum age before evaluation
    _EXPIRY_SEC: float = 300.0           # mark stale signals as MISSED
    _TARGET_PCT: float = 0.50            # 50 % move toward target = TRUE
    _DELAYED_ENTRY_PCT: float = 0.005    # 0.5 % past entry = delayed
    _MISSED_OPP_PCT: float = 0.01        # 1 % move with no signal = missed
    _MISSED_OPP_WINDOW_SEC: float = 60.0 # look-back for recent signals

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

        # signal_id → TrackedSignal
        self._signals: Dict[str, TrackedSignal] = {}

        # index/symbol → MarketSnapshot
        self._market: Dict[str, MarketSnapshot] = {}

        # Aggregated counters
        self._total_signals: int = 0
        self._true_signals: int = 0
        self._false_signals: int = 0
        self._missed_signals: int = 0
        self._delayed_signals: int = 0

        # Validation issues accumulated during the session
        self._issues: List[ValidationIssue] = []

        # Recent price history per index for missed-opportunity detection
        # index → list of (monotonic_time, price)
        self._price_history: Dict[str, List[tuple]] = defaultdict(list)

        # Track which indices had signals recently (index → latest mono time)
        self._recent_signal_times: Dict[str, float] = {}

    # ── Public API ──────────────────────────────────────────────────────────

    async def on_signal(self, message: dict) -> None:
        """Record a new signal for later validation."""

        now = time.monotonic()
        signal_id = message.get("signal_id", "")
        index = message.get("index", "")
        entry_price = float(message.get("entry_price", 0))

        tracked = TrackedSignal(
            signal_id=signal_id,
            side=message.get("side", ""),
            entry_price=entry_price,
            target=float(message.get("target", 0)),
            stop_loss=float(message.get("stop_loss", 0)),
            index=index,
            received_at=now,
            timestamp=message.get("timestamp", ""),
        )

        # Delayed-signal detection: market already moved past entry
        snapshot = self._market.get(index)
        if snapshot is not None and entry_price > 0:
            pct_move = abs(snapshot.price - entry_price) / entry_price
            if pct_move > self._DELAYED_ENTRY_PCT:
                tracked = TrackedSignal(
                    signal_id=tracked.signal_id,
                    side=tracked.side,
                    entry_price=tracked.entry_price,
                    target=tracked.target,
                    stop_loss=tracked.stop_loss,
                    index=tracked.index,
                    received_at=tracked.received_at,
                    timestamp=tracked.timestamp,
                    status=DELAYED_SIGNAL,
                    resolved_at=now,
                )
                self._delayed_signals += 1
                self._issues.append(
                    ValidationIssue(
                        severity="WARNING",
                        category="strategy",
                        topic=SCALPING_TOPICS.get("signals", ""),
                        message=f"Delayed signal {signal_id}: market already "
                                f"moved {pct_move:.2%} past entry",
                        timestamp=_iso_now(),
                        details={
                            "signal_id": signal_id,
                            "entry_price": entry_price,
                            "market_price": snapshot.price,
                            "pct_move": round(pct_move, 6),
                        },
                    )
                )

        self._signals[signal_id] = tracked
        self._total_signals += 1
        self._recent_signal_times[index] = now

    async def on_market_data(self, message: dict) -> None:
        """Update market prices and evaluate pending signals."""

        now = time.monotonic()
        symbol = message.get("symbol", message.get("index", ""))
        price = float(message.get("ltp", 0))

        if not symbol or price <= 0:
            return

        self._market[symbol] = MarketSnapshot(
            symbol=symbol, price=price, updated_at=now,
        )

        # Keep recent price history (cap at 600 entries ≈ 10 min at 1/s)
        history = self._price_history[symbol]
        history.append((now, price))
        if len(history) > 600:
            self._price_history[symbol] = history[-600:]

        # Evaluate all pending signals on every tick
        self._evaluate_signals(now)

        # Missed-opportunity detection
        self._detect_missed_opportunities(symbol, now)

    def get_report(self) -> Dict[str, Any]:
        """Signal accuracy report."""

        accuracy_pct = 0.0
        evaluated = self._true_signals + self._false_signals
        if evaluated > 0:
            accuracy_pct = round(
                (self._true_signals / evaluated) * 100, 2,
            )

        return {
            "total_signals": self._total_signals,
            "true_signals": self._true_signals,
            "false_signals": self._false_signals,
            "missed": self._missed_signals,
            "delayed_signals": self._delayed_signals,
            "accuracy_pct": accuracy_pct,
            "pending": sum(
                1 for s in self._signals.values() if s.status == PENDING
            ),
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "message": i.message,
                    "timestamp": i.timestamp,
                    "details": i.details,
                }
                for i in self._issues[-50:]   # last 50 issues
            ],
        }

    # ── Internal helpers ────────────────────────────────────────────────────

    def _evaluate_signals(self, now: float) -> None:
        """Walk pending signals and resolve those old enough."""

        for signal_id, sig in list(self._signals.items()):
            if sig.status != PENDING:
                continue

            age = now - sig.received_at
            if age < self._EVAL_AGE_SEC:
                continue

            snapshot = self._market.get(sig.index)
            if snapshot is None:
                # No market data for this index — cannot evaluate yet
                if age > self._EXPIRY_SEC:
                    self._mark_signal(signal_id, MISSED, now)
                continue

            current_price = snapshot.price

            # Determine direction: bullish for CE/BUY, bearish for PE/SELL
            is_bullish = sig.side.upper() in ("CE", "BUY")

            if is_bullish:
                target_distance = sig.target - sig.entry_price
                current_move = current_price - sig.entry_price
                hit_stop = current_price <= sig.stop_loss
            else:
                target_distance = sig.entry_price - sig.target
                current_move = sig.entry_price - current_price
                hit_stop = current_price >= sig.stop_loss

            # Guard against zero-distance targets
            if abs(target_distance) < 1e-9:
                if age > self._EXPIRY_SEC:
                    self._mark_signal(signal_id, MISSED, now)
                continue

            progress = current_move / target_distance

            if hit_stop:
                self._mark_signal(signal_id, FALSE_SIGNAL, now)
            elif progress >= self._TARGET_PCT:
                self._mark_signal(signal_id, TRUE_SIGNAL, now)
            elif age > self._EXPIRY_SEC:
                self._mark_signal(signal_id, MISSED, now)

    def _mark_signal(
        self, signal_id: str, status: str, now: float,
    ) -> None:
        """Immutably transition a signal to a resolved state."""

        old = self._signals[signal_id]
        self._signals[signal_id] = TrackedSignal(
            signal_id=old.signal_id,
            side=old.side,
            entry_price=old.entry_price,
            target=old.target,
            stop_loss=old.stop_loss,
            index=old.index,
            received_at=old.received_at,
            timestamp=old.timestamp,
            status=status,
            resolved_at=now,
        )

        if status == TRUE_SIGNAL:
            self._true_signals += 1
        elif status == FALSE_SIGNAL:
            self._false_signals += 1
            self._issues.append(
                ValidationIssue(
                    severity="WARNING",
                    category="strategy",
                    topic=SCALPING_TOPICS.get("signals", ""),
                    message=f"False signal {signal_id} — stop loss hit",
                    timestamp=_iso_now(),
                    details={"signal_id": signal_id, "side": old.side},
                )
            )
        elif status == MISSED:
            self._missed_signals += 1

    def _detect_missed_opportunities(
        self, symbol: str, now: float,
    ) -> None:
        """Flag a sudden market move with no corresponding signal."""

        history = self._price_history.get(symbol, [])
        if len(history) < 2:
            return

        # Compare current price against price ~60 s ago
        cutoff = now - self._MISSED_OPP_WINDOW_SEC
        old_entries = [
            (t, p) for t, p in history if t <= cutoff
        ]
        if not old_entries:
            return

        _, old_price = old_entries[-1]
        _, current_price = history[-1]

        if old_price <= 0:
            return

        pct_change = abs(current_price - old_price) / old_price
        if pct_change < self._MISSED_OPP_PCT:
            return

        # Check if any signal was generated for this index recently
        last_signal_time = self._recent_signal_times.get(symbol, 0)
        if now - last_signal_time < self._MISSED_OPP_WINDOW_SEC:
            return  # A signal was generated — no missed opportunity

        self._issues.append(
            ValidationIssue(
                severity="INFO",
                category="strategy",
                topic=SCALPING_TOPICS.get("market_data", ""),
                message=f"Missed opportunity on {symbol}: {pct_change:.2%} "
                        f"move with no signal in last "
                        f"{self._MISSED_OPP_WINDOW_SEC:.0f}s",
                timestamp=_iso_now(),
                details={
                    "symbol": symbol,
                    "old_price": old_price,
                    "current_price": current_price,
                    "pct_change": round(pct_change, 6),
                },
            )
        )

        # Prevent duplicate alerts: set a synthetic "signal time" to suppress
        # repeated missed-opportunity warnings for the same move.
        self._recent_signal_times[symbol] = now


# ── Module-level utility ────────────────────────────────────────────────────

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

"""Microstructure tracker — rolling book snapshots + derived order flow signals.

Tracks the order book evolution for shortlisted candidate option contracts.
Stores last N observations per strike and derives features like:
- Persistent walls (stable large qty at a level)
- Pulls (sudden qty drop — order withdrawn)
- Refills (qty recovers after pull)
- Absorption (large qty consumed, price holds)
- Breakthrough (price breaks through wall)
- Spoof risk (wall appears then vanishes)

All labels are neutral — no "institutional" or "smart money" claims.
Used as a CONFIRMATION LAYER only, not standalone entry logic.
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ── Config ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MicrostructureConfig:
    """Configuration for microstructure tracking."""
    buffer_size: int = 20                    # observations to retain per strike
    wall_qty_threshold: int = 5000           # qty above this = "wall"
    wall_persistence_min: int = 5            # consecutive observations to confirm wall
    pull_drop_pct: float = 50.0              # % drop in qty = "pull"
    refill_recovery_pct: float = 70.0        # % recovery after pull = "refill"
    absorption_volume_multiplier: float = 2.0  # volume spike during price hold
    spoof_appear_disappear_window: int = 3   # observations for appear+disappear


# ── Data Models ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MicroSnapshot:
    """Single observation of a candidate's order book state."""
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None
    spread: Optional[float] = None
    spread_pct: Optional[float] = None
    ltp: Optional[float] = None
    volume: Optional[int] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "bid": self.bid, "ask": self.ask,
            "bid_qty": self.bid_qty, "ask_qty": self.ask_qty,
            "spread": self.spread, "spread_pct": self.spread_pct,
            "ltp": self.ltp, "volume": self.volume,
            "timestamp": self.timestamp.isoformat(),
        }


class MicroSignalType(Enum):
    """Neutral microstructure signal labels."""
    PERSISTENT_BID_WALL = "PERSISTENT_BID_WALL"
    PERSISTENT_ASK_WALL = "PERSISTENT_ASK_WALL"
    PULLED_BID = "PULLED_BID"
    PULLED_ASK = "PULLED_ASK"
    REFILLED_BID = "REFILLED_BID"
    REFILLED_ASK = "REFILLED_ASK"
    ABSORPTION_BID = "ABSORPTION_BID"
    ABSORPTION_ASK = "ABSORPTION_ASK"
    BREAKTHROUGH_BID = "BREAKTHROUGH_BID"
    BREAKTHROUGH_ASK = "BREAKTHROUGH_ASK"
    SPOOF_RISK_BID = "SPOOF_RISK_BID"
    SPOOF_RISK_ASK = "SPOOF_RISK_ASK"


@dataclass(frozen=True)
class MicroSignal:
    """A detected microstructure event."""
    signal_type: MicroSignalType
    strike: float
    side: str                    # "BID" or "ASK"
    strength: float = 0.0       # 0.0-1.0 normalized strength
    detail: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "signal_type": self.signal_type.value,
            "strike": self.strike,
            "side": self.side,
            "strength": round(self.strength, 3),
            "detail": self.detail,
            "timestamp": self.timestamp.isoformat(),
        }


# ── Tracker ────────────────────────────────────────────────────────────────

class MicrostructureTracker:
    """Tracks rolling order book snapshots per candidate strike.

    Feed it with data from candidate quote refresh (TriggerSnapshot).
    Query it for derived microstructure features.
    """

    def __init__(self, config: MicrostructureConfig) -> None:
        self._config = config
        # Per-strike rolling buffer: key = (strike, option_type_str)
        self._buffers: dict[tuple[float, str], deque[MicroSnapshot]] = {}

    def record(
        self,
        strike: float,
        option_type: str,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        bid_qty: Optional[int] = None,
        ask_qty: Optional[int] = None,
        ltp: Optional[float] = None,
        volume: Optional[int] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Record a new book snapshot for a candidate."""
        key = (strike, option_type)
        if key not in self._buffers:
            self._buffers[key] = deque(maxlen=self._config.buffer_size)

        spread = None
        spread_pct = None
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            spread = round(ask - bid, 2)
            mid = (bid + ask) / 2
            spread_pct = round((spread / mid) * 100, 2) if mid > 0 else None

        snap = MicroSnapshot(
            bid=bid, ask=ask,
            bid_qty=bid_qty, ask_qty=ask_qty,
            spread=spread, spread_pct=spread_pct,
            ltp=ltp, volume=volume,
            timestamp=timestamp or datetime.now(timezone.utc),
        )
        self._buffers[key].append(snap)

    def get_history(self, strike: float, option_type: str) -> list[MicroSnapshot]:
        """Get rolling snapshot history for a strike."""
        key = (strike, option_type)
        return list(self._buffers.get(key, []))

    def get_latest(self, strike: float, option_type: str) -> Optional[MicroSnapshot]:
        """Get most recent snapshot for a strike."""
        buf = self._buffers.get((strike, option_type))
        return buf[-1] if buf else None

    def observation_count(self, strike: float, option_type: str) -> int:
        """Number of observations for a strike."""
        return len(self._buffers.get((strike, option_type), []))

    def clear(self, strike: Optional[float] = None, option_type: Optional[str] = None) -> None:
        """Clear buffers — optionally for a specific strike."""
        if strike is not None and option_type is not None:
            self._buffers.pop((strike, option_type), None)
        else:
            self._buffers.clear()

    @property
    def tracked_strikes(self) -> list[tuple[float, str]]:
        """All currently tracked (strike, option_type) pairs."""
        return list(self._buffers.keys())

    # ── Derived Features ───────────────────────────────────────────

    def detect_signals(self, strike: float, option_type: str) -> list[MicroSignal]:
        """Detect all microstructure signals for a strike.

        Returns list of detected signals (may be empty).
        """
        history = self.get_history(strike, option_type)
        if len(history) < 3:
            return []

        signals: list[MicroSignal] = []
        cfg = self._config
        now = history[-1].timestamp

        # ── Persistent Wall ────────────────────────────────────────
        for side, qty_getter in [("BID", lambda s: s.bid_qty), ("ASK", lambda s: s.ask_qty)]:
            wall = self._detect_persistent_wall(history, qty_getter, cfg.wall_qty_threshold, cfg.wall_persistence_min)
            if wall:
                sig_type = MicroSignalType.PERSISTENT_BID_WALL if side == "BID" else MicroSignalType.PERSISTENT_ASK_WALL
                signals.append(MicroSignal(
                    signal_type=sig_type, strike=strike, side=side,
                    strength=wall["strength"],
                    detail=f"qty={wall['avg_qty']:.0f} for {wall['consecutive']} obs",
                    timestamp=now,
                ))

        # ── Pull Detection ─────────────────────────────────────────
        for side, qty_getter in [("BID", lambda s: s.bid_qty), ("ASK", lambda s: s.ask_qty)]:
            pull = self._detect_pull(history, qty_getter, cfg.pull_drop_pct)
            if pull:
                sig_type = MicroSignalType.PULLED_BID if side == "BID" else MicroSignalType.PULLED_ASK
                signals.append(MicroSignal(
                    signal_type=sig_type, strike=strike, side=side,
                    strength=pull["drop_pct"] / 100,
                    detail=f"from {pull['before']:.0f} to {pull['after']:.0f} ({pull['drop_pct']:.0f}% drop)",
                    timestamp=now,
                ))

        # ── Refill Detection ───────────────────────────────────────
        for side, qty_getter in [("BID", lambda s: s.bid_qty), ("ASK", lambda s: s.ask_qty)]:
            refill = self._detect_refill(history, qty_getter, cfg.pull_drop_pct, cfg.refill_recovery_pct)
            if refill:
                sig_type = MicroSignalType.REFILLED_BID if side == "BID" else MicroSignalType.REFILLED_ASK
                signals.append(MicroSignal(
                    signal_type=sig_type, strike=strike, side=side,
                    strength=refill["recovery_pct"] / 100,
                    detail=f"recovered {refill['recovery_pct']:.0f}% after pull",
                    timestamp=now,
                ))

        # ── Absorption Detection ───────────────────────────────────
        absorption = self._detect_absorption(history, cfg.absorption_volume_multiplier)
        if absorption:
            signals.append(MicroSignal(
                signal_type=MicroSignalType.ABSORPTION_BID if absorption["side"] == "BID" else MicroSignalType.ABSORPTION_ASK,
                strike=strike, side=absorption["side"],
                strength=min(absorption["volume_ratio"] / 5, 1.0),
                detail=f"volume {absorption['volume_ratio']:.1f}x avg, price held",
                timestamp=now,
            ))

        # ── Spoof Risk ─────────────────────────────────────────────
        for side, qty_getter in [("BID", lambda s: s.bid_qty), ("ASK", lambda s: s.ask_qty)]:
            spoof = self._detect_spoof(history, qty_getter, cfg.wall_qty_threshold, cfg.spoof_appear_disappear_window)
            if spoof:
                sig_type = MicroSignalType.SPOOF_RISK_BID if side == "BID" else MicroSignalType.SPOOF_RISK_ASK
                signals.append(MicroSignal(
                    signal_type=sig_type, strike=strike, side=side,
                    strength=0.8,
                    detail=f"wall appeared then vanished in {spoof['window']} obs",
                    timestamp=now,
                ))

        return signals

    def get_confirmation_summary(self, strike: float, option_type: str) -> dict:
        """Get a summary for use in confirmation layer."""
        signals = self.detect_signals(strike, option_type)
        history = self.get_history(strike, option_type)

        bullish_signals = [s for s in signals if s.signal_type in (
            MicroSignalType.PERSISTENT_BID_WALL,
            MicroSignalType.REFILLED_BID,
            MicroSignalType.ABSORPTION_BID,
            MicroSignalType.PULLED_ASK,
        )]
        bearish_signals = [s for s in signals if s.signal_type in (
            MicroSignalType.PERSISTENT_ASK_WALL,
            MicroSignalType.REFILLED_ASK,
            MicroSignalType.ABSORPTION_ASK,
            MicroSignalType.PULLED_BID,
        )]
        risk_signals = [s for s in signals if "SPOOF" in s.signal_type.value]

        return {
            "observations": len(history),
            "total_signals": len(signals),
            "bullish_count": len(bullish_signals),
            "bearish_count": len(bearish_signals),
            "risk_count": len(risk_signals),
            "signals": [s.to_dict() for s in signals],
            "has_spoof_risk": len(risk_signals) > 0,
        }

    def to_dict(self) -> dict:
        """Serialize all tracked state for debugging."""
        result = {}
        for (strike, otype), buf in self._buffers.items():
            key = f"{strike}_{otype}"
            result[key] = {
                "observations": len(buf),
                "latest": buf[-1].to_dict() if buf else None,
                "signals": [s.to_dict() for s in self.detect_signals(strike, otype)],
            }
        return result

    # ── Signal Detection Algorithms ────────────────────────────────

    @staticmethod
    def _detect_persistent_wall(
        history: list[MicroSnapshot],
        qty_getter,
        threshold: int,
        min_persistence: int,
    ) -> Optional[dict]:
        """Detect if qty has been consistently above threshold."""
        recent = history[-min_persistence:]
        if len(recent) < min_persistence:
            return None

        qtys = [qty_getter(s) for s in recent if qty_getter(s) is not None]
        if len(qtys) < min_persistence:
            return None

        above = sum(1 for q in qtys if q >= threshold)
        if above >= min_persistence:
            avg = sum(qtys) / len(qtys)
            return {"consecutive": above, "avg_qty": avg, "strength": min(avg / (threshold * 3), 1.0)}

        return None

    @staticmethod
    def _detect_pull(
        history: list[MicroSnapshot],
        qty_getter,
        drop_pct_threshold: float,
    ) -> Optional[dict]:
        """Detect sudden qty drop (order pulled)."""
        if len(history) < 2:
            return None

        prev_qty = qty_getter(history[-2])
        curr_qty = qty_getter(history[-1])

        if prev_qty is None or curr_qty is None or prev_qty <= 0:
            return None

        drop_pct = ((prev_qty - curr_qty) / prev_qty) * 100
        if drop_pct >= drop_pct_threshold:
            return {"before": prev_qty, "after": curr_qty, "drop_pct": drop_pct}

        return None

    @staticmethod
    def _detect_refill(
        history: list[MicroSnapshot],
        qty_getter,
        drop_pct_threshold: float,
        recovery_pct_threshold: float,
    ) -> Optional[dict]:
        """Detect recovery after a pull — qty bounces back."""
        if len(history) < 3:
            return None

        qty_3 = qty_getter(history[-3])
        qty_2 = qty_getter(history[-2])
        qty_1 = qty_getter(history[-1])

        if None in (qty_3, qty_2, qty_1) or qty_3 <= 0:
            return None

        # Was there a pull at -2?
        drop_pct = ((qty_3 - qty_2) / qty_3) * 100
        if drop_pct < drop_pct_threshold:
            return None

        # Did it recover at -1?
        if qty_3 == 0:
            return None
        recovery_pct = (qty_1 / qty_3) * 100
        if recovery_pct >= recovery_pct_threshold:
            return {"recovery_pct": recovery_pct}

        return None

    @staticmethod
    def _detect_absorption(
        history: list[MicroSnapshot],
        volume_multiplier: float,
    ) -> Optional[dict]:
        """Detect high volume with price stability (absorption)."""
        if len(history) < 5:
            return None

        recent_vols = [s.volume for s in history[-5:] if s.volume is not None]
        if len(recent_vols) < 5:
            return None

        avg_vol = sum(recent_vols[:-1]) / len(recent_vols[:-1])
        latest_vol = recent_vols[-1]

        if avg_vol <= 0:
            return None

        ratio = latest_vol / avg_vol

        # Check price stability (LTP didn't move much)
        ltps = [s.ltp for s in history[-5:] if s.ltp is not None and s.ltp > 0]
        if len(ltps) < 2:
            return None

        price_range = max(ltps) - min(ltps)
        avg_price = sum(ltps) / len(ltps)
        price_change_pct = (price_range / avg_price) * 100 if avg_price > 0 else 999

        if ratio >= volume_multiplier and price_change_pct < 5.0:
            # Determine side: if bid held, absorption on bid side
            side = "BID" if history[-1].bid_qty and history[-1].bid_qty > 0 else "ASK"
            return {"volume_ratio": ratio, "price_change_pct": price_change_pct, "side": side}

        return None

    @staticmethod
    def _detect_spoof(
        history: list[MicroSnapshot],
        qty_getter,
        wall_threshold: int,
        window: int,
    ) -> Optional[dict]:
        """Detect wall that appears then disappears quickly (spoof risk)."""
        if len(history) < window + 1:
            return None

        recent = history[-(window + 1):]
        qtys = [qty_getter(s) for s in recent if qty_getter(s) is not None]

        if len(qtys) < window + 1:
            return None

        # Pattern: low → high → low (wall appeared then vanished)
        had_wall = any(q >= wall_threshold for q in qtys[1:-1])
        start_low = qtys[0] < wall_threshold
        end_low = qtys[-1] < wall_threshold

        if start_low and had_wall and end_low:
            return {"window": window}

        return None

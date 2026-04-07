"""Position manager with partial fill tracking, P&L computation, and DB persistence."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import kafka_config as bus

_log = logging.getLogger("scalping.dry_run.positions")

try:
    from knowledge.trade_memory import TradeMemory, TradeRecord, get_trade_memory
    _HAS_DB = True
except ImportError:
    try:
        from engines.scalping.knowledge.trade_memory import TradeMemory, TradeRecord, get_trade_memory
        _HAS_DB = True
    except ImportError:
        _HAS_DB = False


@dataclass
class SimulatedPosition:
    position_id: str
    symbol: str
    strike: int
    option_type: str
    entry_price: float
    entry_time: datetime
    expected_qty: int
    filled_qty: int = 0
    lot_size: int = 25
    direction: str = "long"
    status: str = "pending"  # pending, partial, open, closed
    sl_price: float = 0.0
    target_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    partial_exits: List[Dict[str, Any]] = field(default_factory=list)
    exit_price: float = 0.0
    exit_time: Optional[datetime] = None
    exit_reason: str = ""
    entry_spread_pct: float = 0.0
    fills: List[Dict[str, Any]] = field(default_factory=list)


class PositionManager:
    """Tracks all simulated positions with fill reconciliation and DB persistence."""

    def __init__(self) -> None:
        self._positions: Dict[str, SimulatedPosition] = {}
        self._closed: List[SimulatedPosition] = []
        self._db: Optional[Any] = None
        if _HAS_DB:
            try:
                self._db = get_trade_memory()
                _log.info("TradeMemory DB connected for paper trade persistence")
            except Exception as e:
                _log.warning("TradeMemory DB init failed: %s — trades will be in-memory only", e)

    def create_position(
        self,
        position_id: str,
        symbol: str,
        strike: int,
        option_type: str,
        entry_price: float,
        expected_qty: int,
        sl_price: float,
        target_price: float,
        lot_size: int = 25,
        entry_spread_pct: float = 0.0,
        entry_time: Optional[datetime] = None,
    ) -> SimulatedPosition:
        pos = SimulatedPosition(
            position_id=position_id,
            symbol=symbol,
            strike=strike,
            option_type=option_type,
            entry_price=entry_price,
            entry_time=entry_time or datetime.now(),
            expected_qty=expected_qty,
            lot_size=lot_size,
            sl_price=sl_price,
            target_price=target_price,
            current_price=entry_price,
            entry_spread_pct=entry_spread_pct,
        )
        self._positions[position_id] = pos
        bus.publish("positions", {
            "event": "position_created",
            "position_id": position_id,
            "symbol": symbol,
            "strike": strike,
            "option_type": option_type,
            "entry_price": entry_price,
            "expected_qty": expected_qty,
            "sl": sl_price,
            "target": target_price,
        })

        # Persist to DB
        if self._db and _HAS_DB:
            try:
                record = TradeRecord(
                    trade_id=position_id,
                    symbol=symbol,
                    direction="long",
                    entry_time=(entry_time or datetime.now()).isoformat(),
                    exit_time=None,
                    entry_price=entry_price,
                    exit_price=None,
                    quantity=expected_qty,
                    strategy="scalping_paper",
                    regime="unknown",
                    setup={"strike": strike, "option_type": option_type, "sl": sl_price, "target": target_price},
                    status="open",
                    tags=["paper", "dry_run"],
                )
                self._db.record_trade(record)
            except Exception as e:
                _log.warning("DB write failed for %s: %s", position_id, e)

        return pos

    def apply_fill(self, position_id: str, fill_qty: int, fill_price: float, fill_time: Optional[datetime] = None) -> None:
        pos = self._positions.get(position_id)
        if not pos:
            return
        pos.filled_qty += fill_qty
        pos.fills.append({"qty": fill_qty, "price": fill_price, "time": (fill_time or datetime.now()).isoformat()})
        if pos.filled_qty >= pos.expected_qty:
            pos.status = "open"
            # Recalculate weighted average entry
            total_cost = sum(f["qty"] * f["price"] for f in pos.fills)
            pos.entry_price = total_cost / pos.filled_qty if pos.filled_qty > 0 else pos.entry_price
        else:
            pos.status = "partial"
        bus.publish("fills", {
            "event": "fill",
            "position_id": position_id,
            "fill_qty": fill_qty,
            "fill_price": fill_price,
            "total_filled": pos.filled_qty,
            "expected": pos.expected_qty,
            "status": pos.status,
        })

    def update_price(self, position_id: str, current_price: float) -> None:
        pos = self._positions.get(position_id)
        if not pos or pos.status == "closed":
            return
        pos.current_price = current_price
        pos.unrealized_pnl = (current_price - pos.entry_price) * pos.filled_qty

    def close_position(self, position_id: str, exit_price: float, exit_reason: str, exit_time: Optional[datetime] = None) -> Optional[SimulatedPosition]:
        pos = self._positions.get(position_id)
        if not pos or pos.status == "closed":
            return None
        pos.exit_price = exit_price
        pos.exit_time = exit_time or datetime.now()
        pos.exit_reason = exit_reason
        pos.realized_pnl = (exit_price - pos.entry_price) * pos.filled_qty
        pos.unrealized_pnl = 0.0
        pos.status = "closed"
        self._closed.append(pos)
        del self._positions[position_id]
        bus.publish("positions", {
            "event": "position_closed",
            "position_id": position_id,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "qty": pos.filled_qty,
            "realized_pnl": round(pos.realized_pnl, 2),
            "exit_reason": exit_reason,
        })

        # Persist close to DB
        if self._db and _HAS_DB:
            try:
                record = TradeRecord(
                    trade_id=position_id,
                    symbol=pos.symbol,
                    direction="long",
                    entry_time=pos.entry_time.isoformat() if pos.entry_time else "",
                    exit_time=(exit_time or datetime.now()).isoformat(),
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    quantity=pos.filled_qty,
                    strategy="scalping_paper",
                    regime="unknown",
                    setup={"strike": pos.strike, "option_type": pos.option_type, "exit_reason": exit_reason},
                    pnl=round(pos.realized_pnl, 2),
                    pnl_pct=round((exit_price - pos.entry_price) / pos.entry_price * 100, 2) if pos.entry_price > 0 else 0,
                    status="closed",
                    tags=["paper", "dry_run"],
                )
                self._db.record_trade(record)
            except Exception as e:
                _log.warning("DB close write failed for %s: %s", position_id, e)

        return pos

    def get_open_positions(self) -> List[SimulatedPosition]:
        return [p for p in self._positions.values() if p.status in ("open", "partial")]

    def get_all_closed(self) -> List[SimulatedPosition]:
        return list(self._closed)

    @property
    def total_realized_pnl(self) -> float:
        return sum(p.realized_pnl for p in self._closed)

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self._positions.values())

    @property
    def daily_pnl(self) -> float:
        return self.total_realized_pnl + self.total_unrealized_pnl

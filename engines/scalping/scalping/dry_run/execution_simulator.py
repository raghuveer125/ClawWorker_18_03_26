"""Execution simulator — simulates partial fills, delays, and rejections."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ..risk_engine import estimate_slippage, SlippageEstimate
from .position_manager import PositionManager
from . import kafka_config as bus


class ExecutionSimulator:
    """Simulates order execution with realistic slippage, partial fills, and delays."""

    def __init__(self, position_manager: PositionManager) -> None:
        self.pm = position_manager
        self._pending_orders: list = []

    def submit_entry_order(
        self,
        symbol: str,
        strike: int,
        option_type: str,
        entry_price: float,
        qty: int,
        lot_size: int,
        sl_price: float,
        target_price: float,
        signal: Dict[str, Any],
        cycle_time: Optional[datetime] = None,
    ) -> str:
        position_id = f"SIM_{uuid.uuid4().hex[:8]}"
        spread = float(signal.get("spread", 0.2) or 0.2)
        bid_qty = int(signal.get("bid_qty", 5000) or 5000)
        ask_qty = int(signal.get("ask_qty", 3000) or 3000)
        vix = float(signal.get("_vix", 15) or 15)
        volatility = max(0.005, (vix - 10) / 1000)

        slip = estimate_slippage(
            order_qty=qty,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            spread=spread,
            spread_pct=float(signal.get("spread_pct", 0.3) or 0.3),
            volatility=volatility,
            side="buy",
            entry_price=entry_price,
        )

        # Simulate fill outcome
        if slip.fill_confidence < 0.40:
            bus.publish("fills", {
                "event": "fill_rejected",
                "position_id": position_id,
                "reason": f"fill_confidence_too_low:{slip.fill_confidence:.2f}",
                "slippage": slip.slippage_pct,
            })
            return ""

        fill_price = round(entry_price + slip.expected_fill, 2)

        # Partial fill simulation
        if slip.fill_confidence < 0.70:
            filled_qty = max(lot_size, int(qty * random.uniform(0.4, 0.7)))
        elif slip.fill_confidence < 0.90:
            filled_qty = max(lot_size, int(qty * random.uniform(0.7, 0.95)))
        else:
            filled_qty = qty

        now = cycle_time or datetime.now()
        pos = self.pm.create_position(
            position_id=position_id,
            symbol=symbol,
            strike=strike,
            option_type=option_type,
            entry_price=fill_price,
            expected_qty=qty,
            sl_price=sl_price,
            target_price=target_price,
            lot_size=lot_size,
            entry_spread_pct=float(signal.get("spread_pct", 0) or 0),
            entry_time=now,
        )

        # Apply fill (possibly partial)
        fill_delay_ms = random.randint(50, 300)
        fill_time = now + timedelta(milliseconds=fill_delay_ms)
        self.pm.apply_fill(position_id, filled_qty, fill_price, fill_time)

        # If partial, queue remainder
        if filled_qty < qty:
            remaining = qty - filled_qty
            self._pending_orders.append({
                "position_id": position_id,
                "remaining_qty": remaining,
                "limit_price": fill_price,
                "submitted_at": now,
            })

        bus.publish("decisions", {
            "event": "entry_executed",
            "position_id": position_id,
            "fill_price": fill_price,
            "fill_qty": filled_qty,
            "expected_qty": qty,
            "slippage": round(slip.expected_fill, 4),
            "slippage_pct": round(slip.slippage_pct, 4),
            "fill_confidence": slip.fill_confidence,
            "fill_delay_ms": fill_delay_ms,
        })

        return position_id

    def submit_exit_order(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: str,
        exit_qty: int,
        cycle_time: Optional[datetime] = None,
    ) -> bool:
        pos = self.pm._positions.get(position_id)
        if not pos:
            return False

        spread = pos.entry_price * (pos.entry_spread_pct / 100) if pos.entry_spread_pct > 0 else 0.2
        slip = estimate_slippage(
            order_qty=exit_qty,
            bid_qty=max(1, exit_qty * 3),
            ask_qty=max(1, exit_qty * 3),
            spread=spread,
            spread_pct=pos.entry_spread_pct,
            volatility=0.01,
            side="sell",
            entry_price=exit_price,
        )

        actual_exit = round(exit_price - slip.expected_fill, 2)
        actual_exit = max(0.05, actual_exit)

        self.pm.close_position(position_id, actual_exit, exit_reason, cycle_time)
        bus.publish("fills", {
            "event": "exit_fill",
            "position_id": position_id,
            "requested_price": exit_price,
            "actual_fill": actual_exit,
            "exit_slippage": round(slip.expected_fill, 4),
            "exit_reason": exit_reason,
        })
        return True

    def process_pending_orders(self, cycle_time: Optional[datetime] = None) -> None:
        """Process pending partial fill completions."""
        now = cycle_time or datetime.now()
        completed = []
        for i, order in enumerate(self._pending_orders):
            age = (now - order["submitted_at"]).total_seconds()
            if age > 5.0:
                # Cancel stale pending order
                bus.publish("fills", {
                    "event": "pending_cancelled",
                    "position_id": order["position_id"],
                    "remaining_qty": order["remaining_qty"],
                    "reason": "stale_5s",
                })
                completed.append(i)
            elif age > 1.0 and random.random() > 0.3:
                # Fill remainder
                self.pm.apply_fill(
                    order["position_id"],
                    order["remaining_qty"],
                    order["limit_price"],
                    now,
                )
                completed.append(i)
        for i in sorted(completed, reverse=True):
            self._pending_orders.pop(i)

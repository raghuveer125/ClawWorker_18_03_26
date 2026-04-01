"""Exit engine — monitors positions and triggers exits via risk_engine."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import ScalpingConfig
from ..risk_engine import validate_exit, ExitDecision
from .execution_simulator import ExecutionSimulator
from .position_manager import PositionManager, SimulatedPosition
from . import kafka_config as bus


class ExitEngine:
    """Monitors open positions every cycle, applies exit rules."""

    def __init__(self, executor: ExecutionSimulator, position_manager: PositionManager, config: ScalpingConfig) -> None:
        self.executor = executor
        self.pm = position_manager
        self.config = config
        self._total_exits = 0
        self._exit_reasons: Dict[str, int] = {}

    def check_exits(
        self,
        price_map: Dict[str, float],
        context: Dict[str, Any],
    ) -> List[str]:
        """Check all open positions for exit conditions.

        Args:
            price_map: {position_id: current_ltp}
            context: engine context with momentum_signals, etc.

        Returns list of closed position_ids.
        """
        closed_ids = []
        for pos in list(self.pm.get_open_positions()):
            current_price = price_map.get(pos.position_id, 0)
            if current_price <= 0:
                continue

            self.pm.update_price(pos.position_id, current_price)
            decision = validate_exit(pos, current_price, context, self.config)

            if decision.should_exit:
                success = self.executor.submit_exit_order(
                    position_id=pos.position_id,
                    exit_price=current_price,
                    exit_reason=decision.reason.value if hasattr(decision.reason, "value") else str(decision.reason),
                    exit_qty=decision.exit_qty,
                    cycle_time=context.get("cycle_now"),
                )
                if success:
                    closed_ids.append(pos.position_id)
                    self._total_exits += 1
                    reason_key = str(decision.reason.value if hasattr(decision.reason, "value") else decision.reason)
                    self._exit_reasons[reason_key] = self._exit_reasons.get(reason_key, 0) + 1

                    bus.publish("decisions", {
                        "event": "exit_executed",
                        "position_id": pos.position_id,
                        "exit_price": current_price,
                        "exit_reason": reason_key,
                        "urgency": decision.urgency,
                        "pnl": round((current_price - pos.entry_price) * pos.filled_qty, 2),
                    })

        return closed_ids

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_exits": self._total_exits,
            "exit_reasons": dict(self._exit_reasons),
        }

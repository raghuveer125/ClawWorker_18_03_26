"""Entry engine — validates signals and submits simulated orders."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import ScalpingConfig, get_index_config, IndexType
from ..risk_engine import validate_entry, compute_position_size, EntryDecision
from .execution_simulator import ExecutionSimulator
from . import kafka_config as bus


class EntryEngine:
    """Consumes signals, validates via risk_engine, submits to ExecutionSimulator."""

    def __init__(self, executor: ExecutionSimulator, config: ScalpingConfig) -> None:
        self.executor = executor
        self.config = config
        self._total_evaluated = 0
        self._total_approved = 0
        self._total_rejected = 0

    def evaluate_signals(
        self,
        signals: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> List[str]:
        """Evaluate a batch of signals. Returns list of position_ids created."""
        context["_approved_entries_this_cycle"] = 0
        position_ids = []

        for signal in signals:
            self._total_evaluated += 1
            decision = validate_entry(signal, context, self.config)

            bus.publish("decisions", {
                "event": "entry_decision",
                "symbol": signal.get("symbol", ""),
                "strike": signal.get("strike", 0),
                "option_type": signal.get("option_type", ""),
                "approved": decision.approved,
                "reason": decision.reason,
                "lots": decision.lots,
                "adjusted_rr": round(decision.adjusted_rr, 3),
                "sl_price": decision.sl_price,
                "target_price": decision.target_price,
            })

            if not decision.approved:
                self._total_rejected += 1
                continue

            self._total_approved += 1
            symbol = signal.get("symbol", "")
            idx_config = None
            for idx_type in IndexType:
                cfg = get_index_config(idx_type)
                if symbol in (str(idx_type.value), str(cfg.symbol)):
                    idx_config = cfg
                    break
            lot_size = idx_config.lot_size if idx_config else 25

            signal["_vix"] = context.get("vix", 15)
            pos_id = self.executor.submit_entry_order(
                symbol=symbol,
                strike=int(signal.get("strike", 0)),
                option_type=signal.get("option_type", "PE"),
                entry_price=float(signal.get("entry", signal.get("premium", 0))),
                qty=decision.lots * lot_size,
                lot_size=lot_size,
                sl_price=decision.sl_price,
                target_price=decision.target_price,
                signal=signal,
                cycle_time=context.get("cycle_now"),
            )
            if pos_id:
                position_ids.append(pos_id)

        return position_ids

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "evaluated": self._total_evaluated,
            "approved": self._total_approved,
            "rejected": self._total_rejected,
            "approval_rate": round(self._total_approved / max(self._total_evaluated, 1) * 100, 1),
        }

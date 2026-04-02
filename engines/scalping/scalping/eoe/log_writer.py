"""Log writer — produces 5 files per session per EOE_SESSION_SPEC.md."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any, Dict, List


class EOELogWriter:
    """Writes session logs to eoe_shadow/<date>/ directory."""

    CYCLE_HEADERS = [
        "timestamp", "eoe_state", "sensex_ltp", "vwap", "pct_from_extreme",
        "bos_active_count", "bos_direction", "candidate_strike", "candidate_type",
        "candidate_premium", "candidate_bid", "candidate_ask", "candidate_spread_pct",
        "candidate_bid_qty", "candidate_ask_qty", "tradable", "entry_signal",
        "entry_blocked_reason",
    ]

    def __init__(self, base_dir: str = "logs/eoe_shadow") -> None:
        self._base = base_dir
        self._date = datetime.now().strftime("%Y%m%d")
        self._dir = os.path.join(self._base, self._date)
        os.makedirs(self._dir, exist_ok=True)

        self._cycle_file = os.path.join(self._dir, "cycle_log.csv")
        self._cycle_writer = None
        self._cycle_fh = None

        # Initialize cycle CSV with header
        if not os.path.exists(self._cycle_file):
            with open(self._cycle_file, "w", newline="") as f:
                csv.writer(f).writerow(self.CYCLE_HEADERS)

    def write_cycle(self, row: Dict[str, Any]) -> None:
        """Append one cycle row to cycle_log.csv."""
        try:
            with open(self._cycle_file, "a", newline="") as f:
                w = csv.writer(f)
                w.writerow([row.get(h, "") for h in self.CYCLE_HEADERS])
        except Exception:
            pass

    def write_transition(self, transition: Dict[str, Any]) -> None:
        """Append state transition to state_transitions.csv."""
        path = os.path.join(self._dir, "state_transitions.csv")
        exists = os.path.exists(path)
        try:
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if not exists:
                    w.writerow(["timestamp", "from_state", "to_state", "trigger_reason",
                                "sensex_ltp", "session_high", "session_low", "vwap",
                                "pct_from_extreme", "bos_count_30min", "bos_direction"])
                w.writerow([
                    transition.get("timestamp", ""),
                    transition.get("from_state", ""),
                    transition.get("to_state", ""),
                    transition.get("trigger_reason", ""),
                    transition.get("sensex_ltp", ""),
                    transition.get("session_high", ""),
                    transition.get("session_low", ""),
                    transition.get("vwap", ""),
                    transition.get("pct_from_extreme", ""),
                    transition.get("bos_count_30min", ""),
                    transition.get("bos_direction", ""),
                ])
        except Exception:
            pass

    def write_hypo_trade(self, trade_dict: Dict[str, Any]) -> None:
        """Write hypothetical trade to hypo_trades.csv."""
        path = os.path.join(self._dir, "hypo_trades.csv")
        exists = os.path.exists(path)
        headers = list(trade_dict.keys())
        try:
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if not exists:
                    w.writerow(headers)
                w.writerow([trade_dict.get(h, "") for h in headers])
        except Exception:
            pass

    def write_missed_activation(self, missed: Dict[str, Any]) -> None:
        """Write missed activation record."""
        path = os.path.join(self._dir, "missed_activations.csv")
        exists = os.path.exists(path)
        try:
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if not exists:
                    w.writerow(["timestamp", "reason_missed", "sensex_ltp", "reversal_pct",
                                "bos_count", "eoe_state", "required_condition"])
                w.writerow([missed.get(k, "") for k in
                           ["timestamp", "reason_missed", "sensex_ltp", "reversal_pct",
                            "bos_count", "eoe_state", "required_condition"]])
        except Exception:
            pass

    def write_session_meta(self, meta: Dict[str, Any]) -> None:
        """Write session metadata JSON."""
        path = os.path.join(self._dir, "session_meta.json")
        try:
            with open(path, "w") as f:
                json.dump(meta, f, indent=2, default=str)
        except Exception:
            pass

    @property
    def session_dir(self) -> str:
        return self._dir

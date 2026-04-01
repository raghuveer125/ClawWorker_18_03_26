"""
Trade Recorder Mixin — dual-write JSONL + PostgreSQL, trade history
sanitization, and recent-trade queries.

Extracted from auto_trader.py to keep the AutoTrader class focused on
orchestration.  AutoTrader inherits from TradeRecorderMixin and the
public API is unchanged.
"""

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Centralised PostgreSQL trade recording
try:
    from data_platform.db.trades import (
        TradeRecord as DBTradeRecord,
        TradesConfig,
        sync_insert_trade,
    )
    _DB_TRADES_AVAILABLE = True
except ImportError:
    _DB_TRADES_AVAILABLE = False

logger = logging.getLogger(__name__)


class TradeRecorderMixin:
    """Methods for persisting, sanitizing, and querying trade history.

    Expects the following attributes on ``self`` (set by AutoTrader.__init__):
        trades_log_file         : Path
        trades_quarantine_file  : Path
        trade_history_status    : Dict[str, Any]
        strategy_id             : str
        mode                    : TradingMode enum (has ``.value``)
        recent_exit_times       : Dict[str, str]
        learning_adaptation_min_trades : int
        learning_adaptation_min_wins   : int
    """

    # ------------------------------------------------------------------
    # Dual-write: JSONL + PostgreSQL
    # ------------------------------------------------------------------

    def _log_trade(self, trade) -> None:
        """Log trade for learning -- dual-write to JSONL + PostgreSQL."""
        try:
            with open(self.trades_log_file, "a") as f:
                f.write(json.dumps(asdict(trade)) + "\n")
        except Exception as e:
            logger.error(
                "[_log_trade] Failed to write trade log for %s: %s "
                "-- trade occurred but record is missing",
                trade.trade_id, e,
            )

        # Persist to centralised PostgreSQL trades table
        if _DB_TRADES_AVAILABLE:
            try:
                entry_time = (
                    datetime.fromisoformat(trade.timestamp)
                    if trade.timestamp
                    else datetime.now()
                )
                db_record = DBTradeRecord(
                    trade_id=trade.trade_id,
                    strategy=trade.strategy_id or self.strategy_id or "clawwork",
                    bot_name="autotrader",
                    index_name=trade.index,
                    entry_price=trade.entry_price,
                    quantity=trade.quantity,
                    mode=trade.mode or "paper",
                    entry_time=entry_time,
                    option_type=trade.option_type or "",
                    strike=trade.strike,
                    exit_price=trade.exit_price,
                    exit_time=entry_time,  # close_position calls _log_trade at exit time
                    pnl=trade.pnl,
                    pnl_pct=trade.pnl_pct,
                    outcome=trade.outcome,
                    signal_source=trade.exit_reason or "",
                    market_snapshot={
                        "vix": trade.vix,
                        "pcr": trade.pcr,
                        "index_change_pct": trade.index_change_pct,
                        "market_bias": trade.market_bias,
                    },
                    reasoning=str(trade.bot_signals) if trade.bot_signals else "",
                )
                sync_insert_trade(TradesConfig.from_env(), db_record)
            except Exception as e:
                logger.warning(
                    "[_log_trade] DB write failed for %s: %s -- JSONL record intact",
                    trade.trade_id, e,
                )

    # ------------------------------------------------------------------
    # Trade-history status helpers
    # ------------------------------------------------------------------

    def _set_trade_history_status(
        self,
        *,
        healthy: bool,
        sanitized: bool,
        valid_rows: int,
        quarantined_rows: int,
        message: str,
    ) -> None:
        self.trade_history_status = {
            "healthy": healthy,
            "sanitized": sanitized,
            "valid_rows": valid_rows,
            "quarantined_rows": quarantined_rows,
            "message": message,
            "quarantine_file": str(self.trades_quarantine_file),
        }

    # ------------------------------------------------------------------
    # Recent exit-time loading (cooldown enforcement)
    # ------------------------------------------------------------------

    def _load_recent_exit_times(self) -> None:
        """Load the latest exit timestamp per symbol for cooldown enforcement."""
        latest_exit_times: Dict[str, str] = {}
        if not self.trades_log_file.exists():
            self.recent_exit_times = latest_exit_times
            return

        try:
            with open(self.trades_log_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    symbol = str(row.get("symbol", "")).strip()
                    timestamp = row.get("timestamp")
                    if not symbol or not timestamp:
                        continue
                    previous = latest_exit_times.get(symbol)
                    if previous is None or timestamp > previous:
                        latest_exit_times[symbol] = timestamp
        except OSError:
            latest_exit_times = {}

        self.recent_exit_times = latest_exit_times

    # ------------------------------------------------------------------
    # Sanitization pipeline
    # ------------------------------------------------------------------

    def _sanitize_trade_history(self) -> None:
        """Quarantine legacy/corrupt trade rows and rebuild learning from trusted rows."""
        if not self.trades_log_file.exists():
            self._set_trade_history_status(
                healthy=True,
                sanitized=False,
                valid_rows=0,
                quarantined_rows=0,
                message="No trade history found",
            )
            return

        quarantined_entries: List[Dict[str, Any]] = []
        candidate_rows: List[Dict[str, Any]] = []

        try:
            with open(self.trades_log_file) as f:
                raw_lines = f.readlines()
        except OSError as e:
            self._set_trade_history_status(
                healthy=False,
                sanitized=False,
                valid_rows=0,
                quarantined_rows=0,
                message=f"Failed to read trade history: {e}",
            )
            return

        for idx, line in enumerate(raw_lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                quarantined_entries.append(
                    self._make_quarantine_entry("invalid_json", {"raw": line.strip()})
                )
                continue

            valid, reason = self._validate_trade_history_row(row)
            if not valid:
                quarantined_entries.append(self._make_quarantine_entry(reason, row))
                continue

            candidate_rows.append({
                "row": row,
                "line_index": idx,
                "sort_key": self._trade_history_sort_key(row),
            })

        keep_indices = set()
        by_trade_id: Dict[str, List[Dict[str, Any]]] = {}
        for entry in candidate_rows:
            trade_id = str(entry["row"].get("trade_id", "")).strip()
            by_trade_id.setdefault(trade_id, []).append(entry)

        for entries in by_trade_id.values():
            if len(entries) == 1:
                keep_indices.add(entries[0]["line_index"])
                continue

            winner = max(entries, key=lambda item: item["sort_key"])
            keep_indices.add(winner["line_index"])
            for entry in entries:
                if entry["line_index"] == winner["line_index"]:
                    continue
                quarantined_entries.append(
                    self._make_quarantine_entry("duplicate_trade_id", entry["row"])
                )

        valid_rows = [
            entry["row"]
            for entry in candidate_rows
            if entry["line_index"] in keep_indices
        ]

        valid_rows.sort(key=self._trade_history_sort_key)

        sanitized = len(quarantined_entries) > 0 or len(valid_rows) != len(raw_lines)
        if quarantined_entries:
            with open(self.trades_quarantine_file, "a") as f:
                for entry in quarantined_entries:
                    f.write(json.dumps(entry) + "\n")

        if sanitized:
            with open(self.trades_log_file, "w") as f:
                for row in valid_rows:
                    f.write(json.dumps(row) + "\n")

        self._rebuild_learning_insights(valid_rows)
        self._set_trade_history_status(
            healthy=len(quarantined_entries) == 0,
            sanitized=sanitized,
            valid_rows=len(valid_rows),
            quarantined_rows=len(quarantined_entries),
            message=(
                f"Quarantined {len(quarantined_entries)} legacy/corrupt trade row(s)"
                if quarantined_entries
                else "Trade history verified"
            ),
        )

    # ------------------------------------------------------------------
    # Quarantine / validation helpers
    # ------------------------------------------------------------------

    def _make_quarantine_entry(self, reason: str, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "quarantined_at": datetime.now().isoformat(),
            "reason": reason,
            "row": row,
        }

    def _trade_history_sort_key(self, row: Dict[str, Any]) -> tuple:
        timestamp = str(row.get("timestamp", "") or "")
        duration = row.get("duration_minutes")
        duration_value = duration if isinstance(duration, (int, float)) else -1
        return (timestamp, duration_value)

    def _validate_trade_history_row(self, row: Dict[str, Any]) -> tuple:
        mode = str(row.get("mode", "") or "").strip().lower()
        if mode not in {"paper", "live"}:
            return False, "missing_or_invalid_mode"

        required_string_fields = [
            "trade_id", "timestamp", "symbol", "index",
            "option_type", "outcome", "exit_reason",
        ]
        for field in required_string_fields:
            if not str(row.get(field, "") or "").strip():
                return False, f"missing_{field}"

        option_type = str(row.get("option_type", "")).strip().upper()
        if option_type not in {"CE", "PE"}:
            return False, "invalid_option_type"

        numeric_fields = [
            "strike", "entry_price", "exit_price", "quantity",
            "pnl", "pnl_pct", "duration_minutes", "probability",
        ]
        for field in numeric_fields:
            if not isinstance(row.get(field), (int, float)):
                return False, f"invalid_{field}"

        entry_price = float(row.get("entry_price", 0))
        exit_price = float(row.get("exit_price", 0))
        quantity = float(row.get("quantity", 0))
        strike = float(row.get("strike", 0))
        if entry_price <= 0 or exit_price <= 0 or quantity <= 0 or strike <= 0:
            return False, "non_positive_trade_values"

        max_plausible_exit = max(entry_price * 25, strike * 0.25, 5000.0)
        if exit_price > max_plausible_exit:
            return False, "implausible_exit_price"

        return True, ""

    # ------------------------------------------------------------------
    # Recent trade queries
    # ------------------------------------------------------------------

    def get_recent_trades(
        self, limit: int = 100, mode: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return recently closed trades from the canonical auto-trader trade log."""
        if limit <= 0 or not self.trades_log_file.exists():
            return []

        normalized_mode = str(mode or "").strip().lower() or None
        trades: List[Dict[str, Any]] = []

        try:
            with open(self.trades_log_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        trade = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    trade_mode = str(trade.get("mode", "") or "").strip().lower()
                    if normalized_mode and trade_mode != normalized_mode:
                        continue
                    trades.append(trade)
        except OSError:
            return []

        trades.reverse()
        return trades[:limit]

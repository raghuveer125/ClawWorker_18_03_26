"""SQLite persistence — schema, connection, and CRUD for 8 tables.

Tables:
1. raw_chain_snapshots — raw chain data per cycle
2. validated_chain_rows — rows that passed quality checks
3. calculated_rows — per-strike computed metrics
4. signal_events — every signal (valid + rejected)
5. paper_trades — trade journal
6. capital_ledger — capital events
7. debug_events — stepwise debug traces
8. config_versions — config snapshots for audit

DB path includes symbol name for multi-instrument isolation.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import LotteryConfig
from ..models import (
    CalculatedRow,
    CapitalLedgerEntry,
    ChainSnapshot,
    DebugTrace,
    PaperTrade,
    QualityReport,
    SignalEvent,
)

logger = logging.getLogger(__name__)


class LotteryDB:
    """SQLite persistence for the lottery pipeline.

    One DB per instrument (path includes symbol).
    Thread-safe via WAL mode.
    """

    def __init__(self, config: LotteryConfig, symbol: str) -> None:
        self._config = config

        # DB path: resolve relative to engines/lottery/ package root, not cwd
        _ENGINE_ROOT = Path(__file__).resolve().parents[1]
        base_path = Path(config.storage.db_path)
        if not base_path.is_absolute():
            # Strip "engines/lottery/" prefix if present, resolve from package root
            path_str = str(base_path)
            for prefix in ("engines/lottery/", "engines\\lottery\\"):
                if path_str.startswith(prefix):
                    path_str = path_str[len(prefix):]
                    break
            base_path = _ENGINE_ROOT / path_str
        db_dir = base_path.parent / symbol.upper()
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_dir / base_path.name)

        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Create all tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()
        logger.info("DB initialized: %s", self._db_path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── 1. Raw Chain Snapshots ─────────────────────────────────────

    def save_chain_snapshot(self, snapshot: ChainSnapshot) -> None:
        """Persist a raw chain snapshot."""
        conn = self._get_conn()
        rows_json = json.dumps([
            {
                "strike": r.strike,
                "option_type": r.option_type.value,
                "ltp": r.ltp,
                "change": r.change,
                "volume": r.volume,
                "oi": r.oi,
                "bid": r.bid,
                "ask": r.ask,
                "iv": r.iv,
            }
            for r in snapshot.rows
        ])
        conn.execute(
            """INSERT INTO raw_chain_snapshots
               (snapshot_id, symbol, expiry, spot_ltp, snapshot_timestamp, row_count, rows_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.snapshot_id, snapshot.symbol, snapshot.expiry,
                snapshot.spot_ltp, snapshot.snapshot_timestamp.isoformat(),
                len(snapshot.rows), rows_json,
            ),
        )
        conn.commit()

    # ── 2. Quality Reports ─────────────────────────────────────────

    def save_quality_report(self, report: QualityReport) -> None:
        """Persist a quality validation report."""
        conn = self._get_conn()
        checks_json = json.dumps([
            {
                "check_name": c.check_name,
                "status": c.status.value,
                "threshold": c.threshold,
                "observed": c.observed,
                "result": c.result,
                "reason": c.reason,
            }
            for c in report.checks
        ])
        conn.execute(
            """INSERT INTO validated_chain_rows
               (snapshot_id, symbol, overall_status, quality_score, checks_json, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                report.snapshot_id, report.symbol, report.overall_status.value,
                report.quality_score, checks_json, report.timestamp.isoformat(),
            ),
        )
        conn.commit()

    # ── 3. Calculated Rows ─────────────────────────────────────────

    def save_calculated_rows(
        self,
        snapshot_id: str,
        symbol: str,
        spot: float,
        config_version: str,
        rows: list[CalculatedRow],
    ) -> None:
        """Persist calculated metrics for a snapshot."""
        conn = self._get_conn()
        rows_json = json.dumps([
            {
                "strike": r.strike,
                "distance": r.distance,
                "call_intrinsic": r.call_intrinsic,
                "call_extrinsic": r.call_extrinsic,
                "put_intrinsic": r.put_intrinsic,
                "put_extrinsic": r.put_extrinsic,
                "call_decay_ratio": r.call_decay_ratio,
                "put_decay_ratio": r.put_decay_ratio,
                "liquidity_skew": r.liquidity_skew,
                "call_spread_pct": r.call_spread_pct,
                "put_spread_pct": r.put_spread_pct,
                "call_band_eligible": r.call_band_eligible,
                "put_band_eligible": r.put_band_eligible,
                "call_candidate_score": r.call_candidate_score,
                "put_candidate_score": r.put_candidate_score,
                "call_slope": r.call_slope,
                "put_slope": r.put_slope,
                "call_theta_density": r.call_theta_density,
                "put_theta_density": r.put_theta_density,
            }
            for r in rows
        ])
        conn.execute(
            """INSERT INTO calculated_rows
               (snapshot_id, symbol, spot_ltp, config_version, row_count, rows_json, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id, symbol, spot, config_version,
                len(rows), rows_json, datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    # ── 4. Signal Events ───────────────────────────────────────────

    def save_signal(self, signal: SignalEvent) -> None:
        """Persist a signal event."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO signal_events
               (signal_id, timestamp, symbol, side_bias, zone, machine_state,
                selected_strike, selected_option_type, selected_premium,
                trigger_status, validity, rejection_reason, rejection_detail,
                snapshot_id, config_version, spot_ltp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal.signal_id, signal.timestamp.isoformat(), signal.symbol,
                signal.side_bias.value if signal.side_bias else None,
                signal.zone, signal.machine_state.value,
                signal.selected_strike,
                signal.selected_option_type.value if signal.selected_option_type else None,
                signal.selected_premium,
                signal.trigger_status, signal.validity.value,
                signal.rejection_reason.value if signal.rejection_reason else None,
                signal.rejection_detail,
                signal.snapshot_id, signal.config_version, signal.spot_ltp,
            ),
        )
        conn.commit()

    # ── 5. Paper Trades ────────────────────────────────────────────

    def save_trade(self, trade: PaperTrade) -> None:
        """Insert or update a paper trade."""
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO paper_trades
               (trade_id, timestamp_entry, timestamp_exit, side, symbol, expiry,
                strike, option_type, entry_price, exit_price, qty, lots,
                capital_before, capital_after, sl, t1, t2, t3,
                pnl, charges, status, reason_entry, reason_exit, exit_detail,
                signal_id, snapshot_id, config_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.trade_id, trade.timestamp_entry.isoformat(),
                trade.timestamp_exit.isoformat() if trade.timestamp_exit else None,
                trade.side.value, trade.symbol, trade.expiry,
                trade.strike, trade.option_type.value,
                trade.entry_price, trade.exit_price,
                trade.qty, trade.lots,
                trade.capital_before, trade.capital_after,
                trade.sl, trade.t1, trade.t2, trade.t3,
                trade.pnl, trade.charges, trade.status.value,
                trade.reason_entry,
                trade.reason_exit.value if trade.reason_exit else None,
                trade.exit_detail,
                trade.signal_id, trade.snapshot_id, trade.config_version,
            ),
        )
        conn.commit()

    # ── 6. Capital Ledger ──────────────────────────────────────────

    def save_ledger_entry(self, entry: CapitalLedgerEntry) -> None:
        """Persist a capital ledger entry."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO capital_ledger
               (entry_id, timestamp, symbol, trade_id, event, amount,
                running_capital, realized_pnl, unrealized_pnl,
                daily_pnl, drawdown, peak_capital)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.entry_id, entry.timestamp.isoformat(), entry.symbol,
                entry.trade_id, entry.event, entry.amount,
                entry.running_capital, entry.realized_pnl, entry.unrealized_pnl,
                entry.daily_pnl, entry.drawdown, entry.peak_capital,
            ),
        )
        conn.commit()

    # ── 7. Debug Events ────────────────────────────────────────────

    def save_debug_trace(self, trace: DebugTrace) -> None:
        """Persist a debug trace."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO debug_events
               (cycle_id, timestamp, symbol, snapshot_id, config_version,
                fetch_summary, validation_result, derived_variables,
                side_bias_decision, strike_scan_results, final_selection,
                trade_decision, paper_execution, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trace.cycle_id, trace.timestamp.isoformat(), trace.symbol,
                trace.snapshot_id, trace.config_version,
                json.dumps(trace.fetch_summary) if trace.fetch_summary else None,
                json.dumps(trace.validation_result) if trace.validation_result else None,
                json.dumps(trace.derived_variables) if trace.derived_variables else None,
                json.dumps(trace.side_bias_decision) if trace.side_bias_decision else None,
                json.dumps(trace.strike_scan_results) if trace.strike_scan_results else None,
                json.dumps(trace.final_selection) if trace.final_selection else None,
                json.dumps(trace.trade_decision) if trace.trade_decision else None,
                json.dumps(trace.paper_execution) if trace.paper_execution else None,
                json.dumps(trace.latency_ms) if trace.latency_ms else None,
            ),
        )
        conn.commit()

    # ── 8. Config Versions ─────────────────────────────────────────

    def save_config_version(self, config: LotteryConfig) -> None:
        """Persist a config version snapshot."""
        conn = self._get_conn()
        conn.execute(
            """INSERT OR IGNORE INTO config_versions
               (version_hash, config_json, saved_at)
               VALUES (?, ?, ?)""",
            (
                config.version_hash,
                json.dumps(config.to_dict(), default=str),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    # ── Queries ────────────────────────────────────────────────────

    def get_recent_signals(self, limit: int = 50) -> list[dict]:
        """Get recent signal events."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM signal_events ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_trades(self, limit: int = 50) -> list[dict]:
        """Get recent paper trades."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM paper_trades ORDER BY timestamp_entry DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_capital_ledger(self, limit: int = 100) -> list[dict]:
        """Get capital ledger entries."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM capital_ledger ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_snapshot_by_id(self, snapshot_id: str) -> Optional[dict]:
        """Get a raw chain snapshot by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM raw_chain_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_config_version(self, version_hash: str) -> Optional[dict]:
        """Get a config version by hash."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM config_versions WHERE version_hash = ?",
            (version_hash,),
        ).fetchone()
        return dict(row) if row else None

    def get_trade_count_today(self) -> int:
        """Get number of trades entered today."""
        conn = self._get_conn()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM paper_trades WHERE timestamp_entry LIKE ?",
            (f"{today}%",),
        ).fetchone()
        return row["cnt"] if row else 0

    @property
    def db_path(self) -> str:
        return self._db_path


# ── Schema ─────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_chain_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    expiry TEXT,
    spot_ltp REAL NOT NULL,
    snapshot_timestamp TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    rows_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validated_chain_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    overall_status TEXT NOT NULL,
    quality_score REAL NOT NULL,
    checks_json TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calculated_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    spot_ltp REAL NOT NULL,
    config_version TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    rows_json TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_events (
    signal_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    symbol TEXT,
    side_bias TEXT,
    zone TEXT,
    machine_state TEXT NOT NULL,
    selected_strike REAL,
    selected_option_type TEXT,
    selected_premium REAL,
    trigger_status TEXT,
    validity TEXT NOT NULL,
    rejection_reason TEXT,
    rejection_detail TEXT,
    snapshot_id TEXT,
    config_version TEXT,
    spot_ltp REAL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id TEXT PRIMARY KEY,
    timestamp_entry TEXT NOT NULL,
    timestamp_exit TEXT,
    side TEXT NOT NULL,
    symbol TEXT NOT NULL,
    expiry TEXT,
    strike REAL NOT NULL,
    option_type TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    qty INTEGER NOT NULL,
    lots INTEGER NOT NULL,
    capital_before REAL,
    capital_after REAL,
    sl REAL,
    t1 REAL,
    t2 REAL,
    t3 REAL,
    pnl REAL,
    charges REAL,
    status TEXT NOT NULL,
    reason_entry TEXT,
    reason_exit TEXT,
    exit_detail TEXT,
    signal_id TEXT,
    snapshot_id TEXT,
    config_version TEXT
);

CREATE TABLE IF NOT EXISTS capital_ledger (
    entry_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    symbol TEXT,
    trade_id TEXT,
    event TEXT NOT NULL,
    amount REAL NOT NULL,
    running_capital REAL NOT NULL,
    realized_pnl REAL,
    unrealized_pnl REAL,
    daily_pnl REAL,
    drawdown REAL,
    peak_capital REAL
);

CREATE TABLE IF NOT EXISTS debug_events (
    cycle_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    symbol TEXT,
    snapshot_id TEXT,
    config_version TEXT,
    fetch_summary TEXT,
    validation_result TEXT,
    derived_variables TEXT,
    side_bias_decision TEXT,
    strike_scan_results TEXT,
    final_selection TEXT,
    trade_decision TEXT,
    paper_execution TEXT,
    latency_ms TEXT
);

CREATE TABLE IF NOT EXISTS config_versions (
    version_hash TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    saved_at TEXT NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signal_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_validity ON signal_events(validity);
CREATE INDEX IF NOT EXISTS idx_trades_status ON paper_trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_entry ON paper_trades(timestamp_entry);
CREATE INDEX IF NOT EXISTS idx_ledger_timestamp ON capital_ledger(timestamp);
CREATE INDEX IF NOT EXISTS idx_debug_timestamp ON debug_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_snapshots_symbol ON raw_chain_snapshots(symbol);
"""

"""Replay engine — deterministic snapshot-by-snapshot backtest with config version restore.

Guarantees: same snapshot + same config = identical output.

Modes:
- DB replay: replay snapshots persisted in SQLite
- File replay: replay from JSONL snapshot files

Produces full audit trail: signals, trades, capital, debug traces.
"""

import json
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import LotteryConfig, load_config
from .calculations import (
    compute_advanced_metrics,
    compute_base_metrics,
    compute_side_bias,
    extrapolate_otm_strikes,
    filter_window,
    score_and_select,
    update_rows_with_scores,
)
from .data_quality import DataQualityValidator
from .debugging import CycleTracer
from .models import (
    ChainSnapshot,
    ExitReason,
    MachineState,
    OptionRow,
    OptionType,
    QualityStatus,
    SignalValidity,
    TradeStatus,
)
from .paper_trading import CapitalManager, PaperBroker
from .storage import LotteryDB
from .strategy import SignalEngine, StateMachine, resolve_triggers

logger = logging.getLogger(__name__)


class ReplayResult:
    """Results from a replay run."""

    __slots__ = (
        "total_snapshots", "processed", "skipped",
        "signals_valid", "signals_invalid",
        "trades_entered", "trades_closed",
        "final_capital", "total_pnl", "max_drawdown",
        "trades", "signals", "traces",
    )

    def __init__(self) -> None:
        self.total_snapshots = 0
        self.processed = 0
        self.skipped = 0
        self.signals_valid = 0
        self.signals_invalid = 0
        self.trades_entered = 0
        self.trades_closed = 0
        self.final_capital = 0.0
        self.total_pnl = 0.0
        self.max_drawdown = 0.0
        self.trades: list[dict] = []
        self.signals: list[dict] = []
        self.traces: list[dict] = []

    def summary(self) -> dict:
        return {
            "total_snapshots": self.total_snapshots,
            "processed": self.processed,
            "skipped": self.skipped,
            "signals_valid": self.signals_valid,
            "signals_invalid": self.signals_invalid,
            "trades_entered": self.trades_entered,
            "trades_closed": self.trades_closed,
            "final_capital": round(self.final_capital, 2),
            "total_pnl": round(self.total_pnl, 2),
            "max_drawdown": round(self.max_drawdown, 2),
        }


class ReplayEngine:
    """Deterministic replay engine for the lottery pipeline.

    Replays historical snapshots through the full pipeline:
    fetch(skipped) → validate → calculate → score → signal → paper trade

    Config version is restored per-snapshot if available.
    """

    def __init__(
        self,
        config: LotteryConfig,
        symbol: str,
    ) -> None:
        self._config = config
        self._symbol = symbol

    def replay_from_db(
        self,
        db: LotteryDB,
        limit: int = 1000,
        config_override: Optional[LotteryConfig] = None,
    ) -> ReplayResult:
        """Replay snapshots stored in the database.

        Args:
            db: LotteryDB instance with historical data.
            limit: Max snapshots to replay.
            config_override: Use this config instead of stored versions.

        Returns:
            ReplayResult with full audit.
        """
        conn = db._get_conn()
        rows = conn.execute(
            "SELECT * FROM raw_chain_snapshots WHERE symbol = ? ORDER BY snapshot_timestamp ASC LIMIT ?",
            (self._symbol, limit),
        ).fetchall()

        snapshots = []
        for row in rows:
            snap = self._db_row_to_snapshot(dict(row))
            if snap:
                snapshots.append((snap, dict(row).get("config_version")))

        return self._run_replay(snapshots, config_override)

    def replay_from_file(
        self,
        file_path: str,
        config_override: Optional[LotteryConfig] = None,
    ) -> ReplayResult:
        """Replay snapshots from a JSONL file.

        File format: one JSON object per line with fields:
        - spot_ltp, symbol, expiry, snapshot_timestamp
        - rows: [{strike, option_type, ltp, change, volume, oi, bid, ask, iv}, ...]

        Args:
            file_path: Path to JSONL snapshot file.
            config_override: Config to use for all snapshots.

        Returns:
            ReplayResult with full audit.
        """
        path = Path(file_path)
        if not path.exists():
            logger.error("Replay file not found: %s", file_path)
            result = ReplayResult()
            return result

        snapshots = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    snap = self._json_to_snapshot(data)
                    if snap:
                        snapshots.append((snap, data.get("config_version")))
                except json.JSONDecodeError as e:
                    logger.warning("Skipping invalid JSON line: %s", e)

        return self._run_replay(snapshots, config_override)

    def _run_replay(
        self,
        snapshots: list[tuple[ChainSnapshot, Optional[str]]],
        config_override: Optional[LotteryConfig],
    ) -> ReplayResult:
        """Core replay loop — processes snapshots deterministically."""
        result = ReplayResult()
        result.total_snapshots = len(snapshots)

        if not snapshots:
            logger.warning("No snapshots to replay")
            return result

        cfg = config_override or self._config

        # Initialize components
        validator = DataQualityValidator(config=cfg)
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        broker = PaperBroker(config=cfg)
        capital = CapitalManager(config=cfg, symbol=self._symbol)

        # Confirmation parity — same gate as live path
        from .strategy.confirmation import (
            BreakoutConfirmation, ConfirmationConfig, ConfirmationMode,
        )
        from .calculations.candle_builder import CandleBuilder

        confirmation = BreakoutConfirmation(config=ConfirmationConfig(
            mode=ConfirmationMode(cfg.confirmation.mode),
            quorum=cfg.confirmation.quorum,
            hold_duration_seconds=cfg.confirmation.hold_duration_seconds,
            premium_expansion_min_pct=cfg.confirmation.premium_expansion_min_pct,
            volume_spike_multiplier=cfg.confirmation.volume_spike_multiplier,
            spread_widen_max_pct=cfg.confirmation.spread_widen_max_pct,
        ))
        candle_builder = CandleBuilder(config=cfg, symbol=self._symbol)

        active_trade = None
        prev_snapshot = None

        for snap, stored_config_version in snapshots:
            cycle_start = time.monotonic()
            tracer = CycleTracer(config=cfg, symbol=self._symbol)

            # Feed candle builder every snapshot for continuous candle data
            candle_builder.on_tick(snap.spot_ltp, snap.snapshot_timestamp)

            # ── Validate ───────────────────────────────────────────
            t0 = time.monotonic()
            tracer.record_fetch(snap, 0)
            report = validator.validate(snap, prev_snapshot)
            tracer.record_validation(report, (time.monotonic() - t0) * 1000)

            if report.overall_status == QualityStatus.FAIL:
                result.skipped += 1
                prev_snapshot = snap
                continue

            # ── Calculate ──────────────────────────────────────────
            t0 = time.monotonic()
            rows = compute_base_metrics(snap, cfg)
            rows = compute_advanced_metrics(rows, cfg)
            window = filter_window(rows, snap.spot_ltp, cfg)
            side, bias, avg_c, avg_p = compute_side_bias(window, cfg)
            ext_ce, ext_pe = extrapolate_otm_strikes(rows, snap.spot_ltp, cfg)
            best_ce, best_pe, cands = score_and_select(
                rows, ext_ce, ext_pe, snap.spot_ltp, side, bias, cfg,
            )
            rows = update_rows_with_scores(rows, cands)
            calc_ms = (time.monotonic() - t0) * 1000

            tracer.record_calculations(len(rows), len(window),
                len([r for r in rows if r.call_band_eligible]),
                len([r for r in rows if r.put_band_eligible]),
                calc_ms)
            tracer.record_side_bias(side, bias, avg_c, avg_p)
            tracer.record_strike_scan(len(cands),
                len([c for c in cands if c.option_type == OptionType.CE]),
                len([c for c in cands if c.option_type == OptionType.PE]),
                len(ext_ce), len(ext_pe))
            tracer.record_selection(best_ce, best_pe, 0)

            # ── Check active trade for exit ────────────────────────
            if active_trade and active_trade.status == TradeStatus.OPEN:
                triggers = resolve_triggers(snap.spot_ltp, cfg, snap.strikes)
                # Get current LTP for the traded strike
                current_ltp = self._get_strike_ltp(snap, active_trade.strike, active_trade.option_type)
                if current_ltp is not None:
                    exit_reason = se.evaluate_exit(
                        active_trade, current_ltp, snap.spot_ltp, triggers,
                        snap.snapshot_timestamp,
                    )
                    if exit_reason:
                        lot_size = cfg.paper_trading.lot_size
                        closed = broker.execute_exit(
                            active_trade, current_ltp, exit_reason, active_trade.lots,
                        )
                        capital.record_exit(closed)
                        sm.exit_trade(closed.pnl or 0)
                        active_trade = None
                        result.trades_closed += 1
                        result.trades.append({
                            "trade_id": closed.trade_id,
                            "strike": closed.strike,
                            "side": closed.side.value,
                            "entry": closed.entry_price,
                            "exit": closed.exit_price,
                            "pnl": closed.pnl,
                            "reason": exit_reason.value,
                        })
                        tracer.record_paper_execution(closed, "EXIT")

            # ── Signal for new entry ───────────────────────────────
            if active_trade is None:
                triggers = resolve_triggers(snap.spot_ltp, cfg, snap.strikes)
                signal = se.evaluate_entry(
                    snap.spot_ltp, report.overall_status, triggers,
                    best_ce, best_pe, side,
                    snap.snapshot_id, cfg.version_hash,
                    snap.snapshot_timestamp,
                )
                tracer.record_trade_decision(signal)

                result.signals.append({
                    "timestamp": signal.timestamp.isoformat(),
                    "validity": signal.validity.value,
                    "strike": signal.selected_strike,
                    "state": signal.machine_state.value,
                })

                if signal.validity == SignalValidity.VALID:
                    candidate = sm.context.candidate
                    if candidate:
                        # Feed candle builder with snapshot spot
                        candle_builder.on_tick(snap.spot_ltp, snap.snapshot_timestamp)

                        # Confirmation gate — same logic as live path
                        sm_state = sm.state
                        if sm_state in (MachineState.ZONE_ACTIVE_CE, MachineState.ZONE_ACTIVE_PE):
                            confirmation.on_zone_active(timestamp=snap.snapshot_timestamp)
                        if sm_state == MachineState.CANDIDATE_FOUND:
                            confirmation.on_candidate_found(candidate, timestamp=snap.snapshot_timestamp)

                            direction = "above" if candidate.option_type == OptionType.CE else "below"
                            trigger_price = triggers.upper_trigger if direction == "above" else triggers.lower_trigger

                            conf_result = confirmation.evaluate(
                                candidate=candidate,
                                trigger_price=trigger_price,
                                direction=direction,
                                candle_builder=candle_builder,
                                timestamp=snap.snapshot_timestamp,
                            )

                            if not conf_result.confirmed:
                                result.signals_invalid += 1
                                tracer.record_paper_execution(None, "CONFIRMATION_BLOCKED")
                                confirmation.reset()
                                prev_snapshot = snap
                                continue

                        # Confirmation passed — enter trade
                        result.signals_valid += 1
                        sm.enter_trade()
                        lot_size = cfg.paper_trading.lot_size
                        qty, lots = capital.compute_position_size(candidate.ltp, lot_size)
                        trade = broker.execute_entry(
                            candidate=candidate,
                            symbol=self._symbol,
                            expiry=snap.expiry,
                            qty=qty, lots=lots,
                            capital_before=capital.running_capital,
                            signal_id=signal.signal_id,
                            snapshot_id=snap.snapshot_id,
                            config_version=cfg.version_hash,
                            selection_price=candidate.ltp,
                        )
                        capital.record_entry(trade)
                        active_trade = trade
                        result.trades_entered += 1
                        tracer.record_paper_execution(trade, "ENTRY")
                        confirmation.reset()
                    else:
                        result.signals_valid += 1
                        tracer.record_paper_execution(None, "NO_CANDIDATE")
                else:
                    result.signals_invalid += 1
                    tracer.record_paper_execution(None, "NO_TRADE")

            # ── Finalize cycle ─────────────────────────────────────
            trace = tracer.build()
            result.traces.append({
                "cycle_id": trace.cycle_id,
                "latency": trace.latency_ms,
            })
            result.processed += 1
            prev_snapshot = snap

            # Track max drawdown
            if capital.drawdown > result.max_drawdown:
                result.max_drawdown = capital.drawdown

        # ── Final state ────────────────────────────────────────────
        result.final_capital = capital.running_capital
        result.total_pnl = capital.realized_pnl

        logger.info(
            "Replay complete: %d/%d processed, %d trades, PnL=₹%.2f, drawdown=₹%.2f",
            result.processed, result.total_snapshots,
            result.trades_entered, result.total_pnl, result.max_drawdown,
        )

        return result

    # ── Snapshot Parsers ───────────────────────────────────────────

    @staticmethod
    def _db_row_to_snapshot(row: dict) -> Optional[ChainSnapshot]:
        """Convert a DB row to ChainSnapshot."""
        try:
            rows_data = json.loads(row["rows_json"])
            option_rows = tuple(
                OptionRow(
                    symbol=row["symbol"],
                    expiry=row.get("expiry", ""),
                    strike=float(r["strike"]),
                    option_type=OptionType(r["option_type"]),
                    ltp=float(r.get("ltp", 0)),
                    change=r.get("change"),
                    volume=r.get("volume"),
                    oi=r.get("oi"),
                    bid=r.get("bid"),
                    ask=r.get("ask"),
                    iv=r.get("iv"),
                )
                for r in rows_data
            )
            return ChainSnapshot(
                snapshot_id=row["snapshot_id"],
                symbol=row["symbol"],
                expiry=row.get("expiry", ""),
                spot_ltp=float(row["spot_ltp"]),
                snapshot_timestamp=datetime.fromisoformat(row["snapshot_timestamp"]),
                rows=option_rows,
            )
        except Exception as e:
            logger.warning("Failed to parse DB snapshot: %s", e)
            return None

    @staticmethod
    def _json_to_snapshot(data: dict) -> Optional[ChainSnapshot]:
        """Convert a JSON dict to ChainSnapshot."""
        try:
            option_rows = tuple(
                OptionRow(
                    symbol=data.get("symbol", ""),
                    expiry=data.get("expiry", ""),
                    strike=float(r["strike"]),
                    option_type=OptionType(r["option_type"]),
                    ltp=float(r.get("ltp", 0)),
                    change=r.get("change"),
                    volume=r.get("volume"),
                    oi=r.get("oi"),
                    bid=r.get("bid"),
                    ask=r.get("ask"),
                    iv=r.get("iv"),
                )
                for r in data.get("rows", [])
            )
            ts = data.get("snapshot_timestamp", "")
            if isinstance(ts, str) and ts:
                timestamp = datetime.fromisoformat(ts)
            else:
                timestamp = datetime.now(timezone.utc)

            return ChainSnapshot(
                symbol=data.get("symbol", ""),
                expiry=data.get("expiry", ""),
                spot_ltp=float(data.get("spot_ltp", 0)),
                snapshot_timestamp=timestamp,
                rows=option_rows,
            )
        except Exception as e:
            logger.warning("Failed to parse JSON snapshot: %s", e)
            return None

    @staticmethod
    def _get_strike_ltp(
        snapshot: ChainSnapshot,
        strike: float,
        option_type: OptionType,
    ) -> Optional[float]:
        """Get LTP for a specific strike+type from a snapshot."""
        for row in snapshot.rows:
            if row.strike == strike and row.option_type == option_type:
                return row.ltp
        return None

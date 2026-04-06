"""Lottery pipeline entry point — WebSocket-driven with REST chain refresh.

Data flow:
  WebSocket → spot ticks every ~1s (no rate limit)
  REST      → option chain every 30s (configurable, cached between)
  Pipeline  → runs calculation cycle on each tick

Usage:
    python -m engines.lottery.main                    # NIFTY default
    python -m engines.lottery.main --symbol BANKNIFTY
    python -m engines.lottery.main --config path/to/settings.yaml
"""

import argparse
import asyncio
import logging
import signal
import sys
import time
import threading
from dataclasses import replace as dc_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .calculations import (
    compute_advanced_metrics,
    compute_base_metrics,
    compute_side_bias,
    extrapolate_otm_strikes,
    filter_window,
    score_and_select,
    update_rows_with_scores,
)
from .config import LotteryConfig, load_config
from .data_fetch import FyersAdapter, FyersWebSocketClient
from .data_fetch.fyers_adapter import _DEFAULT_FYERS_SYMBOLS
from .data_quality import DataQualityValidator
from .debugging import CycleTracer, FailureBucket, setup_logger
from .memory_state import RuntimeStateManager
from .models import (
    CalculatedSnapshot,
    ChainSnapshot,
    ExitReason,
    MachineState,
    OptionType,
    QualityStatus,
    SignalValidity,
    TradeStatus,
    UnderlyingTick,
)
from .paper_trading import CapitalManager, PaperBroker
from .storage import LotteryDB
from .strategy import RiskGuard, SignalEngine, StateMachine, resolve_triggers


class LotteryPipeline:
    """Main orchestrator — WebSocket-driven pipeline.

    WebSocket provides real-time spot ticks.
    REST fetches full option chain at configurable intervals.
    Pipeline cycle runs on each spot tick using cached chain.
    """

    def __init__(
        self,
        config: LotteryConfig,
        symbol: str,
        exchange: str = "NSE",
    ) -> None:
        self._config = config
        self._symbol = symbol
        self._exchange = exchange
        self._running = False

        # ── Initialize all components ──────────────────────────────
        self._logger = setup_logger(config, symbol, f"lottery.{symbol}")
        self._adapter = FyersAdapter(config=config)
        self._validator = DataQualityValidator(config=config)
        self._sm = StateMachine(config=config)
        self._se = SignalEngine(config=config, state_machine=self._sm)
        self._broker = PaperBroker(config=config)
        self._capital = CapitalManager(config=config, symbol=symbol)
        self._risk = RiskGuard(config=config)
        self._db = LotteryDB(config=config, symbol=symbol)
        self._rsm = RuntimeStateManager(config=config, symbol=symbol)
        self._failures = FailureBucket()

        # ── Chain cache ────────────────────────────────────────────
        self._cached_chain: Optional[ChainSnapshot] = None
        self._last_chain_fetch: float = 0
        self._chain_lock = threading.Lock()

        # ── WebSocket client ───────────────────────────────────────
        self._ws_client: Optional[FyersWebSocketClient] = None
        self._last_ws_ltp: Optional[float] = None

        # Save config version
        self._db.save_config_version(config)

        self._logger.info(
            "Pipeline initialized: %s @ %s | config=%s | capital=%.2f | chain_refresh=%ds",
            symbol, exchange, config.version_hash, config.paper_trading.starting_capital,
            config.polling.chain_refresh_seconds,
        )

    @property
    def rsm(self) -> RuntimeStateManager:
        return self._rsm

    @property
    def db(self) -> LotteryDB:
        return self._db

    @property
    def config(self) -> LotteryConfig:
        return self._config

    def stop(self) -> None:
        """Signal the pipeline to stop."""
        self._running = False
        if self._ws_client:
            self._ws_client.stop()
        self._logger.info("Stop requested")

    # ── Main Loop ──────────────────────────────────────────────────

    def run(self) -> None:
        """Run the pipeline — WebSocket for spot, REST for chain refresh."""
        self._running = True
        interval = self._config.polling.interval_seconds

        # ── Step 1: Initial chain fetch via REST ───────────────────
        self._logger.info("Fetching initial option chain via REST...")
        self._refresh_chain()

        # ── Step 1b: If REST failed, try loading last snapshot from DB ──
        if self._cached_chain is None:
            self._load_last_snapshot_from_db()

        # ── Step 2: Start WebSocket for spot ticks ─────────────────
        self._start_websocket()

        # ── Step 3: Main loop — tick-driven cycles ─────────────────
        self._logger.info(
            "Pipeline running: WS for spot ticks, REST chain every %ds",
            self._config.polling.chain_refresh_seconds,
        )

        while self._running:
            cycle_start = time.monotonic()

            try:
                # Refresh chain if needed (REST, every N seconds)
                self._maybe_refresh_chain()

                # Run pipeline cycle with latest spot + cached chain
                self._run_cycle()
            except Exception as e:
                self._logger.error("Cycle error: %s", e, exc_info=True)
                self._failures.record("PARSING", str(e))

            # Sleep for remainder of interval
            elapsed = time.monotonic() - cycle_start
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        self._logger.info("Pipeline stopped")
        self._db.close()

    async def run_async(self) -> None:
        """Async version of run loop."""
        self._running = True
        interval = self._config.polling.interval_seconds

        self._logger.info("Fetching initial option chain via REST...")
        self._refresh_chain()
        self._start_websocket()

        self._logger.info("Pipeline running (async)")

        while self._running:
            cycle_start = time.monotonic()

            try:
                self._maybe_refresh_chain()
                self._run_cycle()
            except Exception as e:
                self._logger.error("Cycle error: %s", e, exc_info=True)
                self._failures.record("PARSING", str(e))

            elapsed = time.monotonic() - cycle_start
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        self._logger.info("Pipeline stopped")
        self._db.close()

    # ── WebSocket ──────────────────────────────────────────────────

    def _load_last_snapshot_from_db(self) -> None:
        """Load the most recent snapshot from DB as fallback when REST is down."""
        try:
            from .replay import ReplayEngine
            conn = self._db._get_conn()
            row = conn.execute(
                """SELECT * FROM raw_chain_snapshots
                   WHERE symbol = ? AND row_count > 10
                   ORDER BY snapshot_timestamp DESC LIMIT 1""",
                (self._symbol,),
            ).fetchone()

            if row:
                snapshot = ReplayEngine._db_row_to_snapshot(dict(row))
                if snapshot:
                    with self._chain_lock:
                        self._cached_chain = snapshot
                        self._last_chain_fetch = time.monotonic()
                    self._logger.info(
                        "Loaded last snapshot from DB: %s spot=%.2f rows=%d",
                        snapshot.snapshot_id, snapshot.spot_ltp, len(snapshot.rows),
                    )
                    return

            self._logger.warning("No usable snapshot in DB to fall back on")
        except Exception as e:
            self._logger.warning("DB snapshot fallback failed: %s", e)

    def _start_websocket(self) -> None:
        """Start FYERS WebSocket for real-time spot ticks."""
        try:
            client = self._adapter._get_client()
            if not client.access_token or not client.client_id:
                self._logger.warning("No FYERS credentials for WebSocket — falling back to REST-only")
                return

            fyers_symbol = _DEFAULT_FYERS_SYMBOLS.get(self._symbol.upper())
            if not fyers_symbol:
                self._logger.warning("No FYERS symbol for %s — WebSocket disabled", self._symbol)
                return

            self._ws_client = FyersWebSocketClient(
                config=self._config,
                access_token=client.access_token,
                client_id=client.client_id,
                on_tick=self._on_ws_tick,
            )
            self._ws_client.start([fyers_symbol])
            self._logger.info("WebSocket started for %s (%s)", self._symbol, fyers_symbol)

        except Exception as e:
            self._logger.warning("WebSocket start failed: %s — using REST-only", e)
            self._ws_client = None

    def _on_ws_tick(self, tick: dict) -> None:
        """Callback for each WebSocket spot tick."""
        ltp = tick.get("ltp")
        if ltp and ltp > 0:
            self._last_ws_ltp = ltp
            self._rsm.update_spot(ltp, tick.get("timestamp"))

    # ── Chain Refresh (REST) ───────────────────────────────────────

    def _refresh_chain(self) -> None:
        """Fetch fresh option chain via REST API."""
        max_retries = self._config.polling.retry_max_attempts
        backoff_base = self._config.polling.retry_backoff_base_ms / 1000

        for attempt in range(max_retries):
            snapshot = self._adapter.fetch_option_chain(
                self._symbol, self._exchange, "",
            )
            if snapshot:
                with self._chain_lock:
                    self._cached_chain = snapshot
                    self._last_chain_fetch = time.monotonic()
                self._logger.info(
                    "Chain refreshed: %d rows, spot=%.2f, %d strikes",
                    len(snapshot.rows), snapshot.spot_ltp, len(snapshot.strikes),
                )
                return

            if attempt < max_retries - 1:
                wait = backoff_base * (2 ** attempt)
                self._logger.warning(
                    "Chain fetch %d/%d failed, retry in %.1fs",
                    attempt + 1, max_retries, wait,
                )
                time.sleep(wait)

        self._logger.error("Chain refresh failed after %d retries", max_retries)
        self._failures.record("DATA_FETCH", "chain refresh failed all retries")

    def _maybe_refresh_chain(self) -> None:
        """Refresh chain if enough time has passed since last fetch."""
        now = time.monotonic()
        if now - self._last_chain_fetch >= self._config.polling.chain_refresh_seconds:
            self._refresh_chain()

    def _get_current_snapshot(self) -> Optional[ChainSnapshot]:
        """Get the current snapshot — cached chain with latest WS spot overlaid."""
        with self._chain_lock:
            if self._cached_chain is None:
                return None

            chain = self._cached_chain

            # Overlay WebSocket spot if fresher than chain's spot
            if self._last_ws_ltp and self._last_ws_ltp > 0:
                ws_tick = self._ws_client.last_tick_time if self._ws_client else None
                chain_time = chain.snapshot_timestamp

                # Use WS spot if available (it's always more recent)
                if self._last_ws_ltp != chain.spot_ltp:
                    chain = ChainSnapshot(
                        snapshot_id=chain.snapshot_id,
                        symbol=chain.symbol,
                        expiry=chain.expiry,
                        spot_ltp=self._last_ws_ltp,
                        snapshot_timestamp=ws_tick or chain.snapshot_timestamp,
                        rows=chain.rows,
                        spot_tick=UnderlyingTick(
                            symbol=self._symbol,
                            exchange=self._exchange,
                            ltp=self._last_ws_ltp,
                            timestamp=ws_tick or datetime.now(timezone.utc),
                        ),
                    )

            return chain

    # ── Single Cycle ───────────────────────────────────────────────

    def _run_cycle(self) -> None:
        """Execute one full pipeline cycle."""
        self._rsm.start_cycle()
        tracer = CycleTracer(config=self._config, symbol=self._symbol)
        cycle_start = time.monotonic()

        # ── 1. Get snapshot (cached chain + WS spot) ───────────────
        t0 = time.monotonic()
        snapshot = self._get_current_snapshot()
        fetch_ms = (time.monotonic() - t0) * 1000
        tracer.record_fetch(snapshot, fetch_ms)

        if snapshot is None:
            self._failures.record("DATA_FETCH", "no cached chain available")
            self._finalize_cycle(tracer, cycle_start)
            return

        self._rsm.update_chain_snapshot(snapshot)
        self._rsm.update_spot(snapshot.spot_ltp, snapshot.snapshot_timestamp)

        # ── 2. Validate ───────────────────────────────────────────
        t0 = time.monotonic()
        report = self._validator.validate(snapshot)
        val_ms = (time.monotonic() - t0) * 1000
        tracer.record_validation(report, val_ms)
        self._rsm.update_quality(report)

        # Persist snapshot + quality (only on chain refresh, not every tick)
        if fetch_ms < 1:  # cached snapshot, skip heavy persistence
            pass
        else:
            try:
                self._db.save_chain_snapshot(snapshot)
                self._db.save_quality_report(report)
            except Exception as e:
                self._failures.record("PERSISTENCE", f"snapshot save: {e}")

        if report.overall_status == QualityStatus.FAIL:
            self._failures.record("VALIDATION", "quality FAIL")
            self._run_signal_no_data(tracer, snapshot, report)
            self._finalize_cycle(tracer, cycle_start)
            return

        # ── 3. Calculate ──────────────────────────────────────────
        t0 = time.monotonic()
        rows = compute_base_metrics(snapshot, self._config)
        rows = compute_advanced_metrics(rows, self._config)
        window = filter_window(rows, snapshot.spot_ltp, self._config)
        side, bias, avg_c, avg_p = compute_side_bias(window, self._config)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, snapshot.spot_ltp, self._config)
        best_ce, best_pe, all_cands = score_and_select(
            rows, ext_ce, ext_pe, snapshot.spot_ltp, side, bias, self._config,
        )
        rows = update_rows_with_scores(rows, all_cands)
        calc_ms = (time.monotonic() - t0) * 1000

        calc_snapshot = CalculatedSnapshot(
            snapshot_id=snapshot.snapshot_id,
            symbol=self._symbol,
            spot_ltp=snapshot.spot_ltp,
            config_version=self._config.version_hash,
            rows=tuple(rows),
            extrapolated_ce=tuple(ext_ce),
            extrapolated_pe=tuple(ext_pe),
            avg_call_decay=avg_c,
            avg_put_decay=avg_p,
            bias_score=bias,
            preferred_side=side,
        )
        self._rsm.update_calculated(calc_snapshot)

        band_ce = len([r for r in rows if r.call_band_eligible])
        band_pe = len([r for r in rows if r.put_band_eligible])
        tracer.record_calculations(len(rows), len(window), band_ce, band_pe, calc_ms)
        tracer.record_side_bias(side, bias, avg_c, avg_p)
        tracer.record_strike_scan(
            len(all_cands),
            len([c for c in all_cands if c.option_type == OptionType.CE]),
            len([c for c in all_cands if c.option_type == OptionType.PE]),
            len(ext_ce), len(ext_pe),
        )
        tracer.record_selection(best_ce, best_pe, 0)

        # ── 4. Check active trade for exit ────────────────────────
        active = self._rsm.state.active_trade
        if active and active.status == TradeStatus.OPEN:
            self._check_exit(active, snapshot, tracer)

        # ── 5. Signal for new entry ───────────────────────────────
        if self._rsm.state.active_trade is None:
            triggers = resolve_triggers(snapshot.spot_ltp, self._config, snapshot.strikes)

            signal_event = self._se.evaluate_entry(
                snapshot.spot_ltp, report.overall_status, triggers,
                best_ce, best_pe, side,
                snapshot.snapshot_id, self._config.version_hash,
            )
            tracer.record_trade_decision(signal_event)
            self._rsm.update_signal(signal_event)
            self._rsm.update_machine_state(signal_event.machine_state)

            try:
                self._db.save_signal(signal_event)
            except Exception as e:
                self._failures.record("PERSISTENCE", f"signal save: {e}")

            # ── 6. Entry if valid ─────────────────────────────────
            if signal_event.validity == SignalValidity.VALID:
                self._execute_entry(signal_event, snapshot, tracer)
            else:
                tracer.record_paper_execution(None, "NO_TRADE")
        else:
            tracer.record_paper_execution(None, "IN_TRADE")

        # ── Finalize ──────────────────────────────────────────────
        self._finalize_cycle(tracer, cycle_start)

    # ── Sub-steps ──────────────────────────────────────────────────

    def _check_exit(self, trade, snapshot, tracer):
        """Check if active trade should be exited."""
        triggers = resolve_triggers(snapshot.spot_ltp, self._config, snapshot.strikes)

        current_ltp = None
        for row in snapshot.rows:
            if row.strike == trade.strike and row.option_type == trade.option_type:
                current_ltp = row.ltp
                break

        if current_ltp is None:
            self._logger.warning("Cannot find LTP for K=%s %s", trade.strike, trade.option_type.value)
            return

        self._rsm.update_trade_ltp(current_ltp)

        exit_reason = self._se.evaluate_exit(
            trade, current_ltp, snapshot.spot_ltp, triggers,
        )

        if exit_reason:
            closed = self._broker.execute_exit(
                trade, current_ltp, exit_reason, trade.lots,
            )
            self._capital.record_exit(closed)
            self._sm.exit_trade(closed.pnl or 0)
            self._rsm.close_active_trade(closed)
            self._rsm.update_machine_state(self._sm.state)
            tracer.record_paper_execution(closed, "EXIT")

            try:
                self._db.save_trade(closed)
                for entry in self._capital.ledger[-1:]:
                    self._db.save_ledger_entry(entry)
            except Exception as e:
                self._failures.record("PERSISTENCE", f"trade exit save: {e}")

            self._logger.info(
                "EXIT: K=%s %s exit=%.2f PnL=%.2f (%s)",
                closed.strike, closed.side.value,
                closed.exit_price, closed.pnl, exit_reason.value,
            )

    def _execute_entry(self, signal_event, snapshot, tracer):
        """Execute a paper trade entry."""
        candidate = self._sm.context.candidate
        if not candidate:
            tracer.record_paper_execution(None, "NO_CANDIDATE")
            return

        lot_size = self._adapter.get_lot_size(self._symbol)
        risk_result = self._risk.check_entry(
            self._sm.context, self._capital,
            QualityStatus.PASS, candidate.ltp, lot_size,
        )

        if not risk_result.allowed:
            self._logger.info("Entry blocked by risk: %s", risk_result.reason)
            tracer.record_paper_execution(None, f"RISK_BLOCKED: {risk_result.reason}")
            return

        qty, lots = self._capital.compute_position_size(candidate.ltp, lot_size)

        bid, ask = None, None
        for row in snapshot.rows:
            if row.strike == candidate.strike and row.option_type == candidate.option_type:
                bid, ask = row.bid, row.ask
                break

        self._sm.enter_trade()
        trade = self._broker.execute_entry(
            candidate=candidate,
            symbol=self._symbol,
            expiry=snapshot.expiry,
            qty=qty, lots=lots,
            capital_before=self._capital.running_capital,
            signal_id=signal_event.signal_id,
            snapshot_id=snapshot.snapshot_id,
            config_version=self._config.version_hash,
            bid=bid, ask=ask,
        )
        self._capital.record_entry(trade)
        self._rsm.set_active_trade(trade)
        self._rsm.update_machine_state(self._sm.state)
        tracer.record_paper_execution(trade, "ENTRY")

        try:
            self._db.save_trade(trade)
            for entry in self._capital.ledger[-1:]:
                self._db.save_ledger_entry(entry)
        except Exception as e:
            self._failures.record("PERSISTENCE", f"trade entry save: {e}")

        self._logger.info(
            "ENTRY: K=%s %s entry=%.2f qty=%d SL=%.2f T1=%.2f",
            trade.strike, trade.side.value,
            trade.entry_price, trade.qty, trade.sl, trade.t1,
        )

    def _run_signal_no_data(self, tracer, snapshot, report):
        """Run signal engine when quality fails."""
        triggers = resolve_triggers(snapshot.spot_ltp, self._config, snapshot.strikes)
        signal_event = self._se.evaluate_entry(
            snapshot.spot_ltp, report.overall_status, triggers,
            None, None, None,
            snapshot.snapshot_id, self._config.version_hash,
        )
        tracer.record_trade_decision(signal_event)
        self._rsm.update_signal(signal_event)
        self._rsm.update_machine_state(signal_event.machine_state)
        tracer.record_paper_execution(None, "QUALITY_FAIL")

    def _finalize_cycle(self, tracer, cycle_start):
        """Record trace, update latency."""
        total_ms = (time.monotonic() - cycle_start) * 1000
        self._rsm.end_cycle(total_ms)

        trace = tracer.build()
        self._rsm.add_debug_trace(trace)

        try:
            self._db.save_debug_trace(trace)
        except Exception as e:
            self._failures.record("PERSISTENCE", f"debug trace save: {e}")


# ── Global pipeline registry ──────────────────────────────────────────────

_pipelines: dict[str, LotteryPipeline] = {}


def get_pipeline(symbol: str) -> Optional[LotteryPipeline]:
    return _pipelines.get(symbol.upper())


def register_pipeline(pipeline: LotteryPipeline) -> None:
    _pipelines[pipeline._symbol.upper()] = pipeline


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Lottery Strike Picker Pipeline")
    parser.add_argument("--symbol", default="NIFTY", help="Instrument symbol")
    parser.add_argument("--exchange", default="NSE", help="Exchange code")
    parser.add_argument("--config", default=None, help="Path to settings.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    pipeline = LotteryPipeline(config=config, symbol=args.symbol, exchange=args.exchange)
    register_pipeline(pipeline)

    def handle_signal(sig, frame):
        pipeline.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    pipeline.run()


if __name__ == "__main__":
    main()

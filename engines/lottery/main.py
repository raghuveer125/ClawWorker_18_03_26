"""Lottery pipeline — dual-cycle architecture with breakout confirmation.

Two cycles:
  Analysis cycle (every 30s): full chain → validate → calculate → score → candidates
  Trigger cycle  (every 1s):  WS spot + candidate quotes → trigger → confirm → entry/exit

Data flow:
  FYERS WebSocket ──→ spot ticks (real-time) ──→ CandleBuilder + TriggerSnapshot
  FYERS REST      ──→ full chain (every 30s) ──→ AnalysisSnapshot
  FYERS REST      ──→ candidate quotes (1-5s) ──→ TriggerSnapshot

Usage:
    python -m engines.lottery.main --symbol NIFTY
"""

import argparse
import asyncio
import logging
import signal
import sys
import time
import threading
from datetime import datetime, timezone
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
from .calculations.candle_builder import CandleBuilder
from .config import LotteryConfig, load_config
from .data_fetch import FyersAdapter, FyersWebSocketClient
from .data_fetch.fyers_adapter import _DEFAULT_FYERS_SYMBOLS
from .data_quality import DataQualityValidator
from .debugging import CycleTracer, FailureBucket, setup_logger
from .memory_state import RuntimeStateManager
from .models import (
    AnalysisSnapshot,
    CalculatedSnapshot,
    CandidateQuote,
    ExitReason,
    MachineState,
    OptionType,
    QualityStatus,
    SignalValidity,
    TradeStatus,
    TriggerSnapshot,
    UnderlyingTick,
)
from .paper_trading import CapitalManager, PaperBroker
from .storage import LotteryDB
from .strategy import RiskGuard, SignalEngine, StateMachine, resolve_triggers
from .strategy.confirmation import (
    BreakoutConfirmation,
    ConfirmationConfig,
    ConfirmationMode,
)


class LotteryPipeline:
    """Dual-cycle pipeline orchestrator.

    Analysis cycle: full chain → validate → calculate → score → select candidates
    Trigger cycle:  WS spot + candidate quotes → trigger → confirm → entry/exit
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

        # ── Core components ────────────────────────────────────────
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

        # ── Telegram alerts ────────────────────────────────────────
        from .alerting import AlertNotifier
        self._alerts = AlertNotifier(
            token_env=config.alerting.telegram_bot_token_env,
            chat_id_env=config.alerting.telegram_chat_id_env,
            enabled=config.alerting.enabled,
        )

        # ── Candle builder ─────────────────────────────────────────
        self._candle_builder = CandleBuilder(config=config, symbol=symbol)

        # ── DTE detector + strategy profile ────────────────────────
        from .strategy import DTEDetector, StrategyMode
        manual_mode = None
        if config.strategy.mode != "AUTO":
            try:
                manual_mode = StrategyMode(config.strategy.mode)
            except ValueError:
                pass
        self._dte_detector = DTEDetector(symbol=symbol, manual_override=manual_mode)
        self._active_profile = self._dte_detector.detect()

        # ── Breakout confirmation (uses profile settings) ──────────
        conf_mode_str = config.confirmation.mode
        if self._active_profile.confirmation_mode is not None:
            conf_mode_str = self._active_profile.confirmation_mode.value
        self._confirmation = BreakoutConfirmation(config=ConfirmationConfig(
            mode=ConfirmationMode(conf_mode_str),
            quorum=self._active_profile.confirmation_quorum or config.confirmation.quorum,
            hold_duration_seconds=self._active_profile.hold_duration_seconds or config.confirmation.hold_duration_seconds,
            premium_expansion_min_pct=self._active_profile.premium_expansion_min_pct or config.confirmation.premium_expansion_min_pct,
            volume_spike_multiplier=config.confirmation.volume_spike_multiplier,
            spread_widen_max_pct=config.confirmation.spread_widen_max_pct,
        ))

        # ── Adaptive refresh scheduler ─────────────────────────────
        from .strategy.refresh_scheduler import RefreshScheduler, RefreshConfig
        self._refresh_sched = RefreshScheduler(
            base_config=RefreshConfig(
                chain_idle_seconds=config.refresh.chain_idle_seconds,
                chain_active_seconds=config.refresh.chain_active_seconds,
                candidate_zone_seconds=config.refresh.candidate_zone_seconds,
                candidate_found_seconds=config.refresh.candidate_found_seconds,
                trade_quote_seconds=config.refresh.trade_quote_seconds,
                spot_drift_threshold=config.refresh.spot_drift_threshold,
                candidate_stale_seconds=config.refresh.candidate_stale_seconds,
            ),
            profile=self._active_profile,
        )

        # ── Analysis state (updated every 30s) ─────────────────────
        self._last_analysis: Optional[AnalysisSnapshot] = None
        self._last_analysis_time: float = 0
        self._chain_lock = threading.Lock()

        # ── WebSocket state ────────────────────────────────────────
        self._ws_client: Optional[FyersWebSocketClient] = None
        self._last_ws_ltp: Optional[float] = None
        self._last_ws_time: Optional[datetime] = None

        # ── Candidate refresh state ────────────────────────────────
        self._cached_candidate_quotes: dict = {}

        # Save config version
        self._db.save_config_version(config)

        self._logger.info(
            "Pipeline initialized: %s @ %s | config=%s | capital=%.2f | "
            "profile=%s | DTE=%s | confirmation=%s(%d)",
            symbol, exchange, config.version_hash,
            config.paper_trading.starting_capital,
            self._active_profile.mode.value,
            self._dte_detector.dte,
            self._confirmation._config.mode.value,
            self._confirmation._config.quorum,
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
        self._running = False
        if self._ws_client:
            self._ws_client.stop()
        self._logger.info("Stop requested")

    # ── Main Loop ──────────────────────────────────────────────────

    def run(self) -> None:
        """Run the dual-cycle pipeline."""
        self._running = True
        interval = self._config.polling.interval_seconds

        # Step 1: Initial analysis cycle (full chain fetch)
        self._logger.info("Running initial analysis cycle...")
        self._run_analysis_cycle()

        # Step 1b: DB fallback if REST failed
        if self._last_analysis is None or not self._last_analysis.is_valid:
            self._load_last_snapshot_from_db()

        # Step 2: Warmup candles from historical API
        self._warmup_candles()

        # Step 3: Start WebSocket
        self._start_websocket()

        # Step 4: Main loop — trigger cycles with periodic analysis refreshes
        self._logger.info("Pipeline running: trigger every %ds, analysis every %ds",
            interval, self._config.polling.chain_refresh_seconds)

        self._alerts.on_pipeline_start(
            symbol=self._symbol,
            profile=self._active_profile.mode.value,
            dte=self._dte_detector.dte or -1,
            capital=self._config.paper_trading.starting_capital,
        )

        while self._running:
            cycle_start = time.monotonic()

            try:
                # Check if analysis cycle is due
                self._maybe_run_analysis_cycle()

                # Always run trigger cycle
                self._run_trigger_cycle()
            except Exception as e:
                self._logger.error("Cycle error: %s", e, exc_info=True)
                self._failures.record("PARSING", str(e))
                self._alerts.on_system_error(self._symbol, str(e))

            elapsed = time.monotonic() - cycle_start
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        self._alerts.on_pipeline_stop(
            symbol=self._symbol,
            trades=self._sm.context.daily_trade_count,
            pnl=self._capital.realized_pnl,
        )
        self._logger.info("Pipeline stopped")
        self._db.close()

    async def run_async(self) -> None:
        """Async version."""
        self._running = True
        interval = self._config.polling.interval_seconds

        self._run_analysis_cycle()
        if self._last_analysis is None or not self._last_analysis.is_valid:
            self._load_last_snapshot_from_db()
        self._warmup_candles()
        self._start_websocket()

        while self._running:
            cycle_start = time.monotonic()
            try:
                self._maybe_run_analysis_cycle()
                self._run_trigger_cycle()
            except Exception as e:
                self._logger.error("Cycle error: %s", e, exc_info=True)
            elapsed = time.monotonic() - cycle_start
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)

        self._db.close()

    # ══════════════════════════════════════════════════════════════
    # ANALYSIS CYCLE — every 30s (full chain)
    # ══════════════════════════════════════════════════════════════

    def _maybe_run_analysis_cycle(self) -> None:
        """Run analysis cycle if refresh scheduler says it's due."""
        decision = self._refresh_sched.should_refresh(
            state=self._sm.state,
            current_spot=self._last_ws_ltp,
            has_candidates=bool(self._last_analysis and self._last_analysis.all_candidates),
        )
        if decision.refresh_chain:
            self._logger.debug("Chain refresh triggered: %s", decision.chain_reason)
            self._run_analysis_cycle()

    def _run_analysis_cycle(self) -> None:
        """Full chain → validate → calculate → score → select candidates."""
        t_start = time.monotonic()

        # ── Fetch full chain ───────────────────────────────────────
        snapshot = self._fetch_full_chain()
        if snapshot is None:
            self._failures.record("DATA_FETCH", "analysis chain fetch failed")
            return

        # ── Validate ───────────────────────────────────────────────
        report = self._validator.validate(snapshot)
        self._rsm.update_quality(report)

        # Persist
        try:
            self._db.save_chain_snapshot(snapshot)
            self._db.save_quality_report(report)
        except Exception as e:
            self._failures.record("PERSISTENCE", f"analysis save: {e}")

        if report.overall_status == QualityStatus.FAIL:
            self._failures.record("VALIDATION", "analysis quality FAIL")
            # Still update the analysis snapshot (with quality=FAIL)
            with self._chain_lock:
                self._last_analysis = AnalysisSnapshot(
                    symbol=self._symbol, spot_ltp=snapshot.spot_ltp,
                    chain=snapshot, quality=report,
                    config_version=self._config.version_hash,
                )
                self._last_analysis_time = time.monotonic()
                self._refresh_sched.record_chain_refresh(snapshot.spot_ltp)
            self._rsm.update_chain_snapshot(snapshot)
            return

        # ── Calculate ──────────────────────────────────────────────
        rows = compute_base_metrics(snapshot, self._config)
        rows = compute_advanced_metrics(rows, self._config)
        window = filter_window(rows, snapshot.spot_ltp, self._config)
        side, bias, avg_c, avg_p = compute_side_bias(window, self._config)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, snapshot.spot_ltp, self._config)
        best_ce, best_pe, all_cands = score_and_select(
            rows, ext_ce, ext_pe, snapshot.spot_ltp, side, bias, self._config,
        )
        rows = update_rows_with_scores(rows, all_cands)

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

        # ── Build AnalysisSnapshot ─────────────────────────────────
        with self._chain_lock:
            self._last_analysis = AnalysisSnapshot(
                symbol=self._symbol,
                expiry=snapshot.expiry,
                spot_ltp=snapshot.spot_ltp,
                chain=snapshot,
                calculated=calc_snapshot,
                quality=report,
                best_ce=best_ce,
                best_pe=best_pe,
                all_candidates=tuple(all_cands),
                config_version=self._config.version_hash,
            )
            self._last_analysis_time = time.monotonic()

        self._rsm.update_chain_snapshot(snapshot)
        self._rsm.update_calculated(calc_snapshot)

        # Persist calculations
        try:
            self._db.save_calculated_rows(
                snapshot.snapshot_id, self._symbol, snapshot.spot_ltp,
                self._config.version_hash, rows,
            )
        except Exception as e:
            self._failures.record("PERSISTENCE", f"calc save: {e}")

        elapsed = (time.monotonic() - t_start) * 1000
        self._logger.info(
            "Analysis cycle: spot=%.2f quality=%s candidates=%d (CE=%s PE=%s) %.1fms",
            snapshot.spot_ltp, report.overall_status.value, len(all_cands),
            f"K={best_ce.strike:.0f}" if best_ce else "None",
            f"K={best_pe.strike:.0f}" if best_pe else "None",
            elapsed,
        )

    # ══════════════════════════════════════════════════════════════
    # TRIGGER CYCLE — every 1s (live spot + candidate quotes)
    # ══════════════════════════════════════════════════════════════

    def _run_trigger_cycle(self) -> None:
        """Live spot + candidate quotes → trigger → confirm → entry/exit."""
        self._rsm.start_cycle()
        tracer = CycleTracer(config=self._config, symbol=self._symbol)
        cycle_start = time.monotonic()

        analysis = self._last_analysis
        if analysis is None or not analysis.is_valid:
            self._rsm.end_cycle((time.monotonic() - cycle_start) * 1000)
            return

        # ── Get live spot ──────────────────────────────────────────
        live_spot = self._last_ws_ltp or analysis.spot_ltp
        spot_time = self._last_ws_time or datetime.now(timezone.utc)
        self._rsm.update_spot(live_spot, spot_time)

        # Feed candle builder
        self._candle_builder.on_tick(live_spot, spot_time)

        # ── Refresh candidate quotes if needed ─────────────────────
        self._maybe_refresh_candidate_quotes(analysis)

        # ── Build TriggerSnapshot ──────────────────────────────────
        trigger_snap = self._build_trigger_snapshot(live_spot, analysis)
        tracer.record_fetch(analysis.chain, 0)

        # ── Quality gate ───────────────────────────────────────────
        quality_status = analysis.quality.overall_status if analysis.quality else QualityStatus.FAIL
        if quality_status == QualityStatus.FAIL:
            self._finalize_trigger_cycle(tracer, cycle_start)
            return

        # ── Check active trade for exit ────────────────────────────
        active = self._rsm.state.active_trade
        if active and active.status == TradeStatus.OPEN:
            self._check_exit(active, live_spot, trigger_snap, analysis, tracer)

        # ── Signal for new entry ───────────────────────────────────
        if self._rsm.state.active_trade is None:
            triggers = resolve_triggers(live_spot, self._config, analysis.chain.strikes)

            # Use analysis candidates but live spot for trigger detection
            signal_event = self._se.evaluate_entry(
                live_spot, quality_status, triggers,
                analysis.best_ce, analysis.best_pe,
                analysis.calculated.preferred_side if analysis.calculated else None,
                analysis.chain.snapshot_id, self._config.version_hash,
            )
            tracer.record_trade_decision(signal_event)
            self._rsm.update_signal(signal_event)
            self._rsm.update_machine_state(signal_event.machine_state)

            try:
                self._db.save_signal(signal_event)
            except Exception as e:
                self._failures.record("PERSISTENCE", f"signal save: {e}")

            # ── Confirmation gate ──────────────────────────────────
            if signal_event.validity == SignalValidity.VALID:
                # Track confirmation state
                sm_state = self._sm.state
                if sm_state == MachineState.ZONE_ACTIVE_CE or sm_state == MachineState.ZONE_ACTIVE_PE:
                    self._confirmation.on_zone_active()
                if sm_state == MachineState.CANDIDATE_FOUND and self._sm.context.candidate:
                    self._confirmation.on_candidate_found(self._sm.context.candidate)

                    # Evaluate confirmation
                    candidate = self._sm.context.candidate
                    direction = "above" if candidate.option_type == OptionType.CE else "below"
                    trigger_price = triggers.upper_trigger if direction == "above" else triggers.lower_trigger

                    # Get candidate's live volume for confirmation
                    cq = trigger_snap.get_candidate_quote(candidate.strike, candidate.option_type)
                    current_vol = cq.volume if cq else candidate.volume
                    recent_avg_vol = float(candidate.volume or 0) * 0.8  # rough estimate
                    current_spread = cq.spread_pct if cq else candidate.spread_pct

                    conf_result = self._confirmation.evaluate(
                        candidate=candidate,
                        trigger_price=trigger_price,
                        direction=direction,
                        candle_builder=self._candle_builder,
                        current_volume=current_vol,
                        recent_avg_volume=recent_avg_vol,
                        current_spread_pct=current_spread,
                    )

                    if conf_result.confirmed:
                        self._execute_entry(signal_event, analysis, trigger_snap, tracer)
                    else:
                        self._logger.debug(
                            "Entry blocked by confirmation: %d/%d checks",
                            conf_result.checks_passed, conf_result.checks_total,
                        )
                        tracer.record_paper_execution(
                            None, f"CONFIRMATION_BLOCKED: {conf_result.checks_passed}/{conf_result.checks_total}",
                        )
                else:
                    tracer.record_paper_execution(None, "NO_CANDIDATE")
            else:
                tracer.record_paper_execution(None, "NO_TRADE")
        else:
            tracer.record_paper_execution(None, "IN_TRADE")

        self._finalize_trigger_cycle(tracer, cycle_start)

    # ── Sub-steps ──────────────────────────────────────────────────

    def _fetch_full_chain(self):
        """Fetch full option chain with retry."""
        max_retries = self._config.polling.retry_max_attempts
        backoff_base = self._config.polling.retry_backoff_base_ms / 1000

        for attempt in range(max_retries):
            snapshot = self._adapter.fetch_option_chain(
                self._symbol, self._exchange, "",
            )
            if snapshot:
                return snapshot
            if attempt < max_retries - 1:
                wait = backoff_base * (2 ** attempt)
                self._logger.warning("Chain fetch %d/%d failed, retry %.1fs", attempt + 1, max_retries, wait)
                time.sleep(wait)

        return None

    def _maybe_refresh_candidate_quotes(self, analysis: AnalysisSnapshot) -> None:
        """Refresh candidate quotes if refresh scheduler says it's due."""
        decision = self._refresh_sched.should_refresh(
            state=self._sm.state,
            current_spot=self._last_ws_ltp,
            has_candidates=bool(analysis.all_candidates),
        )

        if not decision.refresh_candidates:
            return

        # Build candidate list
        candidates_to_refresh = []

        if analysis.best_ce:
            candidates_to_refresh.append((analysis.best_ce.strike, analysis.best_ce.option_type.value))
        if analysis.best_pe:
            candidates_to_refresh.append((analysis.best_pe.strike, analysis.best_pe.option_type.value))

        active = self._rsm.state.active_trade
        if active:
            candidates_to_refresh.append((active.strike, active.option_type.value))

        if not candidates_to_refresh:
            return

        candidates_to_refresh = list(set(candidates_to_refresh))

        quotes = self._adapter.fetch_candidate_quotes(self._symbol, candidates_to_refresh)
        self._cached_candidate_quotes = quotes
        self._refresh_sched.record_candidate_refresh()

    def _build_trigger_snapshot(self, live_spot: float, analysis: AnalysisSnapshot) -> TriggerSnapshot:
        """Build a TriggerSnapshot from live data."""
        candidate_quotes = []
        for (strike, otype), data in self._cached_candidate_quotes.items():
            candidate_quotes.append(CandidateQuote(
                strike=strike,
                option_type=OptionType.CE if otype == "CE" else OptionType.PE,
                ltp=data.get("ltp", 0),
                bid=data.get("bid"),
                ask=data.get("ask"),
                bid_qty=data.get("bid_qty"),
                ask_qty=data.get("ask_qty"),
                volume=data.get("volume"),
                spread_pct=data.get("spread_pct"),
                timestamp=data.get("timestamp", datetime.now(timezone.utc)),
            ))

        triggers = resolve_triggers(live_spot, self._config, analysis.chain.strikes if analysis.chain else None)

        return TriggerSnapshot(
            spot_ltp=live_spot,
            spot_timestamp=self._last_ws_time or datetime.now(timezone.utc),
            symbol=self._symbol,
            candidate_quotes=tuple(candidate_quotes),
            candle_confirmed_above=triggers.upper_trigger if self._candle_builder.is_candle_confirmed_beyond(triggers.upper_trigger, "above") else None,
            candle_confirmed_below=triggers.lower_trigger if self._candle_builder.is_candle_confirmed_beyond(triggers.lower_trigger, "below") else None,
            candle_degraded=self._candle_builder.is_degraded,
        )

    def _check_exit(self, trade, live_spot, trigger_snap, analysis, tracer):
        """Check active trade for exit using live data."""
        triggers = resolve_triggers(live_spot, self._config,
            analysis.chain.strikes if analysis.chain else None)

        # Get current LTP — prefer live candidate quote, fall back to chain
        cq = trigger_snap.get_candidate_quote(trade.strike, trade.option_type)
        if cq and cq.ltp > 0:
            current_ltp = cq.ltp
        else:
            # Fall back to chain data
            current_ltp = None
            if analysis.chain:
                for row in analysis.chain.rows:
                    if row.strike == trade.strike and row.option_type == trade.option_type:
                        current_ltp = row.ltp
                        break

        if current_ltp is None:
            return

        self._rsm.update_trade_ltp(current_ltp)

        exit_reason = self._se.evaluate_exit(trade, current_ltp, live_spot, triggers)

        if exit_reason:
            closed = self._broker.execute_exit(trade, current_ltp, exit_reason, trade.lots)
            self._capital.record_exit(closed)
            self._sm.exit_trade(closed.pnl or 0)
            self._rsm.close_active_trade(closed)
            self._rsm.update_machine_state(self._sm.state)
            self._confirmation.reset()
            tracer.record_paper_execution(closed, "EXIT")

            try:
                self._db.save_trade(closed)
                for entry in self._capital.ledger[-1:]:
                    self._db.save_ledger_entry(entry)
            except Exception as e:
                self._failures.record("PERSISTENCE", f"exit save: {e}")

            self._logger.info(
                "EXIT: K=%s %s exit=%.2f PnL=%.2f (%s)",
                closed.strike, closed.side.value,
                closed.exit_price, closed.pnl, exit_reason.value,
            )

            self._alerts.on_trade_exit(
                symbol=self._symbol, strike=closed.strike, side=closed.side.value,
                entry_price=closed.entry_price, exit_price=closed.exit_price or 0,
                pnl=closed.pnl or 0, reason=exit_reason.value,
            )

    def _execute_entry(self, signal_event, analysis, trigger_snap, tracer):
        """Execute paper trade entry with live candidate quote."""
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

        # Use live bid/ask from trigger snapshot if available
        cq = trigger_snap.get_candidate_quote(candidate.strike, candidate.option_type)
        bid = cq.bid if cq else None
        ask = cq.ask if cq else None

        self._sm.enter_trade()
        trade = self._broker.execute_entry(
            candidate=candidate,
            symbol=self._symbol,
            expiry=analysis.expiry,
            qty=qty, lots=lots,
            capital_before=self._capital.running_capital,
            signal_id=signal_event.signal_id,
            snapshot_id=analysis.chain.snapshot_id if analysis.chain else "",
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
            self._failures.record("PERSISTENCE", f"entry save: {e}")

        self._logger.info(
            "ENTRY: K=%s %s entry=%.2f qty=%d SL=%.2f T1=%.2f",
            trade.strike, trade.side.value,
            trade.entry_price, trade.qty, trade.sl, trade.t1,
        )

        self._alerts.on_trade_entry(
            symbol=self._symbol, strike=trade.strike, side=trade.side.value,
            entry_price=trade.entry_price, sl=trade.sl, t1=trade.t1, qty=trade.qty,
        )

    # ── Support Methods ────────────────────────────────────────────

    def _start_websocket(self) -> None:
        """Start FYERS WebSocket for real-time spot ticks."""
        try:
            client = self._adapter._get_client()
            if not client.access_token or not client.client_id:
                self._logger.warning("No FYERS credentials for WebSocket")
                return

            fyers_symbol = _DEFAULT_FYERS_SYMBOLS.get(self._symbol.upper())
            if not fyers_symbol:
                return

            self._ws_client = FyersWebSocketClient(
                config=self._config,
                access_token=client.access_token,
                client_id=client.client_id,
                on_tick=self._on_ws_tick,
            )
            self._ws_client.start([fyers_symbol])
            self._logger.info("WebSocket started for %s", fyers_symbol)

        except Exception as e:
            self._logger.warning("WebSocket start failed: %s", e)

    def _on_ws_tick(self, tick: dict) -> None:
        """WebSocket tick callback — updates spot + candle builder."""
        ltp = tick.get("ltp")
        if ltp and ltp > 0:
            self._last_ws_ltp = ltp
            self._last_ws_time = tick.get("timestamp") or datetime.now(timezone.utc)

    def _warmup_candles(self) -> None:
        """Seed candle builder from historical API."""
        try:
            client = self._adapter._get_client()
            fyers_symbol = _DEFAULT_FYERS_SYMBOLS.get(self._symbol.upper())
            if fyers_symbol and client:
                seeded = self._candle_builder.warmup(client, fyers_symbol, count=10)
                self._logger.info("Candle warmup: %d candles seeded", seeded)
        except Exception as e:
            self._logger.warning("Candle warmup failed: %s", e)

    def _load_last_snapshot_from_db(self) -> None:
        """Load most recent snapshot from DB as fallback."""
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
                    # Run analysis on the DB snapshot
                    report = self._validator.validate(snapshot)
                    rows = compute_base_metrics(snapshot, self._config)
                    rows = compute_advanced_metrics(rows, self._config)
                    window = filter_window(rows, snapshot.spot_ltp, self._config)
                    side, bias, _, _ = compute_side_bias(window, self._config)
                    ext_ce, ext_pe = extrapolate_otm_strikes(rows, snapshot.spot_ltp, self._config)
                    best_ce, best_pe, cands = score_and_select(
                        rows, ext_ce, ext_pe, snapshot.spot_ltp, side, bias, self._config,
                    )

                    with self._chain_lock:
                        self._last_analysis = AnalysisSnapshot(
                            symbol=self._symbol, spot_ltp=snapshot.spot_ltp,
                            chain=snapshot, quality=report,
                            best_ce=best_ce, best_pe=best_pe,
                            all_candidates=tuple(cands),
                            config_version=self._config.version_hash,
                        )
                        self._last_analysis_time = time.monotonic()
                        self._refresh_sched.record_chain_refresh(snapshot.spot_ltp)

                    self._rsm.update_chain_snapshot(snapshot)
                    self._rsm.update_spot(snapshot.spot_ltp)
                    self._logger.info(
                        "DB fallback: loaded snapshot %s spot=%.2f rows=%d",
                        snapshot.snapshot_id, snapshot.spot_ltp, len(snapshot.rows),
                    )
                    return

            self._logger.warning("No usable snapshot in DB")
        except Exception as e:
            self._logger.warning("DB fallback failed: %s", e)

    def _finalize_trigger_cycle(self, tracer, cycle_start):
        """Finalize trigger cycle."""
        total_ms = (time.monotonic() - cycle_start) * 1000
        self._rsm.end_cycle(total_ms)

        trace = tracer.build()
        self._rsm.add_debug_trace(trace)

        try:
            self._db.save_debug_trace(trace)
        except Exception as e:
            self._failures.record("PERSISTENCE", f"trace save: {e}")


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

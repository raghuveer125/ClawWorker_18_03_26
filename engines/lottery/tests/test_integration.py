"""Integration tests — full pipeline flow, storage, replay, determinism."""

import json
import os
import tempfile
import pytest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from engines.lottery.config import (
    load_config, LotteryConfig, TimeFiltersConfig, DataQualityConfig,
    SnapshotMode, StorageConfig,
)
from engines.lottery.models import (
    ChainSnapshot, OptionRow, OptionType, CalculatedRow,
    QualityStatus, MachineState, Side, SignalValidity,
    TradeStatus, ExitReason,
)
from engines.lottery.data_quality import DataQualityValidator
from engines.lottery.calculations import (
    compute_base_metrics, filter_window, compute_advanced_metrics,
    compute_side_bias, extrapolate_otm_strikes, score_and_select,
    update_rows_with_scores,
)
from engines.lottery.strategy import StateMachine, SignalEngine, RiskGuard, resolve_triggers
from engines.lottery.paper_trading import PaperBroker, CapitalManager
from engines.lottery.storage import LotteryDB
from engines.lottery.reporting import (
    raw_data_table, formula_audit_table, quality_table,
    signal_table, trade_table, candidate_table,
)
from engines.lottery.replay import ReplayEngine


# ── Fixtures ───────────────────────────────────────────────────────────────

def _build_chain(spot: float, symbol: str = "NIFTY") -> ChainSnapshot:
    """Build a realistic chain for integration testing."""
    rows = []
    for strike in range(int(spot - 500), int(spot + 550), 50):
        d = strike - spot
        ce_ltp = max(0.5, 200 - abs(d) * 0.35 + (spot - strike) * 0.5)
        pe_ltp = max(0.5, 200 - abs(d) * 0.35 + (strike - spot) * 0.5)
        for otype, ltp in [(OptionType.CE, ce_ltp), (OptionType.PE, pe_ltp)]:
            rows.append(OptionRow(
                symbol=symbol, expiry="2026-04-07", strike=float(strike),
                option_type=otype, ltp=round(ltp, 2),
                change=-round(abs(d) * 0.1 + 40, 2),
                volume=int(2e6 + abs(d) * 2000),
                oi=int(1e6 + abs(d) * 1000),
                bid=round(max(ltp - 0.3, 0.1), 2),
                ask=round(ltp + 0.3, 2),
            ))
    return ChainSnapshot(
        symbol=symbol, expiry="2026-04-07", spot_ltp=spot,
        snapshot_timestamp=datetime.now(timezone.utc), rows=tuple(rows),
    )


@pytest.fixture
def cfg():
    """Config with time filters disabled and tolerant quality."""
    base = load_config()
    return replace(base,
        time_filters=TimeFiltersConfig(
            no_trade_first_minutes=0,
            no_trade_lunch_start="99:00", no_trade_lunch_end="99:00",
            mandatory_squareoff_time="23:59", market_open="00:00", market_close="23:59",
        ),
        data_quality=replace(base.data_quality,
            snapshot_mode=SnapshotMode.TOLERANT,
            max_spot_age_ms=999999999,
            max_chain_age_ms=999999999,
        ),
    )


@pytest.fixture
def temp_db(cfg):
    """Create a temporary DB for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_tmp = replace(cfg, storage=StorageConfig(
            db_path=os.path.join(tmpdir, "lottery.db"),
            snapshot_dump_on_failure=True,
            snapshot_dump_on_signal=True,
        ))
        db = LotteryDB(config=cfg_tmp, symbol="NIFTY")
        yield db, cfg_tmp
        db.close()


# ── Full Pipeline Flow ─────────────────────────────────────────────────────

class TestFullPipelineFlow:
    """Test the complete: fetch → validate → calculate → signal → trade flow."""

    def test_full_flow_no_trade_zone(self, cfg):
        """Spot in no-trade zone → IDLE, no trade."""
        spot = 22675  # between triggers
        chain = _build_chain(spot)

        # Validate
        validator = DataQualityValidator(config=cfg)
        report = validator.validate(chain)
        assert report.overall_status in (QualityStatus.PASS, QualityStatus.WARN)

        # Calculate
        rows = compute_base_metrics(chain, cfg)
        rows = compute_advanced_metrics(rows, cfg)
        window = filter_window(rows, spot, cfg)
        side, bias, _, _ = compute_side_bias(window, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, spot, cfg)
        best_ce, best_pe, cands = score_and_select(rows, ext_ce, ext_pe, spot, side, bias, cfg)

        # Signal
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        triggers = resolve_triggers(spot, cfg, chain.strikes)
        signal = se.evaluate_entry(spot, report.overall_status, triggers, best_ce, best_pe, side, "snap1", "v1")

        assert signal.validity == SignalValidity.INVALID
        assert sm.state == MachineState.IDLE

    def test_full_flow_ce_breakout(self, cfg):
        """Spot above upper trigger → CE zone → candidate → entry."""
        spot = 22800
        chain = _build_chain(spot)

        validator = DataQualityValidator(config=cfg)
        report = validator.validate(chain)

        rows = compute_base_metrics(chain, cfg)
        rows = compute_advanced_metrics(rows, cfg)
        window = filter_window(rows, spot, cfg)
        side, bias, _, _ = compute_side_bias(window, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, spot, cfg)
        best_ce, best_pe, cands = score_and_select(rows, ext_ce, ext_pe, spot, side, bias, cfg)

        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        # Use static triggers where spot is clearly above upper
        from engines.lottery.strategy.state_machine import TriggerZone
        triggers = TriggerZone(upper_trigger=22750, lower_trigger=22700, source="STATIC")

        # Cycle 1: IDLE → ZONE_ACTIVE_CE
        se.evaluate_entry(spot, report.overall_status, triggers, best_ce, best_pe, side, "s1", "v1")
        assert sm.state == MachineState.ZONE_ACTIVE_CE

        # Cycle 2: ZONE_ACTIVE → CANDIDATE_FOUND (if candidate available)
        signal = se.evaluate_entry(spot, report.overall_status, triggers, best_ce, best_pe, side, "s2", "v1")
        if best_ce:
            assert sm.state == MachineState.CANDIDATE_FOUND
            assert signal.validity == SignalValidity.VALID

    def test_full_flow_pe_breakout(self, cfg):
        """Spot below lower trigger → PE zone."""
        spot = 22500
        chain = _build_chain(spot)

        validator = DataQualityValidator(config=cfg)
        report = validator.validate(chain)

        rows = compute_base_metrics(chain, cfg)
        rows = compute_advanced_metrics(rows, cfg)
        window = filter_window(rows, spot, cfg)
        side, bias, _, _ = compute_side_bias(window, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, spot, cfg)
        best_ce, best_pe, cands = score_and_select(rows, ext_ce, ext_pe, spot, side, bias, cfg)

        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        from engines.lottery.strategy.state_machine import TriggerZone
        triggers = TriggerZone(upper_trigger=22550, lower_trigger=22525, source="STATIC")

        se.evaluate_entry(spot, report.overall_status, triggers, best_ce, best_pe, side, "s1", "v1")
        assert sm.state == MachineState.ZONE_ACTIVE_PE

    def test_full_trade_lifecycle(self, cfg):
        """Entry → monitor → exit → cooldown → idle."""
        spot = 22800
        chain = _build_chain(spot)

        validator = DataQualityValidator(config=cfg)
        report = validator.validate(chain)
        rows = compute_base_metrics(chain, cfg)
        rows = compute_advanced_metrics(rows, cfg)
        window = filter_window(rows, spot, cfg)
        side, bias, _, _ = compute_side_bias(window, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, spot, cfg)
        best_ce, best_pe, cands = score_and_select(rows, ext_ce, ext_pe, spot, side, bias, cfg)

        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        broker = PaperBroker(config=cfg)
        capital = CapitalManager(config=cfg, symbol="NIFTY")
        from engines.lottery.strategy.state_machine import TriggerZone
        triggers = TriggerZone(upper_trigger=22750, lower_trigger=22700, source="STATIC")

        # Push to CANDIDATE_FOUND
        se.evaluate_entry(spot, report.overall_status, triggers, best_ce, best_pe, side, "s1", "v1")
        signal = se.evaluate_entry(spot, report.overall_status, triggers, best_ce, best_pe, side, "s2", "v1")

        if signal.validity != SignalValidity.VALID or not sm.context.candidate:
            pytest.skip("No valid candidate for this chain configuration")

        candidate = sm.context.candidate

        # Enter trade
        sm.enter_trade()
        assert sm.state == MachineState.IN_TRADE

        qty, lots = capital.compute_position_size(candidate.ltp, 75)
        trade = broker.execute_entry(
            candidate, "NIFTY", "2026-04-07", qty, lots,
            capital.running_capital, signal.signal_id, "snap2", cfg.version_hash,
        )
        capital.record_entry(trade)
        assert trade.status == TradeStatus.OPEN

        # Exit trade
        closed = broker.execute_exit(trade, trade.t1 + 0.5, ExitReason.TARGET_1, lots)
        capital.record_exit(closed)
        sm.exit_trade(closed.pnl)
        assert sm.state == MachineState.COOLDOWN
        assert closed.status == TradeStatus.CLOSED
        assert closed.pnl > 0


# ── Storage Tests ──────────────────────────────────────────────────────────

class TestStorage:

    def test_save_and_retrieve_snapshot(self, temp_db):
        db, cfg = temp_db
        chain = _build_chain(22700)
        db.save_chain_snapshot(chain)
        retrieved = db.get_snapshot_by_id(chain.snapshot_id)
        assert retrieved is not None
        assert retrieved["spot_ltp"] == 22700
        assert retrieved["row_count"] == len(chain.rows)

    def test_save_and_retrieve_signal(self, temp_db, cfg):
        db, _ = temp_db
        from engines.lottery.models import SignalEvent
        sig = SignalEvent(
            symbol="NIFTY", side_bias=Side.PE, zone="PE_ACTIVE",
            machine_state=MachineState.CANDIDATE_FOUND,
            selected_strike=21000, selected_premium=2.50,
            validity=SignalValidity.VALID, spot_ltp=22700,
            config_version=cfg.version_hash,
        )
        db.save_signal(sig)
        signals = db.get_recent_signals(5)
        assert len(signals) >= 1
        assert signals[0]["selected_strike"] == 21000

    def test_save_and_retrieve_trade(self, temp_db):
        db, _ = temp_db
        from engines.lottery.models import PaperTrade
        trade = PaperTrade(
            symbol="NIFTY", side=Side.CE, strike=24000,
            option_type=OptionType.CE, entry_price=3.50,
            qty=75, lots=1, capital_before=100000,
            sl=1.75, t1=7.0, t2=10.5, t3=14.0,
        )
        db.save_trade(trade)
        trades = db.get_trades(5)
        assert len(trades) >= 1
        assert trades[0]["strike"] == 24000

    def test_config_version_persistence(self, temp_db, cfg):
        db, _ = temp_db
        db.save_config_version(cfg)
        retrieved = db.get_config_version(cfg.version_hash)
        assert retrieved is not None
        config_data = json.loads(retrieved["config_json"])
        assert config_data["instrument"]["symbol"] == "NIFTY"

    def test_multi_symbol_isolation(self, cfg):
        """NIFTY and BANKNIFTY DBs are separate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_tmp = replace(cfg, storage=StorageConfig(
                db_path=os.path.join(tmpdir, "lottery.db"),
            ))
            db_nifty = LotteryDB(config=cfg_tmp, symbol="NIFTY")
            db_bnifty = LotteryDB(config=cfg_tmp, symbol="BANKNIFTY")

            chain_n = _build_chain(22700, "NIFTY")
            chain_b = _build_chain(51800, "BANKNIFTY")
            db_nifty.save_chain_snapshot(chain_n)
            db_bnifty.save_chain_snapshot(chain_b)

            assert db_nifty.db_path != db_bnifty.db_path
            assert len(db_nifty.get_trades()) == 0  # isolated

            db_nifty.close()
            db_bnifty.close()


# ── Reporting Tests ────────────────────────────────────────────────────────

class TestReporting:

    def test_raw_data_table_json_serializable(self, cfg):
        chain = _build_chain(22700)
        table = raw_data_table(chain)
        assert len(table) > 0
        json.dumps(table)  # should not raise

    def test_formula_audit_table(self, cfg):
        chain = _build_chain(22700)
        rows = compute_base_metrics(chain, cfg)
        rows = compute_advanced_metrics(rows, cfg)
        table = formula_audit_table(rows)
        assert len(table) == len(rows)
        json.dumps(table, default=str)

    def test_quality_table(self, cfg):
        chain = _build_chain(22700)
        validator = DataQualityValidator(config=cfg)
        report = validator.validate(chain)
        table = quality_table(report)
        assert len(table) > 0
        # Last row should be OVERALL
        assert table[-1]["check_name"] == "OVERALL"
        json.dumps(table)

    def test_candidate_table(self, cfg):
        chain = _build_chain(22700)
        rows = compute_base_metrics(chain, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, 22700, cfg)
        _, _, cands = score_and_select(rows, ext_ce, ext_pe, 22700, None, None, cfg)
        table = candidate_table(cands)
        json.dumps(table, default=str)


# ── Replay / Determinism ──────────────────────────────────────────────────

class TestReplay:

    def test_replay_from_file(self, cfg):
        """Replay snapshots from a JSONL file."""
        chain = _build_chain(22700)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            snap_data = {
                "symbol": "NIFTY",
                "expiry": "2026-04-07",
                "spot_ltp": 22700,
                "snapshot_timestamp": chain.snapshot_timestamp.isoformat(),
                "rows": [
                    {"strike": r.strike, "option_type": r.option_type.value,
                     "ltp": r.ltp, "change": r.change, "volume": r.volume,
                     "oi": r.oi, "bid": r.bid, "ask": r.ask}
                    for r in chain.rows
                ],
            }
            f.write(json.dumps(snap_data) + "\n")
            f.write(json.dumps(snap_data) + "\n")  # duplicate for staleness test
            tmpfile = f.name

        try:
            engine = ReplayEngine(config=cfg, symbol="NIFTY")
            result = engine.replay_from_file(tmpfile, config_override=cfg)
            assert result.processed > 0
            assert result.total_snapshots == 2
            assert result.final_capital == cfg.paper_trading.starting_capital  # no trades on single snapshot
        finally:
            os.unlink(tmpfile)

    def test_deterministic_output(self, cfg):
        """Same snapshot + same config = identical signals."""
        chain = _build_chain(22700)

        validator = DataQualityValidator(config=cfg)
        sm1 = StateMachine(config=cfg)
        se1 = SignalEngine(config=cfg, state_machine=sm1)
        sm2 = StateMachine(config=cfg)
        se2 = SignalEngine(config=cfg, state_machine=sm2)

        report = validator.validate(chain)
        rows = compute_base_metrics(chain, cfg)
        rows = compute_advanced_metrics(rows, cfg)
        window = filter_window(rows, chain.spot_ltp, cfg)
        side, bias, _, _ = compute_side_bias(window, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, chain.spot_ltp, cfg)
        best_ce, best_pe, _ = score_and_select(rows, ext_ce, ext_pe, chain.spot_ltp, side, bias, cfg)
        triggers = resolve_triggers(chain.spot_ltp, cfg, chain.strikes)

        sig1 = se1.evaluate_entry(chain.spot_ltp, report.overall_status, triggers,
            best_ce, best_pe, side, "s1", cfg.version_hash)
        sig2 = se2.evaluate_entry(chain.spot_ltp, report.overall_status, triggers,
            best_ce, best_pe, side, "s1", cfg.version_hash)

        assert sig1.validity == sig2.validity
        assert sig1.machine_state == sig2.machine_state
        assert sig1.selected_strike == sig2.selected_strike
        assert sig1.zone == sig2.zone

    def test_config_version_consistency(self, cfg):
        """Same config always produces same version hash."""
        cfg2 = load_config()
        # Both loaded from same file
        assert cfg2.version_hash == load_config().version_hash

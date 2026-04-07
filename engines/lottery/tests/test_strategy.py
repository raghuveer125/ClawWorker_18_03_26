"""Unit tests for strategy — state machine, signal engine, risk guard."""

import time
import pytest
from datetime import datetime, timezone
from dataclasses import replace

from engines.lottery.config import load_config, TimeFiltersConfig
from engines.lottery.models import (
    MachineState, QualityStatus, RejectionReason, Side, ExitReason,
    PaperTrade, OptionType, TradeStatus, SignalValidity,
)
from engines.lottery.strategy.state_machine import StateMachine, TriggerZone, resolve_triggers
from engines.lottery.strategy.signal_engine import SignalEngine
from engines.lottery.strategy.risk_guard import RiskGuard
from engines.lottery.paper_trading import CapitalManager
from engines.lottery.calculations.scoring import ScoredCandidate


@pytest.fixture
def cfg():
    """Config with time filters disabled for testing."""
    base = load_config()
    return replace(base, time_filters=TimeFiltersConfig(
        no_trade_first_minutes=0,
        no_trade_lunch_start="99:00", no_trade_lunch_end="99:00",
        mandatory_squareoff_time="23:59", market_open="00:00", market_close="23:59",
    ))


@pytest.fixture
def triggers():
    return TriggerZone(upper_trigger=22700, lower_trigger=22650, source="STATIC")


@pytest.fixture
def candidate_ce():
    return ScoredCandidate(
        strike=24000, option_type=OptionType.CE, ltp=3.50, score=42.5,
        components={"f_dist": 20, "f_mom": 0, "f_liq": 15, "f_band": 0.8, "bias": 0},
        band_fit=0.8, spread_pct=1.2, volume=5000000, distance=1300, source="VISIBLE",
    )


@pytest.fixture
def candidate_pe():
    return ScoredCandidate(
        strike=21000, option_type=OptionType.PE, ltp=2.50, score=51.0,
        components={"f_dist": 30, "f_mom": 0, "f_liq": 16, "f_band": 0.1, "bias": 0},
        band_fit=0.1, spread_pct=2.0, volume=3000000, distance=1700, source="VISIBLE",
    )


# ── State Machine ─────────────────────────────────────────────────────────

class TestStateMachine:

    def test_initial_state_idle(self, cfg):
        sm = StateMachine(config=cfg)
        assert sm.state == MachineState.IDLE

    def test_idle_to_ce_zone(self, cfg, triggers, candidate_ce):
        sm = StateMachine(config=cfg)
        spot = triggers.upper_trigger + 20
        sm.evaluate(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        assert sm.state == MachineState.ZONE_ACTIVE_CE

    def test_idle_to_pe_zone(self, cfg, triggers, candidate_pe):
        sm = StateMachine(config=cfg)
        spot = triggers.lower_trigger - 20
        sm.evaluate(spot, QualityStatus.PASS, triggers, None, candidate_pe, Side.PE)
        assert sm.state == MachineState.ZONE_ACTIVE_PE

    def test_no_trade_zone(self, cfg, triggers):
        sm = StateMachine(config=cfg)
        spot = (triggers.upper_trigger + triggers.lower_trigger) / 2
        sm.evaluate(spot, QualityStatus.PASS, triggers, None, None, None)
        assert sm.state == MachineState.IDLE
        assert sm.context.rejection == RejectionReason.ZONE_INACTIVE

    def test_zone_to_candidate(self, cfg, triggers, candidate_ce):
        sm = StateMachine(config=cfg)
        spot = triggers.upper_trigger + 20
        # First eval: IDLE → ZONE_ACTIVE_CE
        sm.evaluate(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        # Second eval: ZONE_ACTIVE_CE → CANDIDATE_FOUND
        sm.evaluate(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        assert sm.state == MachineState.CANDIDATE_FOUND
        assert sm.context.candidate is not None

    def test_enter_trade(self, cfg, triggers, candidate_ce):
        sm = StateMachine(config=cfg)
        spot = triggers.upper_trigger + 20
        sm.evaluate(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        sm.evaluate(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        sm.enter_trade()
        assert sm.state == MachineState.IN_TRADE
        assert sm.context.daily_trade_count == 1

    def test_exit_trade_to_cooldown(self, cfg, triggers, candidate_ce):
        sm = StateMachine(config=cfg)
        spot = triggers.upper_trigger + 20
        sm.evaluate(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        sm.evaluate(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        sm.enter_trade()
        sm.exit_trade(pnl=100.0)
        assert sm.state == MachineState.COOLDOWN
        assert sm.context.daily_pnl == 100.0
        assert sm.context.consecutive_losses == 0

    def test_exit_trade_loss_tracks_consecutive(self, cfg, triggers, candidate_ce):
        sm = StateMachine(config=cfg)
        spot = triggers.upper_trigger + 20
        sm.evaluate(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        sm.evaluate(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        sm.enter_trade()
        sm.exit_trade(pnl=-50.0)
        assert sm.context.consecutive_losses == 1

    def test_quality_fail_returns_idle(self, cfg, triggers, candidate_ce):
        sm = StateMachine(config=cfg)
        spot = triggers.upper_trigger + 20
        sm.evaluate(spot, QualityStatus.FAIL, triggers, candidate_ce, None, Side.CE)
        assert sm.state == MachineState.IDLE
        assert sm.context.rejection == RejectionReason.DATA_QUALITY_FAIL

    def test_spot_reversal_returns_idle(self, cfg, triggers, candidate_ce):
        sm = StateMachine(config=cfg)
        spot_up = triggers.upper_trigger + 20
        sm.evaluate(spot_up, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        assert sm.state == MachineState.ZONE_ACTIVE_CE
        # Spot drops below trigger
        sm.evaluate(triggers.upper_trigger - 10, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        assert sm.state == MachineState.IDLE

    def test_max_daily_trades_rejects(self, cfg, triggers, candidate_ce):
        sm = StateMachine(config=cfg)
        sm._ctx.daily_trade_count = cfg.paper_trading.max_daily_trades
        spot = triggers.upper_trigger + 20
        sm.evaluate(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE)
        assert sm.context.rejection == RejectionReason.MAX_DAILY_TRADES

    def test_reset(self, cfg):
        sm = StateMachine(config=cfg)
        sm._ctx.daily_trade_count = 5
        sm._ctx.daily_pnl = -500
        sm.reset()
        assert sm.state == MachineState.IDLE
        assert sm.context.daily_trade_count == 0
        assert sm.context.daily_pnl == 0.0


class TestResolveTriggers:

    def test_static_mode(self, cfg):
        from engines.lottery.config import TriggerMode
        cfg_static = replace(cfg, triggers=replace(cfg.triggers, mode=TriggerMode.STATIC,
            upper_trigger=23000, lower_trigger=22500))
        t = resolve_triggers(22700, cfg_static)
        assert t.upper_trigger == 23000
        assert t.lower_trigger == 22500
        assert t.source == "STATIC"

    def test_dynamic_mode(self, cfg):
        strikes = tuple(range(22400, 23100, 50))
        t = resolve_triggers(22675, cfg, strikes)
        assert t.source == "DYNAMIC"
        assert t.lower_trigger <= 22675
        assert t.upper_trigger > 22675


# ── Signal Engine ──────────────────────────────────────────────────────────

class TestSignalEngine:

    def test_valid_signal(self, cfg, triggers, candidate_ce):
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        spot = triggers.upper_trigger + 20
        # Push to ZONE_ACTIVE then CANDIDATE_FOUND
        se.evaluate_entry(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE, "s1", "v1")
        sig = se.evaluate_entry(spot, QualityStatus.PASS, triggers, candidate_ce, None, Side.CE, "s2", "v1")
        assert sig.validity == SignalValidity.VALID
        assert sig.selected_strike == 24000

    def test_invalid_signal_no_trade_zone(self, cfg, triggers):
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        spot = (triggers.upper_trigger + triggers.lower_trigger) / 2
        sig = se.evaluate_entry(spot, QualityStatus.PASS, triggers, None, None, None, "s1", "v1")
        assert sig.validity == SignalValidity.INVALID

    def test_exit_stop_loss(self, cfg, triggers):
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        trade = PaperTrade(
            strike=24000, option_type=OptionType.CE,
            entry_price=3.50, sl=1.40, t1=7.0, t2=10.5, t3=14.0,
        )
        # LTP=1.00 is well below SL floor (3.50 * 0.40 = 1.40)
        result = se.evaluate_exit(trade, current_ltp=1.50, spot=22750, triggers=triggers)
        assert result == ExitReason.STOP_LOSS

    def test_exit_target1(self, cfg, triggers):
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        trade = PaperTrade(
            strike=24000, option_type=OptionType.CE,
            entry_price=3.50, sl=1.75, t1=7.0, t2=10.5, t3=14.0,
        )
        result = se.evaluate_exit(trade, current_ltp=7.5, spot=22750, triggers=triggers)
        assert result == ExitReason.TARGET_1

    def test_exit_target3_priority(self, cfg, triggers):
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        trade = PaperTrade(
            strike=24000, option_type=OptionType.CE,
            entry_price=3.50, sl=1.75, t1=7.0, t2=10.5, t3=14.0,
        )
        # T3 should be returned over T1/T2
        result = se.evaluate_exit(trade, current_ltp=15.0, spot=22750, triggers=triggers)
        assert result == ExitReason.TARGET_3

    def test_exit_invalidation_disabled(self, cfg, triggers):
        """Invalidation is disabled (causes whipsaw). Spot reversal should NOT exit."""
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        trade = PaperTrade(
            strike=24000, option_type=OptionType.CE,
            entry_price=3.50, sl=2.10, t1=7.0, t2=10.5, t3=14.0,
        )
        # Spot drops below upper trigger — with invalidation_exit=false, should HOLD
        result = se.evaluate_exit(trade, current_ltp=3.50, spot=triggers.upper_trigger - 10, triggers=triggers)
        assert result is None

    def test_hold_between_sl_and_t1(self, cfg, triggers):
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        trade = PaperTrade(
            strike=24000, option_type=OptionType.CE,
            entry_price=3.50, sl=1.75, t1=7.0, t2=10.5, t3=14.0,
        )
        result = se.evaluate_exit(trade, current_ltp=5.0, spot=22750, triggers=triggers)
        assert result is None  # HOLD

    def test_exit_trailing_stop_from_peak(self, cfg, triggers):
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        trade = PaperTrade(
            strike=24000, option_type=OptionType.CE,
            entry_price=3.50, sl=1.75, t1=7.0, t2=10.5, t3=14.0,
        )
        # Peak was 6.0, trailing_stop_pct=30% means trail at 6.0*0.70 = 4.20
        # LTP dropped to 4.0 which is below 4.20 → should trigger
        result = se.evaluate_exit(
            trade, current_ltp=4.0, spot=22750, triggers=triggers, peak_ltp=6.0,
        )
        assert result == ExitReason.TRAILING_STOP

    def test_trailing_stop_holds_above_trail(self, cfg, triggers):
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        trade = PaperTrade(
            strike=24000, option_type=OptionType.CE,
            entry_price=3.50, sl=1.75, t1=7.0, t2=10.5, t3=14.0,
        )
        # Peak was 5.0, trail at 5.0*0.70 = 3.50
        # LTP is 4.0 which is above 3.50 → should hold
        result = se.evaluate_exit(
            trade, current_ltp=4.0, spot=22750, triggers=triggers, peak_ltp=5.0,
        )
        assert result is None

    def test_trailing_stop_ignored_below_entry(self, cfg, triggers):
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        trade = PaperTrade(
            strike=24000, option_type=OptionType.CE,
            entry_price=3.50, sl=3.43, t1=7.0, t2=10.5, t3=14.0,
        )
        # Peak is at entry (no gain yet) → trailing should not activate
        # LTP=3.45 is above SL (3.50*0.98=3.43) so no SL hit
        # peak_ltp=3.50 is not > entry=3.50, so trailing guard blocks it
        result = se.evaluate_exit(
            trade, current_ltp=3.45, spot=22750, triggers=triggers, peak_ltp=3.50,
        )
        assert result is None

    def test_exit_levels(self, cfg):
        sm = StateMachine(config=cfg)
        se = SignalEngine(config=cfg, state_machine=sm)
        levels = se.compute_exit_levels(4.0)
        assert levels["sl"] == pytest.approx(4.0 * 0.60, abs=0.01)  # 40% max loss floor
        assert levels["t1"] == 8.0
        assert levels["t2"] == 12.0
        assert levels["t3"] == 16.0


# ── Risk Guard ─────────────────────────────────────────────────────────────

class TestRiskGuard:

    def test_all_pass(self, cfg):
        guard = RiskGuard(config=cfg)
        sm = StateMachine(config=cfg)
        capital = CapitalManager(config=cfg, symbol="NIFTY")
        result = guard.check_entry(sm.context, capital, QualityStatus.PASS, 3.50, 75)
        assert result.allowed

    def test_capital_exhausted(self, cfg):
        guard = RiskGuard(config=cfg)
        sm = StateMachine(config=cfg)
        capital = CapitalManager(config=cfg, symbol="NIFTY")
        capital._running_capital = 0
        result = guard.check_entry(sm.context, capital, QualityStatus.PASS, 3.50, 75)
        assert not result.allowed

    def test_max_daily_loss(self, cfg):
        guard = RiskGuard(config=cfg)
        sm = StateMachine(config=cfg)
        sm._ctx.daily_pnl = -(cfg.paper_trading.max_daily_loss + 1)
        capital = CapitalManager(config=cfg, symbol="NIFTY")
        result = guard.check_entry(sm.context, capital, QualityStatus.PASS, 3.50, 75)
        assert not result.allowed
        assert result.rejection == RejectionReason.MAX_DAILY_LOSS

    def test_max_daily_trades(self, cfg):
        guard = RiskGuard(config=cfg)
        sm = StateMachine(config=cfg)
        sm._ctx.daily_trade_count = cfg.paper_trading.max_daily_trades
        capital = CapitalManager(config=cfg, symbol="NIFTY")
        result = guard.check_entry(sm.context, capital, QualityStatus.PASS, 3.50, 75)
        assert not result.allowed
        assert result.rejection == RejectionReason.MAX_DAILY_TRADES

    def test_max_consecutive_losses(self, cfg):
        guard = RiskGuard(config=cfg)
        sm = StateMachine(config=cfg)
        sm._ctx.consecutive_losses = cfg.paper_trading.max_consecutive_losses
        capital = CapitalManager(config=cfg, symbol="NIFTY")
        result = guard.check_entry(sm.context, capital, QualityStatus.PASS, 3.50, 75)
        assert not result.allowed
        assert result.rejection == RejectionReason.MAX_CONSECUTIVE_LOSSES

    def test_quality_fail_blocked(self, cfg):
        guard = RiskGuard(config=cfg)
        sm = StateMachine(config=cfg)
        capital = CapitalManager(config=cfg, symbol="NIFTY")
        result = guard.check_entry(sm.context, capital, QualityStatus.FAIL, 3.50, 75)
        assert not result.allowed
        assert result.rejection == RejectionReason.DATA_QUALITY_FAIL

    def test_quality_warn_allowed(self, cfg):
        guard = RiskGuard(config=cfg)
        sm = StateMachine(config=cfg)
        capital = CapitalManager(config=cfg, symbol="NIFTY")
        result = guard.check_entry(sm.context, capital, QualityStatus.WARN, 3.50, 75)
        assert result.allowed

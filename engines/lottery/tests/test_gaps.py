"""Tests for all gap modules — Phase 1, 2, and 3.

Covers: candle builder, confirmation, hysteresis, tradability, rejection audit,
microstructure, extrapolation advisory, divergence reporting, DTE detection,
strategy profiles, refresh scheduler.

All tests use synthetic data — no FYERS API needed.
"""

import json
import time
import pytest
from dataclasses import replace
from datetime import datetime, timezone, timedelta

from engines.lottery.config import load_config, HysteresisConfig, TradabilityConfig
from engines.lottery.models import (
    OptionRow, OptionType, ChainSnapshot, Side,
    PaperTrade, TradeStatus, ExitReason,
    StrikeRejectionAudit, CalculatedRow, ExtrapolatedStrike,
)


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def base_time():
    return datetime(2026, 4, 7, 10, 0, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# CANDLE BUILDER
# ══════════════════════════════════════════════════════════════════════════

class TestCandleBuilder:

    def test_tick_accumulation(self, cfg, base_time):
        from engines.lottery.calculations.candle_builder import CandleBuilder
        cb = CandleBuilder(config=cfg, symbol="NIFTY")
        for i in range(5):
            cb.on_tick(22700 + i, base_time + timedelta(seconds=i * 10))
        assert cb.current_candle is not None
        assert cb.current_candle.open == 22700
        assert cb.current_candle.close == 22704
        assert cb.current_candle.tick_count == 5

    def test_minute_rollover(self, cfg, base_time):
        from engines.lottery.calculations.candle_builder import CandleBuilder
        cb = CandleBuilder(config=cfg, symbol="NIFTY")
        # Fill minute 1
        for i in range(10):
            cb.on_tick(22700 + i, base_time + timedelta(seconds=i * 5))
        assert cb.candle_count == 0  # not yet complete
        # Tick in minute 2 → completes minute 1
        cb.on_tick(22720, base_time + timedelta(minutes=1, seconds=1))
        assert cb.candle_count == 1
        assert cb.last_completed.complete

    def test_gap_detection(self, cfg, base_time):
        from engines.lottery.calculations.candle_builder import CandleBuilder
        cb = CandleBuilder(config=cfg, symbol="NIFTY")
        cb.on_tick(22700, base_time)
        cb.on_tick(22710, base_time + timedelta(minutes=3))  # 3 min gap
        assert cb.is_degraded

    def test_candle_confirmed_above(self, cfg, base_time):
        from engines.lottery.calculations.candle_builder import CandleBuilder
        cb = CandleBuilder(config=cfg, symbol="NIFTY")
        for i in range(10):
            cb.on_tick(22710 + i, base_time + timedelta(seconds=i * 5))
        cb.on_tick(22730, base_time + timedelta(minutes=1, seconds=1))
        assert cb.is_candle_confirmed_beyond(22700, "above")
        assert not cb.is_candle_confirmed_beyond(22750, "above")

    def test_candle_confirmed_below(self, cfg, base_time):
        from engines.lottery.calculations.candle_builder import CandleBuilder
        cb = CandleBuilder(config=cfg, symbol="NIFTY")
        for i in range(10):
            cb.on_tick(22680 - i, base_time + timedelta(seconds=i * 5))
        cb.on_tick(22660, base_time + timedelta(minutes=1, seconds=1))
        assert cb.is_candle_confirmed_beyond(22700, "below")

    def test_momentum_expanding(self, cfg, base_time):
        from engines.lottery.calculations.candle_builder import CandleBuilder
        cb = CandleBuilder(config=cfg, symbol="NIFTY")
        # 3 candles with increasing body
        for minute in range(3):
            body = (minute + 1) * 10
            ts_base = base_time + timedelta(minutes=minute)
            cb.on_tick(22700, ts_base)
            cb.on_tick(22700 + body, ts_base + timedelta(seconds=30))
        cb.on_tick(22700, base_time + timedelta(minutes=3))  # trigger final complete
        assert cb.is_momentum_expanding(3)

    def test_serialization(self, cfg, base_time):
        from engines.lottery.calculations.candle_builder import CandleBuilder
        cb = CandleBuilder(config=cfg, symbol="NIFTY")
        cb.on_tick(22700, base_time)
        d = cb.to_dict()
        assert json.dumps(d)  # must be JSON serializable
        assert d["symbol"] == "NIFTY"


# ══════════════════════════════════════════════════════════════════════════
# BREAKOUT CONFIRMATION
# ══════════════════════════════════════════════════════════════════════════

class TestConfirmation:

    def _make_candle_builder(self, cfg, base_time, close_price):
        from engines.lottery.calculations.candle_builder import CandleBuilder
        cb = CandleBuilder(config=cfg, symbol="NIFTY")
        for i in range(5):
            cb.on_tick(close_price - 5 + i, base_time + timedelta(seconds=i * 10))
        cb.on_tick(close_price + 10, base_time + timedelta(minutes=1, seconds=1))
        return cb

    def test_quorum_mode(self, cfg, base_time):
        from engines.lottery.strategy.confirmation import BreakoutConfirmation, ConfirmationConfig, ConfirmationMode
        from engines.lottery.calculations.scoring import ScoredCandidate
        conf = BreakoutConfirmation(config=ConfirmationConfig(
            mode=ConfirmationMode.QUORUM, quorum=2, hold_duration_seconds=0.01,
        ))
        cb = self._make_candle_builder(cfg, base_time, 22720)
        cand = ScoredCandidate(strike=24000, option_type=OptionType.CE, ltp=4.0, score=42,
            components={}, band_fit=0.8, spread_pct=1.5, volume=5000000, distance=1100, source="VISIBLE")
        conf.on_zone_active()
        conf.on_candidate_found(ScoredCandidate(strike=24000, option_type=OptionType.CE, ltp=3.5,
            score=40, components={}, band_fit=0.8, spread_pct=1.5, volume=5000000, distance=1100, source="VISIBLE"))
        time.sleep(0.02)
        result = conf.evaluate(cand, 22700, "above", cb, current_volume=8000000, recent_avg_volume=5000000)
        assert result.confirmed
        assert result.checks_passed >= 2

    def test_disabled_mode(self, cfg, base_time):
        from engines.lottery.strategy.confirmation import BreakoutConfirmation, ConfirmationConfig, ConfirmationMode
        from engines.lottery.calculations.scoring import ScoredCandidate
        conf = BreakoutConfirmation(config=ConfirmationConfig(mode=ConfirmationMode.DISABLED))
        cb = self._make_candle_builder(cfg, base_time, 22720)
        cand = ScoredCandidate(strike=24000, option_type=OptionType.CE, ltp=4.0, score=42,
            components={}, band_fit=0.8, spread_pct=1.5, volume=5000000, distance=1100, source="VISIBLE")
        result = conf.evaluate(cand, 22700, "above", cb)
        assert result.confirmed

    def test_candle_fail(self, cfg, base_time):
        from engines.lottery.strategy.confirmation import BreakoutConfirmation, ConfirmationConfig, ConfirmationMode
        from engines.lottery.calculations.scoring import ScoredCandidate
        conf = BreakoutConfirmation(config=ConfirmationConfig(mode=ConfirmationMode.CANDLE))
        cb = self._make_candle_builder(cfg, base_time, 22690)  # closes below trigger
        cand = ScoredCandidate(strike=24000, option_type=OptionType.CE, ltp=4.0, score=42,
            components={}, band_fit=0.8, spread_pct=1.5, volume=5000000, distance=1100, source="VISIBLE")
        conf.on_zone_active()
        conf.on_candidate_found(cand)
        result = conf.evaluate(cand, 22700, "above", cb)
        assert not result.confirmed

    def test_reset(self):
        from engines.lottery.strategy.confirmation import BreakoutConfirmation, ConfirmationConfig
        conf = BreakoutConfirmation(config=ConfirmationConfig())
        conf.on_zone_active()
        conf.reset()
        assert conf._zone_active_time is None
        assert conf._candidate_initial_ltp is None


# ══════════════════════════════════════════════════════════════════════════
# TRIGGER HYSTERESIS
# ══════════════════════════════════════════════════════════════════════════

class TestHysteresis:

    def test_buffer_blocks_activation(self, cfg, base_time):
        from engines.lottery.strategy.hysteresis import TriggerHysteresis
        h = TriggerHysteresis(config=cfg.hysteresis)
        # Spot barely above trigger — should be blocked by buffer
        ok, side, _ = h.can_activate_zone(22705, 22700, 22650, base_time)
        assert not ok

    def test_buffer_allows_activation(self, cfg, base_time):
        from engines.lottery.strategy.hysteresis import TriggerHysteresis
        h = TriggerHysteresis(config=cfg.hysteresis)
        ok, side, _ = h.can_activate_zone(22715, 22700, 22650, base_time)
        assert ok
        assert side == "CE"

    def test_pe_activation(self, cfg, base_time):
        from engines.lottery.strategy.hysteresis import TriggerHysteresis
        h = TriggerHysteresis(config=cfg.hysteresis)
        ok, side, _ = h.can_activate_zone(22635, 22700, 22650, base_time)
        assert ok
        assert side == "PE"

    def test_hold_duration(self, cfg, base_time):
        from engines.lottery.strategy.hysteresis import TriggerHysteresis
        h = TriggerHysteresis(config=cfg.hysteresis)
        h.can_activate_zone(22715, 22700, 22650, base_time)
        held, secs = h.is_zone_held_long_enough(base_time + timedelta(seconds=2))
        assert not held
        held, secs = h.is_zone_held_long_enough(base_time + timedelta(seconds=10))
        assert held

    def test_invalidation_buffer(self, cfg):
        from engines.lottery.strategy.hysteresis import TriggerHysteresis
        h = TriggerHysteresis(config=cfg.hysteresis)
        h._zone_side = "CE"
        # Spot barely below trigger — buffer prevents invalidation
        inv, _ = h.should_invalidate(22698, 22700, 22650)
        assert not inv
        # Spot well below trigger
        inv, _ = h.should_invalidate(22690, 22700, 22650)
        assert inv

    def test_rearm_distance(self, cfg, base_time):
        from engines.lottery.strategy.hysteresis import TriggerHysteresis
        h = TriggerHysteresis(config=cfg.hysteresis)
        h.can_activate_zone(22715, 22700, 22650, base_time)
        h.record_idle_return(spot=22695)
        # Re-activate near same level — should be blocked
        ok, _, _ = h.can_activate_zone(22712, 22700, 22650, base_time + timedelta(seconds=5))
        # 22712 - 22695 = 17, need 20
        assert not ok
        # Move further away
        ok, _, _ = h.can_activate_zone(22725, 22700, 22650, base_time + timedelta(seconds=10))
        assert ok

    def test_replay_safe_timestamps(self, cfg, base_time):
        """Hysteresis uses datetime timestamps, not time.monotonic()."""
        from engines.lottery.strategy.hysteresis import TriggerHysteresis
        h = TriggerHysteresis(config=cfg.hysteresis)
        h.can_activate_zone(22715, 22700, 22650, base_time)
        # Uses datetime diff, not wall clock
        held, secs = h.is_zone_held_long_enough(base_time + timedelta(seconds=6))
        assert held
        assert secs == pytest.approx(6.0)


# ══════════════════════════════════════════════════════════════════════════
# TRADABILITY
# ══════════════════════════════════════════════════════════════════════════

class TestTradability:

    def test_good_strike_passes(self, cfg):
        from engines.lottery.calculations.tradability import check_tradability
        row = OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=24000,
            option_type=OptionType.CE, ltp=3.50,
            bid=3.40, ask=3.60, bid_qty=500, ask_qty=600, volume=5000000)
        result = check_tradability(row, cfg.tradability)
        assert result.tradable

    def test_no_bid_fails(self, cfg):
        from engines.lottery.calculations.tradability import check_tradability
        row = OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=24000,
            option_type=OptionType.CE, ltp=3.50,
            bid=None, ask=3.60, bid_qty=0, ask_qty=600, volume=5000000)
        result = check_tradability(row, cfg.tradability)
        assert not result.tradable
        assert "bid_missing" in result.rejection_all

    def test_wide_spread_fails(self, cfg):
        from engines.lottery.calculations.tradability import check_tradability
        row = OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=24000,
            option_type=OptionType.CE, ltp=2.00,
            bid=1.00, ask=3.00, bid_qty=500, ask_qty=600, volume=5000000)
        result = check_tradability(row, cfg.tradability)
        assert not result.tradable

    def test_low_volume_fails(self, cfg):
        from engines.lottery.calculations.tradability import check_tradability
        row = OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=24000,
            option_type=OptionType.CE, ltp=3.50,
            bid=3.40, ask=3.60, bid_qty=500, ask_qty=600, volume=10)
        result = check_tradability(row, cfg.tradability)
        assert not result.tradable

    def test_low_qty_passes_when_disabled(self, cfg):
        """With min_bid_qty=0 (FYERS doesn't provide depth qty), low qty passes."""
        from engines.lottery.calculations.tradability import check_tradability
        row = OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=24000,
            option_type=OptionType.CE, ltp=3.50,
            bid=3.40, ask=3.60, bid_qty=5, ask_qty=5, volume=5000000)
        result = check_tradability(row, cfg.tradability)
        assert result.tradable  # min_bid_qty=0 in config, so qty checks are disabled

    def test_low_qty_fails_when_configured(self, cfg):
        """When min_bid_qty is explicitly set, low qty should fail."""
        from dataclasses import replace as dc_replace
        from engines.lottery.calculations.tradability import check_tradability
        strict_trad = dc_replace(cfg.tradability, min_bid_qty=50, min_ask_qty=50)
        row = OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=24000,
            option_type=OptionType.CE, ltp=3.50,
            bid=3.40, ask=3.60, bid_qty=5, ask_qty=5, volume=5000000)
        result = check_tradability(row, strict_trad)
        assert not result.tradable

    def test_multi_failure_all_captured(self, cfg):
        from engines.lottery.calculations.tradability import check_tradability
        row = OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=24000,
            option_type=OptionType.CE, ltp=0.50,
            bid=None, ask=None, bid_qty=0, ask_qty=0, volume=0)
        result = check_tradability(row, cfg.tradability)
        assert not result.tradable
        assert len(result.rejection_all) >= 3

    def test_serialization(self, cfg):
        from engines.lottery.calculations.tradability import check_tradability
        row = OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=24000,
            option_type=OptionType.CE, ltp=3.50,
            bid=3.40, ask=3.60, bid_qty=500, ask_qty=600, volume=5000000)
        result = check_tradability(row, cfg.tradability)
        assert json.dumps(result.to_dict())


# ══════════════════════════════════════════════════════════════════════════
# REJECTION AUDIT
# ══════════════════════════════════════════════════════════════════════════

class TestRejectionAudit:

    def test_produces_audit_per_strike(self, cfg):
        from engines.lottery.calculations.rejection_audit import build_rejection_audit
        rows = [
            OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=24000,
                option_type=OptionType.CE, ltp=3.50,
                bid=3.40, ask=3.60, bid_qty=500, ask_qty=600, volume=5000000),
            OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=22000,
                option_type=OptionType.PE, ltp=2.50,
                bid=2.40, ask=2.60, bid_qty=400, ask_qty=500, volume=4000000),
        ]
        chain = ChainSnapshot(symbol="NIFTY", spot_ltp=22900, rows=tuple(rows))
        audits = build_rejection_audit(chain, cfg)
        assert len(audits) == 2

    def test_itm_rejected(self, cfg):
        from engines.lottery.calculations.rejection_audit import build_rejection_audit
        rows = [
            OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=22500,
                option_type=OptionType.CE, ltp=500,
                bid=499, ask=501, bid_qty=1000, ask_qty=1000, volume=10000000),
        ]
        chain = ChainSnapshot(symbol="NIFTY", spot_ltp=22900, rows=tuple(rows))
        audits = build_rejection_audit(chain, cfg)
        assert len(audits) == 1
        assert not audits[0].direction_pass
        assert "direction_itm" in audits[0].rejection_all

    def test_serializable(self, cfg):
        from engines.lottery.calculations.rejection_audit import build_rejection_audit
        rows = [OptionRow(symbol="NIFTY", expiry="2026-04-09", strike=24000,
            option_type=OptionType.CE, ltp=3.50, bid=3.40, ask=3.60,
            bid_qty=500, ask_qty=600, volume=5000000)]
        chain = ChainSnapshot(symbol="NIFTY", spot_ltp=22900, rows=tuple(rows))
        audits = build_rejection_audit(chain, cfg)
        assert json.dumps(audits[0].to_dict())


# ══════════════════════════════════════════════════════════════════════════
# MICROSTRUCTURE
# ══════════════════════════════════════════════════════════════════════════

class TestMicrostructure:

    def test_rolling_buffer(self, base_time):
        from engines.lottery.calculations.microstructure import MicrostructureTracker, MicrostructureConfig
        t = MicrostructureTracker(config=MicrostructureConfig(buffer_size=5))
        for i in range(10):
            t.record(24000, "CE", bid=3.40, ask=3.60, bid_qty=500, ask_qty=600,
                ltp=3.50, volume=100000, timestamp=base_time + timedelta(seconds=i))
        assert t.observation_count(24000, "CE") == 5  # bounded

    def test_persistent_wall(self, base_time):
        from engines.lottery.calculations.microstructure import MicrostructureTracker, MicrostructureConfig, MicroSignalType
        t = MicrostructureTracker(config=MicrostructureConfig(wall_qty_threshold=5000, wall_persistence_min=3))
        for i in range(5):
            t.record(24000, "CE", bid=3.40, ask=3.60, bid_qty=500, ask_qty=8000,
                ltp=3.50, volume=100000, timestamp=base_time + timedelta(seconds=i * 2))
        signals = t.detect_signals(24000, "CE")
        wall_signals = [s for s in signals if s.signal_type == MicroSignalType.PERSISTENT_ASK_WALL]
        assert len(wall_signals) == 1

    def test_pull_detection(self, base_time):
        from engines.lottery.calculations.microstructure import MicrostructureTracker, MicrostructureConfig, MicroSignalType
        t = MicrostructureTracker(config=MicrostructureConfig(pull_drop_pct=50))
        t.record(24000, "CE", bid=3.40, ask=3.60, bid_qty=500, ask_qty=8000,
            ltp=3.50, volume=100000, timestamp=base_time)
        t.record(24000, "CE", bid=3.40, ask=3.60, bid_qty=500, ask_qty=8000,
            ltp=3.50, volume=100000, timestamp=base_time + timedelta(seconds=2))
        t.record(24000, "CE", bid=3.40, ask=3.60, bid_qty=500, ask_qty=1000,  # drop
            ltp=3.50, volume=100000, timestamp=base_time + timedelta(seconds=4))
        signals = t.detect_signals(24000, "CE")
        pulls = [s for s in signals if s.signal_type == MicroSignalType.PULLED_ASK]
        assert len(pulls) == 1

    def test_spoof_detection(self, base_time):
        from engines.lottery.calculations.microstructure import MicrostructureTracker, MicrostructureConfig, MicroSignalType
        t = MicrostructureTracker(config=MicrostructureConfig(
            wall_qty_threshold=5000, spoof_appear_disappear_window=3))
        qtys = [500, 500, 10000, 10000, 500]
        for i, aq in enumerate(qtys):
            t.record(23500, "CE", bid=5.00, ask=5.20, bid_qty=300, ask_qty=aq,
                ltp=5.10, volume=80000, timestamp=base_time + timedelta(seconds=i * 2))
        signals = t.detect_signals(23500, "CE")
        spoofs = [s for s in signals if "SPOOF" in s.signal_type.value]
        assert len(spoofs) >= 1

    def test_confirmation_summary(self, base_time):
        from engines.lottery.calculations.microstructure import MicrostructureTracker, MicrostructureConfig
        t = MicrostructureTracker(config=MicrostructureConfig())
        for i in range(5):
            t.record(24000, "CE", bid=3.40, ask=3.60, bid_qty=500, ask_qty=600,
                ltp=3.50, volume=100000, timestamp=base_time + timedelta(seconds=i))
        summary = t.get_confirmation_summary(24000, "CE")
        assert "observations" in summary
        assert "total_signals" in summary

    def test_clear(self, base_time):
        from engines.lottery.calculations.microstructure import MicrostructureTracker, MicrostructureConfig
        t = MicrostructureTracker(config=MicrostructureConfig())
        t.record(24000, "CE", ltp=3.50, timestamp=base_time)
        assert t.observation_count(24000, "CE") == 1
        t.clear(24000, "CE")
        assert t.observation_count(24000, "CE") == 0


# ══════════════════════════════════════════════════════════════════════════
# EXTRAPOLATION ADVISORY
# ══════════════════════════════════════════════════════════════════════════

class TestExtrapolationAdvisory:

    def test_visible_blocks_extrapolated(self, cfg):
        from engines.lottery.calculations.scoring import score_and_select
        rows = [
            CalculatedRow(strike=23200, distance=300, abs_distance=300,
                call_ltp=5.0, put_ltp=150, call_volume=2000000, put_volume=1000000),
        ]
        ext_ce = [ExtrapolatedStrike(strike=25000, option_type=OptionType.CE,
            estimated_premium=5.0, adjusted_premium=3.5, steps_from_atm=42,
            alpha_used=0.05, in_band=True)]
        best_ce, _, cands = score_and_select(rows, ext_ce, [], 22900, None, None, cfg)
        extrap = [c for c in cands if c.source == "EXTRAPOLATED" and c.option_type == OptionType.CE]
        assert len(extrap) == 0  # blocked
        if best_ce:
            assert best_ce.source == "VISIBLE"

    def test_no_visible_rejects_extrapolated(self, cfg):
        """Extrapolated candidates are excluded from scoring — advisory only."""
        from engines.lottery.calculations.scoring import score_and_select
        rows = [CalculatedRow(strike=22900, distance=0, abs_distance=0, call_ltp=200, put_ltp=200)]
        ext_pe = [ExtrapolatedStrike(strike=20000, option_type=OptionType.PE,
            estimated_premium=4.0, adjusted_premium=2.8, steps_from_atm=58,
            alpha_used=0.05, in_band=True)]
        _, best_pe, cands = score_and_select(rows, [], ext_pe, 22900, None, None, cfg)
        extrap = [c for c in cands if c.source == "EXTRAPOLATED"]
        # New design: extrapolated candidates are never included in scoring
        assert len(extrap) == 0


# ══════════════════════════════════════════════════════════════════════════
# DIVERGENCE REPORTING
# ══════════════════════════════════════════════════════════════════════════

class TestDivergence:

    def test_trade_divergence(self):
        from engines.lottery.reporting.divergence import build_trade_divergence
        trade = PaperTrade(
            trade_id="t1", symbol="NIFTY", side=Side.CE, strike=24000,
            option_type=OptionType.CE, selection_price=3.45,
            confirmation_price=3.55, entry_price=3.52, exit_price=9.95,
            qty=75, lots=1, pnl=520.0, status=TradeStatus.CLOSED,
            reason_exit=ExitReason.TARGET_2,
        )
        report = build_trade_divergence(trade, peak_ltp=11.0, trough_ltp=2.80)
        assert report.max_favorable_excursion == pytest.approx(7.48)
        assert report.max_adverse_excursion == pytest.approx(0.72)
        assert report.selection_to_entry_slippage == pytest.approx(0.07)
        assert not report.rejected

    def test_rejection_divergence(self):
        from engines.lottery.reporting.divergence import build_rejection_divergence
        report = build_rejection_divergence(
            symbol="NIFTY", strike=21000, side="PE",
            selection_price=2.30, rejection_reasons=["confirmation_failed"],
        )
        assert report.rejected
        assert "confirmation_failed" in report.rejection_reasons

    def test_serialization(self):
        from engines.lottery.reporting.divergence import build_trade_divergence
        trade = PaperTrade(
            trade_id="t1", symbol="NIFTY", side=Side.CE, strike=24000,
            option_type=OptionType.CE, entry_price=3.52, exit_price=9.95,
            qty=75, lots=1, pnl=520.0, status=TradeStatus.CLOSED,
        )
        report = build_trade_divergence(trade)
        assert json.dumps(report.to_dict())


# ══════════════════════════════════════════════════════════════════════════
# DTE DETECTION + PROFILES
# ══════════════════════════════════════════════════════════════════════════

class TestDTEAndProfiles:

    def test_dte_auto_select(self):
        from engines.lottery.strategy.profiles import get_profile_for_dte, StrategyMode
        assert get_profile_for_dte(0).mode == StrategyMode.EXPIRY_DAY_TRUE_LOTTERY
        assert get_profile_for_dte(1).mode == StrategyMode.DTE1_HYBRID
        assert get_profile_for_dte(3).mode == StrategyMode.PRE_EXPIRY_MOMENTUM

    def test_profile_overrides(self):
        from engines.lottery.strategy.profiles import EXPIRY_DAY_TRUE_LOTTERY, PRE_EXPIRY_MOMENTUM
        assert EXPIRY_DAY_TRUE_LOTTERY.premium_band_min < PRE_EXPIRY_MOMENTUM.premium_band_min
        assert EXPIRY_DAY_TRUE_LOTTERY.chain_refresh_seconds < PRE_EXPIRY_MOMENTUM.chain_refresh_seconds
        assert EXPIRY_DAY_TRUE_LOTTERY.confirmation_quorum > PRE_EXPIRY_MOMENTUM.confirmation_quorum

    def test_dte_detector_manual_override(self):
        from engines.lottery.strategy import DTEDetector, StrategyMode
        det = DTEDetector(symbol="NIFTY", manual_override=StrategyMode.EXPIRY_DAY_TRUE_LOTTERY)
        profile = det.detect()
        assert profile.mode == StrategyMode.EXPIRY_DAY_TRUE_LOTTERY
        assert det.source == "manual_override"

    def test_all_profiles_json(self):
        from engines.lottery.strategy.profiles import get_all_profiles
        profiles = get_all_profiles()
        assert len(profiles) == 3
        assert json.dumps(profiles)


# ══════════════════════════════════════════════════════════════════════════
# REFRESH SCHEDULER
# ══════════════════════════════════════════════════════════════════════════

class TestRefreshScheduler:

    def test_idle_no_candidate_refresh(self):
        from engines.lottery.strategy.refresh_scheduler import RefreshScheduler, RefreshConfig
        from engines.lottery.models import MachineState
        s = RefreshScheduler(base_config=RefreshConfig())
        s.record_chain_refresh(22900)
        s.record_candidate_refresh()
        d = s.should_refresh(MachineState.IDLE, current_spot=22905)
        assert not d.refresh_candidates

    def test_zone_active_refreshes_candidates(self):
        from engines.lottery.strategy.refresh_scheduler import RefreshScheduler, RefreshConfig
        from engines.lottery.models import MachineState
        import time as t
        s = RefreshScheduler(base_config=RefreshConfig(candidate_zone_seconds=0))
        s.record_chain_refresh(22900)
        s.record_candidate_refresh()
        t.sleep(0.01)
        d = s.should_refresh(MachineState.ZONE_ACTIVE_CE, current_spot=22905)
        assert d.refresh_candidates

    def test_spot_drift_forces_chain(self):
        from engines.lottery.strategy.refresh_scheduler import RefreshScheduler, RefreshConfig
        from engines.lottery.models import MachineState
        s = RefreshScheduler(base_config=RefreshConfig(spot_drift_threshold=50))
        s.record_chain_refresh(22900)
        d = s.should_refresh(MachineState.ZONE_ACTIVE_CE, current_spot=22960)
        assert d.refresh_chain

    def test_profile_overrides_interval(self):
        from engines.lottery.strategy.refresh_scheduler import RefreshScheduler, RefreshConfig
        from engines.lottery.strategy.profiles import EXPIRY_DAY_TRUE_LOTTERY
        from engines.lottery.models import MachineState
        s = RefreshScheduler(base_config=RefreshConfig(), profile=EXPIRY_DAY_TRUE_LOTTERY)
        s.record_chain_refresh(22900)
        s._last_chain_refresh -= 16  # 16s ago, expiry interval=15s
        d = s.should_refresh(MachineState.ZONE_ACTIVE_CE, current_spot=22905)
        assert d.refresh_chain

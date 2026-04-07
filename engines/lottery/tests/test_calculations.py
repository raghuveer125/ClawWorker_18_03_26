"""Unit tests for the calculation engine — base metrics, advanced, extrapolation, scoring."""

import math
import pytest
from datetime import datetime, timezone

from engines.lottery.config import load_config, LotteryConfig, DecayMode, BandFitMode, BiasAggregation
from engines.lottery.models import (
    ChainSnapshot, OptionRow, OptionType, CalculatedRow, Side,
)
from engines.lottery.calculations.base_metrics import compute_base_metrics, filter_window
from engines.lottery.calculations.advanced_metrics import (
    compute_advanced_metrics, compute_side_bias, compute_pcr_bias, compute_slope_acceleration,
)
from engines.lottery.calculations.extrapolation import extrapolate_otm_strikes
from engines.lottery.calculations.scoring import score_and_select, _compute_band_fit
from dataclasses import replace


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def sample_chain():
    """Build a realistic chain snapshot for testing."""
    spot = 22700.0
    rows = []
    for strike in range(22400, 23100, 50):
        d = strike - spot
        # Simulate realistic premiums
        ce_ltp = max(0, (spot - strike) + 200 - abs(d) * 0.3)
        pe_ltp = max(0, (strike - spot) + 200 - abs(d) * 0.3)
        rows.append(OptionRow(
            symbol="NIFTY", expiry="2026-04-07", strike=float(strike),
            option_type=OptionType.CE, ltp=round(ce_ltp, 2),
            change=-round(abs(d) * 0.1 + 50, 2), volume=int(1e6 + abs(d) * 1000),
            oi=int(5e5 + abs(d) * 500), bid=round(ce_ltp - 0.5, 2), ask=round(ce_ltp + 0.5, 2),
        ))
        rows.append(OptionRow(
            symbol="NIFTY", expiry="2026-04-07", strike=float(strike),
            option_type=OptionType.PE, ltp=round(pe_ltp, 2),
            change=-round(abs(d) * 0.08 + 30, 2), volume=int(8e5 + abs(d) * 800),
            oi=int(4e5 + abs(d) * 400), bid=round(pe_ltp - 0.5, 2), ask=round(pe_ltp + 0.5, 2),
        ))
    return ChainSnapshot(
        symbol="NIFTY", expiry="2026-04-07", spot_ltp=spot,
        snapshot_timestamp=datetime.now(timezone.utc), rows=tuple(rows),
    )


# ── Base Metrics ───────────────────────────────────────────────────────────

class TestBaseMetrics:

    def test_distance_calculation(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        spot = sample_chain.spot_ltp
        for r in rows:
            assert r.distance == pytest.approx(r.strike - spot)
            assert r.abs_distance == pytest.approx(abs(r.strike - spot))

    def test_intrinsic_extrinsic_call(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        spot = sample_chain.spot_ltp
        for r in rows:
            expected_intr = max(spot - r.strike, 0)
            assert r.call_intrinsic == pytest.approx(expected_intr)
            if r.call_ltp and r.call_ltp > 0:
                assert r.call_extrinsic == pytest.approx(max(r.call_ltp - expected_intr, 0))

    def test_intrinsic_extrinsic_put(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        spot = sample_chain.spot_ltp
        for r in rows:
            expected_intr = max(r.strike - spot, 0)
            assert r.put_intrinsic == pytest.approx(expected_intr)
            if r.put_ltp and r.put_ltp > 0:
                assert r.put_extrinsic == pytest.approx(max(r.put_ltp - expected_intr, 0))

    def test_decay_normalized(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        for r in rows:
            if r.call_decay_abs is not None and r.call_ltp and r.call_ltp > 0:
                expected = r.call_decay_abs / max(r.call_ltp, cfg.decay.epsilon)
                assert r.call_decay_ratio == pytest.approx(expected)

    def test_liquidity_skew(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        for r in rows:
            if r.call_volume and r.put_volume and r.call_volume > 0:
                expected = r.put_volume / max(r.call_volume, 1)
                assert r.liquidity_skew == pytest.approx(expected)

    def test_spread_calculation(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        for r in rows:
            if r.call_spread is not None:
                assert r.call_spread >= 0
            if r.call_spread_pct is not None:
                assert r.call_spread_pct >= 0

    def test_band_eligibility(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        band_min = cfg.premium_band.min
        band_max = cfg.premium_band.max
        for r in rows:
            if r.call_ltp and band_min <= r.call_ltp <= band_max:
                assert r.call_band_eligible
            elif r.call_ltp:
                assert not r.call_band_eligible

    def test_sorted_by_strike(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        strikes = [r.strike for r in rows]
        assert strikes == sorted(strikes)


class TestFilterWindow:

    def test_atm_symmetric(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        window = filter_window(rows, sample_chain.spot_ltp, cfg)
        max_dist = cfg.instrument.strike_step * cfg.window.size
        for r in window:
            assert r.abs_distance <= max_dist

    def test_full_chain(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        from engines.lottery.config import WindowType
        cfg_full = replace(cfg, window=replace(cfg.window, type=WindowType.FULL_CHAIN))
        window = filter_window(rows, sample_chain.spot_ltp, cfg_full)
        assert len(window) == len(rows)


# ── Advanced Metrics ───────────────────────────────────────────────────────

class TestAdvancedMetrics:

    def test_premium_slope(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        rows = compute_advanced_metrics(rows, cfg)
        # Last row has no forward neighbor — slope should be None
        assert rows[-1].call_slope is None
        # Others should have slopes
        for r in rows[:-1]:
            assert r.call_slope is not None

    def test_theta_density(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        rows = compute_advanced_metrics(rows, cfg)
        for r in rows[:-1]:
            assert r.call_theta_density is not None
            assert r.put_theta_density is not None

    def test_slope_acceleration(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        rows = compute_advanced_metrics(rows, cfg)
        accel = compute_slope_acceleration(rows)
        assert len(accel) == len(rows) - 1
        for a in accel:
            assert "strike" in a
            assert "call_accel" in a


class TestSideBias:

    def test_mean_bias(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        window = filter_window(rows, sample_chain.spot_ltp, cfg)
        side, bias, avg_c, avg_p = compute_side_bias(window, cfg)
        if avg_c is not None and avg_p is not None:
            assert bias == pytest.approx(avg_c - avg_p)
            if bias > 0:
                assert side == Side.PE
            elif bias < 0:
                assert side == Side.CE

    def test_no_data_returns_none(self, cfg):
        empty_rows = []
        side, bias, avg_c, avg_p = compute_side_bias(empty_rows, cfg)
        assert side is None
        assert bias is None

    def test_pcr_bias(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        window = filter_window(rows, sample_chain.spot_ltp, cfg)
        pcr = compute_pcr_bias(window)
        assert pcr is not None
        assert pcr > 0


# ── Extrapolation ──────────────────────────────────────────────────────────

class TestExtrapolation:

    def test_extrapolation_produces_results(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, sample_chain.spot_ltp, cfg)
        # May or may not produce results depending on chain coverage
        assert isinstance(ext_ce, list)
        assert isinstance(ext_pe, list)

    def test_extrapolated_strikes_beyond_visible(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, sample_chain.spot_ltp, cfg)
        visible_strikes = {r.strike for r in rows}
        for e in ext_ce:
            assert e.strike not in visible_strikes
            assert e.option_type == OptionType.CE
        for e in ext_pe:
            assert e.strike not in visible_strikes
            assert e.option_type == OptionType.PE

    def test_compression_reduces_premium(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, sample_chain.spot_ltp, cfg)
        for e in ext_ce + ext_pe:
            assert e.adjusted_premium <= e.estimated_premium

    def test_insufficient_strikes_guard(self, cfg):
        # Chain with only 2 strikes — should not extrapolate
        rows = [
            CalculatedRow(strike=22700, distance=0, abs_distance=0, call_ltp=200, put_ltp=200),
            CalculatedRow(strike=22750, distance=50, abs_distance=50, call_ltp=180, put_ltp=220),
        ]
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, 22700, cfg)
        assert len(ext_ce) == 0
        assert len(ext_pe) == 0


# ── Scoring ────────────────────────────────────────────────────────────────

class TestScoring:

    def test_band_fit_gaussian(self, cfg):
        """Band fit is now a soft Gaussian preference, not a hard gate."""
        mid = (cfg.premium_band.min + cfg.premium_band.max) / 2
        score_mid = _compute_band_fit(mid, cfg)
        score_far = _compute_band_fit(50.0, cfg)
        score_zero = _compute_band_fit(0.0, cfg)
        # Mid should score highest
        assert score_mid > score_far
        # Zero premium should score 0
        assert score_zero == 0.0
        # All positive premiums get some score (Gaussian never reaches 0)
        assert _compute_band_fit(0.10, cfg) > 0
        assert _compute_band_fit(100.0, cfg) > 0  # very low but not zero

    def test_band_fit_preference(self, cfg):
        """Premiums closer to band center score higher."""
        mid = (cfg.premium_band.min + cfg.premium_band.max) / 2
        score_mid = _compute_band_fit(mid, cfg)
        score_edge = _compute_band_fit(cfg.premium_band.min, cfg)
        assert score_mid > score_edge

    def test_score_and_select(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        rows = compute_advanced_metrics(rows, cfg)
        window = filter_window(rows, sample_chain.spot_ltp, cfg)
        side, bias, _, _ = compute_side_bias(window, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, sample_chain.spot_ltp, cfg)
        best_ce, best_pe, cands = score_and_select(
            rows, ext_ce, ext_pe, sample_chain.spot_ltp, side, bias, cfg,
        )
        # Should produce candidates
        assert isinstance(cands, list)
        # If candidates exist, best should be selected
        ce_cands = [c for c in cands if c.option_type == OptionType.CE]
        if ce_cands:
            assert best_ce is not None
            assert best_ce.score >= max(c.score for c in ce_cands) - cfg.scoring.tie_epsilon

    def test_score_components_stored(self, sample_chain, cfg):
        rows = compute_base_metrics(sample_chain, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, sample_chain.spot_ltp, cfg)
        _, _, cands = score_and_select(
            rows, ext_ce, ext_pe, sample_chain.spot_ltp, None, None, cfg,
        )
        for c in cands:
            # New market-relative scoring components
            assert "f_liquidity" in c.components
            assert "f_structure" in c.components
            assert "f_premium" in c.components
            assert "f_distance" in c.components
            assert "f_tradability" in c.components
            assert "f_momentum" in c.components

    def test_otm_candidates_are_tradable(self, sample_chain, cfg):
        """All scored candidates must pass tradability gate (bid/ask + spread)."""
        rows = compute_base_metrics(sample_chain, cfg)
        ext_ce, ext_pe = extrapolate_otm_strikes(rows, sample_chain.spot_ltp, cfg)
        _, _, cands = score_and_select(
            rows, ext_ce, ext_pe, sample_chain.spot_ltp, None, None, cfg,
        )
        for c in cands:
            assert c.source == "VISIBLE"  # no extrapolated candidates in scoring
            assert c.spread_pct is not None
            assert c.spread_pct <= cfg.tradability.max_spread_pct

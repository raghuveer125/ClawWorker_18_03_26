"""Far-OTM extrapolation — linear decay + exponential compression model.

When the premium band [Emin, Emax] falls beyond visible chain strikes,
this module extrapolates premiums by:
1. Computing average step decay from the last N OTM strikes (fit window)
2. Projecting premiums linearly to further OTM strikes
3. Applying exponential compression: LTP_adj = LTP_est * e^(-α * n)
4. Identifying strikes where adjusted premium falls within the band

CE and PE are calibrated separately.
α can be FIXED (from config) or CALIBRATED (fit from visible chain).
"""

import logging
import math
from typing import Optional

from ..config import AlphaMode, LotteryConfig
from ..models import CalculatedRow, ExtrapolatedStrike, OptionType

logger = logging.getLogger(__name__)


def extrapolate_otm_strikes(
    rows: list[CalculatedRow],
    spot: float,
    config: LotteryConfig,
) -> tuple[list[ExtrapolatedStrike], list[ExtrapolatedStrike]]:
    """Extrapolate far-OTM premiums for CE and PE sides.

    Args:
        rows: Full chain of CalculatedRow sorted by strike.
        spot: Current spot price.
        config: Config with extrapolation settings and premium band.

    Returns:
        (extrapolated_ce, extrapolated_pe) — lists of projected strikes.
    """
    step = config.instrument.strike_step
    band_min = config.premium_band.min
    band_max = config.premium_band.max

    # ── CE extrapolation (strikes above spot, moving further OTM) ──
    ce_otm = [r for r in rows if r.strike > spot and r.call_ltp is not None and r.call_ltp > 0]
    ce_otm.sort(key=lambda r: r.strike)

    extrapolated_ce = _extrapolate_side(
        otm_rows=ce_otm,
        spot=spot,
        step=step,
        config=config,
        option_type=OptionType.CE,
        direction=1,  # strikes increase
        fit_window=config.extrapolation.fit_window_ce,
        band_min=band_min,
        band_max=band_max,
        ltp_getter=lambda r: r.call_ltp,
    )

    # ── PE extrapolation (strikes below spot, moving further OTM) ──
    pe_otm = [r for r in rows if r.strike < spot and r.put_ltp is not None and r.put_ltp > 0]
    pe_otm.sort(key=lambda r: r.strike, reverse=True)  # closest to spot first

    extrapolated_pe = _extrapolate_side(
        otm_rows=pe_otm,
        spot=spot,
        step=step,
        config=config,
        option_type=OptionType.PE,
        direction=-1,  # strikes decrease
        fit_window=config.extrapolation.fit_window_pe,
        band_min=band_min,
        band_max=band_max,
        ltp_getter=lambda r: r.put_ltp,
    )

    return extrapolated_ce, extrapolated_pe


def _extrapolate_side(
    otm_rows: list[CalculatedRow],
    spot: float,
    step: int,
    config: LotteryConfig,
    option_type: OptionType,
    direction: int,
    fit_window: int,
    band_min: float,
    band_max: float,
    ltp_getter,
) -> list[ExtrapolatedStrike]:
    """Extrapolate one side (CE or PE) beyond visible chain.

    Steps:
    1. Take last N OTM strikes (furthest from spot)
    2. Compute average premium decay per step
    3. Project forward until premium drops to 0
    4. Apply compression: adj = est * e^(-α * n)
    5. Return strikes where adj premium falls in [band_min, band_max]
    """
    min_strikes = config.extrapolation.min_valid_strikes

    if len(otm_rows) < min_strikes:
        logger.warning(
            "INSUFFICIENT_OTM_POINTS: %s has %d OTM strikes, need %d",
            option_type.value, len(otm_rows), min_strikes,
        )
        return []

    # ── Step 1: Get the last N furthest OTM strikes ────────────────
    # otm_rows are sorted: closest to spot first
    # For fitting, we want the FURTHEST from spot (tail end of OTM)
    tail = otm_rows[-fit_window:]  # furthest OTM strikes

    # ── Step 2: Compute average step decay ─────────────────────────
    step_decays: list[float] = []
    # Sort tail by distance from spot (ascending) for correct diff
    if direction > 0:
        tail_sorted = sorted(tail, key=lambda r: r.strike)
    else:
        tail_sorted = sorted(tail, key=lambda r: r.strike, reverse=True)

    for i in range(len(tail_sorted) - 1):
        ltp_curr = ltp_getter(tail_sorted[i])
        ltp_next = ltp_getter(tail_sorted[i + 1])
        if ltp_curr is not None and ltp_next is not None and ltp_curr > ltp_next:
            # Normalize to per-step decay
            actual_gap = abs(tail_sorted[i + 1].strike - tail_sorted[i].strike)
            if actual_gap > 0:
                decay_per_step = (ltp_curr - ltp_next) * (step / actual_gap)
                step_decays.append(decay_per_step)

    if not step_decays:
        # Fallback: compute from the full OTM tail
        for i in range(max(0, len(otm_rows) - fit_window - 1), len(otm_rows) - 1):
            ltp_curr = ltp_getter(otm_rows[i])
            ltp_next = ltp_getter(otm_rows[i + 1])
            if ltp_curr is not None and ltp_next is not None:
                diff = abs(ltp_curr - ltp_next)
                actual_gap = abs(otm_rows[i + 1].strike - otm_rows[i].strike)
                if actual_gap > 0 and diff > 0:
                    step_decays.append(diff * (step / actual_gap))

    if not step_decays:
        logger.warning("No valid step decays for %s extrapolation", option_type.value)
        return []

    avg_decay = sum(step_decays) / len(step_decays)
    if avg_decay <= 0:
        logger.warning("Non-positive avg decay for %s: %.4f", option_type.value, avg_decay)
        return []

    # ── Step 3: Calibrate α ────────────────────────────────────────
    alpha = _calibrate_alpha(otm_rows, spot, step, config, ltp_getter, fit_window_override=fit_window)

    # ── Step 4: Project forward ────────────────────────────────────
    # Start from the furthest visible OTM strike
    if direction > 0:
        last_strike = max(r.strike for r in otm_rows)
        last_ltp = ltp_getter(next(r for r in otm_rows if r.strike == last_strike))
    else:
        last_strike = min(r.strike for r in otm_rows)
        last_ltp = ltp_getter(next(r for r in otm_rows if r.strike == last_strike))

    if last_ltp is None or last_ltp <= 0:
        return []

    # ATM strike for step counting
    atm_strike = round(spot / step) * step
    atm_steps = abs(last_strike - atm_strike) / step

    results: list[ExtrapolatedStrike] = []
    current_ltp = last_ltp
    current_strike = last_strike
    max_projections = 50  # safety limit

    for proj in range(1, max_projections + 1):
        current_strike += direction * step
        current_ltp = max(current_ltp - avg_decay, 0)

        if current_ltp <= 0:
            break

        # Steps from ATM for compression
        n = atm_steps + proj
        adjusted = current_ltp * math.exp(-alpha * n)

        in_band = band_min <= adjusted <= band_max

        results.append(ExtrapolatedStrike(
            strike=current_strike,
            option_type=option_type,
            estimated_premium=round(current_ltp, 2),
            adjusted_premium=round(adjusted, 2),
            steps_from_atm=int(n),
            alpha_used=round(alpha, 6),
            in_band=in_band,
        ))

        # Stop once we've gone well past the band
        if adjusted < band_min * 0.1:
            break

    return results


def _calibrate_alpha(
    otm_rows: list[CalculatedRow],
    spot: float,
    step: int,
    config: LotteryConfig,
    ltp_getter,
    fit_window_override: Optional[int] = None,
) -> float:
    """Calibrate compression factor α from visible chain.

    If mode=FIXED, return config value.
    If mode=CALIBRATED, fit exponential decay to last OTM strikes.

    The model is: LTP(n) ≈ LTP_0 * e^(-α * n)
    Taking log: ln(LTP(n)) = ln(LTP_0) - α * n
    α = -slope of ln(LTP) vs n
    """
    if config.extrapolation.alpha_mode == AlphaMode.FIXED:
        return config.extrapolation.alpha_value

    # CALIBRATED: fit from visible OTM data
    atm_strike = round(spot / step) * step

    points: list[tuple[float, float]] = []  # (n, ln_ltp)
    for row in otm_rows:
        ltp = ltp_getter(row)
        if ltp is not None and ltp > 0:
            n = abs(row.strike - atm_strike) / step
            if n > 0:
                points.append((n, math.log(ltp)))

    if len(points) < 3:
        logger.debug("Too few points for α calibration, using fixed value")
        return config.extrapolation.alpha_value

    # Use last fit_window points (furthest OTM) for fitting
    fit_window = fit_window_override or config.extrapolation.fit_window_ce
    fit_points = sorted(points, key=lambda p: p[0])[-max(fit_window, 3):]

    # Simple linear regression: ln(LTP) = a - α * n
    n_vals = [p[0] for p in fit_points]
    y_vals = [p[1] for p in fit_points]

    n_mean = sum(n_vals) / len(n_vals)
    y_mean = sum(y_vals) / len(y_vals)

    numerator = sum((n - n_mean) * (y - y_mean) for n, y in zip(n_vals, y_vals))
    denominator = sum((n - n_mean) ** 2 for n in n_vals)

    if denominator == 0:
        return config.extrapolation.alpha_value

    slope = numerator / denominator
    alpha = -slope  # negative slope means decay, so α is positive

    # Clamp to reasonable range [0.001, 0.5]
    alpha = max(0.001, min(0.5, alpha))

    logger.debug("Calibrated α = %.6f from %d points", alpha, len(fit_points))
    return alpha

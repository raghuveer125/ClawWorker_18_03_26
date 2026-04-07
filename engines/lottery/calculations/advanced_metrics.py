"""Advanced metrics — curvature, theta density, side bias aggregation.

All functions are pure — they take CalculatedRows + config, return new data.
Strike gap handling: if strikes are non-contiguous, slopes are normalized
by the actual gap, not assumed step size.
"""

import logging
import math
from dataclasses import replace
from typing import Optional

from ..config import BiasAggregation, LotteryConfig
from ..models import CalculatedRow, Side

logger = logging.getLogger(__name__)


def compute_advanced_metrics(
    rows: list[CalculatedRow],
    config: LotteryConfig,
) -> list[CalculatedRow]:
    """Compute curvature (premium slope) and theta density for each strike.

    Args:
        rows: Base-metric calculated rows, sorted by strike.
        config: Lottery config.

    Returns:
        New list of CalculatedRow with slope and theta density populated.
    """
    if len(rows) < 2:
        return rows

    updated: list[CalculatedRow] = []

    for i, row in enumerate(rows):
        call_slope: Optional[float] = None
        put_slope: Optional[float] = None
        call_theta: Optional[float] = None
        put_theta: Optional[float] = None

        # Forward difference slope: m(Ki) = (LTP(Ki+1) - LTP(Ki)) / (Ki+1 - Ki)
        if i < len(rows) - 1:
            nxt = rows[i + 1]
            dk = nxt.strike - row.strike
            if dk > 0:
                # ── Premium slope (curvature) ──────────────────────
                if row.call_ltp is not None and nxt.call_ltp is not None:
                    call_slope = (nxt.call_ltp - row.call_ltp) / dk

                if row.put_ltp is not None and nxt.put_ltp is not None:
                    put_slope = (nxt.put_ltp - row.put_ltp) / dk

                # ── Extrinsic gradient (theta density) ─────────────
                call_theta = (nxt.call_extrinsic - row.call_extrinsic) / dk
                put_theta = (nxt.put_extrinsic - row.put_extrinsic) / dk

        updated.append(replace(
            row,
            call_slope=call_slope,
            put_slope=put_slope,
            call_theta_density=call_theta,
            put_theta_density=put_theta,
        ))

    return updated


def compute_side_bias(
    window_rows: list[CalculatedRow],
    config: LotteryConfig,
    spot: Optional[float] = None,
    spot_history: Optional[list[float]] = None,
) -> tuple[Optional[Side], Optional[float], Optional[float], Optional[float]]:
    """Compute directional side bias from decay asymmetry.

    Aggregation modes:
    - MEAN: simple average of |ΔC| and |ΔP|
    - VOLUME_WEIGHTED: weighted by respective volumes
    - DISTANCE_WEIGHTED: weighted by inverse distance from ATM

    Falls back to multi-factor bias (OI PCR + momentum + price position)
    when decay data is unavailable (FYERS returns change=None).

    Args:
        window_rows: Rows within the configured strike window.
        config: Config with bias aggregation mode.
        spot: Current spot price (for multi-factor fallback).
        spot_history: Recent spot prices newest-first (for momentum).

    Returns:
        (preferred_side, bias_score, avg_call_decay, avg_put_decay)
    """
    mode = config.bias.aggregation

    if mode == BiasAggregation.MEAN:
        result = _bias_simple_mean(window_rows)
    elif mode == BiasAggregation.VOLUME_WEIGHTED:
        result = _bias_volume_weighted(window_rows)
    elif mode == BiasAggregation.DISTANCE_WEIGHTED:
        result = _bias_distance_weighted(window_rows)
    else:
        result = _bias_simple_mean(window_rows)

    # Fallback: if decay data unavailable (FYERS returns change=None),
    # use multi-factor bias: OI PCR + spot momentum + price position
    if result[0] is None:
        bias = _bias_multi_factor(window_rows, spot, spot_history)
        if bias[0] is not None:
            return bias

    return result


def compute_pcr_bias(
    window_rows: list[CalculatedRow],
) -> Optional[float]:
    """Compute aggregate PCR from window rows (if use_pcr enabled).

    Returns:
        PCR = total_put_volume / total_call_volume, or None if no volume data.
    """
    total_call_vol = 0
    total_put_vol = 0

    for row in window_rows:
        if row.call_volume is not None:
            total_call_vol += row.call_volume
        if row.put_volume is not None:
            total_put_vol += row.put_volume

    if total_call_vol == 0:
        return None

    return total_put_vol / total_call_vol


def compute_slope_acceleration(
    rows: list[CalculatedRow],
) -> list[dict]:
    """Compute slope acceleration (second derivative of premium curve).

    Useful for detecting convexity changes near ATM.

    Args:
        rows: Rows with call_slope and put_slope populated.

    Returns:
        List of dicts with strike, call_accel, put_accel.
    """
    results: list[dict] = []

    for i in range(1, len(rows)):
        prev = rows[i - 1]
        curr = rows[i]
        dk = curr.strike - prev.strike

        call_accel: Optional[float] = None
        put_accel: Optional[float] = None

        if dk > 0 and prev.call_slope is not None and curr.call_slope is not None:
            call_accel = (curr.call_slope - prev.call_slope) / dk

        if dk > 0 and prev.put_slope is not None and curr.put_slope is not None:
            put_accel = (curr.put_slope - prev.put_slope) / dk

        results.append({
            "strike": curr.strike,
            "call_accel": call_accel,
            "put_accel": put_accel,
        })

    return results


# ── Bias Aggregation Implementations ──────────────────────────────────────

def _bias_simple_mean(
    rows: list[CalculatedRow],
) -> tuple[Optional[Side], Optional[float], Optional[float], Optional[float]]:
    """Simple mean of |ΔC| and |ΔP| across window rows."""
    call_decays = [r.call_decay_abs for r in rows if r.call_decay_abs is not None]
    put_decays = [r.put_decay_abs for r in rows if r.put_decay_abs is not None]

    if not call_decays or not put_decays:
        return None, None, None, None

    avg_call = sum(call_decays) / len(call_decays)
    avg_put = sum(put_decays) / len(put_decays)
    bias = avg_call - avg_put

    side = Side.PE if bias > 0 else Side.CE if bias < 0 else None
    return side, bias, avg_call, avg_put


def _bias_volume_weighted(
    rows: list[CalculatedRow],
) -> tuple[Optional[Side], Optional[float], Optional[float], Optional[float]]:
    """Volume-weighted average of |ΔC| and |ΔP|."""
    call_num = 0.0
    call_den = 0.0
    put_num = 0.0
    put_den = 0.0

    for row in rows:
        if row.call_decay_abs is not None and row.call_volume is not None and row.call_volume > 0:
            call_num += row.call_decay_abs * row.call_volume
            call_den += row.call_volume
        if row.put_decay_abs is not None and row.put_volume is not None and row.put_volume > 0:
            put_num += row.put_decay_abs * row.put_volume
            put_den += row.put_volume

    if call_den == 0 or put_den == 0:
        return _bias_simple_mean(rows)

    avg_call = call_num / call_den
    avg_put = put_num / put_den
    bias = avg_call - avg_put

    side = Side.PE if bias > 0 else Side.CE if bias < 0 else None
    return side, bias, avg_call, avg_put


def _bias_distance_weighted(
    rows: list[CalculatedRow],
) -> tuple[Optional[Side], Optional[float], Optional[float], Optional[float]]:
    """Distance-weighted average: closer to ATM gets higher weight."""
    call_num = 0.0
    call_den = 0.0
    put_num = 0.0
    put_den = 0.0

    for row in rows:
        # Weight = 1 / (1 + |distance|/50) — closer strikes get higher weight
        weight = 1.0 / (1.0 + row.abs_distance / 50.0)

        if row.call_decay_abs is not None:
            call_num += row.call_decay_abs * weight
            call_den += weight
        if row.put_decay_abs is not None:
            put_num += row.put_decay_abs * weight
            put_den += weight

    if call_den == 0 or put_den == 0:
        return _bias_simple_mean(rows)

    avg_call = call_num / call_den
    avg_put = put_num / put_den
    bias = avg_call - avg_put

    side = Side.PE if bias > 0 else Side.CE if bias < 0 else None
    return side, bias, avg_call, avg_put


def _bias_multi_factor(
    rows: list[CalculatedRow],
    spot: Optional[float] = None,
    spot_history: Optional[list[float]] = None,
) -> tuple[Optional[Side], Optional[float], Optional[float], Optional[float]]:
    """Multi-factor bias when decay data is unavailable.

    Factors (weighted):
    1. OI/Volume PCR: >1 = bearish (PE), <1 = bullish (CE)   [40%]
    2. Spot momentum: falling = PE, rising = CE               [35%]
    3. Price position: near resistance = PE, near support = CE [25%]

    Bias > 0 = PE favoured, Bias < 0 = CE favoured.
    """
    if not rows:
        return None, None, None, None

    # Factor 1: PCR from volume (OI not available from FYERS option chain)
    total_call_vol = 0
    total_put_vol = 0
    for row in rows:
        if row.call_volume is not None:
            total_call_vol += row.call_volume
        if row.put_volume is not None:
            total_put_vol += row.put_volume

    oi_factor = 0.0
    if total_call_vol > 0:
        pcr = total_put_vol / total_call_vol
        # PCR > 1 = more puts = bearish = PE favoured (positive)
        # PCR < 1 = more calls = bullish = CE favoured (negative)
        oi_factor = pcr - 1.0
        oi_factor = max(-1.0, min(1.0, oi_factor))

    # Factor 2: Spot momentum
    momentum_factor = 0.0
    if spot_history and len(spot_history) >= 2:
        recent = spot_history[0]
        older = spot_history[min(len(spot_history) - 1, 9)]
        if older > 0:
            pct_change = (recent - older) / older * 100
            # Falling = PE favoured (positive), Rising = CE favoured (negative)
            momentum_factor = -pct_change * 10
            momentum_factor = max(-1.0, min(1.0, momentum_factor))

    # Factor 3: Price position relative to nearest strikes
    position_factor = 0.0
    if spot and rows:
        strikes_below = [r.strike for r in rows if r.strike < spot]
        strikes_above = [r.strike for r in rows if r.strike > spot]
        if strikes_below and strikes_above:
            support = max(strikes_below)
            resistance = min(strikes_above)
            rng = resistance - support
            if rng > 0:
                position = (spot - support) / rng
                # Near resistance (1) = PE, near support (0) = CE
                position_factor = (position - 0.5) * 2
                position_factor = max(-1.0, min(1.0, position_factor))

    # Weighted combination
    composite = 0.40 * oi_factor + 0.35 * momentum_factor + 0.25 * position_factor

    # Always return a side — even weak bias is better than no bias
    side = Side.PE if composite >= 0 else Side.CE
    logger.debug("Multi-factor bias: oi=%.3f mom=%.3f pos=%.3f → %s (%.4f)",
                  oi_factor, momentum_factor, position_factor, side.value, composite)
    return side, round(composite, 4), None, None

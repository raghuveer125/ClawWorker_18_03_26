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
) -> tuple[Optional[Side], Optional[float], Optional[float], Optional[float]]:
    """Compute directional side bias from decay asymmetry.

    Aggregation modes:
    - MEAN: simple average of |ΔC| and |ΔP|
    - VOLUME_WEIGHTED: weighted by respective volumes
    - DISTANCE_WEIGHTED: weighted by inverse distance from ATM

    Args:
        window_rows: Rows within the configured strike window.
        config: Config with bias aggregation mode.

    Returns:
        (preferred_side, bias_score, avg_call_decay, avg_put_decay)
        - preferred_side: Side.PE if calls decay faster, Side.CE if puts decay faster, None if no data
        - bias_score: avg_call_decay - avg_put_decay (positive = PE preferred)
        - avg_call_decay: aggregated call decay
        - avg_put_decay: aggregated put decay
    """
    mode = config.bias.aggregation

    if mode == BiasAggregation.MEAN:
        return _bias_simple_mean(window_rows)
    elif mode == BiasAggregation.VOLUME_WEIGHTED:
        return _bias_volume_weighted(window_rows)
    elif mode == BiasAggregation.DISTANCE_WEIGHTED:
        return _bias_distance_weighted(window_rows)
    else:
        return _bias_simple_mean(window_rows)


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

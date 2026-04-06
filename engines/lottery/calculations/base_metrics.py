"""Base metrics — distance, intrinsic/extrinsic, decay, liquidity per strike.

All calculations are pure functions operating on frozen dataclasses.
Strike window filtering is applied before aggregation.
No hardcoded instrument values — everything from config or data.
"""

import logging
import math
from typing import Optional

from ..config import DecayMode, LotteryConfig, WindowType
from ..models import (
    CalculatedRow,
    ChainSnapshot,
    OptionRow,
    OptionType,
)

logger = logging.getLogger(__name__)


def compute_base_metrics(
    snapshot: ChainSnapshot,
    config: LotteryConfig,
) -> list[CalculatedRow]:
    """Compute base metrics for every strike in the chain.

    Per strike computes:
    - distance from spot
    - call/put intrinsic and extrinsic values
    - decay/momentum proxies (raw and normalized)
    - liquidity metrics (volume, skew, spread)

    Args:
        snapshot: Validated chain snapshot.
        config: Lottery config for thresholds and modes.

    Returns:
        List of CalculatedRow, one per unique strike, sorted by strike.
    """
    spot = snapshot.spot_ltp
    strikes = snapshot.strikes
    eps = config.decay.epsilon

    # Build lookup: strike → {CE: OptionRow, PE: OptionRow}
    strike_map: dict[float, dict[str, OptionRow]] = {}
    for row in snapshot.rows:
        key = row.option_type.value
        strike_map.setdefault(row.strike, {})[key] = row

    results: list[CalculatedRow] = []

    for strike in strikes:
        ce = strike_map.get(strike, {}).get("CE")
        pe = strike_map.get(strike, {}).get("PE")

        distance = strike - spot
        abs_distance = abs(distance)

        # ── Intrinsic / Extrinsic ──────────────────────────────────
        call_intrinsic = max(spot - strike, 0)
        call_ltp = ce.ltp if ce else 0.0
        call_extrinsic = max(call_ltp - call_intrinsic, 0)

        put_intrinsic = max(strike - spot, 0)
        put_ltp = pe.ltp if pe else 0.0
        put_extrinsic = max(put_ltp - put_intrinsic, 0)

        # ── Decay / Momentum ──────────────────────────────────────
        call_decay_abs = _abs_or_none(ce.change if ce else None)
        put_decay_abs = _abs_or_none(pe.change if pe else None)

        call_decay_ratio: Optional[float] = None
        put_decay_ratio: Optional[float] = None

        if config.decay.mode == DecayMode.NORMALIZED:
            if call_decay_abs is not None and call_ltp > 0:
                call_decay_ratio = call_decay_abs / max(call_ltp, eps)
            if put_decay_abs is not None and put_ltp > 0:
                put_decay_ratio = put_decay_abs / max(put_ltp, eps)
        else:
            # RAW mode: ratio = raw absolute decay
            call_decay_ratio = call_decay_abs
            put_decay_ratio = put_decay_abs

        # ── Liquidity ─────────────────────────────────────────────
        call_volume = ce.volume if ce else None
        put_volume = pe.volume if pe else None

        liquidity_skew: Optional[float] = None
        if put_volume is not None and call_volume is not None and call_volume > 0:
            liquidity_skew = put_volume / max(call_volume, 1)

        # ── Spread Quality ────────────────────────────────────────
        call_spread, call_spread_pct = _compute_spread(ce)
        put_spread, put_spread_pct = _compute_spread(pe)

        # ── Premium Band Eligibility ──────────────────────────────
        band_min = config.premium_band.min
        band_max = config.premium_band.max
        call_band_eligible = band_min <= call_ltp <= band_max if call_ltp > 0 else False
        put_band_eligible = band_min <= put_ltp <= band_max if put_ltp > 0 else False

        results.append(CalculatedRow(
            strike=strike,
            distance=distance,
            abs_distance=abs_distance,
            call_intrinsic=call_intrinsic,
            call_extrinsic=call_extrinsic,
            put_intrinsic=put_intrinsic,
            put_extrinsic=put_extrinsic,
            call_decay_abs=call_decay_abs,
            call_decay_ratio=call_decay_ratio,
            put_decay_abs=put_decay_abs,
            put_decay_ratio=put_decay_ratio,
            call_volume=call_volume,
            put_volume=put_volume,
            liquidity_skew=liquidity_skew,
            call_spread=call_spread,
            call_spread_pct=call_spread_pct,
            put_spread=put_spread,
            put_spread_pct=put_spread_pct,
            call_ltp=call_ltp,
            put_ltp=put_ltp,
            call_band_eligible=call_band_eligible,
            put_band_eligible=put_band_eligible,
        ))

    return sorted(results, key=lambda r: r.strike)


def filter_window(
    rows: list[CalculatedRow],
    spot: float,
    config: LotteryConfig,
) -> list[CalculatedRow]:
    """Filter calculated rows to the configured strike window.

    Used before aggregation functions (decay averages, bias, etc.)
    to ensure they operate only on the relevant ATM neighborhood.

    Args:
        rows: Full list of CalculatedRow.
        spot: Current spot price.
        config: Config with window type and size.

    Returns:
        Filtered subset of CalculatedRow within the window.
    """
    wtype = config.window.type
    wsize = config.window.size
    step = config.instrument.strike_step

    if wtype == WindowType.FULL_CHAIN:
        return rows

    if wtype == WindowType.ATM_SYMMETRIC:
        max_dist = step * wsize
        return [r for r in rows if r.abs_distance <= max_dist]

    if wtype == WindowType.VISIBLE_RANGE:
        # Use the middle portion of the chain
        if len(rows) <= wsize * 2:
            return rows
        mid = len(rows) // 2
        return rows[max(0, mid - wsize):mid + wsize]

    return rows


# ── Helpers ────────────────────────────────────────────────────────────────

def _abs_or_none(val: Optional[float]) -> Optional[float]:
    """Return abs(val) or None."""
    if val is None:
        return None
    return abs(val)


def _compute_spread(row: Optional[OptionRow]) -> tuple[Optional[float], Optional[float]]:
    """Compute absolute spread and spread % for an option row."""
    if row is None or row.bid is None or row.ask is None:
        return None, None
    if row.bid <= 0 or row.ask <= 0:
        return None, None
    spread = row.ask - row.bid
    mid = (row.bid + row.ask) / 2
    spread_pct = (spread / mid) * 100 if mid > 0 else None
    return spread, spread_pct

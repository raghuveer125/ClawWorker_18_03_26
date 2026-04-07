"""Per-strike rejection audit — produces one audit row per scanned strike.

Integrates with the scoring pipeline to capture WHY each strike was
accepted or rejected. This is the primary optimization dataset.

For each strike in the chain, records:
- premium band pass/fail
- OTM distance pass/fail
- direction pass/fail
- tradability pass/fail (from tradability module)
- liquidity pass/fail
- spread pass/fail
- bias alignment
- trigger pass
- final score (if accepted)
- primary + all rejection reasons
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from ..config import LotteryConfig
from ..models import (
    ChainSnapshot,
    OptionType,
    Side,
    StrikeRejectionAudit,
)
from .tradability import check_tradability, TradabilityResult

logger = logging.getLogger(__name__)


def build_rejection_audit(
    snapshot: ChainSnapshot,
    config: LotteryConfig,
    preferred_side: Optional[Side] = None,
    trigger_ce_active: bool = False,
    trigger_pe_active: bool = False,
    scored_strikes: Optional[dict[tuple[float, str], float]] = None,
) -> list[StrikeRejectionAudit]:
    """Scan all strikes and produce a rejection audit for each.

    Args:
        snapshot: Current chain snapshot.
        config: Lottery config.
        preferred_side: Side bias from decay analysis.
        trigger_ce_active: Whether CE trigger zone is active.
        trigger_pe_active: Whether PE trigger zone is active.
        scored_strikes: Dict of (strike, type) → score for accepted candidates.

    Returns:
        List of StrikeRejectionAudit, one per scanned strike+side.
    """
    spot = snapshot.spot_ltp
    band_min = config.premium_band.min
    band_max = config.premium_band.max
    otm_min = config.otm_distance.min_points
    tc = config.tradability
    scored = scored_strikes or {}

    audits: list[StrikeRejectionAudit] = []

    # Build strike → row lookup
    strike_rows: dict[tuple[float, str], object] = {}
    for row in snapshot.rows:
        strike_rows[(row.strike, row.option_type.value)] = row

    # Scan all unique strikes for both CE and PE
    strikes = sorted(set(r.strike for r in snapshot.rows))

    for strike in strikes:
        for otype in [OptionType.CE, OptionType.PE]:
            key = (strike, otype.value)
            row = strike_rows.get(key)
            if row is None:
                continue

            ltp = row.ltp
            rejections: list[str] = []

            # 1. Direction check
            if otype == OptionType.CE:
                direction_pass = strike > spot
            else:
                direction_pass = strike < spot
            if not direction_pass:
                rejections.append("direction_itm")

            # 2. Distance check
            distance = abs(strike - spot)
            distance_pass = distance >= otm_min
            if not distance_pass:
                rejections.append(f"distance_short({distance:.0f}<{otm_min})")

            # 3. Band check
            band_pass = band_min <= ltp <= band_max if ltp > 0 else False
            if not band_pass:
                if ltp <= 0:
                    rejections.append("ltp_zero")
                elif ltp < band_min:
                    rejections.append(f"premium_low({ltp:.2f}<{band_min})")
                else:
                    rejections.append(f"premium_high({ltp:.2f}>{band_max})")

            # 4. Tradability check
            trad_result = check_tradability(row, tc)
            tradability_pass = trad_result.tradable
            if not tradability_pass:
                for r in trad_result.rejection_all:
                    rejections.append(f"trad:{r}")

            # 5. Liquidity (volume)
            vol = row.volume or 0
            liquidity_pass = vol >= tc.min_recent_volume
            if not liquidity_pass and f"trad:volume_low({vol})" not in rejections:
                rejections.append(f"liquidity_low({vol})")

            # 6. Spread
            spread_pass = True
            if row.bid is not None and row.ask is not None and row.bid > 0 and row.ask > 0:
                mid = (row.bid + row.ask) / 2
                spread_pct = ((row.ask - row.bid) / mid) * 100 if mid > 0 else 999
                spread_pass = spread_pct <= tc.max_spread_pct
                if not spread_pass and f"trad:spread_wide({spread_pct:.1f}%)" not in rejections:
                    rejections.append(f"spread({spread_pct:.1f}%)")

            # 7. Bias alignment
            bias_pass = True
            if preferred_side is not None:
                if otype == OptionType.CE and preferred_side == Side.PE:
                    bias_pass = False  # wrong side
                elif otype == OptionType.PE and preferred_side == Side.CE:
                    bias_pass = False
                # Not adding to rejections — bias is a scoring factor, not a hard rejection

            # 8. Trigger pass
            if otype == OptionType.CE:
                trigger_pass = trigger_ce_active
            else:
                trigger_pass = trigger_pe_active

            # Score (if this strike was accepted and scored)
            score = scored.get(key)
            accepted = len(rejections) == 0 and direction_pass and distance_pass and band_pass

            audit = StrikeRejectionAudit(
                snapshot_id=snapshot.snapshot_id,
                symbol=snapshot.symbol,
                strike=strike,
                option_type=otype,
                ltp=ltp,
                band_pass=band_pass,
                distance_pass=distance_pass,
                direction_pass=direction_pass,
                tradability_pass=tradability_pass,
                liquidity_pass=liquidity_pass,
                spread_pass=spread_pass,
                bias_pass=bias_pass,
                trigger_pass=trigger_pass,
                score=score,
                accepted=accepted and tradability_pass,
                rejection_primary=rejections[0] if rejections else None,
                rejection_all=tuple(rejections),
            )
            audits.append(audit)

    return audits

"""Strike scoring — market-relative composite score and selection.

Candidates are selected by market structure, not fixed premium bands.
Tradability is the primary gate. Premium is a soft preference.

Score = w_structure * StructureScore
      + w_liquidity * LiquidityScore
      + w_premium_eff * PremiumEfficiency
      + w_distance * DistanceSuitability
      + w_tradability * TradabilityScore
      + w_momentum * MomentumScore

All raw component values and weighted final score are stored for audit.
"""

import logging
import math
from dataclasses import replace
from typing import Optional

from ..config import BandFitMode, LotteryConfig
from ..models import (
    CalculatedRow,
    ExtrapolatedStrike,
    OptionType,
    Side,
)

logger = logging.getLogger(__name__)


# ── Candidate dataclass for scoring ────────────────────────────────────────

class ScoredCandidate:
    """A scored strike candidate (visible or extrapolated)."""

    __slots__ = (
        "strike", "option_type", "ltp", "score", "components",
        "band_fit", "spread_pct", "volume", "distance", "source",
    )

    def __init__(
        self,
        strike: float,
        option_type: OptionType,
        ltp: float,
        score: float,
        components: dict,
        band_fit: float,
        spread_pct: Optional[float],
        volume: Optional[int],
        distance: float,
        source: str,
    ):
        self.strike = strike
        self.option_type = option_type
        self.ltp = ltp
        self.score = score
        self.components = components
        self.band_fit = band_fit
        self.spread_pct = spread_pct
        self.volume = volume
        self.distance = distance
        self.source = source  # "VISIBLE" or "EXTRAPOLATED"


def score_and_select(
    rows: list[CalculatedRow],
    extrapolated_ce: list[ExtrapolatedStrike],
    extrapolated_pe: list[ExtrapolatedStrike],
    spot: float,
    preferred_side: Optional[Side],
    bias_score: Optional[float],
    config: LotteryConfig,
) -> tuple[Optional[ScoredCandidate], Optional[ScoredCandidate], list[ScoredCandidate]]:
    """Score all tradable OTM candidates and select best CE and PE.

    Key design: tradability is the PRIMARY gate, premium is a soft preference.
    All OTM strikes with valid bid/ask and acceptable spread are scored.
    """
    step = config.instrument.strike_step
    max_spread = config.tradability.max_spread_pct
    min_volume = config.tradability.min_recent_volume
    min_candidates = config.scoring.min_valid_candidates
    eps = config.scoring.tie_epsilon

    # Compute chain-wide statistics for relative scoring
    all_volumes = []
    all_oi = []
    for row in rows:
        if row.call_volume and row.call_volume > 0:
            all_volumes.append(row.call_volume)
        if row.put_volume and row.put_volume > 0:
            all_volumes.append(row.put_volume)
        if hasattr(row, 'call_vol_oi_ratio'):
            pass  # OI accessed via volume/ratio
    max_volume = max(all_volumes) if all_volumes else 1

    all_candidates: list[ScoredCandidate] = []

    # ── Score ALL tradable OTM strikes (no premium band gate) ──────
    for row in rows:
        # CE: must be OTM, have bid/ask, acceptable spread
        if (row.call_ltp is not None and row.call_ltp > 0
                and row.strike > spot):
            spread = row.call_spread_pct
            vol = row.call_volume or 0
            # Tradability gate: bid/ask exist + spread ok + volume ok
            tradable = (
                spread is not None
                and spread <= max_spread
                and vol >= min_volume
            )
            if tradable:
                candidate = _score_candidate(
                    row=row, option_type=OptionType.CE, ltp=row.call_ltp,
                    spot=spot, step=step, bias_score=bias_score,
                    config=config, max_volume=max_volume,
                )
                all_candidates.append(candidate)

        # PE: must be OTM, have bid/ask, acceptable spread
        if (row.put_ltp is not None and row.put_ltp > 0
                and row.strike < spot):
            spread = row.put_spread_pct
            vol = row.put_volume or 0
            tradable = (
                spread is not None
                and spread <= max_spread
                and vol >= min_volume
            )
            if tradable:
                candidate = _score_candidate(
                    row=row, option_type=OptionType.PE, ltp=row.put_ltp,
                    spot=spot, step=step, bias_score=bias_score,
                    config=config, max_volume=max_volume,
                )
                all_candidates.append(candidate)

    # ── Separate by side ──────────────────────────────────────────
    ce_candidates = [c for c in all_candidates if c.option_type == OptionType.CE]
    pe_candidates = [c for c in all_candidates if c.option_type == OptionType.PE]

    # ── Select best per side ──────────────────────────────────────
    best_ce = _select_best(ce_candidates, eps, config) if len(ce_candidates) >= min_candidates else None
    best_pe = _select_best(pe_candidates, eps, config) if len(pe_candidates) >= min_candidates else None

    logger.info(
        "Scoring: %d CE, %d PE tradable. Best CE=%s, Best PE=%s",
        len(ce_candidates), len(pe_candidates),
        f"K={best_ce.strike:.0f} @{best_ce.ltp} score={best_ce.score:.2f}" if best_ce else "None",
        f"K={best_pe.strike:.0f} @{best_pe.ltp} score={best_pe.score:.2f}" if best_pe else "None",
    )

    return best_ce, best_pe, all_candidates


def update_rows_with_scores(
    rows: list[CalculatedRow],
    all_candidates: list[ScoredCandidate],
) -> list[CalculatedRow]:
    """Write scoring results back into CalculatedRows for audit/display."""
    score_map: dict[tuple[float, str], ScoredCandidate] = {}
    for c in all_candidates:
        score_map[(c.strike, c.option_type.value)] = c

    updated: list[CalculatedRow] = []
    for row in rows:
        ce_cand = score_map.get((row.strike, "CE"))
        pe_cand = score_map.get((row.strike, "PE"))
        updated.append(replace(
            row,
            call_candidate_score=ce_cand.score if ce_cand else None,
            call_score_components=ce_cand.components if ce_cand else None,
            put_candidate_score=pe_cand.score if pe_cand else None,
            put_score_components=pe_cand.components if pe_cand else None,
        ))
    return updated


# ── Scoring Engine ────────────────────────────────────────────────────────

def _score_candidate(
    row: CalculatedRow,
    option_type: OptionType,
    ltp: float,
    spot: float,
    step: int,
    bias_score: Optional[float],
    config: LotteryConfig,
    max_volume: int,
) -> ScoredCandidate:
    """Score a tradable OTM candidate using market-relative features.

    Six scoring components:
    1. LiquidityScore:     volume relative to chain max + spread quality
    2. OIStructure:        OI concentration (high OI = market commitment)
    3. PremiumEfficiency:   payoff potential relative to cost
    4. DistanceSuitability: closeness to ideal OTM distance
    5. TradabilityScore:   spread tightness (tighter = better)
    6. MomentumScore:      bias alignment + premium ROC
    """
    w = config.scoring
    distance = abs(row.strike - spot)

    # ── 1. Liquidity Score (volume + OI relative to chain) ─────────
    if option_type == OptionType.CE:
        vol = row.call_volume or 0
        spread_pct = row.call_spread_pct
    else:
        vol = row.put_volume or 0
        spread_pct = row.put_spread_pct

    # Volume percentile within chain (0-1)
    vol_score = math.log(1 + vol) / math.log(1 + max_volume) if max_volume > 0 and vol > 0 else 0.0
    # Spread quality (inverted: lower spread = higher score)
    spread_score = max(0.0, 1.0 - (spread_pct or 10.0) / 10.0)
    f_liquidity = 0.5 * vol_score + 0.5 * spread_score

    # ── 2. OI Structure (high OI = market participants committed) ──
    if option_type == OptionType.CE:
        oi_ratio = row.call_vol_oi_ratio
    else:
        oi_ratio = row.put_vol_oi_ratio
    # vol/OI ratio 1-3 = healthy, >10 = churn, <0.5 = stale
    if oi_ratio is not None and oi_ratio > 0:
        if oi_ratio < 0.5:
            f_structure = 0.2  # stale
        elif oi_ratio <= 5.0:
            f_structure = 1.0  # healthy participation
        else:
            f_structure = max(0.3, 1.0 - (oi_ratio - 5.0) / 20.0)  # churn penalty
    else:
        f_structure = 0.5  # neutral when OI ratio unavailable

    # ── 3. Premium Efficiency (payoff potential / cost) ────────────
    # A Rs 5 option 100pts OTM can move Rs 15-20 on a 100pt breakout = 3-4x
    # A Rs 0.40 option 300pts OTM barely moves = poor efficiency
    # Efficiency = distance / (premium * step) — how much OTM bang per premium buck
    if ltp > 0:
        f_premium_eff = min(1.0, (distance / step) / (ltp * 2 + 1))
    else:
        f_premium_eff = 0.0

    # Soft premium preference (Gaussian around preferred mid)
    # Instead of hard band, prefer premiums in Rs 1-20 range (configurable center)
    band_min = config.premium_band.min
    band_max = config.premium_band.max
    preferred_mid = (band_min + band_max) / 2
    sigma = (band_max - band_min) / 2 if band_max > band_min else 5.0
    premium_pref = math.exp(-((ltp - preferred_mid) ** 2) / (2 * sigma ** 2))

    # Combine efficiency + preference
    f_premium = 0.6 * f_premium_eff + 0.4 * premium_pref

    # ── 4. Distance Suitability (closeness to ideal OTM) ──────────
    otm_min = config.otm_distance.min_points
    otm_max = config.otm_distance.max_points
    target_otm = (otm_min + otm_max) / 2
    otm_range = (otm_max - otm_min) / 2 if otm_max > otm_min else 100.0

    if distance < otm_min * 0.5:
        # Too close to ATM — less lottery-like
        f_distance = max(0.0, distance / (otm_min * 0.5)) * 0.5
    elif otm_min <= distance <= otm_max:
        # Sweet spot — peaks at target
        f_distance = max(0.0, 1.0 - abs(distance - target_otm) / otm_range)
    else:
        # Beyond max — penalty but don't exclude
        f_distance = max(0.0, 1.0 - (distance - otm_max) / otm_max)

    # ── 5. Tradability Score (spread tightness) ───────────────────
    # Already gated by max_spread, but tighter is better for scoring
    f_tradability = max(0.0, 1.0 - (spread_pct or 10.0) / config.tradability.max_spread_pct)

    # ── 6. Momentum Score (bias alignment + premium ROC) ──────────
    b = bias_score if bias_score is not None else 0.0
    if option_type == OptionType.CE:
        raw_roc = row.call_premium_roc if row.call_premium_roc is not None else 0.0
    else:
        raw_roc = row.put_premium_roc if row.put_premium_roc is not None else 0.0
    f_roc = max(0.0, raw_roc * 100)
    f_momentum = 0.5 * min(1.0, abs(b)) + 0.5 * min(1.0, f_roc)

    # ── Composite Score ───────────────────────────────────────────
    score = (
        w.w1_distance * f_distance          # was distance, now suitability
        + w.w2_momentum * f_momentum         # bias + ROC
        + w.w3_liquidity * f_liquidity       # volume + spread quality
        + w.w4_band_fit * f_premium          # premium efficiency + soft preference
        + w.w5_bias * f_structure            # OI structure
        + w.w6_roc * f_tradability           # spread tightness
    )

    components = {
        "f_liquidity": round(f_liquidity, 4),
        "f_structure": round(f_structure, 4),
        "f_premium": round(f_premium, 4),
        "f_distance": round(f_distance, 4),
        "f_tradability": round(f_tradability, 4),
        "f_momentum": round(f_momentum, 4),
        "premium_pref": round(premium_pref, 4),
        "premium_eff": round(f_premium_eff, 4),
        "vol_score": round(vol_score, 4),
        "spread_score": round(spread_score, 4),
        "ltp": round(ltp, 2),
        "spread_pct": round(spread_pct, 2) if spread_pct else None,
        "volume": vol,
        "distance_pts": round(distance, 0),
    }

    return ScoredCandidate(
        strike=row.strike,
        option_type=option_type,
        ltp=ltp,
        score=round(score, 6),
        components=components,
        band_fit=premium_pref,
        spread_pct=spread_pct,
        volume=vol,
        distance=distance,
        source="VISIBLE",
    )


def _compute_band_fit(ltp: float, config: LotteryConfig) -> float:
    """Compute premium band fit score (soft Gaussian preference).

    Used by external modules that still reference this function.
    """
    band_min = config.premium_band.min
    band_max = config.premium_band.max
    preferred_mid = (band_min + band_max) / 2
    sigma = (band_max - band_min) / 2 if band_max > band_min else 5.0

    if ltp <= 0:
        return 0.0

    return math.exp(-((ltp - preferred_mid) ** 2) / (2 * sigma ** 2))


def _select_best(
    candidates: list[ScoredCandidate],
    tie_epsilon: float,
    config: Optional[LotteryConfig] = None,
) -> Optional[ScoredCandidate]:
    """Select best candidate. Highest score wins. Tie-break by spread then volume."""
    if not candidates:
        return None

    sorted_cands = sorted(candidates, key=lambda c: c.score, reverse=True)
    best = sorted_cands[0]

    tied = [c for c in sorted_cands if abs(c.score - best.score) <= tie_epsilon]
    if len(tied) == 1:
        return tied[0]

    # Tie-break: lowest spread
    tied.sort(key=lambda c: c.spread_pct if c.spread_pct is not None else 999.0)
    if len(tied) == 1:
        return tied[0]

    # Tie-break: highest volume
    tied.sort(key=lambda c: c.volume if c.volume is not None else 0, reverse=True)
    return tied[0]

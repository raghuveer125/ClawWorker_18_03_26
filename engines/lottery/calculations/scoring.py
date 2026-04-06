"""Strike scoring — composite score, tie-break, and final selection.

Scores visible chain candidates and extrapolated candidates.
Composite score: w1*f_dist + w2*f_mom + w3*f_liq + w4*f_band + w5*B

Tie-break priority:
1. band-fit score
2. spread quality (lowest spread %)
3. liquidity (highest volume)
4. distance closeness to target OTM
5. highest composite score

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
    """Score all candidates and select best CE and PE strikes.

    Args:
        rows: Full chain CalculatedRows with base + advanced metrics.
        extrapolated_ce: Projected CE strikes beyond visible chain.
        extrapolated_pe: Projected PE strikes beyond visible chain.
        spot: Current spot price.
        preferred_side: Side bias from decay analysis (or None).
        bias_score: Numeric bias value (positive = PE preferred).
        config: Config with scoring weights, band, distance rules.

    Returns:
        (best_ce, best_pe, all_candidates) — best picks per side + full scored list.
    """
    band_min = config.premium_band.min
    band_max = config.premium_band.max
    otm_min = config.otm_distance.min_points
    otm_max = config.otm_distance.max_points
    weights = config.scoring
    step = config.instrument.strike_step
    eps = config.scoring.tie_epsilon
    min_candidates = config.scoring.min_valid_candidates

    all_candidates: list[ScoredCandidate] = []

    # ── Score visible chain candidates ─────────────────────────────
    for row in rows:
        # CE candidate
        if (row.call_ltp is not None
                and band_min <= row.call_ltp <= band_max
                and row.strike > spot
                and row.distance >= otm_min):
            candidate = _score_visible_candidate(
                row=row,
                option_type=OptionType.CE,
                ltp=row.call_ltp,
                spot=spot,
                step=step,
                bias_score=bias_score,
                config=config,
            )
            all_candidates.append(candidate)

        # PE candidate
        if (row.put_ltp is not None
                and band_min <= row.put_ltp <= band_max
                and row.strike < spot
                and abs(row.distance) >= otm_min):
            candidate = _score_visible_candidate(
                row=row,
                option_type=OptionType.PE,
                ltp=row.put_ltp,
                spot=spot,
                step=step,
                bias_score=bias_score,
                config=config,
            )
            all_candidates.append(candidate)

    # ── Score extrapolated candidates (ADVISORY ONLY) ───────────────
    # Extrapolated candidates are only included if no real quoted
    # candidates exist for that side. Prefer real market data.
    visible_ce = [c for c in all_candidates if c.option_type == OptionType.CE]
    visible_pe = [c for c in all_candidates if c.option_type == OptionType.PE]

    if not visible_ce:
        for ext in extrapolated_ce:
            if ext.in_band and abs(ext.strike - spot) >= otm_min:
                candidate = _score_extrapolated_candidate(
                    ext=ext,
                    spot=spot,
                    step=step,
                    bias_score=bias_score,
                    config=config,
                )
                all_candidates.append(candidate)
        if extrapolated_ce:
            logger.info("Extrapolation advisory: %d CE projected (no visible CE in band)", len(extrapolated_ce))

    if not visible_pe:
        for ext in extrapolated_pe:
            if ext.in_band and abs(ext.strike - spot) >= otm_min:
                candidate = _score_extrapolated_candidate(
                    ext=ext,
                    spot=spot,
                    step=step,
                    bias_score=bias_score,
                    config=config,
                )
                all_candidates.append(candidate)
        if extrapolated_pe:
            logger.info("Extrapolation advisory: %d PE projected (no visible PE in band)", len(extrapolated_pe))

    # ── Separate by side ──────────────────────────────────────────
    ce_candidates = [c for c in all_candidates if c.option_type == OptionType.CE]
    pe_candidates = [c for c in all_candidates if c.option_type == OptionType.PE]

    # ── Select best per side with tie-break ────────────────────────
    best_ce = _select_best(ce_candidates, eps, config) if len(ce_candidates) >= min_candidates else None
    best_pe = _select_best(pe_candidates, eps, config) if len(pe_candidates) >= min_candidates else None

    logger.info(
        "Scoring: %d CE (%d visible, %d extrap), %d PE (%d visible, %d extrap). Best CE=%s, Best PE=%s",
        len(ce_candidates), len(visible_ce), len(ce_candidates) - len(visible_ce),
        len(pe_candidates), len(visible_pe), len(pe_candidates) - len(visible_pe),
        f"K={best_ce.strike:.0f} score={best_ce.score:.4f} ({best_ce.source})" if best_ce else "None",
        f"K={best_pe.strike:.0f} score={best_pe.score:.4f} ({best_pe.source})" if best_pe else "None",
    )

    return best_ce, best_pe, all_candidates


def update_rows_with_scores(
    rows: list[CalculatedRow],
    all_candidates: list[ScoredCandidate],
) -> list[CalculatedRow]:
    """Write scoring results back into CalculatedRows for audit/display.

    Args:
        rows: Original CalculatedRows.
        all_candidates: All scored candidates.

    Returns:
        New list with score fields populated where candidates matched.
    """
    # Build lookup: (strike, option_type) → ScoredCandidate
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


# ── Scoring Helpers ────────────────────────────────────────────────────────

def _score_visible_candidate(
    row: CalculatedRow,
    option_type: OptionType,
    ltp: float,
    spot: float,
    step: int,
    bias_score: Optional[float],
    config: LotteryConfig,
) -> ScoredCandidate:
    """Score a visible chain candidate."""
    w = config.scoring

    # f_dist: distance from spot normalized by step
    f_dist = abs(row.strike - spot) / step

    # f_mom: momentum (decay ratio)
    if option_type == OptionType.CE:
        f_mom = row.call_decay_ratio if row.call_decay_ratio is not None else 0.0
    else:
        f_mom = row.put_decay_ratio if row.put_decay_ratio is not None else 0.0

    # f_liq: liquidity (log volume)
    if option_type == OptionType.CE:
        vol = row.call_volume or 0
    else:
        vol = row.put_volume or 0
    f_liq = math.log(1 + vol) if vol > 0 else 0.0

    # f_band: premium band fit
    f_band = _compute_band_fit(ltp, config)

    # B: bias alignment
    b = bias_score if bias_score is not None else 0.0

    # Composite score
    score = (
        w.w1_distance * f_dist
        + w.w2_momentum * f_mom
        + w.w3_liquidity * f_liq
        + w.w4_band_fit * f_band
        + w.w5_bias * b
    )

    components = {
        "f_dist": round(f_dist, 4),
        "f_mom": round(f_mom, 4),
        "f_liq": round(f_liq, 4),
        "f_band": round(f_band, 4),
        "bias": round(b, 4),
        "w1_dist": round(w.w1_distance * f_dist, 4),
        "w2_mom": round(w.w2_momentum * f_mom, 4),
        "w3_liq": round(w.w3_liquidity * f_liq, 4),
        "w4_band": round(w.w4_band_fit * f_band, 4),
        "w5_bias": round(w.w5_bias * b, 4),
    }

    # Spread % for tie-break
    if option_type == OptionType.CE:
        spread_pct = row.call_spread_pct
        volume = row.call_volume
    else:
        spread_pct = row.put_spread_pct
        volume = row.put_volume

    return ScoredCandidate(
        strike=row.strike,
        option_type=option_type,
        ltp=ltp,
        score=round(score, 6),
        components=components,
        band_fit=f_band,
        spread_pct=spread_pct,
        volume=volume,
        distance=abs(row.strike - spot),
        source="VISIBLE",
    )


def _score_extrapolated_candidate(
    ext: ExtrapolatedStrike,
    spot: float,
    step: int,
    bias_score: Optional[float],
    config: LotteryConfig,
) -> ScoredCandidate:
    """Score an extrapolated candidate (no spread/volume data)."""
    w = config.scoring
    ltp = ext.adjusted_premium

    f_dist = abs(ext.strike - spot) / step
    f_mom = 0.0  # no decay data for extrapolated strikes
    f_liq = 0.0  # no volume data
    f_band = _compute_band_fit(ltp, config)
    b = bias_score if bias_score is not None else 0.0

    score = (
        w.w1_distance * f_dist
        + w.w2_momentum * f_mom
        + w.w3_liquidity * f_liq
        + w.w4_band_fit * f_band
        + w.w5_bias * b
    )

    components = {
        "f_dist": round(f_dist, 4),
        "f_mom": 0.0,
        "f_liq": 0.0,
        "f_band": round(f_band, 4),
        "bias": round(b, 4),
        "w1_dist": round(w.w1_distance * f_dist, 4),
        "w2_mom": 0.0,
        "w3_liq": 0.0,
        "w4_band": round(w.w4_band_fit * f_band, 4),
        "w5_bias": round(w.w5_bias * b, 4),
    }

    return ScoredCandidate(
        strike=ext.strike,
        option_type=ext.option_type,
        ltp=ltp,
        score=round(score, 6),
        components=components,
        band_fit=f_band,
        spread_pct=None,
        volume=None,
        distance=abs(ext.strike - spot),
        source="EXTRAPOLATED",
    )


def _compute_band_fit(ltp: float, config: LotteryConfig) -> float:
    """Compute premium band fit score.

    BINARY mode: 1 if in band, 0 otherwise.
    DISTANCE mode: 1 - |LTP - mid| / range (closer to center = higher).
    """
    band_min = config.premium_band.min
    band_max = config.premium_band.max

    if ltp < band_min or ltp > band_max:
        return 0.0

    if config.premium_band.fit_mode == BandFitMode.BINARY:
        return 1.0

    # DISTANCE mode
    mid = (band_min + band_max) / 2
    band_range = (band_max - band_min) / 2
    if band_range <= 0:
        return 1.0
    return max(0.0, 1.0 - abs(ltp - mid) / band_range)


def _select_best(
    candidates: list[ScoredCandidate],
    tie_epsilon: float,
    config: Optional[LotteryConfig] = None,
) -> Optional[ScoredCandidate]:
    """Select best candidate with tie-break logic.

    Priority:
    1. Highest composite score
    2. If tie (within epsilon): best band-fit
    3. If still tied: lowest spread %
    4. If still tied: highest volume
    5. If still tied: closest to target OTM distance (from config)
    """
    if not candidates:
        return None

    sorted_cands = sorted(candidates, key=lambda c: c.score, reverse=True)
    best = sorted_cands[0]

    tied = [c for c in sorted_cands if abs(c.score - best.score) <= tie_epsilon]

    if len(tied) == 1:
        return tied[0]

    # Tie-break 1: best band-fit
    tied.sort(key=lambda c: c.band_fit, reverse=True)
    best_band = tied[0].band_fit
    tied = [c for c in tied if abs(c.band_fit - best_band) <= tie_epsilon]
    if len(tied) == 1:
        return tied[0]

    # Tie-break 2: lowest spread %
    tied.sort(key=lambda c: c.spread_pct if c.spread_pct is not None else 999.0)
    if len(tied) > 1 and tied[0].spread_pct is not None:
        best_spread = tied[0].spread_pct
        tied = [c for c in tied if c.spread_pct is not None and c.spread_pct <= best_spread + tie_epsilon]
    if len(tied) == 1:
        return tied[0]

    # Tie-break 3: highest volume
    tied.sort(key=lambda c: c.volume if c.volume is not None else 0, reverse=True)
    if len(tied) == 1:
        return tied[0]

    # Tie-break 4: closest to target OTM distance — from config, not hardcoded
    otm_min = config.otm_distance.min_points if config else 250
    otm_max = config.otm_distance.max_points if config else 450
    target_otm = (otm_min + otm_max) / 2
    tied.sort(key=lambda c: abs(c.distance - target_otm))

    return tied[0]

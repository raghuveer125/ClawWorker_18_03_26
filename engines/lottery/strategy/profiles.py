"""Strategy mode profiles — PRE_EXPIRY, EXPIRY_DAY, DTE1_HYBRID.

Each profile overrides specific config parameters at runtime to adapt
the pipeline behavior based on days-to-expiry (DTE).

Profiles do NOT replace the config — they overlay specific values.
The base config remains the single source of truth for everything else.

PRE_EXPIRY_MOMENTUM:  DTE >= 2 — wider bands, slower refresh, directional
EXPIRY_DAY_TRUE_LOTTERY: DTE == 0 — tight bands, fast refresh, strict confirmation
DTE1_HYBRID:          DTE == 1 — intermediate settings
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ..strategy.confirmation import ConfirmationConfig, ConfirmationMode

logger = logging.getLogger(__name__)


class StrategyMode(Enum):
    PRE_EXPIRY_MOMENTUM = "PRE_EXPIRY_MOMENTUM"
    DTE1_HYBRID = "DTE1_HYBRID"
    EXPIRY_DAY_TRUE_LOTTERY = "EXPIRY_DAY_TRUE_LOTTERY"


@dataclass(frozen=True)
class StrategyProfile:
    """Parameter overrides for a strategy mode.

    Only non-None values override the base config.
    None means "use base config default".
    """
    mode: StrategyMode
    label: str

    # Premium band
    premium_band_min: Optional[float] = None
    premium_band_max: Optional[float] = None

    # OTM distance
    otm_distance_min: Optional[int] = None
    otm_distance_max: Optional[int] = None

    # Chain refresh
    chain_refresh_seconds: Optional[int] = None

    # Candidate quote refresh (in zone active)
    candidate_refresh_seconds: Optional[int] = None

    # Confirmation
    confirmation_mode: Optional[ConfirmationMode] = None
    confirmation_quorum: Optional[int] = None
    hold_duration_seconds: Optional[float] = None
    premium_expansion_min_pct: Optional[float] = None

    # Spread / liquidity thresholds
    max_spread_pct: Optional[float] = None
    min_volume: Optional[int] = None

    # Cooldown
    cooldown_seconds: Optional[int] = None

    # Scoring weights (override only if set)
    w1_distance: Optional[float] = None
    w2_momentum: Optional[float] = None
    w3_liquidity: Optional[float] = None
    w4_band_fit: Optional[float] = None
    w5_bias: Optional[float] = None


# ── Profile Definitions ────────────────────────────────────────────────────

PRE_EXPIRY_MOMENTUM = StrategyProfile(
    mode=StrategyMode.PRE_EXPIRY_MOMENTUM,
    label="Pre-Expiry Momentum (DTE >= 2)",

    # Wider band — options have more time value
    premium_band_min=3.0,
    premium_band_max=15.0,

    # Further OTM — more room for directional moves
    otm_distance_min=300,
    otm_distance_max=600,

    # Slower refresh acceptable — less urgency
    chain_refresh_seconds=30,
    candidate_refresh_seconds=5,

    # Less strict confirmation — directional continuation
    confirmation_mode=ConfirmationMode.HYBRID,
    confirmation_quorum=2,
    hold_duration_seconds=20.0,
    premium_expansion_min_pct=3.0,

    # More tolerant spreads — less liquid far OTM
    max_spread_pct=5.0,
    min_volume=500,

    # Longer cooldown — fewer opportunities per day
    cooldown_seconds=600,
)

DTE1_HYBRID = StrategyProfile(
    mode=StrategyMode.DTE1_HYBRID,
    label="DTE-1 Hybrid (DTE == 1)",

    # Intermediate band
    premium_band_min=2.5,
    premium_band_max=10.0,

    # Intermediate distance
    otm_distance_min=250,
    otm_distance_max=500,

    # Moderate refresh
    chain_refresh_seconds=20,
    candidate_refresh_seconds=3,

    # Moderate confirmation
    confirmation_mode=ConfirmationMode.QUORUM,
    confirmation_quorum=2,
    hold_duration_seconds=15.0,
    premium_expansion_min_pct=5.0,

    # Moderate thresholds
    max_spread_pct=4.0,
    min_volume=800,

    cooldown_seconds=300,
)

EXPIRY_DAY_TRUE_LOTTERY = StrategyProfile(
    mode=StrategyMode.EXPIRY_DAY_TRUE_LOTTERY,
    label="Expiry Day True Lottery (DTE == 0)",

    # Tight band — low premium, high leverage
    premium_band_min=2.0,
    premium_band_max=8.5,

    # Tighter distance — closer to spot
    otm_distance_min=200,
    otm_distance_max=400,

    # Fast refresh — expiry-day moves are fast
    chain_refresh_seconds=15,
    candidate_refresh_seconds=2,

    # Strict confirmation — false breaks are common on expiry
    confirmation_mode=ConfirmationMode.QUORUM,
    confirmation_quorum=3,
    hold_duration_seconds=10.0,
    premium_expansion_min_pct=8.0,

    # Strict spread/liquidity — must be tradable
    max_spread_pct=3.0,
    min_volume=2000,

    # Short cooldown — more action on expiry day
    cooldown_seconds=180,
)

# ── Profile Registry ───────────────────────────────────────────────────────

_PROFILES: dict[StrategyMode, StrategyProfile] = {
    StrategyMode.PRE_EXPIRY_MOMENTUM: PRE_EXPIRY_MOMENTUM,
    StrategyMode.DTE1_HYBRID: DTE1_HYBRID,
    StrategyMode.EXPIRY_DAY_TRUE_LOTTERY: EXPIRY_DAY_TRUE_LOTTERY,
}


def get_profile(mode: StrategyMode) -> StrategyProfile:
    """Get a strategy profile by mode."""
    return _PROFILES[mode]


def get_profile_for_dte(dte: int) -> StrategyProfile:
    """Auto-select profile based on days-to-expiry.

    Args:
        dte: Days to expiry (0 = expiry day).

    Returns:
        Appropriate strategy profile.
    """
    if dte <= 0:
        return EXPIRY_DAY_TRUE_LOTTERY
    elif dte == 1:
        return DTE1_HYBRID
    else:
        return PRE_EXPIRY_MOMENTUM


def get_all_profiles() -> dict[str, dict]:
    """Get all profiles as dicts for API/display."""
    result = {}
    for mode, profile in _PROFILES.items():
        result[mode.value] = {
            "label": profile.label,
            "premium_band": f"{profile.premium_band_min}-{profile.premium_band_max}",
            "otm_distance": f"{profile.otm_distance_min}-{profile.otm_distance_max}",
            "chain_refresh": f"{profile.chain_refresh_seconds}s",
            "confirmation": f"{profile.confirmation_mode.value}({profile.confirmation_quorum})",
            "max_spread": f"{profile.max_spread_pct}%",
            "cooldown": f"{profile.cooldown_seconds}s",
        }
    return result

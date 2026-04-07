"""Adaptive refresh scheduler — adjust intervals based on pipeline state.

Refresh policies:
  IDLE:            full chain every N seconds, no candidate quotes
  ZONE_ACTIVE:     full chain every N seconds, candidate quotes every 5s
  CANDIDATE_FOUND: full chain every N seconds, candidate quotes every 2s
  IN_TRADE:        full chain every N seconds, trade strike quote every 1s
  COOLDOWN:        same as IDLE

Also triggers forced full-chain refresh when:
  - no candidate remains valid
  - opposite side activates
  - spot moves > threshold from analysis snapshot
  - candidate data stale > threshold

Intervals are further adjusted by the active strategy profile.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from ..models import MachineState
from .profiles import StrategyProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefreshConfig:
    """Base refresh intervals (overridden by profile)."""
    chain_idle_seconds: int = 30
    chain_active_seconds: int = 30
    candidate_zone_seconds: int = 5
    candidate_found_seconds: int = 2
    trade_quote_seconds: int = 1
    spot_drift_threshold: float = 100.0   # force chain refresh if spot drifts > N points
    candidate_stale_seconds: float = 60.0  # force refresh if candidate data > N seconds old


@dataclass
class RefreshDecision:
    """What to refresh this cycle."""
    refresh_chain: bool = False
    refresh_candidates: bool = False
    chain_reason: str = ""
    candidate_reason: str = ""


class RefreshScheduler:
    """Determines what to refresh each cycle based on pipeline state.

    Does not perform the refresh — just decides what's due.
    The pipeline calls should_refresh() and acts on the decision.
    """

    def __init__(
        self,
        base_config: RefreshConfig,
        profile: Optional[StrategyProfile] = None,
    ) -> None:
        self._base = base_config
        self._profile = profile

        # Timestamps
        self._last_chain_refresh: float = 0
        self._last_candidate_refresh: float = 0

        # Analysis state tracking
        self._analysis_spot: Optional[float] = None
        self._last_side: Optional[str] = None

    def update_profile(self, profile: StrategyProfile) -> None:
        """Update the active profile (changes refresh intervals)."""
        self._profile = profile
        logger.info("Refresh scheduler profile updated: %s", profile.mode.value)

    def record_chain_refresh(self, spot: float) -> None:
        """Record that a chain refresh just completed."""
        self._last_chain_refresh = time.monotonic()
        self._analysis_spot = spot

    def record_candidate_refresh(self) -> None:
        """Record that a candidate quote refresh just completed."""
        self._last_candidate_refresh = time.monotonic()

    def record_side_change(self, new_side: Optional[str]) -> None:
        """Record a side change for forced refresh detection."""
        if new_side != self._last_side and self._last_side is not None:
            # Side changed — force chain refresh on next check
            self._last_chain_refresh = 0
            logger.info("Side changed %s → %s — forcing chain refresh", self._last_side, new_side)
        self._last_side = new_side

    def should_refresh(
        self,
        state: MachineState,
        current_spot: Optional[float] = None,
        has_candidates: bool = False,
    ) -> RefreshDecision:
        """Determine what to refresh this cycle.

        Args:
            state: Current state machine state.
            current_spot: Live spot price (for drift detection).
            has_candidates: Whether any valid candidates exist.

        Returns:
            RefreshDecision indicating what to refresh and why.
        """
        now = time.monotonic()
        decision = RefreshDecision()

        # ── Chain refresh intervals ────────────────────────────────
        chain_interval = self._get_chain_interval(state)
        chain_age = now - self._last_chain_refresh

        if chain_age >= chain_interval:
            decision.refresh_chain = True
            decision.chain_reason = f"interval ({chain_age:.0f}s >= {chain_interval}s)"

        # Force chain refresh: spot drift
        if (current_spot is not None
                and self._analysis_spot is not None
                and abs(current_spot - self._analysis_spot) > self._get_drift_threshold()):
            decision.refresh_chain = True
            drift = abs(current_spot - self._analysis_spot)
            decision.chain_reason = f"spot drift ({drift:.1f} > {self._get_drift_threshold():.0f})"

        # Force chain refresh: no candidates in active zone
        if (state in (MachineState.ZONE_ACTIVE_CE, MachineState.ZONE_ACTIVE_PE)
                and not has_candidates
                and chain_age > 10):
            decision.refresh_chain = True
            decision.chain_reason = "no candidates in active zone"

        # ── Candidate quote refresh ────────────────────────────────
        candidate_interval = self._get_candidate_interval(state)
        candidate_age = now - self._last_candidate_refresh

        if candidate_interval is not None and candidate_age >= candidate_interval:
            decision.refresh_candidates = True
            decision.candidate_reason = f"{state.value} interval ({candidate_age:.0f}s >= {candidate_interval}s)"

        # Force candidate refresh: stale data
        stale_threshold = self._base.candidate_stale_seconds
        if candidate_age > stale_threshold and state != MachineState.IDLE:
            decision.refresh_candidates = True
            decision.candidate_reason = f"stale ({candidate_age:.0f}s > {stale_threshold:.0f}s)"

        return decision

    # ── Interval Getters ───────────────────────────────────────────

    def _get_chain_interval(self, state: MachineState) -> int:
        """Get chain refresh interval based on state + profile."""
        # Profile override
        if self._profile and self._profile.chain_refresh_seconds is not None:
            return self._profile.chain_refresh_seconds

        # Base config by state
        if state in (MachineState.IDLE, MachineState.COOLDOWN):
            return self._base.chain_idle_seconds
        return self._base.chain_active_seconds

    def _get_candidate_interval(self, state: MachineState) -> Optional[int]:
        """Get candidate quote refresh interval based on state + profile."""
        if state in (MachineState.IDLE, MachineState.COOLDOWN):
            return None  # no candidate refresh in IDLE

        # Profile override for candidate refresh
        profile_interval = None
        if self._profile and self._profile.candidate_refresh_seconds is not None:
            profile_interval = self._profile.candidate_refresh_seconds

        if state == MachineState.IN_TRADE:
            return profile_interval or self._base.trade_quote_seconds
        elif state == MachineState.CANDIDATE_FOUND:
            return profile_interval or self._base.candidate_found_seconds
        elif state in (MachineState.ZONE_ACTIVE_CE, MachineState.ZONE_ACTIVE_PE):
            return profile_interval or self._base.candidate_zone_seconds
        elif state == MachineState.EXIT_PENDING:
            return self._base.trade_quote_seconds

        return None

    def _get_drift_threshold(self) -> float:
        """Get spot drift threshold for forced chain refresh."""
        return self._base.spot_drift_threshold

    def to_dict(self) -> dict:
        """Serialize for debugging."""
        now = time.monotonic()
        return {
            "chain_age_seconds": round(now - self._last_chain_refresh, 1),
            "candidate_age_seconds": round(now - self._last_candidate_refresh, 1),
            "analysis_spot": self._analysis_spot,
            "last_side": self._last_side,
            "profile": self._profile.mode.value if self._profile else None,
        }

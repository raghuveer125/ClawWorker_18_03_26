"""Breakout confirmation — configurable quorum-based entry gate.

Before a CANDIDATE_FOUND signal becomes a trade, the breakout must be
confirmed by multiple independent checks. This prevents false breaks.

Confirmation modes:
- CANDLE:  1-min candle close beyond trigger (single check)
- PREMIUM: premium expansion on candidate (single check)
- HYBRID:  candle + premium (both required)
- QUORUM:  configurable N-of-M checks must pass

Checks available:
1. candle_close     — last completed 1-min candle closed beyond trigger
2. premium_expand   — candidate LTP increased since candidate was found
3. volume_spike     — current candidate volume > recent average
4. spread_stable    — candidate spread not widening
5. hold_duration    — spot held beyond trigger for N seconds

All check results are stored in the confirmation audit for debugging.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from ..calculations.candle_builder import CandleBuilder
from ..calculations.scoring import ScoredCandidate

logger = logging.getLogger(__name__)


class ConfirmationMode(Enum):
    CANDLE = "CANDLE"
    PREMIUM = "PREMIUM"
    HYBRID = "HYBRID"
    QUORUM = "QUORUM"
    DISABLED = "DISABLED"


@dataclass(frozen=True)
class ConfirmationConfig:
    """Configuration for breakout confirmation."""
    mode: ConfirmationMode = ConfirmationMode.QUORUM
    quorum: int = 2                         # min checks to pass in QUORUM mode
    hold_duration_seconds: float = 15.0     # min seconds spot must hold beyond trigger
    premium_expansion_min_pct: float = 5.0  # min % premium increase since candidate found
    volume_spike_multiplier: float = 1.5    # current vol > avg * multiplier
    spread_widen_max_pct: float = 20.0      # max % spread can widen during confirmation


@dataclass
class ConfirmationCheck:
    """Result of a single confirmation check."""
    name: str
    passed: bool
    observed: str
    threshold: str


@dataclass
class ConfirmationResult:
    """Aggregate result of all confirmation checks."""
    confirmed: bool
    mode: str
    checks_passed: int
    checks_total: int
    checks: list[ConfirmationCheck] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "confirmed": self.confirmed,
            "mode": self.mode,
            "checks_passed": self.checks_passed,
            "checks_total": self.checks_total,
            "checks": [
                {"name": c.name, "passed": c.passed, "observed": c.observed, "threshold": c.threshold}
                for c in self.checks
            ],
        }


class BreakoutConfirmation:
    """Quorum-based entry gate that validates breakouts before trading.

    Tracks state from when a candidate is first found to when confirmation
    is evaluated. Records the candidate's initial LTP, volume, spread,
    and the time the zone became active for hold-duration checks.
    """

    def __init__(self, config: ConfirmationConfig) -> None:
        self._config = config

        # State tracking — set when candidate is first discovered
        self._candidate_found_time: Optional[float] = None
        self._candidate_initial_ltp: Optional[float] = None
        self._candidate_initial_spread_pct: Optional[float] = None
        self._zone_active_time: Optional[float] = None

    def on_zone_active(self) -> None:
        """Call when state machine enters ZONE_ACTIVE_CE or ZONE_ACTIVE_PE."""
        if self._zone_active_time is None:
            self._zone_active_time = time.monotonic()

    def on_candidate_found(self, candidate: ScoredCandidate) -> None:
        """Call when a new candidate is discovered for the first time."""
        self._candidate_found_time = time.monotonic()
        self._candidate_initial_ltp = candidate.ltp
        self._candidate_initial_spread_pct = candidate.spread_pct

    def reset(self) -> None:
        """Reset confirmation state (call on state machine reset / zone change)."""
        self._candidate_found_time = None
        self._candidate_initial_ltp = None
        self._candidate_initial_spread_pct = None
        self._zone_active_time = None

    def evaluate(
        self,
        candidate: ScoredCandidate,
        trigger_price: float,
        direction: str,
        candle_builder: CandleBuilder,
        current_volume: Optional[int] = None,
        recent_avg_volume: Optional[float] = None,
        current_spread_pct: Optional[float] = None,
    ) -> ConfirmationResult:
        """Evaluate all confirmation checks for the current candidate.

        Args:
            candidate: Current best candidate strike.
            trigger_price: The breakout trigger level.
            direction: "above" for CE breakout, "below" for PE breakout.
            candle_builder: CandleBuilder with recent 1-min candles.
            current_volume: Current candidate volume (from trigger snapshot).
            recent_avg_volume: Average recent volume for comparison.
            current_spread_pct: Current candidate spread %.

        Returns:
            ConfirmationResult with all check outcomes.
        """
        mode = self._config.mode

        if mode == ConfirmationMode.DISABLED:
            return ConfirmationResult(
                confirmed=True, mode="DISABLED",
                checks_passed=0, checks_total=0,
            )

        checks: list[ConfirmationCheck] = []

        # ── Check 1: Candle Close ──────────────────────────────────
        candle_ok = self._check_candle_close(candle_builder, trigger_price, direction)
        checks.append(candle_ok)

        # ── Check 2: Premium Expansion ─────────────────────────────
        premium_ok = self._check_premium_expansion(candidate)
        checks.append(premium_ok)

        # ── Check 3: Volume Spike ──────────────────────────────────
        volume_ok = self._check_volume_spike(current_volume, recent_avg_volume)
        checks.append(volume_ok)

        # ── Check 4: Spread Stability ──────────────────────────────
        spread_ok = self._check_spread_stability(current_spread_pct)
        checks.append(spread_ok)

        # ── Check 5: Hold Duration ─────────────────────────────────
        hold_ok = self._check_hold_duration()
        checks.append(hold_ok)

        # ── Decision ───────────────────────────────────────────────
        passed_count = sum(1 for c in checks if c.passed)
        total = len(checks)

        if mode == ConfirmationMode.CANDLE:
            confirmed = candle_ok.passed
        elif mode == ConfirmationMode.PREMIUM:
            confirmed = premium_ok.passed
        elif mode == ConfirmationMode.HYBRID:
            confirmed = candle_ok.passed and premium_ok.passed
        elif mode == ConfirmationMode.QUORUM:
            confirmed = passed_count >= self._config.quorum
        else:
            confirmed = True

        result = ConfirmationResult(
            confirmed=confirmed,
            mode=mode.value,
            checks_passed=passed_count,
            checks_total=total,
            checks=checks,
        )

        if confirmed:
            logger.info(
                "Breakout CONFIRMED (%s): %d/%d checks passed | K=%s %s",
                mode.value, passed_count, total,
                candidate.strike, candidate.option_type.value,
            )
        else:
            logger.debug(
                "Breakout NOT confirmed (%s): %d/%d checks passed",
                mode.value, passed_count, total,
            )

        return result

    # ── Individual Checks ──────────────────────────────────────────

    def _check_candle_close(
        self,
        candle_builder: CandleBuilder,
        trigger_price: float,
        direction: str,
    ) -> ConfirmationCheck:
        """Check if last completed 1-min candle closed beyond trigger."""
        if candle_builder.is_degraded:
            return ConfirmationCheck(
                name="candle_close", passed=False,
                observed="candle data DEGRADED",
                threshold=f"close {direction} {trigger_price:.1f}",
            )

        last = candle_builder.last_completed
        if last is None:
            return ConfirmationCheck(
                name="candle_close", passed=False,
                observed="no completed candle",
                threshold=f"close {direction} {trigger_price:.1f}",
            )

        confirmed = candle_builder.is_candle_confirmed_beyond(trigger_price, direction)
        return ConfirmationCheck(
            name="candle_close", passed=confirmed,
            observed=f"close={last.close:.2f} ({direction} {trigger_price:.1f})",
            threshold=f"close {direction} {trigger_price:.1f}",
        )

    def _check_premium_expansion(self, candidate: ScoredCandidate) -> ConfirmationCheck:
        """Check if candidate premium has expanded since discovery."""
        if self._candidate_initial_ltp is None or self._candidate_initial_ltp <= 0:
            return ConfirmationCheck(
                name="premium_expand", passed=False,
                observed="no initial LTP recorded",
                threshold=f">= {self._config.premium_expansion_min_pct:.1f}% expansion",
            )

        current_ltp = candidate.ltp
        initial_ltp = self._candidate_initial_ltp
        expansion_pct = ((current_ltp - initial_ltp) / initial_ltp) * 100

        passed = expansion_pct >= self._config.premium_expansion_min_pct
        return ConfirmationCheck(
            name="premium_expand", passed=passed,
            observed=f"initial={initial_ltp:.2f} current={current_ltp:.2f} change={expansion_pct:+.1f}%",
            threshold=f">= {self._config.premium_expansion_min_pct:.1f}%",
        )

    def _check_volume_spike(
        self,
        current_volume: Optional[int],
        recent_avg_volume: Optional[float],
    ) -> ConfirmationCheck:
        """Check if current volume exceeds recent average."""
        if current_volume is None or recent_avg_volume is None or recent_avg_volume <= 0:
            return ConfirmationCheck(
                name="volume_spike", passed=False,
                observed="volume data unavailable",
                threshold=f"> {self._config.volume_spike_multiplier:.1f}x avg",
            )

        ratio = current_volume / recent_avg_volume
        passed = ratio >= self._config.volume_spike_multiplier
        return ConfirmationCheck(
            name="volume_spike", passed=passed,
            observed=f"current={current_volume:,} avg={recent_avg_volume:,.0f} ratio={ratio:.2f}x",
            threshold=f"> {self._config.volume_spike_multiplier:.1f}x avg",
        )

    def _check_spread_stability(self, current_spread_pct: Optional[float]) -> ConfirmationCheck:
        """Check if spread hasn't widened excessively since candidate found."""
        if current_spread_pct is None or self._candidate_initial_spread_pct is None:
            # If no spread data, pass by default (don't block on missing data)
            return ConfirmationCheck(
                name="spread_stable", passed=True,
                observed="spread data unavailable — defaulting to pass",
                threshold=f"widen < {self._config.spread_widen_max_pct:.0f}%",
            )

        if self._candidate_initial_spread_pct <= 0:
            return ConfirmationCheck(
                name="spread_stable", passed=True,
                observed="initial spread zero — defaulting to pass",
                threshold=f"widen < {self._config.spread_widen_max_pct:.0f}%",
            )

        widen_pct = ((current_spread_pct - self._candidate_initial_spread_pct)
                     / self._candidate_initial_spread_pct) * 100
        passed = widen_pct < self._config.spread_widen_max_pct
        return ConfirmationCheck(
            name="spread_stable", passed=passed,
            observed=f"initial={self._candidate_initial_spread_pct:.2f}% current={current_spread_pct:.2f}% widen={widen_pct:+.1f}%",
            threshold=f"widen < {self._config.spread_widen_max_pct:.0f}%",
        )

    def _check_hold_duration(self) -> ConfirmationCheck:
        """Check if spot has held beyond trigger for minimum duration."""
        if self._zone_active_time is None:
            return ConfirmationCheck(
                name="hold_duration", passed=False,
                observed="zone active time not recorded",
                threshold=f">= {self._config.hold_duration_seconds:.0f}s",
            )

        elapsed = time.monotonic() - self._zone_active_time
        passed = elapsed >= self._config.hold_duration_seconds
        return ConfirmationCheck(
            name="hold_duration", passed=passed,
            observed=f"{elapsed:.1f}s held",
            threshold=f">= {self._config.hold_duration_seconds:.0f}s",
        )

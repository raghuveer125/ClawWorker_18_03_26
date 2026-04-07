"""Trigger hysteresis — prevent state flicker near trigger boundaries.

Without hysteresis, the state machine oscillates when spot hovers
near a trigger level. This module adds four layers of protection:

1. buffer_points:         spot must EXCEED trigger by N points to activate
2. min_zone_hold_seconds: zone must be held for N seconds before candidate search
3. rearm_distance_points: after returning to IDLE, spot must move N points
                          AWAY from trigger before zone can re-activate
4. invalidation_buffer:   spot must reverse N points PAST trigger to invalidate

Uses snapshot timestamps (not time.monotonic) for replay determinism.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from ..config import HysteresisConfig

logger = logging.getLogger(__name__)


class TriggerHysteresis:
    """Applies hysteresis rules to trigger zone transitions.

    Wraps around the state machine's trigger checks.
    Call methods in the pipeline cycle before state machine evaluation.
    """

    def __init__(self, config: HysteresisConfig) -> None:
        self._config = config

        # Zone activation tracking
        self._zone_activated_at: Optional[datetime] = None
        self._zone_side: Optional[str] = None    # "CE" or "PE"

        # Rearm tracking — after returning to IDLE, prevent immediate re-activation
        self._last_idle_return_spot: Optional[float] = None
        self._last_idle_return_side: Optional[str] = None

    def reset(self) -> None:
        """Reset all hysteresis state."""
        self._zone_activated_at = None
        self._zone_side = None
        self._last_idle_return_spot = None
        self._last_idle_return_side = None

    # ── Zone Activation Check ──────────────────────────────────────

    def can_activate_zone(
        self,
        spot: float,
        upper_trigger: float,
        lower_trigger: float,
        timestamp: Optional[datetime] = None,
    ) -> tuple[bool, Optional[str], str]:
        """Check if zone activation is allowed with hysteresis.

        Args:
            spot: Current spot price.
            upper_trigger: Upper trigger level.
            lower_trigger: Lower trigger level.
            timestamp: Current snapshot timestamp (for replay determinism).

        Returns:
            (allowed, side, reason)
            - allowed: True if zone can activate
            - side: "CE" or "PE" or None
            - reason: human-readable explanation
        """
        buf = self._config.buffer_points

        # CE activation: spot must exceed upper trigger + buffer
        if spot > upper_trigger + buf:
            # Check rearm distance
            if not self._check_rearm("CE", spot, upper_trigger):
                return False, None, (
                    f"rearm blocked: spot {spot:.1f} not far enough from "
                    f"last idle return (need {self._config.rearm_distance_points} pts)"
                )

            # Activate or continue zone
            if self._zone_side != "CE":
                now = timestamp or datetime.now(timezone.utc)
                self._zone_activated_at = now
                self._zone_side = "CE"
                logger.debug(
                    "Hysteresis: CE zone activated at %s (spot %.1f > trigger %.1f + buffer %.1f)",
                    now.isoformat(), spot, upper_trigger, buf,
                )

            return True, "CE", f"spot {spot:.1f} > {upper_trigger:.0f} + {buf:.0f} buffer"

        # PE activation: spot must be below lower trigger - buffer
        if spot < lower_trigger - buf:
            if not self._check_rearm("PE", spot, lower_trigger):
                return False, None, (
                    f"rearm blocked: spot {spot:.1f} not far enough from "
                    f"last idle return (need {self._config.rearm_distance_points} pts)"
                )

            if self._zone_side != "PE":
                now = timestamp or datetime.now(timezone.utc)
                self._zone_activated_at = now
                self._zone_side = "PE"
                logger.debug(
                    "Hysteresis: PE zone activated (spot %.1f < trigger %.1f - buffer %.1f)",
                    spot, lower_trigger, buf,
                )

            return True, "PE", f"spot {spot:.1f} < {lower_trigger:.0f} - {buf:.0f} buffer"

        # In no-trade zone (including buffer band)
        return False, None, (
            f"spot {spot:.1f} within buffer zone "
            f"[{lower_trigger - buf:.0f}, {upper_trigger + buf:.0f}]"
        )

    # ── Zone Hold Check ────────────────────────────────────────────

    def is_zone_held_long_enough(
        self,
        timestamp: Optional[datetime] = None,
    ) -> tuple[bool, float]:
        """Check if the zone has been active for minimum hold duration.

        Uses snapshot timestamps for replay determinism.

        Args:
            timestamp: Current snapshot timestamp.

        Returns:
            (held_enough, seconds_held)
        """
        if self._zone_activated_at is None:
            return False, 0.0

        now = timestamp or datetime.now(timezone.utc)
        held = (now - self._zone_activated_at).total_seconds()
        threshold = self._config.min_zone_hold_seconds

        return held >= threshold, held

    # ── Invalidation Check ─────────────────────────────────────────

    def should_invalidate(
        self,
        spot: float,
        upper_trigger: float,
        lower_trigger: float,
    ) -> tuple[bool, str]:
        """Check if current zone should be invalidated.

        Requires spot to reverse PAST trigger by invalidation_buffer_points.
        Without this, a slight touch of trigger would invalidate immediately.

        Args:
            spot: Current spot price.
            upper_trigger: Upper trigger level.
            lower_trigger: Lower trigger level.

        Returns:
            (should_invalidate, reason)
        """
        inv_buf = self._config.invalidation_buffer_points

        if self._zone_side == "CE":
            # CE invalidates when spot drops below upper_trigger - buffer
            threshold = upper_trigger - inv_buf
            if spot < threshold:
                return True, f"CE invalidated: spot {spot:.1f} < {upper_trigger:.0f} - {inv_buf:.0f}"
            return False, ""

        if self._zone_side == "PE":
            # PE invalidates when spot rises above lower_trigger + buffer
            threshold = lower_trigger + inv_buf
            if spot > threshold:
                return True, f"PE invalidated: spot {spot:.1f} > {lower_trigger:.0f} + {inv_buf:.0f}"
            return False, ""

        return False, ""

    # ── Idle Return Recording ──────────────────────────────────────

    def record_idle_return(self, spot: float) -> None:
        """Record when the state machine returns to IDLE.

        Used for rearm distance check — prevents immediate re-activation
        at the same level after a failed breakout.
        """
        self._last_idle_return_spot = spot
        self._last_idle_return_side = self._zone_side
        self._zone_activated_at = None
        self._zone_side = None

    # ── Helpers ────────────────────────────────────────────────────

    def _check_rearm(self, side: str, spot: float, trigger: float) -> bool:
        """Check if spot has moved far enough from last idle return for rearm."""
        if self._last_idle_return_spot is None:
            return True  # no previous idle return — always allow

        if self._last_idle_return_side != side:
            return True  # opposite side — always allow

        rearm = self._config.rearm_distance_points
        distance = abs(spot - self._last_idle_return_spot)

        if distance >= rearm:
            # Cleared rearm distance — reset tracking
            self._last_idle_return_spot = None
            self._last_idle_return_side = None
            return True

        return False

    def to_dict(self) -> dict:
        """Serialize for debugging."""
        return {
            "zone_side": self._zone_side,
            "zone_activated_at": self._zone_activated_at.isoformat() if self._zone_activated_at else None,
            "last_idle_return_spot": self._last_idle_return_spot,
            "last_idle_return_side": self._last_idle_return_side,
            "config": {
                "buffer_points": self._config.buffer_points,
                "min_zone_hold_seconds": self._config.min_zone_hold_seconds,
                "rearm_distance_points": self._config.rearm_distance_points,
                "invalidation_buffer_points": self._config.invalidation_buffer_points,
            },
        }

"""State machine — 7 states with deterministic transitions.

States:
    IDLE → ZONE_ACTIVE_CE / ZONE_ACTIVE_PE → CANDIDATE_FOUND → IN_TRADE
    → EXIT_PENDING → COOLDOWN → IDLE

Transitions are deterministic: same inputs always produce the same state change.
All transition reasons are logged for audit.
"""

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Optional

from ..config import LotteryConfig
from ..models import (
    MachineState,
    QualityStatus,
    RejectionReason,
    Side,
)
from ..calculations.scoring import ScoredCandidate

logger = logging.getLogger(__name__)


@dataclass
class TriggerZone:
    """Resolved trigger zone from config or derived from chain."""
    upper_trigger: float
    lower_trigger: float
    source: str  # "STATIC" or "DYNAMIC"


@dataclass
class StateContext:
    """Mutable context carried through state transitions."""
    state: MachineState = MachineState.IDLE
    active_side: Optional[Side] = None
    candidate: Optional[ScoredCandidate] = None
    entry_time: Optional[datetime] = None
    cooldown_start: Optional[float] = None
    reentry_count: int = 0
    last_strike: Optional[float] = None
    transition_reason: str = ""
    rejection: Optional[RejectionReason] = None
    consecutive_losses: int = 0
    daily_trade_count: int = 0
    daily_pnl: float = 0.0


class StateMachine:
    """Deterministic state machine for the lottery strategy.

    Evaluates transitions in fixed priority order (no-trade hierarchy):
    1. data quality fail
    2. stale data
    3. time filter (first N minutes, lunch, near close)
    4. risk rejection (max daily trades, consecutive losses, daily loss)
    5. zone inactive (spot in no-trade zone)
    6. no band candidate
    7. spread/liquidity too low
    """

    def __init__(self, config: LotteryConfig) -> None:
        self._config = config
        self._ctx = StateContext()

    @property
    def state(self) -> MachineState:
        return self._ctx.state

    @property
    def context(self) -> StateContext:
        return self._ctx

    def reset(self) -> None:
        """Reset to IDLE — used on new trading day."""
        self._ctx = StateContext()
        logger.info("State machine reset to IDLE")

    def reset_daily_counters(self) -> None:
        """Reset daily counters (called at start of each trading day)."""
        self._ctx.daily_trade_count = 0
        self._ctx.daily_pnl = 0.0
        self._ctx.consecutive_losses = 0

    def evaluate(
        self,
        spot: float,
        quality_status: QualityStatus,
        triggers: TriggerZone,
        best_ce: Optional[ScoredCandidate],
        best_pe: Optional[ScoredCandidate],
        preferred_side: Optional[Side],
        current_time: Optional[datetime] = None,
    ) -> MachineState:
        """Evaluate state transition based on current market conditions.

        This is the main entry point called every cycle (1s).
        Returns the new state after evaluation.

        Args:
            spot: Current spot price.
            quality_status: Data quality result (PASS/WARN/FAIL).
            triggers: Resolved trigger zone.
            best_ce: Best CE candidate (or None).
            best_pe: Best PE candidate (or None).
            preferred_side: Side bias from calculations.
            current_time: Current timestamp (for time filters).
        """
        now = current_time or datetime.now(timezone.utc)
        prev_state = self._ctx.state
        self._ctx.rejection = None
        self._ctx.transition_reason = ""

        # ── No-trade hierarchy (fixed priority order) ──────────────

        # 1. Data quality fail
        if quality_status == QualityStatus.FAIL:
            self._transition_to_idle("data quality FAIL")
            self._ctx.rejection = RejectionReason.DATA_QUALITY_FAIL
            return self._ctx.state

        # 2. Time filters
        time_rejection = self._check_time_filters(now)
        if time_rejection:
            if self._ctx.state == MachineState.IN_TRADE:
                # Don't exit trades due to time filters (EOD exit handled separately)
                pass
            else:
                self._transition_to_idle(f"time filter: {time_rejection}")
                self._ctx.rejection = RejectionReason.TIME_FILTER
                return self._ctx.state

        # 3. Risk rejection
        risk_rejection = self._check_risk_limits()
        if risk_rejection:
            if self._ctx.state != MachineState.IN_TRADE:
                self._transition_to_idle(f"risk: {risk_rejection.value}")
                self._ctx.rejection = risk_rejection
                return self._ctx.state

        # ── State-specific transitions ─────────────────────────────

        if self._ctx.state == MachineState.IDLE:
            return self._eval_idle(spot, triggers, quality_status)

        elif self._ctx.state == MachineState.ZONE_ACTIVE_CE:
            return self._eval_zone_active(spot, triggers, best_ce, OptionType_CE=True)

        elif self._ctx.state == MachineState.ZONE_ACTIVE_PE:
            return self._eval_zone_active(spot, triggers, best_pe, OptionType_CE=False)

        elif self._ctx.state == MachineState.CANDIDATE_FOUND:
            # Candidate is ready — signal engine handles entry decision
            return self._ctx.state

        elif self._ctx.state == MachineState.IN_TRADE:
            # Exit logic handled by signal engine
            return self._ctx.state

        elif self._ctx.state == MachineState.EXIT_PENDING:
            # After exit confirmation → cooldown
            return self._ctx.state

        elif self._ctx.state == MachineState.COOLDOWN:
            return self._eval_cooldown()

        return self._ctx.state

    # ── State Transition Methods ───────────────────────────────────

    def _eval_idle(
        self,
        spot: float,
        triggers: TriggerZone,
        quality_status: QualityStatus,
    ) -> MachineState:
        """IDLE → ZONE_ACTIVE_CE or ZONE_ACTIVE_PE based on spot vs triggers."""
        cfg = self._config

        # No-trade zone check
        if cfg.state_machine.no_trade_zone_enabled:
            if triggers.lower_trigger <= spot <= triggers.upper_trigger:
                self._ctx.transition_reason = (
                    f"spot ₹{spot:.1f} in no-trade zone "
                    f"[{triggers.lower_trigger:.0f}, {triggers.upper_trigger:.0f}]"
                )
                self._ctx.rejection = RejectionReason.ZONE_INACTIVE
                return self._ctx.state  # Stay IDLE

        # CE activation: spot > upper trigger
        if spot > triggers.upper_trigger:
            self._ctx.state = MachineState.ZONE_ACTIVE_CE
            self._ctx.active_side = Side.CE
            self._ctx.transition_reason = (
                f"spot ₹{spot:.1f} > upper trigger {triggers.upper_trigger:.0f} → CE zone"
            )
            logger.info("IDLE → ZONE_ACTIVE_CE: %s", self._ctx.transition_reason)
            return self._ctx.state

        # PE activation: spot < lower trigger
        if spot < triggers.lower_trigger:
            self._ctx.state = MachineState.ZONE_ACTIVE_PE
            self._ctx.active_side = Side.PE
            self._ctx.transition_reason = (
                f"spot ₹{spot:.1f} < lower trigger {triggers.lower_trigger:.0f} → PE zone"
            )
            logger.info("IDLE → ZONE_ACTIVE_PE: %s", self._ctx.transition_reason)
            return self._ctx.state

        self._ctx.rejection = RejectionReason.ZONE_INACTIVE
        self._ctx.transition_reason = "spot in no-trade zone"
        return self._ctx.state  # Stay IDLE

    def _eval_zone_active(
        self,
        spot: float,
        triggers: TriggerZone,
        best_candidate: Optional[ScoredCandidate],
        OptionType_CE: bool,
    ) -> MachineState:
        """ZONE_ACTIVE → CANDIDATE_FOUND if valid candidate exists."""
        # Check if spot has reversed back to no-trade zone
        if OptionType_CE and spot <= triggers.upper_trigger:
            self._transition_to_idle("spot reversed below upper trigger")
            return self._ctx.state

        if not OptionType_CE and spot >= triggers.lower_trigger:
            self._transition_to_idle("spot reversed above lower trigger")
            return self._ctx.state

        # Check candidate
        if best_candidate is None:
            self._ctx.rejection = RejectionReason.NO_BAND_CANDIDATE
            self._ctx.transition_reason = "no valid candidate in premium band"
            return self._ctx.state  # Stay in ZONE_ACTIVE

        # Check spread quality
        if (best_candidate.spread_pct is not None
                and best_candidate.spread_pct > self._config.data_quality.max_spread_pct):
            self._ctx.rejection = RejectionReason.SPREAD_TOO_WIDE
            self._ctx.transition_reason = f"spread {best_candidate.spread_pct:.2f}% > {self._config.data_quality.max_spread_pct}%"
            return self._ctx.state

        # Check liquidity
        if (best_candidate.volume is not None
                and best_candidate.volume < self._config.data_quality.min_volume):
            self._ctx.rejection = RejectionReason.LIQUIDITY_TOO_LOW
            self._ctx.transition_reason = f"volume {best_candidate.volume} < {self._config.data_quality.min_volume}"
            return self._ctx.state

        # Check re-entry rules
        if not self._check_reentry(best_candidate.strike):
            self._ctx.rejection = RejectionReason.COOLDOWN_ACTIVE
            return self._ctx.state

        # Valid candidate found
        self._ctx.state = MachineState.CANDIDATE_FOUND
        self._ctx.candidate = best_candidate
        self._ctx.transition_reason = (
            f"candidate K={best_candidate.strike:.0f} "
            f"{best_candidate.option_type.value} "
            f"LTP=₹{best_candidate.ltp:.2f} score={best_candidate.score:.4f}"
        )
        logger.info(
            "ZONE_ACTIVE → CANDIDATE_FOUND: %s", self._ctx.transition_reason
        )
        return self._ctx.state

    def _eval_cooldown(self) -> MachineState:
        """COOLDOWN → IDLE when cooldown period expires."""
        if self._ctx.cooldown_start is None:
            self._transition_to_idle("cooldown start missing — resetting")
            return self._ctx.state

        elapsed = time.time() - self._ctx.cooldown_start
        if elapsed >= self._config.cooldown.seconds:
            self._transition_to_idle(
                f"cooldown expired ({elapsed:.0f}s >= {self._config.cooldown.seconds}s)"
            )
        else:
            self._ctx.transition_reason = (
                f"cooldown active: {elapsed:.0f}s / {self._config.cooldown.seconds}s"
            )

        return self._ctx.state

    # ── Trade Lifecycle Transitions (called by signal engine) ──────

    def enter_trade(self) -> None:
        """CANDIDATE_FOUND → IN_TRADE (called by signal engine after risk check)."""
        if self._ctx.state != MachineState.CANDIDATE_FOUND:
            logger.warning("enter_trade called in state %s", self._ctx.state.value)
            return
        self._ctx.state = MachineState.IN_TRADE
        self._ctx.entry_time = datetime.now(timezone.utc)
        self._ctx.daily_trade_count += 1
        self._ctx.transition_reason = "trade entered"
        logger.info("CANDIDATE_FOUND → IN_TRADE")

    def exit_trade(self, pnl: float) -> None:
        """IN_TRADE → EXIT_PENDING → COOLDOWN."""
        if self._ctx.state != MachineState.IN_TRADE:
            logger.warning("exit_trade called in state %s", self._ctx.state.value)
            return

        self._ctx.state = MachineState.EXIT_PENDING
        self._ctx.daily_pnl += pnl
        self._ctx.transition_reason = f"trade exited, PnL=₹{pnl:.2f}"

        if pnl < 0:
            self._ctx.consecutive_losses += 1
        else:
            self._ctx.consecutive_losses = 0

        # Track re-entry
        if self._ctx.candidate:
            self._ctx.last_strike = self._ctx.candidate.strike
        self._ctx.reentry_count += 1

        logger.info("IN_TRADE → EXIT_PENDING: %s", self._ctx.transition_reason)

        # Immediately move to cooldown
        self.confirm_exit()

    def confirm_exit(self) -> None:
        """EXIT_PENDING → COOLDOWN."""
        if self._ctx.state != MachineState.EXIT_PENDING:
            return
        self._ctx.state = MachineState.COOLDOWN
        self._ctx.cooldown_start = time.time()
        self._ctx.candidate = None
        self._ctx.entry_time = None
        self._ctx.transition_reason = "cooldown started"
        logger.info("EXIT_PENDING → COOLDOWN")

    # ── Helper Methods ─────────────────────────────────────────────

    def _transition_to_idle(self, reason: str) -> None:
        """Transition any state back to IDLE."""
        prev = self._ctx.state
        self._ctx.state = MachineState.IDLE
        self._ctx.active_side = None
        self._ctx.candidate = None
        self._ctx.transition_reason = reason
        if prev != MachineState.IDLE:
            logger.info("%s → IDLE: %s", prev.value, reason)

    def _check_time_filters(self, now: datetime) -> Optional[str]:
        """Check time-based no-trade conditions. Returns reason or None."""
        tf = self._config.time_filters

        # Convert to IST (UTC+5:30) for market hour comparison
        # Using offset calculation instead of pytz dependency
        ist_offset_seconds = 5 * 3600 + 30 * 60
        ist_ts = now.timestamp() + ist_offset_seconds
        ist_dt = datetime.fromtimestamp(ist_ts, tz=timezone.utc)
        current_time_str = ist_dt.strftime("%H:%M")
        market_minutes = ist_dt.hour * 60 + ist_dt.minute

        # Market open/close
        open_h, open_m = map(int, tf.market_open.split(":"))
        close_h, close_m = map(int, tf.market_close.split(":"))
        open_minutes = open_h * 60 + open_m
        close_minutes = close_h * 60 + close_m

        if market_minutes < open_minutes or market_minutes >= close_minutes:
            return f"outside market hours ({current_time_str})"

        # First N minutes
        if market_minutes < open_minutes + tf.no_trade_first_minutes:
            return f"first {tf.no_trade_first_minutes} minutes ({current_time_str})"

        # Lunch chop
        lunch_start_h, lunch_start_m = map(int, tf.no_trade_lunch_start.split(":"))
        lunch_end_h, lunch_end_m = map(int, tf.no_trade_lunch_end.split(":"))
        lunch_start = lunch_start_h * 60 + lunch_start_m
        lunch_end = lunch_end_h * 60 + lunch_end_m
        if lunch_start <= market_minutes < lunch_end:
            return f"lunch chop zone ({current_time_str})"

        # Near close
        squareoff_h, squareoff_m = map(int, tf.mandatory_squareoff_time.split(":"))
        squareoff_minutes = squareoff_h * 60 + squareoff_m
        no_trade_near_close = self._config.risk.no_trade_near_close_minutes
        if market_minutes >= squareoff_minutes - no_trade_near_close:
            return f"near close ({current_time_str}, squareoff at {tf.mandatory_squareoff_time})"

        return None

    def _check_risk_limits(self) -> Optional[RejectionReason]:
        """Check risk guardrails. Returns rejection reason or None."""
        cfg_pt = self._config.paper_trading

        if self._ctx.daily_trade_count >= cfg_pt.max_daily_trades:
            return RejectionReason.MAX_DAILY_TRADES

        if self._ctx.consecutive_losses >= cfg_pt.max_consecutive_losses:
            return RejectionReason.MAX_CONSECUTIVE_LOSSES

        if abs(self._ctx.daily_pnl) >= cfg_pt.max_daily_loss and self._ctx.daily_pnl < 0:
            return RejectionReason.MAX_DAILY_LOSS

        return None

    def _check_reentry(self, strike: float) -> bool:
        """Check if re-entry is allowed for this strike."""
        cd = self._config.cooldown

        if self._ctx.reentry_count >= cd.max_reentries:
            self._ctx.transition_reason = (
                f"max re-entries reached ({self._ctx.reentry_count} >= {cd.max_reentries})"
            )
            return False

        if (not cd.allow_same_strike_reentry
                and self._ctx.last_strike is not None
                and strike == self._ctx.last_strike):
            self._ctx.transition_reason = (
                f"same-strike re-entry blocked (K={strike:.0f})"
            )
            return False

        return True


def resolve_triggers(
    spot: float,
    config: LotteryConfig,
    strikes: Optional[tuple[float, ...]] = None,
) -> TriggerZone:
    """Resolve trigger zone — STATIC from config or DYNAMIC from chain.

    DYNAMIC mode: derives triggers from nearest round strikes around spot.
    STATIC mode: uses config values directly.

    Args:
        spot: Current spot price.
        config: Config with trigger settings.
        strikes: Available strikes (needed for DYNAMIC mode).

    Returns:
        TriggerZone with upper and lower triggers.
    """
    from ..config import TriggerMode

    if config.triggers.mode == TriggerMode.STATIC:
        return TriggerZone(
            upper_trigger=config.triggers.upper_trigger,
            lower_trigger=config.triggers.lower_trigger,
            source="STATIC",
        )

    # DYNAMIC: derive from chain
    step = config.instrument.strike_step

    if strikes:
        # Find nearest strikes around spot
        below = [s for s in strikes if s <= spot]
        above = [s for s in strikes if s > spot]

        lower = max(below) if below else spot - step
        upper = min(above) if above else spot + step
    else:
        # Fallback: round to nearest step
        lower = (spot // step) * step
        upper = lower + step

    return TriggerZone(
        upper_trigger=upper,
        lower_trigger=lower,
        source="DYNAMIC",
    )

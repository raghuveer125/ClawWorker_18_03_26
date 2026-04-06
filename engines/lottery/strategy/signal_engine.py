"""Signal engine — entry/exit rules, trigger validation, time-based filters.

Bridges the state machine and paper trading engine.
Produces SignalEvent records for every cycle (trade or no-trade).
Handles exit logic: SL, targets, time stop, EOD, trailing, invalidation.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from ..config import LotteryConfig
from ..models import (
    ExitReason,
    MachineState,
    OptionType,
    PaperTrade,
    QualityStatus,
    RejectionReason,
    Side,
    SignalEvent,
    SignalValidity,
    TradeStatus,
)
from ..calculations.scoring import ScoredCandidate
from .state_machine import StateMachine, TriggerZone

logger = logging.getLogger(__name__)


class SignalEngine:
    """Produces entry/exit signals based on state machine + rules.

    Entry: state machine reaches CANDIDATE_FOUND + all risk checks pass.
    Exit: monitors active trade against SL/targets/time/EOD/trailing/invalidation.
    """

    def __init__(self, config: LotteryConfig, state_machine: StateMachine) -> None:
        self._config = config
        self._sm = state_machine

    def evaluate_entry(
        self,
        spot: float,
        quality_status: QualityStatus,
        triggers: TriggerZone,
        best_ce: Optional[ScoredCandidate],
        best_pe: Optional[ScoredCandidate],
        preferred_side: Optional[Side],
        snapshot_id: str,
        config_version: str,
        current_time: Optional[datetime] = None,
    ) -> SignalEvent:
        """Evaluate whether to enter a new trade this cycle.

        Runs the state machine, then produces a SignalEvent.
        If state reaches CANDIDATE_FOUND, the signal is VALID for entry.

        Args:
            spot: Current spot price.
            quality_status: Data quality result.
            triggers: Resolved trigger zone.
            best_ce: Best CE candidate (or None).
            best_pe: Best PE candidate (or None).
            preferred_side: Side bias from calculations.
            snapshot_id: Current snapshot ID for audit.
            config_version: Config version hash for audit.
            current_time: Override timestamp for testing.

        Returns:
            SignalEvent describing the decision.
        """
        now = current_time or datetime.now(timezone.utc)

        # Run state machine
        new_state = self._sm.evaluate(
            spot, quality_status, triggers, best_ce, best_pe, preferred_side, now,
        )
        ctx = self._sm.context

        # Determine zone label
        if ctx.active_side == Side.CE:
            zone = "CE_ACTIVE"
        elif ctx.active_side == Side.PE:
            zone = "PE_ACTIVE"
        else:
            zone = "NO_TRADE"

        # Build signal
        candidate = ctx.candidate
        if new_state == MachineState.CANDIDATE_FOUND and candidate is not None:
            return SignalEvent(
                timestamp=now,
                symbol=candidate.option_type.value,
                side_bias=preferred_side,
                zone=zone,
                machine_state=new_state,
                selected_strike=candidate.strike,
                selected_option_type=candidate.option_type,
                selected_premium=candidate.ltp,
                trigger_status="TRIGGERED",
                validity=SignalValidity.VALID,
                rejection_reason=None,
                rejection_detail="",
                snapshot_id=snapshot_id,
                config_version=config_version,
                spot_ltp=spot,
            )

        # No entry — produce rejection signal
        return SignalEvent(
            timestamp=now,
            symbol="",
            side_bias=preferred_side,
            zone=zone,
            machine_state=new_state,
            selected_strike=None,
            selected_option_type=None,
            selected_premium=None,
            trigger_status="WAITING" if new_state in (
                MachineState.ZONE_ACTIVE_CE, MachineState.ZONE_ACTIVE_PE
            ) else "N/A",
            validity=SignalValidity.INVALID,
            rejection_reason=ctx.rejection,
            rejection_detail=ctx.transition_reason,
            snapshot_id=snapshot_id,
            config_version=config_version,
            spot_ltp=spot,
        )

    def evaluate_exit(
        self,
        trade: PaperTrade,
        current_ltp: float,
        spot: float,
        triggers: TriggerZone,
        current_time: Optional[datetime] = None,
    ) -> Optional[ExitReason]:
        """Evaluate whether an active trade should be exited.

        Checks exits in priority order:
        1. Stop-loss
        2. Target 3 (highest)
        3. Target 2
        4. Target 1
        5. Invalidation (trigger reversal)
        6. Trailing stop
        7. Time stop
        8. EOD exit

        Args:
            trade: Active paper trade.
            current_ltp: Current option LTP.
            spot: Current spot price.
            triggers: Current trigger zone.
            current_time: Override timestamp for testing.

        Returns:
            ExitReason if exit should occur, None if hold.
        """
        if trade.status != TradeStatus.OPEN:
            return None

        now = current_time or datetime.now(timezone.utc)
        cfg = self._config.exit_rules
        entry = trade.entry_price

        # 1. Stop-loss
        sl_price = entry * cfg.sl_ratio
        if current_ltp <= sl_price:
            logger.info(
                "SL hit: LTP=₹%.2f <= SL=₹%.2f (%.0f%% of entry ₹%.2f)",
                current_ltp, sl_price, cfg.sl_ratio * 100, entry,
            )
            return ExitReason.STOP_LOSS

        # 2. Target 3 (check highest first for best exit)
        t3_price = entry * cfg.t3_ratio
        if current_ltp >= t3_price:
            logger.info("T3 hit: LTP=₹%.2f >= T3=₹%.2f", current_ltp, t3_price)
            return ExitReason.TARGET_3

        # 3. Target 2
        t2_price = entry * cfg.t2_ratio
        if current_ltp >= t2_price:
            logger.info("T2 hit: LTP=₹%.2f >= T2=₹%.2f", current_ltp, t2_price)
            return ExitReason.TARGET_2

        # 4. Target 1
        t1_price = entry * cfg.t1_ratio
        if current_ltp >= t1_price:
            logger.info("T1 hit: LTP=₹%.2f >= T1=₹%.2f", current_ltp, t1_price)
            return ExitReason.TARGET_1

        # 5. Invalidation (trigger reversal)
        if cfg.invalidation_exit:
            if trade.option_type == OptionType.CE and spot <= triggers.upper_trigger:
                logger.info(
                    "Invalidation: CE trade but spot=₹%.1f <= upper_trigger=₹%.0f",
                    spot, triggers.upper_trigger,
                )
                return ExitReason.INVALIDATION

            if trade.option_type == OptionType.PE and spot >= triggers.lower_trigger:
                logger.info(
                    "Invalidation: PE trade but spot=₹%.1f >= lower_trigger=₹%.0f",
                    spot, triggers.lower_trigger,
                )
                return ExitReason.INVALIDATION

        # 6. Trailing stop
        if cfg.trailing_stop and cfg.trailing_stop_pct > 0:
            # Track peak LTP (caller should maintain this)
            # For now, simple check against entry-based trailing
            trail_price = current_ltp * (1 - cfg.trailing_stop_pct / 100)
            if current_ltp < trail_price:
                logger.info("Trailing stop: LTP=₹%.2f", current_ltp)
                return ExitReason.TRAILING_STOP

        # 7. Time stop
        if cfg.time_stop_minutes > 0 and trade.timestamp_entry:
            elapsed_minutes = (now - trade.timestamp_entry).total_seconds() / 60
            if elapsed_minutes >= cfg.time_stop_minutes:
                logger.info(
                    "Time stop: %.0f minutes elapsed (limit=%d)",
                    elapsed_minutes, cfg.time_stop_minutes,
                )
                return ExitReason.TIME_STOP

        # 8. EOD exit
        if cfg.eod_exit:
            eod_reason = self._check_eod(now)
            if eod_reason:
                logger.info("EOD exit: %s", eod_reason)
                return ExitReason.EOD_EXIT

        return None

    def compute_exit_levels(
        self,
        entry_price: float,
    ) -> dict:
        """Compute SL and target levels for a given entry price.

        Returns:
            Dict with sl, t1, t2, t3 prices.
        """
        cfg = self._config.exit_rules
        return {
            "sl": round(entry_price * cfg.sl_ratio, 2),
            "t1": round(entry_price * cfg.t1_ratio, 2),
            "t2": round(entry_price * cfg.t2_ratio, 2),
            "t3": round(entry_price * cfg.t3_ratio, 2),
        }

    def _check_eod(self, now: datetime) -> Optional[str]:
        """Check if current time is past mandatory squareoff."""
        tf = self._config.time_filters

        # Convert to IST
        ist_offset_seconds = 5 * 3600 + 30 * 60
        ist_ts = now.timestamp() + ist_offset_seconds
        ist_dt = datetime.fromtimestamp(ist_ts, tz=timezone.utc)
        market_minutes = ist_dt.hour * 60 + ist_dt.minute

        sq_h, sq_m = map(int, tf.mandatory_squareoff_time.split(":"))
        squareoff_minutes = sq_h * 60 + sq_m

        if market_minutes >= squareoff_minutes:
            return f"past squareoff time {tf.mandatory_squareoff_time} (current {ist_dt.strftime('%H:%M')})"

        return None

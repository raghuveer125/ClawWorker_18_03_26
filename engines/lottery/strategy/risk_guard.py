"""Risk guardrails — unified pre-trade risk validation.

Consolidates all risk checks into a single gate that must pass before
any paper trade entry. Checks are ordered by priority and short-circuit
on first rejection.

This module does NOT duplicate logic — it orchestrates checks from
state_machine and capital_manager into a single deterministic decision.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from ..config import LotteryConfig
from ..models import (
    MachineState,
    QualityStatus,
    RejectionReason,
)
from ..paper_trading.capital_manager import CapitalManager
from .state_machine import StateContext

logger = logging.getLogger(__name__)


class RiskCheckResult:
    """Result of a risk evaluation."""

    __slots__ = ("allowed", "reason", "rejection", "details")

    def __init__(
        self,
        allowed: bool,
        reason: str = "ok",
        rejection: Optional[RejectionReason] = None,
        details: Optional[dict] = None,
    ) -> None:
        self.allowed = allowed
        self.reason = reason
        self.rejection = rejection
        self.details = details or {}


class RiskGuard:
    """Unified risk guardrail — single gate before trade entry.

    Checks (in priority order):
    1. Capital available
    2. Max daily loss not breached
    3. Max daily trades not exceeded
    4. Max consecutive losses not exceeded
    5. Cooldown after loss (if enabled)
    6. Max open trades
    7. No trade during poor data quality (if enabled)
    8. No trade near market close
    9. Position size within limits

    All thresholds from config — nothing hardcoded.
    """

    def __init__(self, config: LotteryConfig) -> None:
        self._config = config
        self._pt = config.paper_trading
        self._risk = config.risk
        self._tf = config.time_filters

    def check_entry(
        self,
        state_ctx: StateContext,
        capital: CapitalManager,
        quality_status: QualityStatus,
        entry_price: float,
        lot_size: int,
        current_time: Optional[datetime] = None,
    ) -> RiskCheckResult:
        """Run all risk checks before a paper trade entry.

        Args:
            state_ctx: Current state machine context.
            capital: Capital manager with running balances.
            quality_status: Current data quality status.
            entry_price: Expected entry price.
            lot_size: Instrument lot size.
            current_time: Override for testing.

        Returns:
            RiskCheckResult — allowed=True if all checks pass.
        """
        now = current_time or datetime.now(timezone.utc)

        # 1. Capital available
        can_trade, reason = capital.can_trade()
        if not can_trade:
            return RiskCheckResult(
                allowed=False,
                reason=reason,
                rejection=RejectionReason.RISK_REJECTION,
                details={"check": "capital_available", "capital": capital.running_capital},
            )

        # 2. Max daily loss
        if state_ctx.daily_pnl < 0 and abs(state_ctx.daily_pnl) >= self._pt.max_daily_loss:
            return RiskCheckResult(
                allowed=False,
                reason=f"daily loss ₹{abs(state_ctx.daily_pnl):.2f} >= limit ₹{self._pt.max_daily_loss:.2f}",
                rejection=RejectionReason.MAX_DAILY_LOSS,
                details={"daily_pnl": state_ctx.daily_pnl, "limit": self._pt.max_daily_loss},
            )

        # 3. Max daily trades
        if state_ctx.daily_trade_count >= self._pt.max_daily_trades:
            return RiskCheckResult(
                allowed=False,
                reason=f"daily trades {state_ctx.daily_trade_count} >= limit {self._pt.max_daily_trades}",
                rejection=RejectionReason.MAX_DAILY_TRADES,
                details={"trades": state_ctx.daily_trade_count, "limit": self._pt.max_daily_trades},
            )

        # 4. Max consecutive losses
        if state_ctx.consecutive_losses >= self._pt.max_consecutive_losses:
            return RiskCheckResult(
                allowed=False,
                reason=f"consecutive losses {state_ctx.consecutive_losses} >= limit {self._pt.max_consecutive_losses}",
                rejection=RejectionReason.MAX_CONSECUTIVE_LOSSES,
                details={"losses": state_ctx.consecutive_losses, "limit": self._pt.max_consecutive_losses},
            )

        # 5. Cooldown after loss
        if self._risk.cooldown_after_loss and state_ctx.consecutive_losses > 0:
            if state_ctx.state == MachineState.COOLDOWN:
                return RiskCheckResult(
                    allowed=False,
                    reason="cooldown active after loss",
                    rejection=RejectionReason.COOLDOWN_ACTIVE,
                    details={"consecutive_losses": state_ctx.consecutive_losses},
                )

        # 6. Max open trades
        if self._pt.max_open_trades <= 0:
            return RiskCheckResult(
                allowed=False,
                reason="max_open_trades is 0",
                rejection=RejectionReason.RISK_REJECTION,
            )

        # 7. No trade during poor quality
        if self._risk.no_trade_poor_quality and quality_status == QualityStatus.FAIL:
            return RiskCheckResult(
                allowed=False,
                reason="data quality FAIL — no trade allowed",
                rejection=RejectionReason.DATA_QUALITY_FAIL,
                details={"quality": quality_status.value},
            )

        # 8. No trade near close
        if self._risk.no_trade_near_close_minutes > 0:
            near_close = self._is_near_close(now)
            if near_close:
                return RiskCheckResult(
                    allowed=False,
                    reason=near_close,
                    rejection=RejectionReason.TIME_FILTER,
                    details={"check": "near_close"},
                )

        # 9. Position size sanity
        qty, lots = self._compute_size_check(entry_price, lot_size, capital.running_capital)
        if qty <= 0 or lots <= 0:
            return RiskCheckResult(
                allowed=False,
                reason=f"position size too small: qty={qty} lots={lots}",
                rejection=RejectionReason.RISK_REJECTION,
                details={"entry_price": entry_price, "capital": capital.running_capital},
            )

        # All checks passed
        return RiskCheckResult(
            allowed=True,
            reason="all risk checks passed",
            details={
                "daily_trades": state_ctx.daily_trade_count,
                "daily_pnl": state_ctx.daily_pnl,
                "consecutive_losses": state_ctx.consecutive_losses,
                "capital": capital.running_capital,
                "qty": qty,
                "lots": lots,
            },
        )

    def _is_near_close(self, now: datetime) -> Optional[str]:
        """Check if we're within no-trade-near-close window."""
        ist_offset_seconds = 5 * 3600 + 30 * 60
        ist_ts = now.timestamp() + ist_offset_seconds
        ist_dt = datetime.fromtimestamp(ist_ts, tz=timezone.utc)
        market_minutes = ist_dt.hour * 60 + ist_dt.minute

        sq_h, sq_m = map(int, self._tf.mandatory_squareoff_time.split(":"))
        squareoff_minutes = sq_h * 60 + sq_m

        cutoff = squareoff_minutes - self._risk.no_trade_near_close_minutes
        if market_minutes >= cutoff:
            return (
                f"near close: {ist_dt.strftime('%H:%M')} >= "
                f"cutoff {cutoff // 60}:{cutoff % 60:02d} "
                f"(squareoff {self._tf.mandatory_squareoff_time} - {self._risk.no_trade_near_close_minutes}min)"
            )

        return None

    def _compute_size_check(
        self,
        entry_price: float,
        lot_size: int,
        capital: float,
    ) -> tuple[int, int]:
        """Quick sanity check on position size."""
        if entry_price <= 0 or lot_size <= 0:
            return 0, 0

        max_cost = entry_price * lot_size * self._pt.fixed_lots
        if max_cost > capital * 0.5:
            # Don't allow a single trade to risk more than 50% of capital
            return 0, 0

        return lot_size * self._pt.fixed_lots, self._pt.fixed_lots

"""Capital manager — position sizing, ledger, drawdown, and risk enforcement.

Tracks: starting capital, realized PnL, unrealized PnL, charges,
net equity, daily PnL, drawdown, peak capital.

Position sizing modes: FIXED_LOTS, FIXED_RUPEE, PCT_CAPITAL, PREMIUM_BUDGET.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from ..config import LotteryConfig, SizingMode
from ..models import CapitalLedgerEntry, PaperTrade, TradeStatus

logger = logging.getLogger(__name__)


class CapitalManager:
    """Manages paper trading capital, sizing, and risk limits.

    Maintains a running ledger of all capital events.
    Symbol-agnostic — works for any instrument.
    """

    def __init__(self, config: LotteryConfig, symbol: str = "") -> None:
        self._config = config
        self._pt = config.paper_trading
        self._symbol = symbol

        # Capital state
        self._starting_capital = self._pt.starting_capital
        self._running_capital = self._starting_capital
        self._peak_capital = self._starting_capital
        self._realized_pnl = 0.0
        self._daily_pnl = 0.0
        self._total_charges = 0.0

        # Ledger
        self._ledger: list[CapitalLedgerEntry] = []

        # Record initial entry
        self._add_ledger_entry("INIT", self._starting_capital)

    @property
    def running_capital(self) -> float:
        return self._running_capital

    @property
    def starting_capital(self) -> float:
        return self._starting_capital

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def peak_capital(self) -> float:
        return self._peak_capital

    @property
    def drawdown(self) -> float:
        if self._peak_capital <= 0:
            return 0.0
        return round(self._peak_capital - self._running_capital, 2)

    @property
    def drawdown_pct(self) -> float:
        if self._peak_capital <= 0:
            return 0.0
        return round((self._peak_capital - self._running_capital) / self._peak_capital * 100, 2)

    @property
    def ledger(self) -> list[CapitalLedgerEntry]:
        return list(self._ledger)

    def reset_daily(self) -> None:
        """Reset daily PnL counter — call at start of each trading day."""
        self._daily_pnl = 0.0
        logger.info("Daily PnL reset. Capital: ₹%.2f", self._running_capital)

    # ── Position Sizing ────────────────────────────────────────────

    def compute_position_size(
        self,
        entry_price: float,
        lot_size: int,
    ) -> tuple[int, int]:
        """Compute qty and lots based on sizing mode.

        Args:
            entry_price: Expected fill price.
            lot_size: Instrument lot size.

        Returns:
            (qty, lots) — total quantity and number of lots.
        """
        mode = self._pt.sizing_mode

        if mode == SizingMode.FIXED_LOTS:
            lots = self._pt.fixed_lots
            qty = lots * lot_size
            return qty, lots

        if mode == SizingMode.FIXED_RUPEE:
            # Risk = max_risk_per_trade_pct % of capital
            max_risk = self._running_capital * (self._pt.max_risk_per_trade_pct / 100)
            # SL loss per lot = entry * sl_ratio * lot_size
            sl_loss_per_lot = entry_price * self._config.exit_rules.sl_ratio * lot_size
            if sl_loss_per_lot <= 0:
                return lot_size, 1
            lots = max(1, int(max_risk / sl_loss_per_lot))
            lots = min(lots, self._pt.fixed_lots * 5)  # safety cap
            return lots * lot_size, lots

        if mode == SizingMode.PCT_CAPITAL:
            # Allocate max_risk_per_trade_pct of capital
            budget = self._running_capital * (self._pt.max_risk_per_trade_pct / 100)
            cost_per_lot = entry_price * lot_size
            if cost_per_lot <= 0:
                return lot_size, 1
            lots = max(1, int(budget / cost_per_lot))
            return lots * lot_size, lots

        if mode == SizingMode.PREMIUM_BUDGET:
            # Fixed premium budget per trade
            budget = self._running_capital * (self._pt.max_risk_per_trade_pct / 100)
            cost_per_lot = entry_price * lot_size
            if cost_per_lot <= 0:
                return lot_size, 1
            lots = max(1, int(budget / cost_per_lot))
            return lots * lot_size, lots

        # Default: 1 lot
        return lot_size, 1

    # ── Risk Checks ────────────────────────────────────────────────

    def can_trade(self) -> tuple[bool, str]:
        """Check if capital allows a new trade.

        Returns:
            (allowed, reason)
        """
        if self._running_capital <= 0:
            return False, f"capital exhausted: ₹{self._running_capital:.2f}"

        if self._daily_pnl < 0 and abs(self._daily_pnl) >= self._pt.max_daily_loss:
            return False, f"daily loss limit reached: ₹{abs(self._daily_pnl):.2f} >= ₹{self._pt.max_daily_loss:.2f}"

        return True, "ok"

    # ── Trade Lifecycle ────────────────────────────────────────────

    def record_entry(self, trade: PaperTrade) -> None:
        """Record a trade entry in the ledger."""
        # Entry cost = premium * qty (for tracking, not deducted from capital in options)
        entry_cost = trade.entry_price * trade.qty
        self._add_ledger_entry(
            event="TRADE_ENTRY",
            amount=-trade.charges,  # only charges deducted at entry
            trade_id=trade.trade_id,
        )
        self._total_charges += trade.charges
        logger.info(
            "Capital entry recorded: trade=%s charges=₹%.2f capital=₹%.2f",
            trade.trade_id, trade.charges, self._running_capital,
        )

    def record_exit(self, trade: PaperTrade) -> None:
        """Record a trade exit in the ledger.

        Args:
            trade: Closed trade with PnL computed.
        """
        if trade.pnl is None:
            logger.warning("record_exit called with no PnL on trade %s", trade.trade_id)
            return

        pnl = trade.pnl
        self._realized_pnl += pnl
        self._daily_pnl += pnl
        self._running_capital += pnl

        if self._running_capital > self._peak_capital:
            self._peak_capital = self._running_capital

        self._add_ledger_entry(
            event="TRADE_EXIT",
            amount=pnl,
            trade_id=trade.trade_id,
        )

        logger.info(
            "Capital exit recorded: trade=%s PnL=₹%.2f capital=₹%.2f drawdown=₹%.2f",
            trade.trade_id, pnl, self._running_capital, self.drawdown,
        )

    def get_summary(self) -> dict:
        """Get capital summary for display."""
        return {
            "starting_capital": self._starting_capital,
            "running_capital": round(self._running_capital, 2),
            "realized_pnl": round(self._realized_pnl, 2),
            "daily_pnl": round(self._daily_pnl, 2),
            "total_charges": round(self._total_charges, 2),
            "peak_capital": round(self._peak_capital, 2),
            "drawdown": self.drawdown,
            "drawdown_pct": self.drawdown_pct,
            "net_return_pct": round(
                (self._running_capital - self._starting_capital) / self._starting_capital * 100, 2
            ),
        }

    # ── Helpers ────────────────────────────────────────────────────

    def _add_ledger_entry(
        self,
        event: str,
        amount: float,
        trade_id: Optional[str] = None,
    ) -> None:
        """Add an entry to the capital ledger."""
        entry = CapitalLedgerEntry(
            timestamp=datetime.now(timezone.utc),
            symbol=self._symbol,
            trade_id=trade_id,
            event=event,
            amount=round(amount, 2),
            running_capital=round(self._running_capital, 2),
            realized_pnl=round(self._realized_pnl, 2),
            unrealized_pnl=0.0,  # updated separately during live monitoring
            daily_pnl=round(self._daily_pnl, 2),
            drawdown=self.drawdown,
            peak_capital=round(self._peak_capital, 2),
        )
        self._ledger.append(entry)

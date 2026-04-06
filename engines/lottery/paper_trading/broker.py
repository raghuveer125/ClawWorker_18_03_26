"""Paper broker — simulated execution with configurable fill modes.

Fill modes: LTP, MID, ASK (buy), BID (sell), MID+SLIPPAGE.
Simulates brokerage, exchange charges, and slippage.
Produces PaperTrade records with full audit trail.
"""

import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from ..config import ExecutionMode, LotteryConfig
from ..models import (
    ExitReason,
    OptionType,
    PaperTrade,
    Side,
    TradeStatus,
)
from ..calculations.scoring import ScoredCandidate

logger = logging.getLogger(__name__)


class PaperBroker:
    """Simulated trade execution for paper trading.

    Handles:
    - Fill price calculation based on execution mode
    - Brokerage and charges simulation
    - Trade creation and closing
    """

    def __init__(self, config: LotteryConfig) -> None:
        self._config = config
        self._exec = config.execution

    def execute_entry(
        self,
        candidate: ScoredCandidate,
        symbol: str,
        expiry: str,
        qty: int,
        lots: int,
        capital_before: float,
        signal_id: str,
        snapshot_id: str,
        config_version: str,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        selection_price: Optional[float] = None,
        confirmation_price: Optional[float] = None,
    ) -> PaperTrade:
        """Simulate a buy entry.

        Args:
            candidate: Scored candidate to trade.
            symbol: Instrument symbol.
            expiry: Expiry date.
            qty: Total quantity (lots * lot_size).
            lots: Number of lots.
            capital_before: Capital before this trade.
            signal_id: Signal that triggered this entry.
            snapshot_id: Chain snapshot reference.
            config_version: Config version for audit.
            bid: Current bid price (for fill calculation).
            ask: Current ask price (for fill calculation).
            selection_price: LTP when candidate was scored (from AnalysisSnapshot).
            confirmation_price: LTP when confirmation passed (from TriggerSnapshot).

        Returns:
            PaperTrade with entry filled and all price roles set.
        """
        ltp = candidate.ltp
        fill_price = self._compute_fill_price_buy(ltp, bid, ask)
        charges = self._compute_charges(lots)

        exit_levels = self._compute_exit_levels(fill_price)

        side = Side.CE if candidate.option_type == OptionType.CE else Side.PE

        trade = PaperTrade(
            timestamp_entry=datetime.now(timezone.utc),
            side=side,
            symbol=symbol,
            expiry=expiry,
            strike=candidate.strike,
            option_type=candidate.option_type,
            selection_price=selection_price or candidate.ltp,
            confirmation_price=confirmation_price,
            entry_price=fill_price,
            qty=qty,
            lots=lots,
            capital_before=capital_before,
            sl=exit_levels["sl"],
            t1=exit_levels["t1"],
            t2=exit_levels["t2"],
            t3=exit_levels["t3"],
            charges=charges,
            status=TradeStatus.OPEN,
            reason_entry=(
                f"score={candidate.score:.4f} "
                f"K={candidate.strike:.0f} {candidate.option_type.value} "
                f"LTP=₹{ltp:.2f} fill=₹{fill_price:.2f} "
                f"({self._exec.mode.value})"
            ),
            signal_id=signal_id,
            snapshot_id=snapshot_id,
            config_version=config_version,
        )

        logger.info(
            "PAPER ENTRY: %s K=%s entry=₹%.2f qty=%d lots=%d charges=₹%.2f",
            trade.option_type.value, trade.strike, fill_price, qty, lots, charges,
        )

        return trade

    def execute_exit(
        self,
        trade: PaperTrade,
        current_ltp: float,
        exit_reason: ExitReason,
        lots: int,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
    ) -> PaperTrade:
        """Simulate a sell exit.

        Args:
            trade: Active paper trade to close.
            current_ltp: Current option LTP.
            exit_reason: Why we're exiting.
            lots: Lots being exited.
            bid: Current bid price.
            ask: Current ask price.

        Returns:
            Updated PaperTrade with exit filled and PnL computed.
        """
        fill_price = self._compute_fill_price_sell(current_ltp, bid, ask)
        exit_charges = self._compute_charges(lots)
        total_charges = trade.charges + exit_charges

        # PnL = (exit - entry) * qty - total charges
        gross_pnl = (fill_price - trade.entry_price) * trade.qty
        net_pnl = gross_pnl - total_charges
        capital_after = trade.capital_before + net_pnl

        closed_trade = replace(
            trade,
            timestamp_exit=datetime.now(timezone.utc),
            exit_price=fill_price,
            pnl=round(net_pnl, 2),
            charges=round(total_charges, 2),
            capital_after=round(capital_after, 2),
            status=TradeStatus.CLOSED,
            reason_exit=exit_reason,
            exit_detail=(
                f"exit=₹{fill_price:.2f} gross=₹{gross_pnl:.2f} "
                f"charges=₹{total_charges:.2f} net=₹{net_pnl:.2f} "
                f"({exit_reason.value})"
            ),
        )

        logger.info(
            "PAPER EXIT: %s K=%s exit=₹%.2f PnL=₹%.2f (%s)",
            closed_trade.option_type.value, closed_trade.strike,
            fill_price, net_pnl, exit_reason.value,
        )

        return closed_trade

    # ── Fill Price Logic ───────────────────────────────────────────

    def _compute_fill_price_buy(
        self,
        ltp: float,
        bid: Optional[float],
        ask: Optional[float],
    ) -> float:
        """Compute buy fill price based on execution mode."""
        mode = self._exec.mode
        slippage_pct = self._exec.slippage_pct / 100

        if mode == ExecutionMode.LTP:
            return round(ltp, 2)

        if mode == ExecutionMode.ASK and ask is not None and ask > 0:
            return round(ask, 2)

        if mode == ExecutionMode.MID and bid is not None and ask is not None:
            mid = (bid + ask) / 2
            return round(mid, 2)

        if mode == ExecutionMode.MID_SLIPPAGE:
            if bid is not None and ask is not None and bid > 0 and ask > 0:
                mid = (bid + ask) / 2
                return round(mid * (1 + slippage_pct), 2)
            # Fallback: LTP + slippage
            return round(ltp * (1 + slippage_pct), 2)

        # Default: LTP
        return round(ltp, 2)

    def _compute_fill_price_sell(
        self,
        ltp: float,
        bid: Optional[float],
        ask: Optional[float],
    ) -> float:
        """Compute sell fill price based on execution mode."""
        mode = self._exec.mode
        slippage_pct = self._exec.slippage_pct / 100

        if mode == ExecutionMode.LTP:
            return round(ltp, 2)

        if mode == ExecutionMode.BID and bid is not None and bid > 0:
            return round(bid, 2)

        if mode == ExecutionMode.MID and bid is not None and ask is not None:
            mid = (bid + ask) / 2
            return round(mid, 2)

        if mode == ExecutionMode.MID_SLIPPAGE:
            if bid is not None and ask is not None and bid > 0 and ask > 0:
                mid = (bid + ask) / 2
                return round(mid * (1 - slippage_pct), 2)
            # Fallback: LTP - slippage
            return round(ltp * (1 - slippage_pct), 2)

        return round(ltp, 2)

    def _compute_charges(self, lots: int) -> float:
        """Compute total charges for a trade leg."""
        brokerage = self._exec.brokerage_per_lot * lots
        # Exchange charges as % of notional (simplified)
        exchange = brokerage * (self._exec.exchange_charges_pct / 100)
        return round(brokerage + exchange, 2)

    def _compute_exit_levels(self, entry_price: float) -> dict:
        """Compute SL and target levels."""
        cfg = self._config.exit_rules
        return {
            "sl": round(entry_price * cfg.sl_ratio, 2),
            "t1": round(entry_price * cfg.t1_ratio, 2),
            "t2": round(entry_price * cfg.t2_ratio, 2),
            "t3": round(entry_price * cfg.t3_ratio, 2),
        }

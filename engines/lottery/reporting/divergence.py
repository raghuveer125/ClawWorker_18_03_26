"""Divergence reporting — paper vs realistic execution analysis.

For each trade or near-trade, generates a report explaining the gap between
mathematical selection and realistic execution:

- What price was the candidate scored at?
- What price was it when confirmation passed?
- What was the simulated fill?
- Was the strike truly executable at entry moment?
- What was the max favorable / adverse excursion?
- If no trade, why not?

This is the main tool for auditing whether paper results are realistic.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..models import PaperTrade, TradeStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DivergenceReport:
    """Paper-vs-live divergence analysis for one trade or near-trade."""
    report_id: str = ""
    symbol: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Timing
    candidate_selected_time: Optional[datetime] = None
    trigger_confirmation_time: Optional[datetime] = None
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None

    # Price roles
    selection_price: Optional[float] = None
    confirmation_price: Optional[float] = None
    simulated_entry_price: Optional[float] = None
    simulated_exit_price: Optional[float] = None

    # Spread at entry
    spread_at_entry: Optional[float] = None
    spread_pct_at_entry: Optional[float] = None

    # Tradability at entry moment
    truly_executable: bool = False
    tradability_detail: str = ""

    # Excursion tracking
    max_favorable_excursion: Optional[float] = None   # MFE — best price reached
    max_adverse_excursion: Optional[float] = None     # MAE — worst price reached
    mfe_pct: Optional[float] = None
    mae_pct: Optional[float] = None

    # Trade result
    trade_id: Optional[str] = None
    side: Optional[str] = None
    strike: Optional[float] = None
    pnl: Optional[float] = None
    status: Optional[str] = None

    # Rejection (if no trade)
    rejected: bool = False
    rejection_reasons: tuple[str, ...] = ()

    # Price slippage analysis
    selection_to_entry_slippage: Optional[float] = None    # entry - selection
    selection_to_entry_slippage_pct: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "candidate_selected_time": self.candidate_selected_time.isoformat() if self.candidate_selected_time else None,
            "trigger_confirmation_time": self.trigger_confirmation_time.isoformat() if self.trigger_confirmation_time else None,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "selection_price": self.selection_price,
            "confirmation_price": self.confirmation_price,
            "simulated_entry_price": self.simulated_entry_price,
            "simulated_exit_price": self.simulated_exit_price,
            "spread_at_entry": self.spread_at_entry,
            "spread_pct_at_entry": self.spread_pct_at_entry,
            "truly_executable": self.truly_executable,
            "tradability_detail": self.tradability_detail,
            "max_favorable_excursion": self.max_favorable_excursion,
            "max_adverse_excursion": self.max_adverse_excursion,
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
            "trade_id": self.trade_id,
            "side": self.side,
            "strike": self.strike,
            "pnl": self.pnl,
            "status": self.status,
            "rejected": self.rejected,
            "rejection_reasons": list(self.rejection_reasons),
            "selection_to_entry_slippage": self.selection_to_entry_slippage,
            "selection_to_entry_slippage_pct": self.selection_to_entry_slippage_pct,
        }


def build_trade_divergence(
    trade: PaperTrade,
    peak_ltp: Optional[float] = None,
    trough_ltp: Optional[float] = None,
    spread_at_entry: Optional[float] = None,
    spread_pct_at_entry: Optional[float] = None,
    tradability_passed: bool = True,
    tradability_detail: str = "",
) -> DivergenceReport:
    """Build a divergence report for a completed paper trade.

    Args:
        trade: The closed paper trade.
        peak_ltp: Highest LTP observed during trade (for MFE).
        trough_ltp: Lowest LTP observed during trade (for MAE).
        spread_at_entry: Absolute spread at entry moment.
        spread_pct_at_entry: Spread % at entry moment.
        tradability_passed: Whether tradability checks passed at entry.
        tradability_detail: Detail if tradability failed.

    Returns:
        DivergenceReport with full analysis.
    """
    entry = trade.entry_price
    selection = trade.selection_price or entry
    confirmation = trade.confirmation_price

    # MFE / MAE
    mfe = None
    mae = None
    mfe_pct = None
    mae_pct = None

    if peak_ltp is not None and entry > 0:
        mfe = round(peak_ltp - entry, 2)
        mfe_pct = round((mfe / entry) * 100, 2)
    if trough_ltp is not None and entry > 0:
        mae = round(entry - trough_ltp, 2)
        mae_pct = round((mae / entry) * 100, 2)

    # Slippage: how much did price move from selection to fill
    slippage = None
    slippage_pct = None
    if selection and selection > 0:
        slippage = round(entry - selection, 2)
        slippage_pct = round((slippage / selection) * 100, 2)

    return DivergenceReport(
        report_id=trade.trade_id,
        symbol=trade.symbol,
        timestamp=trade.timestamp_exit or trade.timestamp_entry,
        candidate_selected_time=trade.timestamp_entry,  # approximate
        trigger_confirmation_time=trade.timestamp_entry,  # approximate
        entry_time=trade.timestamp_entry,
        exit_time=trade.timestamp_exit,
        selection_price=selection,
        confirmation_price=confirmation,
        simulated_entry_price=entry,
        simulated_exit_price=trade.exit_price,
        spread_at_entry=spread_at_entry,
        spread_pct_at_entry=spread_pct_at_entry,
        truly_executable=tradability_passed,
        tradability_detail=tradability_detail,
        max_favorable_excursion=mfe,
        max_adverse_excursion=mae,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        trade_id=trade.trade_id,
        side=trade.side.value,
        strike=trade.strike,
        pnl=trade.pnl,
        status=trade.status.value,
        rejected=False,
        selection_to_entry_slippage=slippage,
        selection_to_entry_slippage_pct=slippage_pct,
    )


def build_rejection_divergence(
    symbol: str,
    strike: Optional[float] = None,
    side: Optional[str] = None,
    selection_price: Optional[float] = None,
    rejection_reasons: Optional[list[str]] = None,
    spread_at_moment: Optional[float] = None,
    tradability_passed: bool = False,
    tradability_detail: str = "",
) -> DivergenceReport:
    """Build a divergence report for a near-trade that was rejected.

    Args:
        symbol: Instrument.
        strike: Candidate strike.
        side: CE or PE.
        selection_price: LTP when candidate was scored.
        rejection_reasons: Why the trade didn't happen.
        spread_at_moment: Spread at rejection moment.
        tradability_passed: Whether tradability would have passed.
        tradability_detail: Detail.

    Returns:
        DivergenceReport explaining why no trade occurred.
    """
    return DivergenceReport(
        symbol=symbol,
        selection_price=selection_price,
        strike=strike,
        side=side,
        truly_executable=tradability_passed,
        tradability_detail=tradability_detail,
        spread_at_entry=spread_at_moment,
        rejected=True,
        rejection_reasons=tuple(rejection_reasons or []),
    )


def divergence_table(reports: list[DivergenceReport]) -> list[dict]:
    """Generate divergence report table for dashboard/export."""
    return [r.to_dict() for r in reports]

"""Table generators — raw data, formula audit, quality, signal, and trade tables.

Each generator returns a list of dicts (rows) ready for:
- JSON serialization (frontend API)
- Terminal rendering
- CSV export

All tables are symbol-agnostic.
"""

from typing import Optional

from ..config import LotteryConfig
from ..models import (
    CalculatedRow,
    CapitalLedgerEntry,
    ChainSnapshot,
    ExtrapolatedStrike,
    OptionType,
    PaperTrade,
    QualityReport,
    SignalEvent,
)
from ..calculations.scoring import ScoredCandidate


# ── 1. Raw Data Table ──────────────────────────────────────────────────────

def raw_data_table(snapshot: ChainSnapshot) -> list[dict]:
    """Generate raw option chain table.

    Columns: strike, CE_LTP, CE_change, CE_volume, CE_OI, CE_IV, CE_bid, CE_ask,
             PE_LTP, PE_change, PE_volume, PE_OI, PE_IV, PE_bid, PE_ask
    """
    # Build strike → {CE: row, PE: row} map
    strike_map: dict[float, dict[str, object]] = {}
    for row in snapshot.rows:
        strike_map.setdefault(row.strike, {})[row.option_type.value] = row

    table: list[dict] = []
    for strike in sorted(strike_map.keys()):
        ce = strike_map[strike].get("CE")
        pe = strike_map[strike].get("PE")
        table.append({
            "strike": strike,
            "CE_LTP": ce.ltp if ce else None,
            "CE_change": ce.change if ce else None,
            "CE_volume": ce.volume if ce else None,
            "CE_OI": ce.oi if ce else None,
            "CE_IV": ce.iv if ce else None,
            "CE_bid": ce.bid if ce else None,
            "CE_ask": ce.ask if ce else None,
            "PE_LTP": pe.ltp if pe else None,
            "PE_change": pe.change if pe else None,
            "PE_volume": pe.volume if pe else None,
            "PE_OI": pe.oi if pe else None,
            "PE_IV": pe.iv if pe else None,
            "PE_bid": pe.bid if pe else None,
            "PE_ask": pe.ask if pe else None,
        })
    return table


# ── 2. Formula Audit Table ─────────────────────────────────────────────────

def formula_audit_table(
    rows: list[CalculatedRow],
    extrapolated_ce: Optional[list[ExtrapolatedStrike]] = None,
    extrapolated_pe: Optional[list[ExtrapolatedStrike]] = None,
) -> list[dict]:
    """Generate formula audit table with all derived metrics per strike.

    Columns: strike, distance, CE_intrinsic, CE_extrinsic, PE_intrinsic, PE_extrinsic,
             CE_decay_ratio, PE_decay_ratio, liquidity_skew, CE_spread_pct, PE_spread_pct,
             CE_slope, PE_slope, CE_theta, PE_theta,
             CE_band, PE_band, CE_score, PE_score, source
    """
    table: list[dict] = []

    for r in rows:
        table.append({
            "strike": r.strike,
            "distance": round(r.distance, 2),
            "CE_LTP": r.call_ltp,
            "PE_LTP": r.put_ltp,
            "CE_intrinsic": round(r.call_intrinsic, 2),
            "CE_extrinsic": round(r.call_extrinsic, 2),
            "PE_intrinsic": round(r.put_intrinsic, 2),
            "PE_extrinsic": round(r.put_extrinsic, 2),
            "CE_decay_ratio": _r4(r.call_decay_ratio),
            "PE_decay_ratio": _r4(r.put_decay_ratio),
            "liquidity_skew": _r4(r.liquidity_skew),
            "CE_spread_pct": _r4(r.call_spread_pct),
            "PE_spread_pct": _r4(r.put_spread_pct),
            "CE_slope": _r6(r.call_slope),
            "PE_slope": _r6(r.put_slope),
            "CE_theta_density": _r6(r.call_theta_density),
            "PE_theta_density": _r6(r.put_theta_density),
            "CE_band_eligible": r.call_band_eligible,
            "PE_band_eligible": r.put_band_eligible,
            "CE_score": _r4(r.call_candidate_score),
            "PE_score": _r4(r.put_candidate_score),
            "CE_score_components": r.call_score_components,
            "PE_score_components": r.put_score_components,
            "source": "VISIBLE",
        })

    # Append extrapolated strikes
    for ext in (extrapolated_ce or []):
        table.append({
            "strike": ext.strike,
            "distance": None,
            "CE_LTP": ext.adjusted_premium,
            "PE_LTP": None,
            "CE_intrinsic": 0,
            "CE_extrinsic": ext.adjusted_premium,
            "PE_intrinsic": None,
            "PE_extrinsic": None,
            "CE_decay_ratio": None,
            "PE_decay_ratio": None,
            "liquidity_skew": None,
            "CE_spread_pct": None,
            "PE_spread_pct": None,
            "CE_slope": None,
            "PE_slope": None,
            "CE_theta_density": None,
            "PE_theta_density": None,
            "CE_band_eligible": ext.in_band,
            "PE_band_eligible": False,
            "CE_score": _r4(ext.score),
            "PE_score": None,
            "CE_score_components": ext.score_components,
            "PE_score_components": None,
            "source": "EXTRAPOLATED",
            "est_premium": ext.estimated_premium,
            "adj_premium": ext.adjusted_premium,
            "alpha": ext.alpha_used,
            "steps_from_atm": ext.steps_from_atm,
        })

    for ext in (extrapolated_pe or []):
        table.append({
            "strike": ext.strike,
            "distance": None,
            "CE_LTP": None,
            "PE_LTP": ext.adjusted_premium,
            "CE_intrinsic": None,
            "CE_extrinsic": None,
            "PE_intrinsic": 0,
            "PE_extrinsic": ext.adjusted_premium,
            "CE_decay_ratio": None,
            "PE_decay_ratio": None,
            "liquidity_skew": None,
            "CE_spread_pct": None,
            "PE_spread_pct": None,
            "CE_slope": None,
            "PE_slope": None,
            "CE_theta_density": None,
            "PE_theta_density": None,
            "CE_band_eligible": False,
            "PE_band_eligible": ext.in_band,
            "CE_score": None,
            "PE_score": _r4(ext.score),
            "CE_score_components": None,
            "PE_score_components": ext.score_components,
            "source": "EXTRAPOLATED",
            "est_premium": ext.estimated_premium,
            "adj_premium": ext.adjusted_premium,
            "alpha": ext.alpha_used,
            "steps_from_atm": ext.steps_from_atm,
        })

    return sorted(table, key=lambda r: r["strike"])


# ── 3. Quality Check Table ─────────────────────────────────────────────────

def quality_table(report: QualityReport) -> list[dict]:
    """Generate quality check results table.

    Columns: check_name, status, threshold, observed, result, reason
    """
    table: list[dict] = []
    for check in report.checks:
        table.append({
            "check_name": check.check_name,
            "status": check.status.value,
            "threshold": check.threshold,
            "observed": check.observed,
            "result": "PASS" if check.result else "FAIL",
            "reason": check.reason,
        })
    # Summary row
    table.append({
        "check_name": "OVERALL",
        "status": report.overall_status.value,
        "threshold": "",
        "observed": f"score={report.quality_score}",
        "result": report.overall_status.value,
        "reason": "",
    })
    return table


# ── 4. Signal Table ────────────────────────────────────────────────────────

def signal_table(signals: list[SignalEvent]) -> list[dict]:
    """Generate signal history table.

    Columns: timestamp, side_bias, zone, state, strike, premium,
             trigger, validity, rejection
    """
    table: list[dict] = []
    for sig in signals:
        table.append({
            "timestamp": sig.timestamp.isoformat(),
            "side_bias": sig.side_bias.value if sig.side_bias else None,
            "zone": sig.zone,
            "machine_state": sig.machine_state.value,
            "selected_strike": sig.selected_strike,
            "selected_type": sig.selected_option_type.value if sig.selected_option_type else None,
            "selected_premium": sig.selected_premium,
            "trigger_status": sig.trigger_status,
            "validity": sig.validity.value,
            "rejection_reason": sig.rejection_reason.value if sig.rejection_reason else None,
            "rejection_detail": sig.rejection_detail,
            "spot_ltp": sig.spot_ltp,
            "snapshot_id": sig.snapshot_id,
        })
    return table


# ── 5. Paper Trade Table ──────────────────────────────────────────────────

def trade_table(trades: list[PaperTrade]) -> list[dict]:
    """Generate paper trade table.

    Columns: trade_id, entry_time, side, strike, type, entry, qty, SL,
             T1, T2, T3, exit, pnl, charges, capital, status, reason
    """
    table: list[dict] = []
    for t in trades:
        table.append({
            "trade_id": t.trade_id,
            "entry_time": t.timestamp_entry.isoformat(),
            "exit_time": t.timestamp_exit.isoformat() if t.timestamp_exit else None,
            "side": t.side.value,
            "symbol": t.symbol,
            "strike": t.strike,
            "option_type": t.option_type.value,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "qty": t.qty,
            "lots": t.lots,
            "sl": t.sl,
            "t1": t.t1,
            "t2": t.t2,
            "t3": t.t3,
            "pnl": t.pnl,
            "charges": t.charges,
            "capital_before": t.capital_before,
            "capital_after": t.capital_after,
            "status": t.status.value,
            "reason_entry": t.reason_entry,
            "reason_exit": t.reason_exit.value if t.reason_exit else None,
            "exit_detail": t.exit_detail,
        })
    return table


# ── 6. Capital Summary Table ──────────────────────────────────────────────

def capital_table(ledger: list[CapitalLedgerEntry]) -> list[dict]:
    """Generate capital ledger table."""
    table: list[dict] = []
    for e in ledger:
        table.append({
            "timestamp": e.timestamp.isoformat(),
            "event": e.event,
            "trade_id": e.trade_id,
            "amount": e.amount,
            "running_capital": e.running_capital,
            "realized_pnl": e.realized_pnl,
            "daily_pnl": e.daily_pnl,
            "drawdown": e.drawdown,
            "peak_capital": e.peak_capital,
        })
    return table


# ── 7. Candidate Comparison Table ─────────────────────────────────────────

def candidate_table(candidates: list[ScoredCandidate]) -> list[dict]:
    """Generate scored candidates table for display."""
    table: list[dict] = []
    for c in candidates:
        table.append({
            "strike": c.strike,
            "option_type": c.option_type.value,
            "ltp": c.ltp,
            "score": c.score,
            "band_fit": c.band_fit,
            "spread_pct": c.spread_pct,
            "volume": c.volume,
            "distance": c.distance,
            "source": c.source,
            "f_dist": c.components.get("f_dist"),
            "f_mom": c.components.get("f_mom"),
            "f_liq": c.components.get("f_liq"),
            "f_band": c.components.get("f_band"),
            "bias": c.components.get("bias"),
        })
    return sorted(table, key=lambda r: (r["option_type"], r["strike"]))


# ── Helpers ────────────────────────────────────────────────────────────────

def _r4(val: Optional[float]) -> Optional[float]:
    return round(val, 4) if val is not None else None


def _r6(val: Optional[float]) -> Optional[float]:
    return round(val, 6) if val is not None else None

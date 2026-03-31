#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# Try to import from centralized config
try:
    # Add shared_project_engine to path
    _PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))

    from shared_project_engine.indices import ACTIVE_INDICES, INDEX_CONFIG
    from shared_project_engine.indices.config import MONTHLY_EXPIRY_DATES

    DEFAULT_INDICES = ACTIVE_INDICES
    INDEX_EXPIRY_WEEKDAY = {name: cfg["expiry_weekday"] for name, cfg in INDEX_CONFIG.items()}
    _USING_SHARED_CONFIG = True
except ImportError:
    # Fallback to local constants
    _PROJECT_ROOT = Path(__file__).resolve().parents[4]
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))
    DEFAULT_INDICES = ["SENSEX", "NIFTY50", "BANKNIFTY"]
    INDEX_EXPIRY_WEEKDAY = {
        "SENSEX": 3,      # Thursday
        "BANKNIFTY": 2,   # Wednesday
        "NIFTY": 3,       # Thursday
        "NIFTY50": 3,     # Thursday
        "FINNIFTY": 1,    # Tuesday
        "MIDCPNIFTY": 0,  # Monday
    }
    MONTHLY_EXPIRY_DATES = {}
    _USING_SHARED_CONFIG = False

from core.utils import to_float, parse_dt as _parse_dt


def parse_dt(date_s: str, time_s: str) -> dt.datetime:
    """Wrapper: returns datetime.min instead of None for sort compatibility."""
    return _parse_dt(date_s, time_s) or dt.datetime.min

EVENT_COLS = ["Time", "Type", "Side", "Strike", "Entry", "Exit", "Reason"]
PAPER_TRADE_COLS = [
    "ID", "Time", "Side", "Strike", "Qty", "Entry", "Exit", "Reason", "P&L", "Hold", "Result"
]

# Monthly expiry dates - loaded from shared_project_engine/indices/config.py
# Fallback only if import failed above
if not _USING_SHARED_CONFIG:
    MONTHLY_EXPIRY_DATES = {
        "2026-03": {
            "SENSEX": "2026-03-12",
            "BANKNIFTY": "2026-03-30",
            "NIFTY": "2026-03-10",
            "NIFTY50": "2026-03-10",
            "FINNIFTY": "2026-03-10",
            "MIDCPNIFTY": "2026-03-09",
        },
    }
TABLE1_COLS = [
    "Time",
    "Side",
    "Strike",
    "StrPCR",
    "Level",
    "Entry",
    "SL",
    "T1",
    "T2",
    "Conf",
    "Status",
    "Score",
    "Action",
    "Stable",
    "CooldownS",
    "EntryReady",
    "Selected",
]
TABLE2_COLS = [
    "Time",
    "Side",
    "Strike",
    "Bid",
    "Ask",
    "Spr%",
    "IV%",
    "Delta",
    "Gamma",
    "ThetaD",
    "Decay%",
    "VoteCE",
    "VotePE",
    "VoteSide",
    "VoteDiff",
    "LearnP",
    "LearnGate",
]
TABLE3_COLS = ["Time", "Side", "Strike", "VolDom", "VolSwitch", "Note"]
MERGED_COLS = [
    "Time",
    "Side",
    "Strike",
    "StrPCR",
    "Level",
    "Entry",
    "SL",
    "T1",
    "T2",
    "Conf",
    "Status",
    "Score",
    "Action",
    "Stable",
    "CooldownS",
    "EntryReady",
    "Selected",
    "Bid",
    "Ask",
    "Spr%",
    "IV%",
    "Delta",
    "Gamma",
    "ThetaD",
    "Decay%",
    "VoteCE",
    "VotePE",
    "VoteSide",
    "VoteDiff",
    "LearnP",
    "LearnGate",
    "VolDom",
    "VolSwitch",
    "Note",
]


def ist_now() -> dt.datetime:
    return dt.datetime.now(ZoneInfo("Asia/Kolkata"))


def ist_today() -> str:
    return ist_now().strftime("%Y-%m-%d")


def get_session_status(now: dt.datetime) -> Tuple[str, str]:
    """Returns (status_text, css_class) for current trading session."""
    h, m = now.hour, now.minute
    if h < 9 or (h == 9 and m < 15):
        return "PRE-MARKET", "session-pre"
    elif h < 15 or (h == 15 and m <= 30):
        return "ACTIVE", "session-active"
    else:
        return "CLOSED", "session-closed"


def get_actual_expiry_date(now: dt.datetime, index: str) -> Optional[dt.date]:
    """Get actual expiry date from MONTHLY_EXPIRY_DATES config."""
    month_key = now.strftime("%Y-%m")
    idx_upper = index.upper()
    if idx_upper == "NIFTY50":
        idx_upper = "NIFTY"

    monthly = MONTHLY_EXPIRY_DATES.get(month_key, {})
    date_str = monthly.get(idx_upper) or monthly.get(index.upper())
    if date_str:
        try:
            return dt.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def is_expiry_day(now: dt.datetime, index: str) -> bool:
    """Check if today is expiry day for the index."""
    actual_expiry = get_actual_expiry_date(now, index)
    if actual_expiry:
        return now.date() == actual_expiry
    # Fallback to weekday-based check
    expiry_weekday = INDEX_EXPIRY_WEEKDAY.get(index.upper(), 3)
    return now.weekday() == expiry_weekday


def estimate_time_to_expiry(now: dt.datetime, index: str) -> str:
    """Estimate time remaining to expiry (15:30 on expiry day)."""
    # Try to use actual expiry date from config
    actual_expiry = get_actual_expiry_date(now, index)

    if actual_expiry:
        today = now.date()
        days_until = (actual_expiry - today).days

        if days_until < 0:
            return "EXPIRED"
        elif days_until == 0:
            # Today is expiry day
            expiry_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
            if now >= expiry_time:
                return "EXPIRED"
            diff = expiry_time - now
            hours, remainder = divmod(int(diff.total_seconds()), 3600)
            minutes = remainder // 60
            return f"{hours}h {minutes}m"
        else:
            return f"{days_until}d"

    # Fallback to weekday-based estimate (weekly expiry assumption)
    expiry_weekday = INDEX_EXPIRY_WEEKDAY.get(index.upper(), 3)
    current_weekday = now.weekday()
    days_until = (expiry_weekday - current_weekday) % 7
    if days_until == 0:
        expiry_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if now >= expiry_time:
            return "EXPIRED"
        diff = expiry_time - now
        hours, remainder = divmod(int(diff.total_seconds()), 3600)
        minutes = remainder // 60
        return f"{hours}h {minutes}m"
    else:
        return f"~{days_until}d"  # Prefix with ~ to indicate estimate


def extract_market_context(rows: List[Dict[str, str]], index: str) -> Dict[str, Any]:
    """Extract market context from the first signal row."""
    now = ist_now()
    session_status, session_class = get_session_status(now)
    is_expiry = is_expiry_day(now, index)
    time_to_expiry = estimate_time_to_expiry(now, index)

    ctx = {
        "spot": 0.0,
        "vix": 0.0,
        "net_pcr": 0.0,
        "max_pain": 0,
        "max_pain_dist": 0.0,
        "fut_basis": 0.0,
        "fut_basis_pct": 0.0,
        "session_status": session_status,
        "session_class": session_class,
        "is_expiry": is_expiry,
        "time_to_expiry": time_to_expiry,
        "approved_count": 0,
        "best_entry": None,
        "total_signals": len(rows),
    }

    if not rows:
        return ctx

    # Extract from first row (all rows in a batch have same market context)
    r = rows[0]
    ctx["spot"] = to_float(r.get("spot", "0"))
    ctx["vix"] = to_float(r.get("vix", "0"))
    ctx["net_pcr"] = to_float(r.get("net_pcr", "0"))
    ctx["max_pain"] = int(to_float(r.get("max_pain", "0")))
    ctx["max_pain_dist"] = to_float(r.get("max_pain_dist", "0"))
    ctx["fut_basis"] = to_float(r.get("fut_basis", "0"))
    ctx["fut_basis_pct"] = to_float(r.get("fut_basis_pct", "0"))

    # Count approved and find best entry
    best_score = -1
    for row in rows:
        status = row.get("status", "").upper()
        if status == "APPROVED":
            ctx["approved_count"] += 1
            entry_ready = row.get("entry_ready", "").upper() == "Y"
            score_str = row.get("score", "0/100")
            try:
                score = int(score_str.split("/")[0])
            except (ValueError, IndexError):
                score = 0
            if entry_ready and score > best_score:
                best_score = score
                ctx["best_entry"] = {
                    "strike": row.get("strike", ""),
                    "side": row.get("side", ""),
                    "entry": row.get("entry", ""),
                    "score": score_str,
                }

    return ctx


def pcr_sentiment(pcr: float) -> Tuple[str, str]:
    """Returns (sentiment_text, css_class) based on PCR value."""
    if pcr >= 1.5:
        return "Very Bullish", "pcr-bull-strong"
    elif pcr >= 1.2:
        return "Bullish", "pcr-bull"
    elif pcr >= 0.8:
        return "Neutral", "pcr-neutral"
    elif pcr >= 0.5:
        return "Bearish", "pcr-bear"
    else:
        return "Very Bearish", "pcr-bear-strong"


def pcr_level_label(pcr: float) -> str:
    if pcr >= 5.0:
        return "SUP++"
    if pcr >= 2.0:
        return "SUP+"
    if pcr >= 1.2:
        return "SUP"
    if pcr >= 0.8:
        return "NEUT"
    if pcr >= 0.5:
        return "RES"
    if pcr >= 0.2:
        return "RES+"
    return "RES++"


def fmt_num(v: str, digits: int, blank_if_non_positive: bool = False) -> str:
    x = to_float(v, 0.0)
    if blank_if_non_positive and x <= 0:
        return ""
    return f"{x:.{digits}f}"


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        return [], []

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        headers = [h.strip() for h in (reader.fieldnames or []) if h]
        rows: List[Dict[str, str]] = []
        for r in reader:
            row: Dict[str, str] = {}
            for k, v in r.items():
                if not k:
                    continue
                row[k.strip()] = (v or "").strip()
            rows.append(row)
        return headers, rows


def dedupe_rows(rows: List[Dict[str, str]], cols: List[str]) -> List[Dict[str, str]]:
    seen = set()
    out: List[Dict[str, str]] = []
    for row in rows:
        key = tuple((c, row.get(c, "")) for c in cols)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def latest_batch(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if not rows:
        return []

    ranked = []
    for i, r in enumerate(rows):
        d = r.get("date", "")
        t = r.get("time", "")
        ranked.append((parse_dt(d, t), i, d, t))

    ranked.sort()
    _, _, latest_date, latest_time = ranked[-1]

    out = [r for r in rows if r.get("date", "") == latest_date and r.get("time", "") == latest_time]
    cols = sorted({k for row in out for k in row.keys()})
    return dedupe_rows(out, cols)


def signal_rows_only(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out = []
    for r in rows:
        side = (r.get("side", "") or "").upper()
        strike = (r.get("strike", "") or "").strip()
        if side in {"CE", "PE"} and strike:
            out.append(r)
    return out


def signal_row_to_merged_map(r: Dict[str, str]) -> Dict[str, str]:
    strike_pcr = to_float(r.get("strike_pcr", "0"), 0.0)
    return {
        "Time": r.get("time", "")[-8:],
        "Side": r.get("side", ""),
        "Strike": r.get("strike", ""),
        "StrPCR": f"{strike_pcr:.2f}",
        "Level": pcr_level_label(strike_pcr),
        "Entry": r.get("entry", ""),
        "SL": r.get("sl", ""),
        "T1": r.get("t1", ""),
        "T2": r.get("t2", ""),
        "Conf": r.get("confidence", ""),
        "Status": r.get("status", ""),
        "Score": r.get("score", ""),
        "Action": r.get("action", ""),
        "Stable": r.get("stable", ""),
        "CooldownS": r.get("cooldown_sec", ""),
        "EntryReady": r.get("entry_ready", ""),
        "Selected": r.get("selected", ""),
        "Bid": fmt_num(r.get("bid", ""), 2, blank_if_non_positive=True),
        "Ask": fmt_num(r.get("ask", ""), 2, blank_if_non_positive=True),
        "Spr%": fmt_num(r.get("spread_pct", ""), 2),
        "IV%": fmt_num(r.get("iv", ""), 2),
        "Delta": fmt_num(r.get("delta", ""), 3),
        "Gamma": fmt_num(r.get("gamma", ""), 5),
        "ThetaD": fmt_num(r.get("theta_day", ""), 3),
        "Decay%": fmt_num(r.get("decay_pct", ""), 2),
        "VoteCE": r.get("vote_ce", ""),
        "VotePE": r.get("vote_pe", ""),
        "VoteSide": r.get("vote_side", ""),
        "VoteDiff": r.get("vote_diff", ""),
        "LearnP": fmt_num(r.get("learn_prob", ""), 2, blank_if_non_positive=True),
        "LearnGate": r.get("learn_gate", ""),
        "VolDom": r.get("vol_dom", ""),
        "VolSwitch": r.get("vol_switch", ""),
        "Note": r.get("reason", ""),
    }


def get_row_classes(r: Dict[str, str]) -> str:
    """Determine CSS classes for a row based on its values."""
    classes = []

    # Status-based styling
    status = r.get("status", "").upper()
    if status == "APPROVED":
        classes.append("row-approved")
    elif status == "PREFILTER":
        classes.append("row-prefilter")

    # Entry ready highlight
    entry_ready = r.get("entry_ready", "").upper() == "Y"
    if entry_ready and status == "APPROVED":
        classes.append("entry-ready")

    # Volume switch warning
    vol_switch = r.get("vol_switch", "").upper() == "Y"
    if vol_switch:
        classes.append("vol-switch")

    # High spread warning
    spread = to_float(r.get("spread_pct", "0"))
    if spread > 1.0:
        classes.append("high-spread")

    # High decay warning
    decay = to_float(r.get("decay_pct", "0"))
    if decay > 12.0:
        classes.append("high-decay")

    # Strong conviction
    vote_diff = to_float(r.get("vote_diff", "0"))
    if vote_diff >= 7:
        classes.append("strong-conviction")

    return " ".join(classes)


def make_merged_rows_with_classes(rows: List[Dict[str, str]]) -> List[Tuple[List[str], str]]:
    """Returns list of (row_data, row_css_classes) tuples with deduplication."""
    out: List[Tuple[List[str], str]] = []
    seen_keys = set()
    for r in rows:
        # Dedupe by time + side + strike (unique signal identifier)
        key = (r.get("time", ""), r.get("side", ""), r.get("strike", ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        mapped = signal_row_to_merged_map(r)
        row_data = [mapped.get(c, "") for c in MERGED_COLS]
        row_classes = get_row_classes(r)
        out.append((row_data, row_classes))
    return out


def make_merged_rows(rows: List[Dict[str, str]]) -> List[List[str]]:
    out: List[List[str]] = []
    for r in rows:
        mapped = signal_row_to_merged_map(r)
        out.append([mapped.get(c, "") for c in MERGED_COLS])
    return out


def render_styled_table(
    headers: List[str],
    rows_with_classes: List[Tuple[List[str], str]],
    table_key: str,
    empty_message: str = "No rows in latest batch",
    order_group: str = "",
) -> str:
    """Render table with row-level CSS classes for visual highlighting."""
    if not headers:
        return "<p class='empty'>No columns.</p>"

    head = "".join(
        f"<th draggable='true' class='draggable-col' data-col='{html.escape(h)}'>{html.escape(h)}</th>" for h in headers
    )

    if rows_with_classes:
        body = []
        for row_data, row_classes in rows_with_classes:
            class_attr = f" class='{html.escape(row_classes)}'" if row_classes else ""
            cells = "".join(f"<td>{html.escape(str(c))}</td>" for c in row_data)
            body.append(f"<tr{class_attr}>{cells}</tr>")
        tbody = "\n".join(body)
    else:
        tbody = f"<tr><td colspan='{len(headers)}'>{html.escape(empty_message)}</td></tr>"

    order_group_attr = html.escape(order_group or table_key)

    return (
        "<div class='table-wrap'>"
        f"<table class='reorderable' data-table-key='{html.escape(table_key)}' data-order-group='{order_group_attr}'><thead><tr>{head}</tr></thead><tbody>{tbody}</tbody></table>"
        "</div>"
    )


def render_text_table(
    headers: List[str],
    rows: List[List[str]],
    table_key: str,
    empty_message: str = "No rows in latest batch",
    order_group: str = "",
) -> str:
    if not headers:
        return "<p class='empty'>No columns.</p>"

    head = "".join(
        f"<th draggable='true' class='draggable-col' data-col='{html.escape(h)}'>{html.escape(h)}</th>" for h in headers
    )

    if rows:
        body = []
        for r in rows:
            cells = "".join(f"<td>{html.escape(str(c))}</td>" for c in r)
            body.append(f"<tr>{cells}</tr>")
        tbody = "\n".join(body)
    else:
        tbody = f"<tr><td colspan='{len(headers)}'>{html.escape(empty_message)}</td></tr>"

    order_group_attr = html.escape(order_group or table_key)

    return (
        "<div class='table-wrap'>"
        f"<table class='reorderable' data-table-key='{html.escape(table_key)}' data-order-group='{order_group_attr}'><thead><tr>{head}</tr></thead><tbody>{tbody}</tbody></table>"
        "</div>"
    )


def latest_signal_batch(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if not rows:
        return []

    ranked = []
    for i, r in enumerate(rows):
        d = r.get("date", "")
        t = r.get("time", "")
        ranked.append((parse_dt(d, t), i, d, t))

    ranked.sort(reverse=True)
    seen = set()
    for _, _, d, t in ranked:
        key = (d, t)
        if key in seen:
            continue
        seen.add(key)

        batch = [r for r in rows if r.get("date", "") == d and r.get("time", "") == t]
        sig = signal_rows_only(batch)
        if sig:
            cols = sorted({k for row in sig for k in row.keys()})
            return dedupe_rows(sig, cols)

    return []


def latest_events(rows: List[Dict[str, str]], limit: int) -> List[Dict[str, str]]:
    if not rows:
        return []

    ranked = []
    for i, r in enumerate(rows):
        d = r.get("event_date", "")
        t = r.get("event_time", "")
        ranked.append((parse_dt(d, t), i, r))

    ranked.sort()
    out = [x[2] for x in ranked[-max(1, limit) :]]
    return out


def latest_event_time(rows: List[Dict[str, str]]) -> str:
    if not rows:
        return ""

    ranked = []
    for i, r in enumerate(rows):
        d = r.get("event_date", "")
        t = r.get("event_time", "")
        ranked.append((parse_dt(d, t), i, d, t))

    ranked.sort()
    _, _, d, t = ranked[-1]
    return f"{d} {t}".strip()


def event_row_to_view_map(r: Dict[str, str]) -> Dict[str, str]:
    return {
        "Time": r.get("event_time", ""),
        "Type": r.get("event_type", ""),
        "Side": r.get("side", ""),
        "Strike": r.get("strike", ""),
        "Entry": r.get("entry", ""),
        "Exit": r.get("exit", ""),
        "Reason": r.get("reason", ""),
    }


def make_event_rows(rows: List[Dict[str, str]]) -> List[List[str]]:
    out: List[List[str]] = []
    for r in rows:
        mapped = event_row_to_view_map(r)
        out.append([mapped.get(c, "") for c in EVENT_COLS])
    return out


def read_paper_trades(path: Path) -> List[Dict[str, str]]:
    """Read paper trades CSV file with deduplication."""
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows = []
            seen_keys = set()
            for r in reader:
                row = {k.strip(): (v or "").strip() for k, v in r.items() if k}
                # Dedupe by trade_id + entry_time (unique trade identifier)
                key = (row.get("trade_id", ""), row.get("entry_time", ""), row.get("symbol", ""))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                rows.append(row)
            return rows
    except Exception:
        return []


def paper_trade_to_view_map(r: Dict[str, str]) -> Dict[str, str]:
    """Map paper trade row to display columns."""
    net_pnl = to_float(r.get("net_pnl", "0"))
    pnl_display = f"{net_pnl:+.2f}" if net_pnl != 0 else "0.00"
    hold_sec = int(to_float(r.get("hold_sec", "0")))
    hold_display = f"{hold_sec}s" if hold_sec > 0 else "-"

    return {
        "ID": r.get("trade_id", ""),
        "Time": r.get("entry_time", "")[-8:],
        "Side": r.get("side", ""),
        "Strike": r.get("strike", ""),
        "Qty": r.get("qty", ""),
        "Entry": fmt_num(r.get("entry_price", ""), 2),
        "Exit": fmt_num(r.get("exit_price", ""), 2),
        "Reason": r.get("exit_reason", ""),
        "P&L": pnl_display,
        "Hold": hold_display,
        "Result": r.get("result", ""),
    }


def make_paper_trade_rows(rows: List[Dict[str, str]]) -> List[Tuple[List[str], str]]:
    """Create paper trade rows with CSS classes for styling."""
    out: List[Tuple[List[str], str]] = []
    # Show most recent first
    for r in reversed(rows[-20:]):  # Last 20 trades
        mapped = paper_trade_to_view_map(r)
        row_data = [mapped.get(c, "") for c in PAPER_TRADE_COLS]

        # Determine row class based on result
        result = r.get("result", "").lower()
        net_pnl = to_float(r.get("net_pnl", "0"))
        if result == "win" or net_pnl > 0:
            row_class = "trade-win"
        elif result == "loss" or net_pnl < 0:
            row_class = "trade-loss"
        else:
            row_class = "trade-breakeven"

        out.append((row_data, row_class))
    return out


def make_paper_trade_rows_with_index(rows: List[Dict[str, str]]) -> List[Tuple[List[str], str]]:
    """Create paper trade rows with Index column for consolidated view."""
    out: List[Tuple[List[str], str]] = []
    for r in rows:
        mapped = paper_trade_to_view_map(r)
        idx_name = r.get("_index", r.get("symbol", ""))
        row_data = [idx_name] + [mapped.get(c, "") for c in PAPER_TRADE_COLS]

        # Determine row class based on result
        result = r.get("result", "").lower()
        net_pnl = to_float(r.get("net_pnl", "0"))
        if result == "win" or net_pnl > 0:
            row_class = "trade-win"
        elif result == "loss" or net_pnl < 0:
            row_class = "trade-loss"
        else:
            row_class = "trade-breakeven"

        out.append((row_data, row_class))
    return out


def compute_paper_trade_summary(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Compute paper trading summary statistics."""
    if not rows:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "total_fees": 0.0,
            "net_pnl": 0.0,
            "avg_hold_sec": 0,
            "current_capital": 0.0,
        }

    wins = sum(1 for r in rows if r.get("result", "").lower() == "win")
    losses = sum(1 for r in rows if r.get("result", "").lower() == "loss")
    total = len(rows)
    win_rate = (wins / total * 100) if total > 0 else 0.0

    total_pnl = sum(to_float(r.get("gross_pnl", "0")) for r in rows)
    total_fees = sum(to_float(r.get("fees", "0")) for r in rows)
    net_pnl = sum(to_float(r.get("net_pnl", "0")) for r in rows)

    hold_secs = [int(to_float(r.get("hold_sec", "0"))) for r in rows]
    avg_hold = sum(hold_secs) // len(hold_secs) if hold_secs else 0

    # Get current capital from last trade
    current_capital = to_float(rows[-1].get("capital_after", "0")) if rows else 0.0

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "total_fees": total_fees,
        "net_pnl": net_pnl,
        "avg_hold_sec": avg_hold,
        "current_capital": current_capital,
    }


def render_paper_trade_summary(summary: Dict[str, Any]) -> str:
    """Render paper trading summary panel."""
    total = summary.get("total_trades", 0)
    wins = summary.get("wins", 0)
    losses = summary.get("losses", 0)
    win_rate = summary.get("win_rate", 0.0)
    net_pnl = summary.get("net_pnl", 0.0)
    total_fees = summary.get("total_fees", 0.0)
    avg_hold = summary.get("avg_hold_sec", 0)
    capital = summary.get("current_capital", 0.0)

    pnl_class = "pnl-positive" if net_pnl > 0 else "pnl-negative" if net_pnl < 0 else ""

    if total == 0:
        return """
        <div class="paper-summary paper-summary-empty">
          <span class="summary-label">Paper Trading</span>
          <span class="summary-value">No trades yet</span>
        </div>
        """

    return f"""
    <div class="paper-summary">
      <div class="summary-item">
        <span class="summary-label">Trades</span>
        <span class="summary-value">{total} <small>({wins}W / {losses}L)</small></span>
      </div>
      <div class="summary-item">
        <span class="summary-label">Win Rate</span>
        <span class="summary-value">{win_rate:.1f}%</span>
      </div>
      <div class="summary-item">
        <span class="summary-label">Net P&L</span>
        <span class="summary-value {pnl_class}">{net_pnl:+,.2f}</span>
      </div>
      <div class="summary-item">
        <span class="summary-label">Fees</span>
        <span class="summary-value">{total_fees:,.2f}</span>
      </div>
      <div class="summary-item">
        <span class="summary-label">Avg Hold</span>
        <span class="summary-value">{avg_hold}s</span>
      </div>
      <div class="summary-item">
        <span class="summary-label">Capital</span>
        <span class="summary-value">{capital:,.2f}</span>
      </div>
    </div>
    """


def render_market_context(ctx: Dict[str, Any], index: str) -> str:
    """Render the market context header with key metrics."""
    spot = ctx.get("spot", 0.0)
    vix = ctx.get("vix", 0.0)
    net_pcr = ctx.get("net_pcr", 0.0)
    max_pain = ctx.get("max_pain", 0)
    max_pain_dist = ctx.get("max_pain_dist", 0.0)
    fut_basis = ctx.get("fut_basis", 0.0)
    fut_basis_pct = ctx.get("fut_basis_pct", 0.0)
    session_status = ctx.get("session_status", "UNKNOWN")
    session_class = ctx.get("session_class", "")
    is_expiry = ctx.get("is_expiry", False)
    time_to_expiry = ctx.get("time_to_expiry", "-")
    approved_count = ctx.get("approved_count", 0)
    best_entry = ctx.get("best_entry")
    total_signals = ctx.get("total_signals", 0)

    pcr_text, pcr_class = pcr_sentiment(net_pcr)

    # Best entry display
    best_entry_html = "<span class='no-entry'>-</span>"
    if best_entry:
        best_entry_html = (
            f"<span class='best-entry'>{html.escape(str(best_entry['strike']))}"
            f"{html.escape(str(best_entry['side']))} @{html.escape(str(best_entry['entry']))}</span>"
        )

    # Expiry badge
    expiry_badge = ""
    if is_expiry:
        expiry_badge = "<span class='expiry-badge'>EXPIRY DAY</span>"

    # Futures basis indicator
    basis_class = "basis-premium" if fut_basis > 0 else "basis-discount" if fut_basis < 0 else ""
    basis_label = "Premium" if fut_basis > 0 else "Discount" if fut_basis < 0 else "Flat"

    return f"""
    <div class="market-context">
      <div class="context-row">
        <div class="ctx-item">
          <span class="ctx-label">Spot</span>
          <span class="ctx-value">{spot:,.2f}</span>
        </div>
        <div class="ctx-item">
          <span class="ctx-label">VIX</span>
          <span class="ctx-value vix-value">{vix:.2f}</span>
        </div>
        <div class="ctx-item">
          <span class="ctx-label">PCR</span>
          <span class="ctx-value {pcr_class}">{net_pcr:.2f} <small>({pcr_text})</small></span>
        </div>
        <div class="ctx-item">
          <span class="ctx-label">Max Pain</span>
          <span class="ctx-value">{max_pain:,} <small>({max_pain_dist:+.0f})</small></span>
        </div>
        <div class="ctx-item">
          <span class="ctx-label">Fut Basis</span>
          <span class="ctx-value {basis_class}">{fut_basis:+.2f} <small>({fut_basis_pct:+.2f}% {basis_label})</small></span>
        </div>
      </div>
      <div class="context-row">
        <div class="ctx-item">
          <span class="ctx-label">Session</span>
          <span class="ctx-value {session_class}">{html.escape(session_status)}</span>
        </div>
        <div class="ctx-item">
          <span class="ctx-label">Expiry In</span>
          <span class="ctx-value">{html.escape(time_to_expiry)} {expiry_badge}</span>
        </div>
        <div class="ctx-item">
          <span class="ctx-label">Signals</span>
          <span class="ctx-value">{total_signals} total, <span class="approved-count">{approved_count} APPROVED</span></span>
        </div>
        <div class="ctx-item ctx-item-wide">
          <span class="ctx-label">Best Entry</span>
          {best_entry_html}
        </div>
      </div>
    </div>
    """


def load_signal_cache(path: Path) -> Dict[str, List[Dict[str, str]]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    out: Dict[str, List[Dict[str, str]]] = {}
    for idx, rows in data.items():
        if not isinstance(idx, str) or not isinstance(rows, list):
            continue
        cleaned: List[Dict[str, str]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            cleaned.append({str(k): str(v) for k, v in r.items() if k})
        if cleaned:
            out[idx] = cleaned
    return out


def save_signal_cache(path: Path, cache: Dict[str, List[Dict[str, str]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=True), encoding="utf-8")


def build_html(
    date_str: str,
    source_file: str,
    events_file: str,
    refresh_sec: int,
    indices_order: List[str],
    per_index: Dict[str, Dict[str, object]],
) -> str:
    generated = ist_now().strftime("%Y-%m-%d %H:%M:%S IST")

    sections = []
    for idx in indices_order:
        item = per_index.get(idx)
        if item is None:
            continue

        rows_all = item["rows_latest"]
        rows_signal = item["rows_signal"]  # Raw dicts
        rows_events = item["rows_events"]
        last_event_time = str(item.get("last_event_time", ""))
        events_total = int(item.get("events_total", 0))

        # Extract market context from signal rows
        market_ctx = extract_market_context(rows_signal, idx)

        # Create styled rows with CSS classes
        merged_rows_styled = make_merged_rows_with_classes(rows_signal)
        event_rows = make_event_rows(rows_events)
        signal_source = str(item.get("signal_source", "latest"))
        latest_signal_time = "-"
        event_badge_text = "No events yet"
        event_badge_class = "event-badge event-badge-idle"

        latest_time = "-"
        if rows_all:
            latest_time = f"{rows_all[0].get('date', '')} {rows_all[0].get('time', '')}".strip()
        if rows_signal:
            latest_signal_time = f"{rows_signal[0].get('date', '')} {rows_signal[0].get('time', '')}".strip()
        if last_event_time:
            event_badge_text = f"Last event: {last_event_time}"
            event_badge_class = "event-badge event-badge-live"

        sec = [
            f"<section><h2>{html.escape(idx)} <span class='{event_badge_class}'>{html.escape(event_badge_text)}</span></h2>",
        ]

        # Add market context header
        sec.append(render_market_context(market_ctx, idx))

        sec.append(
            f"<p class='meta'>Latest batch: <code>{html.escape(latest_time)}</code> | "
            f"Signal batch: <code>{html.escape(latest_signal_time)}</code></p>"
        )

        if signal_source == "fallback":
            sec.append(
                "<p class='meta warning'>Latest batch had no CE/PE signal rows, "
                "showing most recent valid batch.</p>"
            )
        elif signal_source == "cache":
            sec.append(
                "<p class='meta warning'>Source files transiently empty, showing cached data.</p>"
            )

        sec.extend(
            [
            "<h3>Signal Table</h3>",
            "<p class='meta'>Drag column headers to reorder. <span class='legend'>Legend: "
            "<span class='legend-approved'>APPROVED</span> "
            "<span class='legend-entry-ready'>Entry Ready</span> "
            "<span class='legend-vol-switch'>Vol Switch</span> "
            "<span class='legend-high-spread'>High Spread</span> "
            "<span class='legend-high-decay'>High Decay</span></span></p>",
            render_styled_table(
                MERGED_COLS,
                merged_rows_styled,
                f"idx-{idx}-merged",
                empty_message="No signal rows available",
                order_group="merged_all_indices",
            ),
            "<h3>Opportunity Events</h3>",
            (
                f"<p class='meta'>Recent: {len(rows_events)} | Total: {events_total}</p>"
            ),
            render_text_table(
                EVENT_COLS,
                event_rows,
                f"idx-{idx}-events",
                empty_message="No opportunity events yet",
                order_group="events_all_indices",
            ),
            "</section>",
            ]
        )
        sections.append("\n".join(sec))

    # Build consolidated Paper Trading section at the bottom
    all_paper_trades: List[Dict[str, str]] = []
    total_summary = {
        "total_trades": 0, "wins": 0, "losses": 0,
        "total_pnl": 0.0, "total_fees": 0.0, "net_pnl": 0.0,
    }
    for idx in indices_order:
        item = per_index.get(idx)
        if item is None:
            continue
        trades = item.get("paper_trades", [])
        # Add index name to each trade for consolidated view
        for t in trades:
            t_copy = dict(t)
            t_copy["_index"] = idx
            all_paper_trades.append(t_copy)
        summary = item.get("paper_summary", {})
        total_summary["total_trades"] += summary.get("total_trades", 0)
        total_summary["wins"] += summary.get("wins", 0)
        total_summary["losses"] += summary.get("losses", 0)
        total_summary["total_pnl"] += summary.get("total_pnl", 0.0)
        total_summary["total_fees"] += summary.get("total_fees", 0.0)
        total_summary["net_pnl"] += summary.get("net_pnl", 0.0)

    total_summary["win_rate"] = (
        (total_summary["wins"] / total_summary["total_trades"] * 100)
        if total_summary["total_trades"] > 0 else 0.0
    )

    # Sort all trades by time (newest first)
    all_paper_trades.sort(
        key=lambda x: (x.get("entry_date", ""), x.get("entry_time", "")),
        reverse=True
    )
    consolidated_rows = make_paper_trade_rows_with_index(all_paper_trades[:30])

    paper_section = [
        "<section class='paper-section'><h2>Paper Trading (All Indices)</h2>",
        render_paper_trade_summary(total_summary),
        "<p class='meta'>Last 30 trades across all indices (newest first):</p>",
        render_styled_table(
            ["Index"] + PAPER_TRADE_COLS,
            consolidated_rows,
            "consolidated-paper",
            empty_message="No paper trades yet. Start with: scripts/start_all.sh paper",
            order_group="paper_consolidated",
        ),
        "</section>",
    ]
    sections.append("\n".join(paper_section))

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <meta http-equiv=\"refresh\" content=\"{refresh_sec}\">
  <title>Live Multi-Index Signal View</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #111827;
      --muted: #6b7280;
      --line: #d1d5db;
      --head: #e5e7eb;
      --green: #16a34a;
      --green-bg: #dcfce7;
      --red: #dc2626;
      --red-bg: #fee2e2;
      --orange: #ea580c;
      --orange-bg: #ffedd5;
      --blue: #2563eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 18px;
      background: linear-gradient(180deg, #eef2ff 0%, var(--bg) 60%);
      color: var(--text);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
    }}
    .shell {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 16px;
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    h2 {{ margin: 16px 0 8px; font-size: 20px; border-bottom: 2px solid var(--line); padding-bottom: 8px; }}
    h3 {{ margin: 12px 0 6px; font-size: 15px; color: #0f172a; }}

    /* Market Context Header */
    .market-context {{
      background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
      border: 1px solid #bae6fd;
      border-radius: 10px;
      padding: 12px 16px;
      margin-bottom: 12px;
    }}
    .context-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      margin-bottom: 8px;
    }}
    .context-row:last-child {{ margin-bottom: 0; }}
    .ctx-item {{
      display: flex;
      flex-direction: column;
      min-width: 100px;
    }}
    .ctx-item-wide {{ min-width: 180px; }}
    .ctx-label {{
      font-size: 11px;
      color: #64748b;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 2px;
    }}
    .ctx-value {{
      font-size: 15px;
      font-weight: 600;
      color: #0f172a;
    }}
    .ctx-value small {{ font-weight: 400; color: #64748b; font-size: 12px; }}

    /* PCR Sentiment Colors */
    .pcr-bull-strong {{ color: #16a34a; }}
    .pcr-bull {{ color: #22c55e; }}
    .pcr-neutral {{ color: #64748b; }}
    .pcr-bear {{ color: #f97316; }}
    .pcr-bear-strong {{ color: #dc2626; }}

    /* Session Status */
    .session-active {{ color: #16a34a; font-weight: 700; }}
    .session-pre {{ color: #f59e0b; }}
    .session-closed {{ color: #94a3b8; }}

    /* Futures Basis */
    .basis-premium {{ color: #16a34a; }}
    .basis-discount {{ color: #dc2626; }}

    /* Expiry Badge */
    .expiry-badge {{
      display: inline-block;
      background: #fef3c7;
      color: #92400e;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 700;
      margin-left: 6px;
      animation: pulse 2s infinite;
    }}
    @keyframes pulse {{
      0%, 100% {{ opacity: 1; }}
      50% {{ opacity: 0.7; }}
    }}

    /* Best Entry */
    .best-entry {{
      color: #16a34a;
      font-weight: 700;
      background: #dcfce7;
      padding: 2px 8px;
      border-radius: 4px;
    }}
    .no-entry {{ color: #94a3b8; }}
    .approved-count {{ color: #16a34a; font-weight: 600; }}

    /* Event badges */
    .event-badge {{
      display: inline-block;
      margin-left: 8px;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      vertical-align: middle;
      font-weight: 600;
      border: 1px solid transparent;
    }}
    .event-badge-live {{
      background: #dcfce7;
      color: #166534;
      border-color: #86efac;
    }}
    .event-badge-idle {{
      background: #f1f5f9;
      color: #475569;
      border-color: #cbd5e1;
    }}

    .meta {{ margin: 0 0 10px; color: var(--muted); font-size: 13px; }}
    .meta code {{ color: #0f766e; font-weight: 600; }}
    .meta.warning {{ color: #b45309; background: #fef3c7; padding: 4px 8px; border-radius: 4px; }}

    /* Legend */
    .legend {{ font-size: 11px; }}
    .legend-approved {{ background: var(--green-bg); color: var(--green); padding: 1px 6px; border-radius: 3px; }}
    .legend-entry-ready {{ background: var(--green-bg); border: 2px solid var(--green); padding: 1px 6px; border-radius: 3px; }}
    .legend-vol-switch {{ background: var(--red-bg); color: var(--red); padding: 1px 6px; border-radius: 3px; }}
    .legend-high-spread {{ background: var(--orange-bg); color: var(--orange); padding: 1px 6px; border-radius: 3px; }}
    .legend-high-decay {{ background: #fecaca; color: #b91c1c; padding: 1px 6px; border-radius: 3px; }}

    /* Paper Trading Summary */
    .paper-summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      background: linear-gradient(135deg, #fefce8 0%, #fef9c3 100%);
      border: 1px solid #fde047;
      border-radius: 10px;
      padding: 12px 16px;
      margin-bottom: 12px;
    }}
    .paper-summary-empty {{
      background: #f8fafc;
      border-color: #e2e8f0;
    }}
    .summary-item {{
      display: flex;
      flex-direction: column;
      min-width: 80px;
    }}
    .summary-label {{
      font-size: 11px;
      color: #64748b;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .summary-value {{
      font-size: 15px;
      font-weight: 600;
      color: #0f172a;
    }}
    .summary-value small {{ font-weight: 400; color: #64748b; font-size: 12px; }}
    .pnl-positive {{ color: #16a34a; }}
    .pnl-negative {{ color: #dc2626; }}

    /* Paper Trade Rows */
    tr.trade-win {{ background: #f0fdf4 !important; }}
    tr.trade-win:nth-child(even) {{ background: #dcfce7 !important; }}
    tr.trade-loss {{ background: #fef2f2 !important; }}
    tr.trade-loss:nth-child(even) {{ background: #fee2e2 !important; }}
    tr.trade-breakeven {{ background: #f8fafc !important; }}

    .table-wrap {{
      overflow-x: auto;
      overflow-y: auto;
      max-height: 420px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
    }}
    table {{
      border-collapse: collapse;
      width: max-content;
      min-width: 100%;
      font-size: 12px;
    }}
    th, td {{
      border: 1px solid var(--line);
      padding: 4px 8px;
      white-space: nowrap;
      text-align: left;
    }}
    thead th {{
      position: sticky;
      top: 0;
      background: var(--head);
      z-index: 2;
    }}
    .draggable-col {{ cursor: grab; user-select: none; }}
    .draggable-col.dragging {{ opacity: 0.45; }}
    .draggable-col.drop-target {{ outline: 2px dashed #0284c7; outline-offset: -2px; }}
    tbody tr:nth-child(even) {{ background: #f9fafb; }}

    /* Row-level styling */
    tr.row-approved {{ background: #f0fdf4 !important; }}
    tr.row-approved:nth-child(even) {{ background: #dcfce7 !important; }}
    tr.row-prefilter {{ opacity: 0.7; }}
    tr.entry-ready {{
      outline: 2px solid #16a34a;
      outline-offset: -2px;
      font-weight: 600;
    }}
    tr.vol-switch {{
      background: #fef2f2 !important;
    }}
    tr.vol-switch td:first-child::before {{
      content: "⚠ ";
      color: #dc2626;
    }}
    tr.high-spread td {{
      color: #ea580c;
    }}
    tr.high-decay td {{
      color: #b91c1c;
    }}
    tr.strong-conviction {{
      font-weight: 600;
    }}
    .empty {{ color: var(--muted); }}
  </style>
</head>
<body>
  <div class=\"shell\">
    <h1>Live Multi-Index Signal View</h1>
    <p class="meta">Date folder: <code>{html.escape(date_str)}</code> | Signal source: <code>{html.escape(source_file)}</code> | Opportunity source: <code>{html.escape(events_file)}</code> | Generated: <code>{html.escape(generated)}</code> | Page auto-refresh: every <code>{refresh_sec}s</code></p>
    {''.join(sections)}
  </div>
    <script>
        (function () {{
            const STORAGE_PREFIX = "live_signal_col_order::";
            const tables = Array.from(document.querySelectorAll("table.reorderable"));

            function getOrderGroup(table) {{
                return table.dataset.orderGroup || table.dataset.tableKey || "default";
            }}

            function getColName(th) {{
                return th.dataset.col || th.textContent.trim();
            }}

            function getHeaderNames(table) {{
                const headerRow = table.tHead && table.tHead.rows && table.tHead.rows[0];
                if (!headerRow) return [];
                return Array.from(headerRow.cells).map(getColName);
            }}

            function reorderColumnsByNames(table, orderedNames) {{
                const headerRow = table.tHead && table.tHead.rows && table.tHead.rows[0];
                if (!headerRow) return;

                const currentNames = getHeaderNames(table);
                const completeOrder = orderedNames.slice();
                currentNames.forEach((name) => {{
                    if (!completeOrder.includes(name)) completeOrder.push(name);
                }});

                const sourceIndex = new Map();
                currentNames.forEach((name, idx) => sourceIndex.set(name, idx));
                const orderIndex = completeOrder
                    .map((name) => sourceIndex.get(name))
                    .filter((idx) => Number.isInteger(idx));

                if (!orderIndex.length || orderIndex.length !== currentNames.length) return;

                Array.from(table.rows).forEach((row) => {{
                    const cells = Array.from(row.cells);
                    if (cells.length !== currentNames.length) return;
                    orderIndex.forEach((srcIdx) => row.appendChild(cells[srcIdx]));
                }});
            }}

            function applyOrderToGroup(group, orderedNames) {{
                tables
                    .filter((table) => getOrderGroup(table) === group)
                    .forEach((table) => reorderColumnsByNames(table, orderedNames));
            }}

            function saveOrder(group, orderedNames) {{
                localStorage.setItem(STORAGE_PREFIX + group, JSON.stringify(orderedNames));
            }}

            function loadOrder(group) {{
                const raw = localStorage.getItem(STORAGE_PREFIX + group);
                if (!raw) return [];
                try {{
                    const names = JSON.parse(raw);
                    if (Array.isArray(names) && names.length > 0) return names;
                }} catch (_e) {{
                    // Ignore malformed saved state.
                }}
                return [];
            }}

            function moveColumn(table, fromIdx, toIdx) {{
                if (fromIdx === toIdx || fromIdx < 0 || toIdx < 0) return;
                Array.from(table.rows).forEach((row) => {{
                    const cells = Array.from(row.cells);
                    if (fromIdx >= cells.length || toIdx >= cells.length) return;

                    const moving = cells[fromIdx];
                    const anchor = cells[toIdx];
                    if (!moving || !anchor || moving === anchor) return;

                    if (fromIdx < toIdx) {{
                        row.insertBefore(moving, anchor.nextSibling);
                    }} else {{
                        row.insertBefore(moving, anchor);
                    }}
                }});
            }}

            function setupDnD(table) {{
                const headerRow = table.tHead && table.tHead.rows && table.tHead.rows[0];
                if (!headerRow) return;
                const group = getOrderGroup(table);

                let dragIndex = -1;

                function clearDropMarkers() {{
                    Array.from(headerRow.cells).forEach((th) => th.classList.remove("drop-target", "dragging"));
                }}

                Array.from(headerRow.cells).forEach((th) => {{
                    th.draggable = true;
                    th.style.webkitUserDrag = "element";

                    th.addEventListener("dragstart", (e) => {{
                        dragIndex = Array.from(headerRow.cells).indexOf(th);
                        th.classList.add("dragging");
                        if (e.dataTransfer) {{
                            e.dataTransfer.effectAllowed = "move";
                            e.dataTransfer.setData("text/plain", getColName(th));
                        }}
                    }});

                    th.addEventListener("dragover", (e) => {{
                        e.preventDefault();
                        if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
                        clearDropMarkers();
                        th.classList.add("drop-target");
                    }});

                    th.addEventListener("dragenter", (e) => {{
                        e.preventDefault();
                        clearDropMarkers();
                        th.classList.add("drop-target");
                    }});

                    th.addEventListener("dragleave", () => {{
                        th.classList.remove("drop-target");
                    }});

                    th.addEventListener("drop", (e) => {{
                        e.preventDefault();
                        const targetIndex = Array.from(headerRow.cells).indexOf(th);

                        if (dragIndex >= 0 && targetIndex >= 0 && dragIndex !== targetIndex) {{
                            moveColumn(table, dragIndex, targetIndex);
                            const ordered = getHeaderNames(table);
                            applyOrderToGroup(group, ordered);
                            saveOrder(group, ordered);
                        }}

                        dragIndex = -1;
                        clearDropMarkers();
                    }});

                    th.addEventListener("dragend", () => {{
                        dragIndex = -1;
                        clearDropMarkers();
                    }});
                }});
            }}

            const groups = Array.from(new Set(tables.map((table) => getOrderGroup(table))));
            groups.forEach((group) => {{
                const saved = loadOrder(group);
                if (saved.length > 0) applyOrderToGroup(group, saved);
            }});

            tables.forEach((table) => setupDnD(table));
        }})();
    </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate live HTML view for latest signal tables.")
    p.add_argument("--base-dir", default="postmortem", help="Base folder containing date/index folders")
    p.add_argument("--date", default=ist_today(), help="Date folder (YYYY-MM-DD), default IST today")
    p.add_argument("--indices", default=",".join(DEFAULT_INDICES), help="Comma-separated index list")
    p.add_argument("--source-file", default="decision_journal.csv", help="CSV name inside each index folder")
    p.add_argument("--events-file", default="opportunity_events.csv", help="Opportunity events CSV name inside each index folder")
    p.add_argument("--events-limit", type=int, default=20, help="Number of recent opportunity events to show")
    p.add_argument("--output", default="", help="Output HTML path")
    p.add_argument("--interval", type=int, default=15, help="Refresh interval seconds")
    p.add_argument("--watch", action="store_true", help="Regenerate HTML continuously")
    return p.parse_args()


def parse_indices(indices_arg: str) -> List[str]:
    indices = [x.strip().upper() for x in indices_arg.split(",") if x.strip()]
    return indices if indices else list(DEFAULT_INDICES)


def source_signature(
    base_dir: Path,
    date_str: str,
    indices: List[str],
    source_file: str,
    events_file: str,
) -> Tuple[Tuple[str, bool, int, int], ...]:
    sig: List[Tuple[str, bool, int, int]] = []
    date_dir = base_dir / date_str
    for idx in indices:
        for name in (source_file, events_file):
            p = date_dir / idx / name
            if p.exists():
                st = p.stat()
                sig.append((str(p), True, int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))), int(st.st_size)))
            else:
                sig.append((str(p), False, 0, 0))
    return tuple(sig)


def generate_once(args: argparse.Namespace) -> Path:
    base_dir = Path(args.base_dir)
    date_dir = base_dir / args.date
    indices = parse_indices(args.indices)
    cache_path = date_dir / ".live_signal_cache.json"
    signal_cache = load_signal_cache(cache_path)
    cache_changed = False

    per_index: Dict[str, Dict[str, object]] = {}
    for idx in indices:
        csv_path = date_dir / idx / args.source_file
        events_path = date_dir / idx / args.events_file
        _, rows = read_csv(csv_path)
        _, event_rows = read_csv(events_path)
        latest = latest_batch(rows)
        latest_signal = signal_rows_only(latest)
        signal_source = "latest"

        if not latest_signal:
            latest_signal = latest_signal_batch(rows)
            signal_source = "fallback" if latest_signal else "none"

        if not latest_signal:
            cached = signal_cache.get(idx, [])
            if cached:
                latest_signal = cached
                signal_source = "cache"

        if latest_signal and signal_source != "cache":
            signal_cache[idx] = latest_signal
            cache_changed = True

        recent_events = latest_events(event_rows, max(1, int(args.events_limit)))
        newest_event = latest_event_time(event_rows)

        # Read paper trades
        paper_trades_path = date_dir / idx / "paper_trades.csv"
        paper_trades = read_paper_trades(paper_trades_path)
        paper_summary = compute_paper_trade_summary(paper_trades)

        per_index[idx] = {
            "csv_path": str(csv_path),
            "events_path": str(events_path),
            "rows_latest": latest,
            "rows_signal": latest_signal,
            "rows_events": recent_events,
            "signal_source": signal_source,
            "last_event_time": newest_event,
            "events_total": len(event_rows),
            "paper_trades": paper_trades,
            "paper_summary": paper_summary,
        }

    if cache_changed:
        save_signal_cache(cache_path, signal_cache)

    html_text = build_html(
        date_str=args.date,
        source_file=args.source_file,
        events_file=args.events_file,
        refresh_sec=max(1, int(args.interval)),
        indices_order=indices,
        per_index=per_index,
    )

    if args.output:
        out = Path(args.output)
    else:
        out = date_dir / "live_signal_view.html"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")

    print(f"Updated: {out}")
    for idx in indices:
        item = per_index.get(idx, {})
        latest_all = item.get("rows_latest", [])
        latest_sig = item.get("rows_signal", [])
        print(f"- {idx}: latest rows={len(latest_all)}, signal rows={len(latest_sig)}")
    return out


def main() -> int:
    args = parse_args()

    if not args.watch:
        generate_once(args)
        return 0

    base_dir = Path(args.base_dir)
    indices = parse_indices(args.indices)
    last_signature: Tuple[Tuple[str, bool, int, int], ...] = tuple()
    last_emit = 0.0

    while True:
        try:
            sig = source_signature(base_dir, args.date, indices, args.source_file, args.events_file)
            now = time.time()
            interval_due = (now - last_emit) >= max(1, int(args.interval))
            changed = sig != last_signature

            if changed or interval_due:
                generate_once(args)
                last_signature = sig
                last_emit = now
        except KeyboardInterrupt:
            print("Stopped.")
            return 0
        except Exception as exc:
            print(f"Warning: generation failed: {exc}")
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())

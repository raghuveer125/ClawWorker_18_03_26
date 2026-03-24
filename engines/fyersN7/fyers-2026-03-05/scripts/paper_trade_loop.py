#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Add shared_project_engine to path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_SCRIPT_DIR)))
sys.path.insert(0, _PROJECT_ROOT)

# Import market hours from shared config
try:
    from shared_project_engine.market import is_within_buffer_hours as is_market_open, IST
except ImportError:
    # Fallback if shared config not available
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")

    def is_market_open() -> bool:
        """Fallback: Check if Indian market is open (9:00-15:45 IST, Mon-Fri)."""
        now = dt.datetime.now(IST)
        if now.weekday() >= 5:
            return False
        now_mins = now.hour * 60 + now.minute
        return (9 * 60) <= now_mins <= (15 * 60 + 45)


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def parse_score(v: str) -> int:
    s = (v or "").strip()
    if "/" in s:
        left = s.split("/", 1)[0].strip()
        return to_int(left, 0)
    return to_int(s, 0)


def parse_dt(date_s: str, time_s: str) -> dt.datetime:
    raw = f"{(date_s or '').strip()} {(time_s or '').strip()}".strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(raw, fmt)
        except Exception:
            pass
    return dt.datetime.now()


def ensure_csv(path: str, headers: List[str]) -> None:
    if os.path.exists(path):
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()


def append_csv(path: str, headers: List[str], row: Dict[str, Any]) -> None:
    ensure_csv(path, headers)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writerow({k: row.get(k, "") for k in headers})


def load_rows(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def normalize_upper(v: Any) -> str:
    return str(v or "").strip().upper()


def normalize_strike(v: Any) -> str:
    try:
        return str(int(round(float(v))))
    except Exception:
        return str(v or "").strip()


def backfill_journal_outcomes(journal_csv: str, trades: List[Dict[str, Any]]) -> int:
    if not trades or not os.path.exists(journal_csv):
        return 0

    with open(journal_csv, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)

    if not rows or "outcome" not in headers:
        return 0

    # Keep queues of unresolved rows so repeated keys can be mapped in order.
    by_full: Dict[Tuple[str, str, str, str, str], List[int]] = {}
    by_relaxed: Dict[Tuple[str, str, str, str], List[int]] = {}
    for idx, row in enumerate(rows):
        if str(row.get("outcome", "") or "").strip():
            continue

        d = str(row.get("date", "") or "").strip()
        t = str(row.get("time", "") or "").strip()
        s = normalize_upper(row.get("symbol", ""))
        side = normalize_upper(row.get("side", ""))
        strike = normalize_strike(row.get("strike", ""))

        by_full.setdefault((d, t, s, side, strike), []).append(idx)
        by_relaxed.setdefault((d, t, side, strike), []).append(idx)

    updated = 0
    for tr in trades:
        result = str(tr.get("result", "") or "").strip()
        if result not in {"Win", "Loss"}:
            continue

        d = str(tr.get("entry_date", "") or "").strip()
        t = str(tr.get("entry_time", "") or "").strip()
        s = normalize_upper(tr.get("symbol", ""))
        side = normalize_upper(tr.get("side", ""))
        strike = normalize_strike(tr.get("strike", ""))

        idx = None
        full_key = (d, t, s, side, strike)
        relaxed_key = (d, t, side, strike)

        if by_full.get(full_key):
            idx = by_full[full_key].pop(0)
            if by_relaxed.get(relaxed_key):
                try:
                    by_relaxed[relaxed_key].remove(idx)
                except ValueError:
                    pass
        elif by_relaxed.get(relaxed_key):
            idx = by_relaxed[relaxed_key].pop(0)

        if idx is None:
            continue

        rows[idx]["outcome"] = result
        updated += 1

    if updated <= 0:
        return 0

    with open(journal_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in headers})

    return updated


def load_trade_results(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []

    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        out: List[Dict[str, Any]] = []
        for r in reader:
            result = str(r.get("result", "") or "").strip()
            if result not in {"Win", "Loss"}:
                continue
            out.append(
                {
                    "entry_date": str(r.get("entry_date", "") or "").strip(),
                    "entry_time": str(r.get("entry_time", "") or "").strip(),
                    "symbol": str(r.get("symbol", "") or "").strip(),
                    "side": str(r.get("side", "") or "").strip(),
                    "strike": str(r.get("strike", "") or "").strip(),
                    "result": result,
                }
            )
        return out


def save_json(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def infer_initial_capital(state: Dict[str, Any], fallback_capital: float) -> float:
    cash = to_float(state.get("cash", fallback_capital), fallback_capital)
    realized_pnl = to_float(state.get("realized_pnl", 0.0))
    open_positions = state.get("open_positions", [])

    deployed_cost = 0.0
    for pos in open_positions:
        qty = int(pos.get("qty", 0))
        entry_price = to_float(pos.get("entry_price", 0.0))
        entry_fee = to_float(pos.get("entry_fee", 0.0))
        deployed_cost += (entry_price * qty) + entry_fee

    inferred = cash - realized_pnl + deployed_cost
    return inferred if inferred > 0 else fallback_capital


def load_state(path: str, capital: float) -> Tuple[Dict[str, Any], Optional[str]]:
    if not os.path.exists(path):
        return (
            {
                "cash": capital,
                "processed_rows": 0,
                "next_trade_id": 1,
                "realized_pnl": 0.0,
                "total_fees": 0.0,
                "wins": 0,
                "losses": 0,
                "open_positions": [],
                "initial_capital": capital,
            },
            None,
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("invalid state")
        data.setdefault("cash", capital)
        data.setdefault("processed_rows", 0)
        data.setdefault("next_trade_id", 1)
        data.setdefault("realized_pnl", 0.0)
        data.setdefault("total_fees", 0.0)
        data.setdefault("wins", 0)
        data.setdefault("losses", 0)
        data.setdefault("open_positions", [])
        previous_capital = to_float(data.get("initial_capital"), 0.0)
        if previous_capital <= 0:
            previous_capital = infer_initial_capital(data, capital)

        migration_note = None
        if abs(previous_capital - capital) >= 0.01:
            delta = capital - previous_capital
            data["cash"] = to_float(data.get("cash", 0.0)) + delta
            migration_note = (
                f"Adjusted paper state cash by {delta:+.2f} to match configured capital "
                f"{capital:.2f} (previous baseline {previous_capital:.2f})"
            )

        data["initial_capital"] = capital
        return data, migration_note
    except Exception:
        return (
            {
                "cash": capital,
                "processed_rows": 0,
                "next_trade_id": 1,
                "realized_pnl": 0.0,
                "total_fees": 0.0,
                "wins": 0,
                "losses": 0,
                "open_positions": [],
                "initial_capital": capital,
            },
            None,
        )


def print_table(headers: List[str], rows: List[List[str]]) -> None:
    if not rows:
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def fmt(row: List[str]) -> str:
        return " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))

    sep = "-+-".join("-" * w for w in widths)
    print(fmt(headers))
    print(sep)
    for row in rows:
        print(fmt(row))


def run_pull(args: argparse.Namespace) -> int:
    if args.no_pull:
        return 0

    cmd = [
        sys.executable,
        args.pull_script,
        "--only-approved",
        "--insecure",
        "--profile",
        args.profile,
        "--ladder-count",
        str(args.ladder_count),
        "--otm-start",
        str(args.otm_start),
        "--max-premium",
        str(args.max_premium),
        "--min-premium",
        str(args.min_premium),
        "--min-confidence",
        str(args.min_confidence),
        "--min-score",
        str(args.min_score),
        "--min-abs-delta",
        str(args.min_abs_delta),
        "--min-vote-diff",
        str(args.min_vote_diff),
        "--adaptive-model-file",
        args.adaptive_model_file,
        "--min-learn-prob",
        str(args.min_learn_prob),
        "--min-model-samples",
        str(args.min_model_samples),
        "--hard-gate-min-model-samples",
        str(args.hard_gate_min_model_samples),
        "--learn-gate-lock-streak",
        str(args.learn_gate_lock_streak),
        "--learn-gate-relax-sec",
        str(args.learn_gate_relax_sec),
        "--journal-csv",
        args.journal_csv,
        "--confirm-pulls",
        str(args.confirm_pulls),
        "--flip-cooldown-sec",
        str(args.flip_cooldown_sec),
        "--max-select-strikes",
        str(args.max_select_strikes),
        "--max-spread-pct",
        str(args.max_spread_pct),
        "--state-file",
        args.signal_state_file,
    ]
    if args.enable_adaptive:
        cmd.append("--enable-adaptive")
    if args.show_signal_table:
        cmd.append("--table")

    p = subprocess.run(cmd, capture_output=not args.show_signal_table, text=True)
    if p.returncode != 0 and p.stdout:
        print(p.stdout.strip())
    if p.returncode != 0 and p.stderr:
        print(p.stderr.strip())
    return p.returncode


def run_adaptive_train(args: argparse.Namespace) -> int:
    cmd = [
        sys.executable,
        args.train_script,
        "--journal-csv",
        args.journal_csv,
        "--model-file",
        args.adaptive_model_file,
        "--min-labels",
        str(args.train_min_labels),
        "--lr",
        str(args.train_lr),
        "--epochs",
        str(args.train_epochs),
    ]

    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.stdout:
        print(p.stdout.strip())
    if p.returncode != 0 and p.stderr:
        print(p.stderr.strip())
    return p.returncode


def close_position(
    state: Dict[str, Any],
    pos: Dict[str, Any],
    exit_price: float,
    exit_ts: dt.datetime,
    exit_reason: str,
    args: argparse.Namespace,
    trades_csv: str,
) -> Dict[str, Any]:
    qty = int(pos["qty"])
    entry_price = to_float(pos["entry_price"])
    entry_fee = to_float(pos.get("entry_fee", 0.0))
    gross = (exit_price - entry_price) * qty
    exit_fee = float(args.exit_fee)
    total_fee = entry_fee + exit_fee
    net = gross - total_fee

    capital_before = to_float(state.get("cash", 0.0))
    state["cash"] = capital_before + (exit_price * qty) - exit_fee
    state["realized_pnl"] = to_float(state.get("realized_pnl", 0.0)) + net
    state["total_fees"] = to_float(state.get("total_fees", 0.0)) + exit_fee
    if net >= 0:
        state["wins"] = int(state.get("wins", 0)) + 1
        result = "Win"
    else:
        state["losses"] = int(state.get("losses", 0)) + 1
        result = "Loss"

    hold_sec = max(0, int((exit_ts - parse_dt(pos["entry_date"], pos["entry_time"])).total_seconds()))

    trade_row = {
        "trade_id": pos["trade_id"],
        "symbol": pos["symbol"],
        "side": pos["side"],
        "strike": pos["strike"],
        "qty": qty,
        "entry_date": pos["entry_date"],
        "entry_time": pos["entry_time"],
        "entry_price": f"{entry_price:.2f}",
        "sl": f"{to_float(pos.get('sl', 0.0)):.2f}",
        "t1": f"{to_float(pos.get('t1', 0.0)):.2f}",
        "t2": f"{to_float(pos.get('t2', 0.0)):.2f}",
        "exit_date": exit_ts.strftime("%Y-%m-%d"),
        "exit_time": exit_ts.strftime("%H:%M:%S"),
        "exit_price": f"{exit_price:.2f}",
        "exit_reason": exit_reason,
        "gross_pnl": f"{gross:.2f}",
        "fees": f"{total_fee:.2f}",
        "net_pnl": f"{net:.2f}",
        "hold_sec": str(hold_sec),
        "result": result,
        "capital_before": f"{capital_before:.2f}",
        "capital_after": f"{to_float(state.get('cash', 0.0)):.2f}",
        "engine_id": "fyersn7",
        "index": os.environ.get("INDEX", "UNKNOWN"),
    }

    append_csv(
        trades_csv,
        [
            "trade_id",
            "symbol",
            "side",
            "strike",
            "qty",
            "entry_date",
            "entry_time",
            "entry_price",
            "sl",
            "t1",
            "t2",
            "exit_date",
            "exit_time",
            "exit_price",
            "exit_reason",
            "gross_pnl",
            "fees",
            "net_pnl",
            "hold_sec",
            "result",
            "capital_before",
            "capital_after",
            "engine_id",
            "index",
        ],
        trade_row,
    )
    return trade_row


def _candidate_ems(c: Dict[str, Any]) -> float:
    """Expected Move Score for a candidate trade row.

    Formula: (confidence/100) * (|delta| * 100) / premium
    Higher score = better risk/reward per rupee of premium paid.
    Falls back to 0.0 if premium <= 0 or delta unavailable.
    """
    premium = c.get("entry", 0.0)
    if premium <= 0:
        return 0.0
    confidence = c.get("confidence", 0) / 100.0
    delta = abs(to_float(c.get("row", {}).get("delta", "0"), 0.0))
    return confidence * delta * 100.0 / premium


def process_new_rows(
    rows: List[Dict[str, str]],
    state: Dict[str, Any],
    args: argparse.Namespace,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
    processed = int(state.get("processed_rows", 0))
    if processed > len(rows):
        processed = 0
    new_rows = rows[processed:]
    state["processed_rows"] = len(rows)
    if not new_rows:
        return [], [], None

    quote_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    candidates: List[Dict[str, Any]] = []
    latest_side: Optional[str] = None
    latest_ts: Optional[dt.datetime] = None

    for r in new_rows:
        side = (r.get("side", "") or "").strip().upper()
        strike = (r.get("strike", "") or "").strip()
        if side not in {"CE", "PE"} or not strike:
            continue
        ts = parse_dt(r.get("date", ""), r.get("time", ""))
        key = (side, strike)
        quote = {
            "ltp": to_float(r.get("entry", "0"), 0.0),
            "sl": to_float(r.get("sl", "0"), 0.0),
            "t1": to_float(r.get("t1", "0"), 0.0),
            "t2": to_float(r.get("t2", "0"), 0.0),
            "ts": ts,
        }
        prev = quote_by_key.get(key)
        if prev is None or ts >= prev["ts"]:
            quote_by_key[key] = quote

        if latest_ts is None or ts >= latest_ts:
            latest_ts = ts
            latest_side = side

        status = (r.get("status", "") or "").strip().upper()
        action = (r.get("action", "") or "").strip().upper()
        entry_ready = (r.get("entry_ready", "") or "").strip().upper() == "Y"
        selected = (r.get("selected", "") or "").strip().upper() == "Y"

        # REQUIRE_TREND_ALIGNMENT filter (66% WR improvement)
        # vol_dom = CE (bullish flow) → only CE entries allowed
        # vol_dom = PE (bearish flow) → only PE entries allowed
        # vol_dom = NEUTRAL → both sides allowed
        vol_dom = (r.get("vol_dom", "") or "").strip().upper()
        trend_aligned = True
        if vol_dom in ("CE", "PE") and vol_dom != side:
            trend_aligned = False

        if status == "APPROVED" and action == "TAKE" and entry_ready and selected and trend_aligned:
            candidates.append(
                {
                    "row": r,
                    "ts": ts,
                    "side": side,
                    "strike": strike,
                    "entry": to_float(r.get("entry", "0"), 0.0),
                    "sl": to_float(r.get("sl", "0"), 0.0),
                    "t1": to_float(r.get("t1", "0"), 0.0),
                    "t2": to_float(r.get("t2", "0"), 0.0),
                    "confidence": to_int(r.get("confidence", "0"), 0),
                    "score": parse_score(r.get("score", "0")),
                }
            )

    closed: List[Dict[str, Any]] = []
    still_open: List[Dict[str, Any]] = []

    for pos in state.get("open_positions", []):
        key = (str(pos.get("side", "")), str(pos.get("strike", "")))
        quote = quote_by_key.get(key)
        if quote:
            pos["last_price"] = quote["ltp"]
            pos["last_date"] = quote["ts"].strftime("%Y-%m-%d")
            pos["last_time"] = quote["ts"].strftime("%H:%M:%S")

        current_price = to_float(pos.get("last_price", pos.get("entry_price", 0.0)), 0.0)
        current_ts = parse_dt(pos.get("last_date", ""), pos.get("last_time", ""))
        entry_ts = parse_dt(pos.get("entry_date", ""), pos.get("entry_time", ""))
        age = max(0, int((current_ts - entry_ts).total_seconds()))
        target_price = to_float(pos.get("t1", 0.0)) if args.exit_target == "t1" else to_float(pos.get("t2", 0.0))

        reason = ""
        if current_price <= to_float(pos.get("sl", 0.0)):
            reason = "SL"
        elif current_price >= target_price > 0:
            reason = args.exit_target.upper()
        elif args.exit_on_side_flip and latest_side and latest_side != str(pos.get("side", "")):
            reason = "SIDE_FLIP"
        elif args.max_hold_sec > 0 and age >= args.max_hold_sec:
            reason = "TIME"

        if reason:
            closed_trade = close_position(
                state=state,
                pos=pos,
                exit_price=current_price,
                exit_ts=current_ts,
                exit_reason=reason,
                args=args,
                trades_csv=args.trades_csv,
            )
            closed.append(closed_trade)
        else:
            still_open.append(pos)

    state["open_positions"] = still_open

    # Deduplicate candidates by (side, strike) before sorting.
    # new_rows spans multiple ticks; each tick writes the same strikes again.
    # _candidate_ems uses entry price (LTP at that tick) as denominator, so a
    # stale row where 73800 was ATM (low premium, high delta, no conf penalty)
    # can score higher than the current-tick 73900 row, causing wrong execution.
    # Fix: keep only the most recent row per (side, strike).
    _deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for _c in candidates:
        _key = (_c["side"], _c["strike"])
        if _key not in _deduped or _c["ts"] >= _deduped[_key]["ts"]:
            _deduped[_key] = _c
    candidates = list(_deduped.values())

    open_keys = {(str(p.get("side", "")), str(p.get("strike", ""))) for p in state.get("open_positions", [])}
    candidates.sort(key=_candidate_ems, reverse=True)

    opened: List[Dict[str, Any]] = []
    for c in candidates:
        key = (c["side"], c["strike"])
        if key in open_keys:
            continue
        if c["entry"] <= 0:
            continue

        qty = int(args.lot_size)
        required_cash = (c["entry"] * qty) + float(args.entry_fee)
        if to_float(state.get("cash", 0.0)) < required_cash:
            continue

        capital_before = to_float(state.get("cash", 0.0))
        state["cash"] = capital_before - required_cash
        state["total_fees"] = to_float(state.get("total_fees", 0.0)) + float(args.entry_fee)

        trade_id = int(state.get("next_trade_id", 1))
        state["next_trade_id"] = trade_id + 1

        entry_ts = c["ts"]
        pos = {
            "trade_id": trade_id,
            "symbol": c["row"].get("symbol", "SENSEX"),
            "side": c["side"],
            "strike": c["strike"],
            "qty": qty,
            "entry_price": c["entry"],
            "entry_fee": float(args.entry_fee),
            "entry_date": entry_ts.strftime("%Y-%m-%d"),
            "entry_time": entry_ts.strftime("%H:%M:%S"),
            "last_price": c["entry"],
            "last_date": entry_ts.strftime("%Y-%m-%d"),
            "last_time": entry_ts.strftime("%H:%M:%S"),
            "sl": c["sl"],
            "t1": c["t1"],
            "t2": c["t2"],
            "confidence": c["confidence"],
            "score": c["score"],
        }
        state["open_positions"].append(pos)
        open_keys.add(key)
        opened.append(
            {
                "trade_id": trade_id,
                "side": c["side"],
                "strike": c["strike"],
                "qty": qty,
                "entry_price": c["entry"],
                "capital_before": capital_before,
                "capital_after": to_float(state.get("cash", 0.0)),
            }
        )
        break  # Execute only the top-EMS candidate per cycle.
               # Without this, all selected=Y strikes open in sorted order and
               # paper_trades.csv shows the last-appended (lowest EMS) as "traded".

    return opened, closed, latest_side


def mark_to_market(state: Dict[str, Any]) -> Tuple[float, float]:
    unrealized = 0.0
    inventory_value = 0.0
    for pos in state.get("open_positions", []):
        qty = int(pos.get("qty", 0))
        entry = to_float(pos.get("entry_price", 0.0))
        last = to_float(pos.get("last_price", entry))
        inventory_value += last * qty
        unrealized += (last - entry) * qty
    equity = to_float(state.get("cash", 0.0)) + inventory_value
    return unrealized, equity


def append_equity(args: argparse.Namespace, state: Dict[str, Any]) -> None:
    now = dt.datetime.now()
    unrealized, equity = mark_to_market(state)
    append_csv(
        args.equity_csv,
        [
            "date",
            "time",
            "cash",
            "open_positions",
            "realized_pnl",
            "unrealized_pnl",
            "equity",
            "total_fees",
            "wins",
            "losses",
        ],
        {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "cash": f"{to_float(state.get('cash', 0.0)):.2f}",
            "open_positions": str(len(state.get("open_positions", []))),
            "realized_pnl": f"{to_float(state.get('realized_pnl', 0.0)):.2f}",
            "unrealized_pnl": f"{unrealized:.2f}",
            "equity": f"{equity:.2f}",
            "total_fees": f"{to_float(state.get('total_fees', 0.0)):.2f}",
            "wins": str(int(state.get("wins", 0))),
            "losses": str(int(state.get("losses", 0))),
        },
    )


def print_cycle_summary(state: Dict[str, Any], opened: List[Dict[str, Any]], closed: List[Dict[str, Any]], latest_side: Optional[str]) -> None:
    unrealized, equity = mark_to_market(state)
    print(
        "PaperSummary | "
        f"Cash: {to_float(state.get('cash', 0.0)):.2f} | "
        f"Open: {len(state.get('open_positions', []))} | "
        f"Realized: {to_float(state.get('realized_pnl', 0.0)):.2f} | "
        f"Unrealized: {unrealized:.2f} | "
        f"Equity: {equity:.2f} | "
        f"Fees: {to_float(state.get('total_fees', 0.0)):.2f} | "
        f"W/L: {int(state.get('wins', 0))}/{int(state.get('losses', 0))} | "
        f"Side: {latest_side or '-'}"
    )

    if opened:
        rows = [
            [
                str(x["trade_id"]),
                x["side"],
                str(x["strike"]),
                str(x["qty"]),
                f"{to_float(x['entry_price']):.2f}",
                f"{to_float(x['capital_after']):.2f}",
            ]
            for x in opened
        ]
        print("Opened")
        print_table(["ID", "Side", "Strike", "Qty", "Entry", "CashAfter"], rows)

    if closed:
        rows = [
            [
                str(x["trade_id"]),
                x["side"],
                str(x["strike"]),
                x["exit_reason"],
                x["entry_price"],
                x["exit_price"],
                x["net_pnl"],
                x["result"],
            ]
            for x in closed[-5:]
        ]
        print("Closed")
        print_table(["ID", "Side", "Strike", "Exit", "Entry", "ExitPx", "NetPnL", "Result"], rows)

    open_positions = state.get("open_positions", [])
    if open_positions:
        rows = []
        for p in open_positions:
            qty = int(p.get("qty", 0))
            entry = to_float(p.get("entry_price", 0.0))
            last = to_float(p.get("last_price", entry))
            u = (last - entry) * qty
            rows.append(
                [
                    str(p.get("trade_id", "")),
                    str(p.get("side", "")),
                    str(p.get("strike", "")),
                    str(qty),
                    f"{entry:.2f}",
                    f"{last:.2f}",
                    f"{to_float(p.get('sl', 0.0)):.2f}",
                    f"{to_float(p.get('t1', 0.0)):.2f}",
                    f"{u:.2f}",
                ]
            )
        print("OpenPositions")
        print_table(["ID", "Side", "Strike", "Qty", "Entry", "LTP", "SL", "T1", "U-PnL"], rows)


def get_next_market_open() -> str:
    """Get the next market open time."""
    now = dt.datetime.now(IST)
    weekday = now.weekday()

    if weekday >= 5:  # Weekend
        days_until_monday = 7 - weekday
        next_open = now.replace(hour=9, minute=15, second=0, microsecond=0) + dt.timedelta(days=days_until_monday)
        return next_open.strftime("Mon %d %b, 9:15 AM")

    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

    if now < market_open:
        return "Today 9:15 AM"
    elif now > market_close:
        if weekday == 4:  # Friday
            next_open = now.replace(hour=9, minute=15) + dt.timedelta(days=3)
            return next_open.strftime("Mon %d %b, 9:15 AM")
        else:
            return "Tomorrow 9:15 AM"
    return "NOW"


def get_last_signal(signals_csv: str) -> Optional[Dict[str, str]]:
    """Read the last actionable signal from signals.csv."""
    if not os.path.exists(signals_csv):
        return None
    try:
        with open(signals_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            last_signal = None
            for row in reader:
                # Look for signals with actual side (CE/PE)
                if row.get("side") in ("CE", "PE") and row.get("strike"):
                    last_signal = row
            return last_signal
    except Exception:
        return None


def print_market_closed_summary(state: Dict[str, Any], args: argparse.Namespace, symbol: str = "INDEX") -> None:
    """Print a summary when market is closed."""
    unrealized, equity = mark_to_market(state)
    initial_capital = float(args.capital)
    total_pnl = equity - initial_capital
    pnl_pct = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0
    wins = int(state.get("wins", 0))
    losses = int(state.get("losses", 0))
    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    next_open = get_next_market_open()

    print()
    print(f"{'=' * 60}")
    print(f"[{symbol}] MARKET CLOSED  |  Opens: {next_open}")
    print(f"{'=' * 60}")

    # P&L Summary
    pnl_sign = "+" if total_pnl >= 0 else ""
    print(f"  Equity: ₹{equity:,.2f}  |  P&L: {pnl_sign}₹{total_pnl:,.2f} ({pnl_sign}{pnl_pct:.1f}%)")
    print(f"  Trades: {total_trades}  |  Win Rate: {win_rate:.0f}% ({wins}W / {losses}L)")
    print(f"  Fees Paid: ₹{to_float(state.get('total_fees', 0)):.2f}")

    # Open positions
    open_pos = state.get("open_positions", [])
    if open_pos:
        print(f"  Open Positions: {len(open_pos)}")
        for p in open_pos[:3]:  # Show max 3
            print(f"    - {p.get('side', '?')} {p.get('strike', '?')} @ {to_float(p.get('entry_price', 0)):.2f}")

    # Last signal
    signals_csv = args.journal_csv.replace("decision_journal", "signals")
    if not os.path.exists(signals_csv):
        signals_csv = os.path.join(os.path.dirname(args.journal_csv), "signals.csv")

    last_sig = get_last_signal(signals_csv)
    if last_sig:
        sig_time = f"{last_sig.get('date', '')} {last_sig.get('time', '')}"
        sig_side = last_sig.get('side', '?')
        sig_strike = last_sig.get('strike', '?')
        sig_entry = last_sig.get('entry', '?')
        sig_conf = last_sig.get('confidence', '?')
        print(f"  Last Signal: {sig_side} {sig_strike} @ {sig_entry} (Conf: {sig_conf}%) [{sig_time}]")

    print(f"{'=' * 60}")
    print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Paper trade loop on top of FYERS signal journal.")

    p.add_argument("--interval-sec", type=int, default=15)
    p.add_argument("--capital", type=float, default=50000.0)
    p.add_argument("--lot-size", type=int, default=10)
    p.add_argument("--entry-fee", type=float, default=40.0)
    p.add_argument("--exit-fee", type=float, default=40.0)
    p.add_argument("--exit-target", choices=["t1", "t2"], default="t1")
    p.add_argument("--max-hold-sec", type=int, default=180)
    p.add_argument("--exit-on-side-flip", dest="exit_on_side_flip", action="store_true")
    p.add_argument("--no-exit-on-side-flip", dest="exit_on_side_flip", action="store_false")
    p.set_defaults(exit_on_side_flip=True)

    p.add_argument("--journal-csv", default="decision_journal.csv")
    p.add_argument("--trades-csv", default="paper_trades.csv")
    p.add_argument("--equity-csv", default="paper_equity.csv")
    p.add_argument("--paper-state-file", default=".paper_trade_state.json")
    p.add_argument("--start-from-latest", dest="start_from_latest", action="store_true")
    p.add_argument("--start-from-beginning", dest="start_from_latest", action="store_false")
    p.set_defaults(start_from_latest=True)
    p.add_argument("--once", action="store_true")

    p.add_argument("--no-pull", action="store_true", help="Skip signal pulling (use when signal_loop is running separately)")
    p.add_argument("--pull-script", default="scripts/pull_fyers_signal.py")
    p.add_argument("--profile", default="expiry")
    p.add_argument("--ladder-count", type=int, default=5)
    p.add_argument("--otm-start", type=int, default=1)
    p.add_argument("--max-premium", type=float, default=220)
    p.add_argument("--min-premium", type=float, default=0)
    p.add_argument("--min-confidence", type=int, default=88)
    p.add_argument("--min-score", type=int, default=95)
    p.add_argument("--min-abs-delta", type=float, default=0.10)
    p.add_argument("--min-vote-diff", type=int, default=2)
    p.add_argument("--adaptive-model-file", default=".adaptive_model.json")
    p.add_argument("--min-learn-prob", type=float, default=0.55)
    p.add_argument("--min-model-samples", type=int, default=20)
    p.add_argument("--hard-gate-min-model-samples", type=int, default=100)
    p.add_argument("--learn-gate-lock-streak", type=int, default=8)
    p.add_argument("--learn-gate-relax-sec", type=int, default=300)
    p.add_argument("--confirm-pulls", type=int, default=2)
    p.add_argument("--flip-cooldown-sec", type=int, default=45)
    p.add_argument("--max-select-strikes", type=int, default=3)
    p.add_argument("--max-spread-pct", type=float, default=2.5)
    p.add_argument("--signal-state-file", default=".signal_state.json")
    p.add_argument("--enable-adaptive", dest="enable_adaptive", action="store_true")
    p.add_argument("--disable-adaptive", dest="enable_adaptive", action="store_false")
    p.set_defaults(enable_adaptive=True)
    p.add_argument("--show-signal-table", action="store_true")

    p.add_argument("--train-script", default="scripts/update_adaptive_model.py")
    p.add_argument("--train-min-labels", type=int, default=20)
    p.add_argument("--train-lr", type=float, default=0.15)
    p.add_argument("--train-epochs", type=int, default=600)
    p.add_argument("--auto-train-on-backfill", dest="auto_train_on_backfill", action="store_true")
    p.add_argument("--no-auto-train-on-backfill", dest="auto_train_on_backfill", action="store_false")
    p.set_defaults(auto_train_on_backfill=True)
    p.add_argument("--skip-market-check", action="store_true",
                   help="Skip market hours check (run even when market is closed)")

    return p


def main() -> int:
    args = build_parser().parse_args()

    # Align trainer threshold with adaptive gate unless user explicitly overrides it.
    train_arg_set = any(
        a == "--train-min-labels" or a.startswith("--train-min-labels=")
        for a in sys.argv[1:]
    )
    if not train_arg_set:
        args.train_min_labels = max(1, int(args.min_model_samples))
    else:
        args.train_min_labels = max(1, int(args.train_min_labels))

    restored = backfill_journal_outcomes(args.journal_csv, load_trade_results(args.trades_csv))
    if restored > 0:
        print(f"Backfilled outcomes from existing trades: {restored}")
        if args.enable_adaptive and args.auto_train_on_backfill:
            print("Training adaptive model after startup backfill...")
            run_adaptive_train(args)

    state, state_note = load_state(args.paper_state_file, args.capital)
    existing_rows = load_rows(args.journal_csv)
    if not os.path.exists(args.paper_state_file) and args.start_from_latest:
        state["processed_rows"] = len(existing_rows)

    print("Starting paper trade loop")
    if state_note:
        print(state_note)
    print(
        f"Interval: {args.interval_sec}s | Capital: {args.capital:.2f} | "
        f"Lot: {args.lot_size} | Fees: {args.entry_fee:.0f}+{args.exit_fee:.0f}"
    )
    print(
        f"Exit: {args.exit_target.upper()} | MaxHold: {args.max_hold_sec}s | "
        f"SideFlipExit: {'Y' if args.exit_on_side_flip else 'N'}"
    )
    print("Press Ctrl+C to stop.")

    last_market_closed_msg = 0
    last_summary_shown = 0
    try:
        while True:
            # Check if market is open
            if not args.skip_market_check and not is_market_open():
                now_ts = int(time.time())
                # Show full summary every 5 minutes, brief message every minute
                if now_ts - last_summary_shown >= 300:
                    # Extract symbol from state or directory
                    symbol = "INDEX"
                    open_pos = state.get("open_positions", [])
                    if open_pos:
                        symbol = open_pos[0].get("symbol", "INDEX")
                    else:
                        # Try to get from directory name
                        cwd = os.getcwd()
                        for idx in ["SENSEX", "NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
                            if idx in cwd.upper():
                                symbol = idx
                                break
                    print_market_closed_summary(state, args, symbol)
                    last_summary_shown = now_ts
                    last_market_closed_msg = now_ts
                elif now_ts - last_market_closed_msg >= 60:
                    print(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Market closed. Waiting...")
                    last_market_closed_msg = now_ts
                time.sleep(max(1, int(args.interval_sec)))
                continue

            print()
            print(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Pulling + simulating...")
            run_pull(args)
            rows = load_rows(args.journal_csv)
            opened, closed, latest_side = process_new_rows(rows, state, args)
            if closed:
                backfilled = backfill_journal_outcomes(args.journal_csv, closed)
                if backfilled > 0:
                    print(f"Outcome backfill: updated {backfilled} journal row(s)")
                    if args.enable_adaptive and args.auto_train_on_backfill:
                        print("Training adaptive model after outcome backfill...")
                        run_adaptive_train(args)
            append_equity(args, state)
            print_cycle_summary(state, opened, closed, latest_side)
            save_json(args.paper_state_file, state)
            if args.once:
                break
            time.sleep(max(1, int(args.interval_sec)))
    except KeyboardInterrupt:
        save_json(args.paper_state_file, state)
        print("\nStopped. State saved.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

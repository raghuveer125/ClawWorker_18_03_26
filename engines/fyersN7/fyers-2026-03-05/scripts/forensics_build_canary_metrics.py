#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.utils import to_float_opt as to_float, to_int_opt as to_int


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_csv_list(raw: str) -> List[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Build canary_metrics_<date>.json from paper equity/state files for rollback guardrails."
        )
    )
    p.add_argument("--base-dir", default="postmortem", help="Base folder containing date subfolders")
    p.add_argument("--date", default="", help="Date folder in YYYY-MM-DD (default: latest available)")
    p.add_argument("--symbols", default="SENSEX,NIFTY50", help="Comma-separated symbol allowlist")
    p.add_argument(
        "--output-json",
        default="",
        help="Output JSON path (default: <base>/<date>/canary_metrics_<date>.json)",
    )
    p.add_argument(
        "--default-baseline-win-rate",
        type=float,
        default=50.0,
        help="Fallback baseline win rate pct when pattern_templates are unavailable",
    )
    p.add_argument(
        "--min-realized-delta",
        type=float,
        default=0.01,
        help="Minimum realized_pnl delta to treat as a closed-trade event",
    )
    p.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit non-zero when no symbol could be processed.",
    )
    return p.parse_args()


def detect_latest_date_folder(base_dir: Path) -> str:
    candidates: List[str] = []
    if not base_dir.exists():
        return ""
    for child in base_dir.iterdir():
        if child.is_dir() and DATE_RE.match(child.name):
            candidates.append(child.name)
    if not candidates:
        return ""
    candidates.sort()
    return candidates[-1]


def resolve_date(base_dir: Path, requested_date: str) -> Tuple[str, str]:
    if requested_date:
        if not DATE_RE.match(requested_date):
            return "", f"Invalid --date format: {requested_date} (expected YYYY-MM-DD)"
        return requested_date, ""
    latest = detect_latest_date_folder(base_dir)
    if not latest:
        return "", f"No date folders found under: {base_dir}"
    return latest, ""


def read_json(path: Path) -> Tuple[Dict[str, object], str]:
    if not path.exists():
        return {}, "missing"
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            return payload, ""
        return {}, "invalid_json_root"
    except Exception as exc:
        return {}, f"parse_error:{type(exc).__name__}"


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]], str]:
    if not path.exists():
        return [], [], "missing"
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            headers = [h.strip() for h in (reader.fieldnames or []) if h]
            if not headers:
                return [], [], "missing_header"
            rows: List[Dict[str, str]] = []
            for raw in reader:
                row: Dict[str, str] = {}
                for h in headers:
                    row[h] = (raw.get(h, "") or "").strip()
                rows.append(row)
        return headers, rows, ""
    except Exception as exc:
        return [], [], f"parse_error:{type(exc).__name__}"


def parse_ts(date_s: str, time_s: str) -> Optional[dt.datetime]:
    raw = f"{(date_s or '').strip()} {(time_s or '').strip()}".strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return dt.datetime.strptime(raw, fmt)
        except Exception:
            pass
    return None


def round_or_none(v: Optional[float], ndigits: int = 4) -> Optional[float]:
    if v is None:
        return None
    return round(float(v), ndigits)


def _compute_loss_streak(events: List[Tuple[dt.datetime, str]]) -> int:
    streak = 0
    for _, result in reversed(events):
        if result == "L":
            streak += 1
        else:
            break
    return streak


def analyze_symbol(
    symbol: str,
    symbol_dir: Path,
    *,
    min_realized_delta: float,
) -> Tuple[Dict[str, object], List[Tuple[dt.datetime, str]]]:
    state_path = symbol_dir / ".paper_trade_state.json"
    equity_path = symbol_dir / "paper_equity.csv"

    state_payload, state_err = read_json(state_path)
    _eh, equity_rows, equity_err = read_csv(equity_path)

    row_parse_errors = 0
    points: List[Dict[str, object]] = []
    for row in equity_rows:
        ts = parse_ts(row.get("date", ""), row.get("time", ""))
        equity = to_float(row.get("equity", ""))
        realized = to_float(row.get("realized_pnl", ""))
        wins_row = to_int(row.get("wins", ""))
        losses_row = to_int(row.get("losses", ""))
        if ts is None or equity is None or realized is None:
            row_parse_errors += 1
            continue
        points.append(
            {
                "ts": ts,
                "equity": float(equity),
                "realized_pnl": float(realized),
                "wins": int(wins_row or 0),
                "losses": int(losses_row or 0),
            }
        )

    state_wins = int(to_int(state_payload.get("wins", 0)) or 0)
    state_losses = int(to_int(state_payload.get("losses", 0)) or 0)

    events: List[Tuple[dt.datetime, str]] = []
    initial_equity = None
    current_equity = None
    peak_equity = None
    current_drawdown_pct = None
    max_drawdown_pct = None

    if points:
        initial_equity = float(points[0]["equity"])
        current_equity = float(points[-1]["equity"])
        running_peak = float(points[0]["equity"])
        max_dd = 0.0
        prev_realized = float(points[0]["realized_pnl"])
        for p in points:
            eq = float(p["equity"])
            running_peak = max(running_peak, eq)
            if running_peak > 0:
                dd = max(0.0, ((running_peak - eq) / running_peak) * 100.0)
                max_dd = max(max_dd, dd)
            realized = float(p["realized_pnl"])
            delta = realized - prev_realized
            if abs(delta) >= max(0.0, min_realized_delta):
                events.append((p["ts"], "W" if delta > 0 else "L"))
            prev_realized = realized
        peak_equity = running_peak
        if running_peak > 0:
            current_drawdown_pct = max(0.0, ((running_peak - current_equity) / running_peak) * 100.0)
            max_drawdown_pct = max_dd

    event_wins = sum(1 for _ts, r in events if r == "W")
    event_losses = sum(1 for _ts, r in events if r == "L")

    wins = max(state_wins, event_wins)
    losses = max(state_losses, event_losses)
    observation_trades = wins + losses

    consecutive_losses: Optional[int]
    if events:
        consecutive_losses = _compute_loss_streak(events)
    elif losses <= 0:
        consecutive_losses = 0
    elif wins <= 0:
        consecutive_losses = losses
    else:
        consecutive_losses = None

    status = "PASS"
    errors: List[str] = []
    if state_err:
        status = "FAIL"
        errors.append(f"paper_state:{state_err}")
    if equity_err:
        status = "FAIL"
        errors.append(f"paper_equity:{equity_err}")
    if not points:
        status = "FAIL"
        errors.append("paper_equity:no_valid_rows")

    win_rate_pct = (wins / observation_trades * 100.0) if observation_trades > 0 else None

    result = {
        "status": status,
        "errors": errors,
        "files": {
            "paper_state": {"path": str(state_path), "status": "PASS" if not state_err else "ERROR", "error": state_err},
            "paper_equity": {"path": str(equity_path), "status": "PASS" if not equity_err else "ERROR", "error": equity_err},
        },
        "rows": {
            "equity_rows_total": len(equity_rows),
            "equity_rows_valid": len(points),
            "equity_row_parse_errors": row_parse_errors,
        },
        "equity": {
            "initial": round_or_none(initial_equity, 4),
            "current": round_or_none(current_equity, 4),
            "peak": round_or_none(peak_equity, 4),
        },
        "drawdown": {
            "current_drawdown_pct": round_or_none(current_drawdown_pct, 4),
            "max_drawdown_pct": round_or_none(max_drawdown_pct, 4),
        },
        "trade_stats": {
            "wins": wins,
            "losses": losses,
            "observation_trades": observation_trades,
            "win_rate_pct": round_or_none(win_rate_pct, 4),
            "consecutive_losses": consecutive_losses,
            "events_from_realized_deltas": len(events),
        },
    }
    return result, events


def weighted_baseline_hit_rate(
    pattern_rows: List[Dict[str, str]],
    symbols: List[str],
    fallback: float,
) -> Tuple[float, str, int]:
    allow = set(symbols)
    total_weight = 0
    weighted_sum = 0.0
    contributing_rows = 0
    for row in pattern_rows:
        symbol = (row.get("symbol", "") or "").upper()
        if symbol not in allow:
            continue
        hit_rate = to_float(row.get("hit_rate", ""))
        sample_count = to_int(row.get("sample_count", ""))
        if hit_rate is None or sample_count is None or sample_count <= 0:
            continue
        weighted_sum += float(hit_rate) * int(sample_count)
        total_weight += int(sample_count)
        contributing_rows += 1
    if total_weight <= 0:
        return float(fallback), "fallback_fixed_default", 0
    return weighted_sum / total_weight, "pattern_templates_weighted_hit_rate", contributing_rows


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir)
    symbols = [x.upper() for x in parse_csv_list(args.symbols)]
    if not symbols:
        print("ERROR: symbol allowlist is empty.", file=sys.stderr)
        return 2

    date_str, err = resolve_date(base_dir, args.date.strip())
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2

    date_dir = base_dir / date_str
    if not date_dir.exists():
        print(f"ERROR: date folder not found: {date_dir}", file=sys.stderr)
        return 2

    output_json = Path(args.output_json) if args.output_json.strip() else (date_dir / f"canary_metrics_{date_str}.json")
    output_json.parent.mkdir(parents=True, exist_ok=True)

    present_symbols = sorted([p.name.upper() for p in date_dir.iterdir() if p.is_dir()])
    allow = set(symbols)
    skipped_symbols = [s for s in present_symbols if s not in allow]

    symbol_results: Dict[str, Dict[str, object]] = {}
    all_events: List[Tuple[dt.datetime, str]] = []
    processed_symbols: List[str] = []
    failed_symbols: List[str] = []

    for symbol in symbols:
        symbol_dir = date_dir / symbol
        if not symbol_dir.exists():
            symbol_results[symbol] = {
                "status": "FAIL",
                "errors": [f"symbol_folder_missing:{symbol_dir}"],
                "files": {
                    "paper_state": {
                        "path": str(symbol_dir / ".paper_trade_state.json"),
                        "status": "ERROR",
                        "error": "missing",
                    },
                    "paper_equity": {
                        "path": str(symbol_dir / "paper_equity.csv"),
                        "status": "ERROR",
                        "error": "missing",
                    },
                },
                "rows": {
                    "equity_rows_total": 0,
                    "equity_rows_valid": 0,
                    "equity_row_parse_errors": 0,
                },
                "equity": {
                    "initial": None,
                    "current": None,
                    "peak": None,
                },
                "drawdown": {
                    "current_drawdown_pct": None,
                    "max_drawdown_pct": None,
                },
                "trade_stats": {
                    "wins": 0,
                    "losses": 0,
                    "observation_trades": 0,
                    "win_rate_pct": None,
                    "consecutive_losses": None,
                    "events_from_realized_deltas": 0,
                },
            }
            failed_symbols.append(symbol)
            continue

        result, events = analyze_symbol(symbol, symbol_dir, min_realized_delta=float(args.min_realized_delta))
        symbol_results[symbol] = result
        all_events.extend(events)
        processed_symbols.append(symbol)
        if str(result.get("status", "")).upper() != "PASS":
            failed_symbols.append(symbol)

    pattern_csv = date_dir / f"pattern_templates_{date_str}.csv"
    _ph, pattern_rows, pattern_err = read_csv(pattern_csv)
    baseline_win_rate, baseline_source, baseline_rows = weighted_baseline_hit_rate(
        pattern_rows=pattern_rows if not pattern_err else [],
        symbols=symbols,
        fallback=float(args.default_baseline_win_rate),
    )

    total_wins = sum(
        int(((symbol_results.get(s, {}).get("trade_stats", {}) or {}).get("wins", 0) or 0))
        for s in symbols
    )
    total_losses = sum(
        int(((symbol_results.get(s, {}).get("trade_stats", {}) or {}).get("losses", 0) or 0))
        for s in symbols
    )
    observation_trades = total_wins + total_losses
    win_rate_pct = (total_wins / observation_trades * 100.0) if observation_trades > 0 else None
    win_rate_delta_pct = (win_rate_pct - baseline_win_rate) if win_rate_pct is not None else None

    current_drawdowns: List[float] = []
    max_drawdowns: List[float] = []
    sum_current_equity = 0.0
    sum_peak_equity = 0.0
    have_portfolio_values = False
    for s in symbols:
        sres = symbol_results.get(s, {})
        sdd = to_float(((sres.get("drawdown", {}) or {}).get("current_drawdown_pct")))
        if sdd is not None:
            current_drawdowns.append(float(sdd))
        smaxdd = to_float(((sres.get("drawdown", {}) or {}).get("max_drawdown_pct")))
        if smaxdd is not None:
            max_drawdowns.append(float(smaxdd))

        scur = to_float(((sres.get("equity", {}) or {}).get("current")))
        speak = to_float(((sres.get("equity", {}) or {}).get("peak")))
        if scur is not None and speak is not None and speak > 0:
            sum_current_equity += float(scur)
            sum_peak_equity += float(speak)
            have_portfolio_values = True

    max_symbol_current_drawdown = max(current_drawdowns) if current_drawdowns else None
    max_symbol_intraday_drawdown = max(max_drawdowns) if max_drawdowns else None
    portfolio_current_drawdown = None
    if have_portfolio_values and sum_peak_equity > 0:
        portfolio_current_drawdown = max(0.0, ((sum_peak_equity - sum_current_equity) / sum_peak_equity) * 100.0)

    drawdown_candidates = [x for x in [max_symbol_current_drawdown, portfolio_current_drawdown] if x is not None]
    current_drawdown_pct = max(drawdown_candidates) if drawdown_candidates else None

    consecutive_losses: Optional[int]
    if all_events:
        all_events.sort(key=lambda x: x[0])
        consecutive_losses = _compute_loss_streak(all_events)
    elif total_losses <= 0:
        consecutive_losses = 0
    elif total_wins <= 0:
        consecutive_losses = total_losses
    else:
        consecutive_losses = None

    file_checks_total = len(symbols) * 2
    file_error_count = 0
    row_total = 0
    row_parse_errors = 0
    for s in symbols:
        sres = symbol_results.get(s, {})
        files = sres.get("files", {}) or {}
        st_status = str(((files.get("paper_state", {}) or {}).get("status", "") or "")).upper()
        eq_status = str(((files.get("paper_equity", {}) or {}).get("status", "") or "")).upper()
        if st_status != "PASS":
            file_error_count += 1
        if eq_status != "PASS":
            file_error_count += 1
        rows = sres.get("rows", {}) or {}
        row_total += int(to_int(rows.get("equity_rows_total", 0)) or 0)
        row_parse_errors += int(to_int(rows.get("equity_row_parse_errors", 0)) or 0)

    file_error_rate = (file_error_count / max(1, file_checks_total)) * 100.0
    row_error_rate = (row_parse_errors / max(1, row_total)) * 100.0 if row_total > 0 else 0.0
    error_rate_pct = max(file_error_rate, row_error_rate)

    pass_symbols = len([s for s in symbols if str((symbol_results.get(s, {}) or {}).get("status", "")).upper() == "PASS"])
    fail_symbols = len(symbols) - pass_symbols
    if pass_symbols <= 0:
        overall_status = "FAIL"
    elif fail_symbols > 0:
        overall_status = "PARTIAL"
    else:
        overall_status = "PASS"

    result = {
        "run_ts": dt.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST"),
        "base_dir": str(base_dir),
        "date": date_str,
        "validated_symbols": symbols,
        "present_symbols": present_symbols,
        "skipped_symbols": skipped_symbols,
        "overall_status": overall_status,
        "input_files": {
            "pattern_templates": str(pattern_csv),
        },
        "canary_metrics": {
            "current_drawdown_pct": round_or_none(current_drawdown_pct, 4),
            "drawdown_pct": round_or_none(current_drawdown_pct, 4),
            "max_intraday_drawdown_pct": round_or_none(max_symbol_intraday_drawdown, 4),
            "portfolio_current_drawdown_pct_est": round_or_none(portfolio_current_drawdown, 4),
            "win_rate_pct": round_or_none(win_rate_pct, 4),
            "baseline_win_rate_pct": round_or_none(baseline_win_rate, 4),
            "win_rate_delta_pct": round_or_none(win_rate_delta_pct, 4),
            "error_rate_pct": round_or_none(error_rate_pct, 4),
            "consecutive_losses": consecutive_losses,
            "observation_trades": observation_trades,
            "trades": observation_trades,
            "wins": total_wins,
            "losses": total_losses,
            "baseline_source": baseline_source,
            "baseline_rows_used": baseline_rows,
        },
        "quality": {
            "file_checks_total": file_checks_total,
            "file_error_count": file_error_count,
            "file_error_rate_pct": round(file_error_rate, 4),
            "row_total": row_total,
            "row_parse_errors": row_parse_errors,
            "row_error_rate_pct": round(row_error_rate, 4),
        },
        "symbol_results": symbol_results,
        "notes": [
            "current_drawdown_pct uses the stricter value between worst symbol drawdown and portfolio estimate.",
            "win_rate_delta_pct compares observed canary win rate vs weighted pattern hit-rate baseline.",
            "consecutive_losses is inferred from realized_pnl change events when possible.",
        ],
    }

    with output_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"[forensics_build_canary_metrics] date={date_str}")
    print(f"[forensics_build_canary_metrics] validated_symbols={','.join(symbols)}")
    print(f"[forensics_build_canary_metrics] present_symbols={','.join(present_symbols)}")
    print(f"[forensics_build_canary_metrics] skipped_symbols={','.join(skipped_symbols)}")
    print(
        f"[forensics_build_canary_metrics] result={overall_status} "
        f"pass_symbols={pass_symbols} fail_symbols={fail_symbols}"
    )
    print(f"[forensics_build_canary_metrics] output_json={output_json}")

    if args.fail_on_errors and pass_symbols <= 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

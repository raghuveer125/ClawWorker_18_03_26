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

from core.utils import to_float_opt as to_float


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_csv_list(raw: str) -> List[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Run post-market quality checks on forensics inputs for selected symbols. "
            "Autodetects latest date folder when --date is not provided."
        )
    )
    p.add_argument("--base-dir", default="postmortem", help="Base folder containing date subfolders")
    p.add_argument("--date", default="", help="Date folder in YYYY-MM-DD (default: latest available)")
    p.add_argument("--symbols", default="SENSEX,NIFTY50", help="Comma-separated symbol allowlist")
    p.add_argument(
        "--report-csv",
        default="",
        help="Output CSV report path (default: <base>/<date>/quality_report_<date>.csv)",
    )
    p.add_argument(
        "--report-json",
        default="",
        help="Output JSON report path (default: <base>/<date>/quality_summary_<date>.json)",
    )
    p.add_argument("--max-decision-duplicate-ratio", type=float, default=0.05)
    p.add_argument("--max-signals-duplicate-ratio", type=float, default=0.95)
    p.add_argument("--max-decision-out-of-order-rows", type=int, default=5)
    p.add_argument("--max-signals-out-of-order-rows", type=int, default=0)
    p.add_argument("--max-missing-minute-ratio", type=float, default=0.05)
    p.add_argument("--max-decision-parse-fail-ratio", type=float, default=0.0)
    p.add_argument("--max-signals-parse-fail-ratio", type=float, default=0.0)
    p.add_argument("--max-take-invalid-quote-ratio", type=float, default=0.0)
    p.add_argument("--max-take-zero-price-ratio", type=float, default=0.0)
    p.add_argument(
        "--fail-on-quality",
        action="store_true",
        help="Exit non-zero when overall quality status is FAIL.",
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


def parse_datetime(date_str: str, time_str: str) -> Optional[dt.datetime]:
    d = (date_str or "").strip()
    t = (time_str or "").strip()
    if not d or not t:
        return None
    if len(t) == 5:
        t = f"{t}:00"
    try:
        return dt.datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def compute_order_metrics(rows: List[Dict[str, str]]) -> Tuple[int, int, List[dt.datetime]]:
    out_of_order = 0
    parse_fail = 0
    parsed: List[dt.datetime] = []
    prev: Optional[dt.datetime] = None

    for row in rows:
        cur = parse_datetime(row.get("date", ""), row.get("time", ""))
        if cur is None:
            parse_fail += 1
            continue
        parsed.append(cur)
        if prev is not None and cur < prev:
            out_of_order += 1
        prev = cur
    return out_of_order, parse_fail, parsed


def compute_missing_minute_ratio(parsed_dts: List[dt.datetime]) -> Tuple[int, int, float]:
    if not parsed_dts:
        return 0, 0, 0.0
    minute_marks = sorted({x.replace(second=0, microsecond=0) for x in parsed_dts})
    start = minute_marks[0]
    end = minute_marks[-1]
    expected = int((end - start).total_seconds() // 60) + 1
    observed = len(minute_marks)
    missing = max(0, expected - observed)
    ratio = (missing / expected) if expected > 0 else 0.0
    return missing, expected, ratio


def compute_duplicate_ratio(headers: List[str], rows: List[Dict[str, str]]) -> Tuple[int, int, float]:
    total = len(rows)
    if total == 0:
        return 0, 0, 0.0
    unique_keys = {tuple(row.get(h, "") for h in headers) for row in rows}
    dup_count = total - len(unique_keys)
    dup_ratio = dup_count / total
    return dup_count, total, dup_ratio


def compute_take_quality(rows: List[Dict[str, str]]) -> Tuple[int, int, int, float, float]:
    take_rows = [r for r in rows if (r.get("action", "") or "").strip().lower() == "take"]
    if not take_rows:
        return 0, 0, 0, 0.0, 0.0

    invalid_quote = 0
    zero_price = 0
    for row in take_rows:
        bid = to_float(row.get("bid", ""))
        ask = to_float(row.get("ask", ""))
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            invalid_quote += 1

        entry = to_float(row.get("entry", ""))
        sl = to_float(row.get("sl", ""))
        t1 = to_float(row.get("t1", ""))
        t2 = to_float(row.get("t2", ""))
        if (
            entry is None or entry <= 0
            or sl is None or sl <= 0
            or t1 is None or t1 <= 0
            or t2 is None or t2 <= 0
        ):
            zero_price += 1

    total = len(take_rows)
    invalid_ratio = invalid_quote / total
    zero_ratio = zero_price / total
    return total, invalid_quote, zero_price, invalid_ratio, zero_ratio


def add_check(
    rows: List[Dict[str, object]],
    *,
    run_ts: str,
    date_str: str,
    symbol: str,
    check_name: str,
    value: float,
    threshold: float,
    unit: str,
    details: str,
    passed: bool,
) -> None:
    rows.append(
        {
            "run_ts": run_ts,
            "date": date_str,
            "symbol": symbol,
            "check_name": check_name,
            "status": "PASS" if passed else "FAIL",
            "value": value,
            "threshold": threshold,
            "unit": unit,
            "details": details,
        }
    )


def write_csv_report(path: Path, rows: List[Dict[str, object]]) -> None:
    cols = [
        "run_ts",
        "date",
        "symbol",
        "check_name",
        "status",
        "value",
        "threshold",
        "unit",
        "details",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in cols})


def write_json_report(path: Path, payload: Dict[str, object]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir)
    symbols = [x.upper() for x in parse_csv_list(args.symbols)]
    run_ts = dt.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")

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

    present_symbols = sorted([d.name.upper() for d in date_dir.iterdir() if d.is_dir()])
    skipped_symbols = [s for s in present_symbols if s not in set(symbols)]
    missing_symbol_folders = [s for s in symbols if s not in present_symbols]

    thresholds = {
        "max_decision_duplicate_ratio": args.max_decision_duplicate_ratio,
        "max_signals_duplicate_ratio": args.max_signals_duplicate_ratio,
        "max_decision_out_of_order_rows": float(args.max_decision_out_of_order_rows),
        "max_signals_out_of_order_rows": float(args.max_signals_out_of_order_rows),
        "max_missing_minute_ratio": args.max_missing_minute_ratio,
        "max_decision_parse_fail_ratio": args.max_decision_parse_fail_ratio,
        "max_signals_parse_fail_ratio": args.max_signals_parse_fail_ratio,
        "max_take_invalid_quote_ratio": args.max_take_invalid_quote_ratio,
        "max_take_zero_price_ratio": args.max_take_zero_price_ratio,
    }

    report_rows: List[Dict[str, object]] = []
    symbol_results: Dict[str, Dict[str, object]] = {}

    for symbol in symbols:
        symbol_dir = date_dir / symbol
        checks_start = len(report_rows)
        symbol_ok = True
        metrics: Dict[str, object] = {}

        if not symbol_dir.exists():
            add_check(
                report_rows,
                run_ts=run_ts,
                date_str=date_str,
                symbol=symbol,
                check_name="symbol_folder_present",
                value=0.0,
                threshold=1.0,
                unit="bool",
                details=f"missing folder: {symbol_dir}",
                passed=False,
            )
            symbol_ok = False
            symbol_results[symbol] = {
                "overall_status": "FAIL",
                "checks": report_rows[checks_start:],
                "metrics": metrics,
            }
            continue

        dec_headers, dec_rows, dec_err = read_csv(symbol_dir / "decision_journal.csv")
        sig_headers, sig_rows, sig_err = read_csv(symbol_dir / "signals.csv")

        if dec_err:
            add_check(
                report_rows,
                run_ts=run_ts,
                date_str=date_str,
                symbol=symbol,
                check_name="decision_file_valid",
                value=0.0,
                threshold=1.0,
                unit="bool",
                details=dec_err,
                passed=False,
            )
            symbol_ok = False
        if sig_err:
            add_check(
                report_rows,
                run_ts=run_ts,
                date_str=date_str,
                symbol=symbol,
                check_name="signals_file_valid",
                value=0.0,
                threshold=1.0,
                unit="bool",
                details=sig_err,
                passed=False,
            )
            symbol_ok = False

        if not dec_err:
            dup_count, total, dup_ratio = compute_duplicate_ratio(dec_headers, dec_rows)
            dec_ooo, dec_parse_fail, dec_dts = compute_order_metrics(dec_rows)
            dec_parse_fail_ratio = (dec_parse_fail / total) if total > 0 else 0.0
            missing_minutes, expected_minutes, missing_ratio = compute_missing_minute_ratio(dec_dts)
            take_total, take_invalid, take_zero, invalid_ratio, zero_ratio = compute_take_quality(dec_rows)

            metrics.update(
                {
                    "decision_rows": total,
                    "decision_duplicate_rows": dup_count,
                    "decision_duplicate_ratio": dup_ratio,
                    "decision_out_of_order_rows": dec_ooo,
                    "decision_parse_fail_rows": dec_parse_fail,
                    "decision_parse_fail_ratio": dec_parse_fail_ratio,
                    "decision_missing_minutes": missing_minutes,
                    "decision_expected_minutes": expected_minutes,
                    "decision_missing_minute_ratio": missing_ratio,
                    "take_rows": take_total,
                    "take_invalid_quote_rows": take_invalid,
                    "take_zero_price_rows": take_zero,
                    "take_invalid_quote_ratio": invalid_ratio,
                    "take_zero_price_ratio": zero_ratio,
                }
            )

            checks = [
                (
                    "decision_duplicate_ratio",
                    dup_ratio,
                    args.max_decision_duplicate_ratio,
                    "%",
                    f"dup={dup_count} total={total}",
                    dup_ratio <= args.max_decision_duplicate_ratio,
                ),
                (
                    "decision_out_of_order_rows",
                    float(dec_ooo),
                    float(args.max_decision_out_of_order_rows),
                    "rows",
                    f"rows={total}",
                    dec_ooo <= args.max_decision_out_of_order_rows,
                ),
                (
                    "decision_parse_fail_ratio",
                    dec_parse_fail_ratio,
                    args.max_decision_parse_fail_ratio,
                    "%",
                    f"parse_fail={dec_parse_fail} total={total}",
                    dec_parse_fail_ratio <= args.max_decision_parse_fail_ratio,
                ),
                (
                    "decision_missing_minute_ratio",
                    missing_ratio,
                    args.max_missing_minute_ratio,
                    "%",
                    f"missing={missing_minutes} expected={expected_minutes}",
                    missing_ratio <= args.max_missing_minute_ratio,
                ),
                (
                    "take_invalid_quote_ratio",
                    invalid_ratio,
                    args.max_take_invalid_quote_ratio,
                    "%",
                    f"take_rows={take_total} invalid={take_invalid}",
                    invalid_ratio <= args.max_take_invalid_quote_ratio,
                ),
                (
                    "take_zero_price_ratio",
                    zero_ratio,
                    args.max_take_zero_price_ratio,
                    "%",
                    f"take_rows={take_total} zero_price={take_zero}",
                    zero_ratio <= args.max_take_zero_price_ratio,
                ),
            ]
            for name, value, threshold, unit, details, passed in checks:
                add_check(
                    report_rows,
                    run_ts=run_ts,
                    date_str=date_str,
                    symbol=symbol,
                    check_name=name,
                    value=value,
                    threshold=threshold,
                    unit=unit,
                    details=details,
                    passed=passed,
                )
                symbol_ok = symbol_ok and passed

        if not sig_err:
            dup_count, total, dup_ratio = compute_duplicate_ratio(sig_headers, sig_rows)
            sig_ooo, sig_parse_fail, _sig_dts = compute_order_metrics(sig_rows)
            sig_parse_fail_ratio = (sig_parse_fail / total) if total > 0 else 0.0

            metrics.update(
                {
                    "signals_rows": total,
                    "signals_duplicate_rows": dup_count,
                    "signals_duplicate_ratio": dup_ratio,
                    "signals_out_of_order_rows": sig_ooo,
                    "signals_parse_fail_rows": sig_parse_fail,
                    "signals_parse_fail_ratio": sig_parse_fail_ratio,
                }
            )

            checks = [
                (
                    "signals_duplicate_ratio",
                    dup_ratio,
                    args.max_signals_duplicate_ratio,
                    "%",
                    f"dup={dup_count} total={total}",
                    dup_ratio <= args.max_signals_duplicate_ratio,
                ),
                (
                    "signals_out_of_order_rows",
                    float(sig_ooo),
                    float(args.max_signals_out_of_order_rows),
                    "rows",
                    f"rows={total}",
                    sig_ooo <= args.max_signals_out_of_order_rows,
                ),
                (
                    "signals_parse_fail_ratio",
                    sig_parse_fail_ratio,
                    args.max_signals_parse_fail_ratio,
                    "%",
                    f"parse_fail={sig_parse_fail} total={total}",
                    sig_parse_fail_ratio <= args.max_signals_parse_fail_ratio,
                ),
            ]
            for name, value, threshold, unit, details, passed in checks:
                add_check(
                    report_rows,
                    run_ts=run_ts,
                    date_str=date_str,
                    symbol=symbol,
                    check_name=name,
                    value=value,
                    threshold=threshold,
                    unit=unit,
                    details=details,
                    passed=passed,
                )
                symbol_ok = symbol_ok and passed

        symbol_results[symbol] = {
            "overall_status": "PASS" if symbol_ok else "FAIL",
            "checks": report_rows[checks_start:],
            "metrics": metrics,
        }

    report_csv = (
        Path(args.report_csv)
        if args.report_csv.strip()
        else (date_dir / f"quality_report_{date_str}.csv")
    )
    report_json = (
        Path(args.report_json)
        if args.report_json.strip()
        else (date_dir / f"quality_summary_{date_str}.json")
    )
    report_csv.parent.mkdir(parents=True, exist_ok=True)
    report_json.parent.mkdir(parents=True, exist_ok=True)

    write_csv_report(report_csv, report_rows)

    pass_symbols = [s for s, r in symbol_results.items() if r["overall_status"] == "PASS"]
    fail_symbols = [s for s, r in symbol_results.items() if r["overall_status"] == "FAIL"]
    overall_status = "PASS" if not fail_symbols else "FAIL"
    summary = {
        "run_ts": run_ts,
        "base_dir": str(base_dir),
        "date": date_str,
        "validated_symbols": symbols,
        "present_symbols": present_symbols,
        "skipped_symbols": skipped_symbols,
        "missing_symbol_folders": missing_symbol_folders,
        "thresholds": thresholds,
        "overall_status": overall_status,
        "pass_symbols": pass_symbols,
        "fail_symbols": fail_symbols,
        "symbol_results": symbol_results,
        "report_csv": str(report_csv),
        "report_json": str(report_json),
    }
    write_json_report(report_json, summary)

    print(f"[forensics_quality_gate] date={date_str}")
    print(f"[forensics_quality_gate] validated_symbols={','.join(symbols)}")
    print(f"[forensics_quality_gate] present_symbols={','.join(present_symbols) or '-'}")
    print(f"[forensics_quality_gate] skipped_symbols={','.join(skipped_symbols) or '-'}")
    print(
        f"[forensics_quality_gate] result={overall_status} "
        f"pass_symbols={len(pass_symbols)} fail_symbols={len(fail_symbols)}"
    )
    print(f"[forensics_quality_gate] report_csv={report_csv}")
    print(f"[forensics_quality_gate] report_json={report_json}")

    if args.fail_on_quality and overall_status != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

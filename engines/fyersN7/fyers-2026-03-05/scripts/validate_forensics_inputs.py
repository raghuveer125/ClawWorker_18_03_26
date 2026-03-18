#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_REQUIRED_FILES = [
    "decision_journal.csv",
    "signals.csv",
    "paper_equity.csv",
    ".signal_state.json",
    ".opportunity_engine_state.json",
    ".paper_trade_state.json",
]


def parse_csv_list(raw: str) -> List[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Validate postmortem daily symbol folders for forensics ingestion. "
            "Autodetects latest date folder when --date is not provided."
        )
    )
    p.add_argument(
        "--base-dir",
        default="postmortem",
        help="Base folder containing date subfolders (default: postmortem)",
    )
    p.add_argument(
        "--date",
        default="",
        help="Date folder in YYYY-MM-DD. If omitted, latest available date is used.",
    )
    p.add_argument(
        "--symbols",
        default="SENSEX,NIFTY50",
        help="Comma-separated symbol allowlist to validate (default: SENSEX,NIFTY50)",
    )
    p.add_argument(
        "--required-files",
        default=",".join(DEFAULT_REQUIRED_FILES),
        help="Comma-separated required file list",
    )
    p.add_argument(
        "--report-csv",
        default="",
        help=(
            "Output CSV report path. "
            "Default: <base>/<date>/forensics_file_validation_report.csv"
        ),
    )
    p.add_argument(
        "--report-json",
        default="",
        help=(
            "Output JSON report path. "
            "Default: <base>/<date>/forensics_file_validation_summary.json"
        ),
    )
    p.add_argument(
        "--fail-on-missing",
        action="store_true",
        help="Exit non-zero when any required file is missing/invalid/empty.",
    )
    return p.parse_args()


def detect_latest_date_folder(base_dir: Path) -> str:
    candidates: List[str] = []
    if not base_dir.exists():
        return ""
    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if not DATE_RE.match(name):
            continue
        candidates.append(name)
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


def check_file(path: Path, fname: str) -> Dict[str, object]:
    out: Dict[str, object] = {
        "exists": False,
        "size_bytes": 0,
        "format_ok": False,
        "non_empty": False,
        "status": "FAIL",
        "reason": "",
    }
    if not path.exists():
        out["reason"] = "missing"
        return out

    out["exists"] = True
    size = path.stat().st_size
    out["size_bytes"] = size
    out["non_empty"] = size > 0
    if size <= 0:
        out["reason"] = "empty"
        return out

    try:
        if fname.endswith(".json"):
            with path.open("r", encoding="utf-8") as f:
                json.load(f)
            out["format_ok"] = True
        elif fname.endswith(".csv"):
            with path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, [])
            out["format_ok"] = len(header) > 0
            if not out["format_ok"]:
                out["reason"] = "csv_missing_header"
                return out
        else:
            out["format_ok"] = True
    except Exception as exc:
        out["reason"] = f"parse_error:{type(exc).__name__}"
        return out

    out["status"] = "PASS"
    out["reason"] = "ok"
    return out


def write_csv_report(path: Path, rows: List[Dict[str, object]]) -> None:
    cols = [
        "run_ts",
        "date",
        "symbol",
        "file_name",
        "status",
        "exists",
        "non_empty",
        "format_ok",
        "size_bytes",
        "reason",
        "file_path",
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
    symbols = [s.upper() for s in parse_csv_list(args.symbols)]
    required_files = parse_csv_list(args.required_files)
    run_ts = dt.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")

    if not symbols:
        print("ERROR: symbol allowlist is empty.", file=sys.stderr)
        return 2
    if not required_files:
        print("ERROR: required-files list is empty.", file=sys.stderr)
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
    allow = set(symbols)
    skipped_symbols = [s for s in present_symbols if s not in allow]
    missing_symbol_folders = [s for s in symbols if s not in present_symbols]

    rows: List[Dict[str, object]] = []
    fail_count = 0
    pass_count = 0

    for symbol in symbols:
        symbol_dir = date_dir / symbol
        for fname in required_files:
            fpath = symbol_dir / fname
            result = check_file(fpath, fname) if symbol_dir.exists() else {
                "exists": False,
                "size_bytes": 0,
                "format_ok": False,
                "non_empty": False,
                "status": "FAIL",
                "reason": "missing_symbol_folder",
            }
            status = str(result["status"])
            if status == "PASS":
                pass_count += 1
            else:
                fail_count += 1

            rows.append(
                {
                    "run_ts": run_ts,
                    "date": date_str,
                    "symbol": symbol,
                    "file_name": fname,
                    "status": status,
                    "exists": result["exists"],
                    "non_empty": result["non_empty"],
                    "format_ok": result["format_ok"],
                    "size_bytes": result["size_bytes"],
                    "reason": result["reason"],
                    "file_path": str(fpath),
                }
            )

    report_csv = (
        Path(args.report_csv)
        if args.report_csv.strip()
        else (date_dir / "forensics_file_validation_report.csv")
    )
    report_json = (
        Path(args.report_json)
        if args.report_json.strip()
        else (date_dir / "forensics_file_validation_summary.json")
    )
    report_csv.parent.mkdir(parents=True, exist_ok=True)
    report_json.parent.mkdir(parents=True, exist_ok=True)

    write_csv_report(report_csv, rows)
    summary = {
        "run_ts": run_ts,
        "base_dir": str(base_dir),
        "date": date_str,
        "validated_symbols": symbols,
        "present_symbols": present_symbols,
        "skipped_symbols": skipped_symbols,
        "missing_symbol_folders": missing_symbol_folders,
        "required_files": required_files,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "overall_status": "PASS" if fail_count == 0 else "FAIL",
        "report_csv": str(report_csv),
        "report_json": str(report_json),
    }
    write_json_report(report_json, summary)

    print(f"[validate_forensics_inputs] date={date_str}")
    print(f"[validate_forensics_inputs] validated_symbols={','.join(symbols)}")
    print(f"[validate_forensics_inputs] present_symbols={','.join(present_symbols) or '-'}")
    print(f"[validate_forensics_inputs] skipped_symbols={','.join(skipped_symbols) or '-'}")
    print(
        f"[validate_forensics_inputs] result={summary['overall_status']} "
        f"pass={pass_count} fail={fail_count}"
    )
    print(f"[validate_forensics_inputs] report_csv={report_csv}")
    print(f"[validate_forensics_inputs] report_json={report_json}")

    if args.fail_on_missing and fail_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

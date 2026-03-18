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


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_csv_list(raw: str) -> List[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Reconstruct canonical per-symbol timelines from postmortem decision/signal CSVs "
            "for SENSEX/NIFTY50 style daily folders."
        )
    )
    p.add_argument("--base-dir", default="postmortem", help="Base folder containing date subfolders")
    p.add_argument("--date", default="", help="Date folder in YYYY-MM-DD (default: latest available)")
    p.add_argument("--symbols", default="SENSEX,NIFTY50", help="Comma-separated symbol allowlist")
    p.add_argument(
        "--output-dir",
        default="",
        help="Output directory (default: <base>/<date>)",
    )
    p.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit non-zero if any symbol folder/file is missing or parse fails occur.",
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


def read_csv_with_lines(path: Path) -> Tuple[List[str], List[Dict[str, str]], str]:
    if not path.exists():
        return [], [], "missing"
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            headers = [h.strip() for h in (reader.fieldnames or []) if h]
            if not headers:
                return [], [], "missing_header"
            rows: List[Dict[str, str]] = []
            for i, raw in enumerate(reader, start=2):
                row: Dict[str, str] = {}
                for h in headers:
                    row[h] = (raw.get(h, "") or "").strip()
                row["_source_line"] = str(i)
                rows.append(row)
        return headers, rows, ""
    except Exception as exc:
        return [], [], f"parse_error:{type(exc).__name__}"


def build_decision_record(row: Dict[str, str], symbol_hint: str) -> Optional[Dict[str, str]]:
    event_dt = parse_datetime(row.get("date", ""), row.get("time", ""))
    if event_dt is None:
        return None
    symbol = row.get("symbol", "").strip() or symbol_hint
    return {
        "ts": event_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "date": row.get("date", ""),
        "time": event_dt.strftime("%H:%M:%S"),
        "symbol": symbol,
        "source": "decision_journal",
        "source_line": row.get("_source_line", ""),
        "spot": row.get("spot", ""),
        "vix": row.get("vix", ""),
        "net_pcr": row.get("net_pcr", ""),
        "max_pain_dist": row.get("max_pain_dist", ""),
        "vote_diff": row.get("vote_diff", ""),
        "vol_dom": row.get("vol_dom", ""),
        "learn_prob": row.get("learn_prob", ""),
        "action": row.get("action", ""),
        "status": row.get("status", ""),
        "final_action": "",
        "side": row.get("side", ""),
        "strike": row.get("strike", ""),
        "entry": row.get("entry", ""),
        "sl": row.get("sl", ""),
        "t1": row.get("t1", ""),
        "t2": row.get("t2", ""),
        "bid": row.get("bid", ""),
        "ask": row.get("ask", ""),
        "spread_pct": row.get("spread_pct", ""),
        "confidence": row.get("confidence", ""),
        "score": row.get("score", ""),
        "reason": row.get("reason", ""),
        "result": row.get("outcome", ""),
        "notes": "",
    }


def build_signal_record(row: Dict[str, str], symbol_hint: str, date_hint: str) -> Optional[Dict[str, str]]:
    date_raw = row.get("date", "").strip() or date_hint
    event_dt = parse_datetime(date_raw, row.get("time", ""))
    if event_dt is None:
        return None
    symbol = row.get("symbol", "").strip() or symbol_hint
    return {
        "ts": event_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "date": date_raw,
        "time": event_dt.strftime("%H:%M:%S"),
        "symbol": symbol,
        "source": "signals",
        "source_line": row.get("_source_line", ""),
        "spot": "",
        "vix": "",
        "net_pcr": "",
        "max_pain_dist": "",
        "vote_diff": "",
        "vol_dom": "",
        "learn_prob": "",
        "action": "",
        "status": "",
        "final_action": row.get("final_action", ""),
        "side": row.get("side", ""),
        "strike": row.get("strike", ""),
        "entry": row.get("entry", ""),
        "sl": row.get("sl", ""),
        "t1": row.get("t1", ""),
        "t2": row.get("t2", ""),
        "bid": "",
        "ask": "",
        "spread_pct": "",
        "confidence": row.get("confidence", ""),
        "score": "",
        "reason": row.get("reason", ""),
        "result": row.get("result", ""),
        "notes": row.get("notes", ""),
    }


def sort_timeline(records: List[Dict[str, str]]) -> List[Dict[str, str]]:
    def key(rec: Dict[str, str]) -> Tuple[str, int, int]:
        src_rank = 0 if rec.get("source") == "decision_journal" else 1
        try:
            line = int(rec.get("source_line", "0") or "0")
        except ValueError:
            line = 0
        return rec.get("ts", ""), src_rank, line

    return sorted(records, key=key)


def write_timeline_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    cols = [
        "ts",
        "date",
        "time",
        "symbol",
        "source",
        "source_line",
        "spot",
        "vix",
        "net_pcr",
        "max_pain_dist",
        "vote_diff",
        "vol_dom",
        "learn_prob",
        "action",
        "status",
        "final_action",
        "side",
        "strike",
        "entry",
        "sl",
        "t1",
        "t2",
        "bid",
        "ask",
        "spread_pct",
        "confidence",
        "score",
        "reason",
        "result",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in cols})


def write_summary_json(path: Path, payload: Dict[str, object]) -> None:
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

    output_dir = Path(args.output_dir) if args.output_dir.strip() else date_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    present_symbols = sorted([d.name.upper() for d in date_dir.iterdir() if d.is_dir()])
    skipped_symbols = [s for s in present_symbols if s not in set(symbols)]
    missing_symbol_folders = [s for s in symbols if s not in present_symbols]

    combined_rows: List[Dict[str, str]] = []
    symbol_results: Dict[str, Dict[str, object]] = {}
    error_count = 0

    for symbol in symbols:
        symbol_dir = date_dir / symbol
        symbol_summary: Dict[str, object] = {
            "status": "PASS",
            "decision_rows_in": 0,
            "signals_rows_in": 0,
            "decision_parse_fail_rows": 0,
            "signals_parse_fail_rows": 0,
            "timeline_rows_out": 0,
            "timeline_file": "",
            "errors": [],
        }

        if not symbol_dir.exists():
            symbol_summary["status"] = "FAIL"
            symbol_summary["errors"] = [f"missing symbol folder: {symbol_dir}"]
            symbol_results[symbol] = symbol_summary
            error_count += 1
            continue

        _dec_headers, dec_rows, dec_err = read_csv_with_lines(symbol_dir / "decision_journal.csv")
        _sig_headers, sig_rows, sig_err = read_csv_with_lines(symbol_dir / "signals.csv")

        if dec_err:
            symbol_summary["status"] = "FAIL"
            symbol_summary["errors"] = symbol_summary["errors"] + [f"decision_journal.csv: {dec_err}"]
            error_count += 1
        if sig_err:
            symbol_summary["status"] = "FAIL"
            symbol_summary["errors"] = symbol_summary["errors"] + [f"signals.csv: {sig_err}"]
            error_count += 1

        symbol_summary["decision_rows_in"] = len(dec_rows)
        symbol_summary["signals_rows_in"] = len(sig_rows)

        timeline_rows: List[Dict[str, str]] = []
        dec_parse_fail = 0
        sig_parse_fail = 0

        if not dec_err:
            for row in dec_rows:
                rec = build_decision_record(row, symbol)
                if rec is None:
                    dec_parse_fail += 1
                    continue
                timeline_rows.append(rec)

        if not sig_err:
            for row in sig_rows:
                rec = build_signal_record(row, symbol, date_str)
                if rec is None:
                    sig_parse_fail += 1
                    continue
                timeline_rows.append(rec)

        symbol_summary["decision_parse_fail_rows"] = dec_parse_fail
        symbol_summary["signals_parse_fail_rows"] = sig_parse_fail
        if dec_parse_fail > 0 or sig_parse_fail > 0:
            symbol_summary["status"] = "FAIL"
            symbol_summary["errors"] = symbol_summary["errors"] + [
                f"parse_fail_rows decision={dec_parse_fail} signals={sig_parse_fail}"
            ]

        timeline_rows = sort_timeline(timeline_rows)
        symbol_summary["timeline_rows_out"] = len(timeline_rows)

        out_file = output_dir / f"forensics_timeline_{symbol}_{date_str}.csv"
        write_timeline_csv(out_file, timeline_rows)
        symbol_summary["timeline_file"] = str(out_file)

        combined_rows.extend(timeline_rows)
        symbol_results[symbol] = symbol_summary

    combined_rows = sorted(
        combined_rows,
        key=lambda r: (
            r.get("ts", ""),
            r.get("symbol", ""),
            0 if r.get("source") == "decision_journal" else 1,
            int(r.get("source_line", "0") or "0"),
        ),
    )
    combined_file = output_dir / f"forensics_timeline_combined_{date_str}.csv"
    write_timeline_csv(combined_file, combined_rows)

    overall_status = "PASS"
    for s in symbol_results.values():
        if s.get("status") != "PASS":
            overall_status = "FAIL"
            break

    summary = {
        "run_ts": run_ts,
        "base_dir": str(base_dir),
        "date": date_str,
        "validated_symbols": symbols,
        "present_symbols": present_symbols,
        "skipped_symbols": skipped_symbols,
        "missing_symbol_folders": missing_symbol_folders,
        "overall_status": overall_status,
        "error_count": error_count,
        "symbol_results": symbol_results,
        "combined_timeline_rows": len(combined_rows),
        "combined_timeline_file": str(combined_file),
    }
    summary_file = output_dir / f"forensics_timeline_summary_{date_str}.json"
    write_summary_json(summary_file, summary)

    print(f"[forensics_reconstruct_timeline] date={date_str}")
    print(f"[forensics_reconstruct_timeline] validated_symbols={','.join(symbols)}")
    print(f"[forensics_reconstruct_timeline] present_symbols={','.join(present_symbols) or '-'}")
    print(f"[forensics_reconstruct_timeline] skipped_symbols={','.join(skipped_symbols) or '-'}")
    print(f"[forensics_reconstruct_timeline] overall_status={overall_status} error_count={error_count}")
    print(f"[forensics_reconstruct_timeline] combined_timeline={combined_file}")
    print(f"[forensics_reconstruct_timeline] summary_json={summary_file}")

    if args.fail_on_errors and (overall_status != "PASS"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

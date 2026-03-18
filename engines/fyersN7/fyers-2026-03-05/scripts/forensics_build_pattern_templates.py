#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_csv_list(raw: str) -> List[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Build pattern_templates from trigger_signals across recent daily folders."
        )
    )
    p.add_argument("--base-dir", default="postmortem", help="Base folder containing date subfolders")
    p.add_argument("--date", default="", help="Date folder in YYYY-MM-DD (default: latest available)")
    p.add_argument("--symbols", default="SENSEX,NIFTY50", help="Comma-separated symbol allowlist")
    p.add_argument(
        "--input-dir",
        default="",
        help="Input directory for target date trigger file (default: <base>/<date>)",
    )
    p.add_argument(
        "--output-dir",
        default="",
        help="Output directory (default: <base>/<date>)",
    )
    p.add_argument(
        "--history-days",
        type=int,
        default=40,
        help="How many latest date folders (<= target date) to scan for trigger history",
    )
    p.add_argument(
        "--min-sample-count",
        type=int,
        default=2,
        help="Minimum sample count to keep a pattern in output",
    )
    p.add_argument(
        "--decay-half-life-days",
        type=float,
        default=10.0,
        help="Half-life (days) for decay_score computation",
    )
    p.add_argument(
        "--score-neutral",
        type=float,
        default=60.0,
        help="Neutral score baseline for expectancy proxy calculation",
    )
    p.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit non-zero when target trigger file is missing or parse errors occur.",
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


def list_date_folders(base_dir: Path) -> List[str]:
    out: List[str] = []
    if not base_dir.exists():
        return out
    for child in base_dir.iterdir():
        if child.is_dir() and DATE_RE.match(child.name):
            out.append(child.name)
    out.sort()
    return out


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
            for i, raw in enumerate(reader, start=2):
                row: Dict[str, str] = {}
                for h in headers:
                    row[h] = (raw.get(h, "") or "").strip()
                row["_source_line"] = str(i)
                rows.append(row)
        return headers, rows, ""
    except Exception as exc:
        return [], [], f"parse_error:{type(exc).__name__}"


def to_float(v: str) -> Optional[float]:
    try:
        return float((v or "").strip())
    except Exception:
        return None


def parse_row_ts(row: Dict[str, str], default_date: str) -> Optional[dt.datetime]:
    d = (row.get("date", "") or "").strip() or default_date
    t = (row.get("time", "") or "").strip()
    if not d or not t:
        return None
    if len(t) == 5:
        t = f"{t}:00"
    try:
        return dt.datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def normalized_trigger_combo(row: Dict[str, str]) -> Tuple[str, str]:
    trigger_type = ((row.get("trigger_type", "") or "").strip().upper() or "UNKNOWN_TRIGGER")
    rec_action = ((row.get("recommended_action", "") or "").strip().upper() or "WATCH")
    vol_state = ((row.get("vol_state", "") or "").strip().upper() or "UNKNOWN_VOL")
    combo = f"{trigger_type}|{rec_action}|{vol_state}"
    return combo, trigger_type


def pattern_id(symbol: str, regime: str, trigger_combo: str) -> str:
    raw = f"{symbol}|{regime}|{trigger_combo}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"PTN_{digest}"


def decay_score(days_since_last: int, half_life_days: float) -> float:
    half_life = max(0.1, half_life_days)
    return math.exp(-math.log(2.0) * max(0, days_since_last) / half_life)


def write_csv(path: Path, rows: List[Dict[str, object]], cols: List[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in cols})


def write_json(path: Path, payload: Dict[str, object]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


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

    output_dir = Path(args.output_dir) if args.output_dir.strip() else date_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    target_input_dir = Path(args.input_dir) if args.input_dir.strip() else date_dir
    all_dates = [d for d in list_date_folders(base_dir) if d <= date_str]
    history_days = max(1, int(args.history_days))
    scan_dates = all_dates[-history_days:] if all_dates else [date_str]
    if date_str not in scan_dates:
        scan_dates.append(date_str)
        scan_dates.sort()

    allow = set(symbols)
    score_parse_fail_rows = 0
    ts_parse_fail_rows = 0
    rows_scanned = 0
    source_files_used: List[str] = []
    source_files_missing: List[str] = []
    source_files_error: List[str] = []
    target_file_present = False

    # key: (symbol, regime, trigger_combo)
    stats: Dict[Tuple[str, str, str], Dict[str, object]] = {}

    for d in scan_dates:
        src_dir = target_input_dir if d == date_str else (base_dir / d)
        trig_file = src_dir / f"trigger_signals_{d}.csv"
        _h, rows, read_err = read_csv(trig_file)
        if read_err:
            if read_err == "missing":
                source_files_missing.append(str(trig_file))
            else:
                source_files_error.append(f"{trig_file}:{read_err}")
            continue

        source_files_used.append(str(trig_file))
        if d == date_str:
            target_file_present = True

        for row in rows:
            symbol = ((row.get("symbol", "") or "").strip().upper())
            if symbol not in allow:
                continue
            rows_scanned += 1

            regime = ((row.get("regime", "") or "").strip().upper() or "UNKNOWN")
            combo, trigger_type = normalized_trigger_combo(row)
            key = (symbol, regime, combo)

            rec = stats.get(key)
            if rec is None:
                rec = {
                    "symbol": symbol,
                    "regime": regime,
                    "trigger_combo": combo,
                    "trigger_type": trigger_type,
                    "sample_count": 0,
                    "actionable_count": 0,
                    "high_priority_count": 0,
                    "score_sum": 0.0,
                    "first_seen_dt": None,
                    "last_seen_dt": None,
                    "source_dates": set(),
                }
                stats[key] = rec

            score = to_float(row.get("score", ""))
            if score is None:
                score = 0.0
                score_parse_fail_rows += 1

            t = parse_row_ts(row, d)
            if t is None:
                ts_parse_fail_rows += 1
            else:
                first_seen = rec["first_seen_dt"]
                last_seen = rec["last_seen_dt"]
                if first_seen is None or t < first_seen:
                    rec["first_seen_dt"] = t
                if last_seen is None or t > last_seen:
                    rec["last_seen_dt"] = t

            rec["sample_count"] = int(rec["sample_count"]) + 1
            rec["score_sum"] = float(rec["score_sum"]) + float(score)
            if (row.get("actionable", "") or "").strip().upper() == "Y":
                rec["actionable_count"] = int(rec["actionable_count"]) + 1
            if (row.get("priority", "") or "").strip().upper() == "HIGH":
                rec["high_priority_count"] = int(rec["high_priority_count"]) + 1
            rec["source_dates"].add(d)

    target_date_obj = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    min_samples = max(1, int(args.min_sample_count))
    neutral = float(args.score_neutral)

    pattern_rows: List[Dict[str, object]] = []
    symbol_stats: Dict[str, Dict[str, object]] = defaultdict(
        lambda: {
            "patterns": 0,
            "sample_count": 0,
            "actionable_count": 0,
            "high_priority_count": 0,
        }
    )

    for rec in stats.values():
        sample_count = int(rec["sample_count"])
        if sample_count < min_samples:
            continue

        score_sum = float(rec["score_sum"])
        avg_score = score_sum / max(1, sample_count)
        actionable_count = int(rec["actionable_count"])
        high_priority_count = int(rec["high_priority_count"])
        hit_rate = (actionable_count / sample_count) * 100.0

        # Proxy until realized trade outcomes are linked to triggers.
        edge_component = (avg_score - neutral) / max(1.0, (100.0 - neutral))
        expectancy = max(-1.0, min(1.0, edge_component * (hit_rate / 100.0)))

        first_seen_dt = rec["first_seen_dt"]
        last_seen_dt = rec["last_seen_dt"]
        if last_seen_dt is None:
            days_since = history_days
            last_seen_s = ""
        else:
            days_since = (target_date_obj - last_seen_dt.date()).days
            last_seen_s = last_seen_dt.strftime("%Y-%m-%d %H:%M:%S")

        first_seen_s = first_seen_dt.strftime("%Y-%m-%d %H:%M:%S") if first_seen_dt else ""
        decay = decay_score(days_since, float(args.decay_half_life_days))

        symbol = str(rec["symbol"])
        regime = str(rec["regime"])
        combo = str(rec["trigger_combo"])
        row = {
            "pattern_id": pattern_id(symbol, regime, combo),
            "symbol": symbol,
            "regime": regime,
            "trigger_combo": combo,
            "sample_count": sample_count,
            "hit_rate": round(hit_rate, 2),
            "expectancy": round(expectancy, 4),
            "last_seen": last_seen_s,
            "decay_score": round(decay, 4),
            "avg_score": round(avg_score, 2),
            "actionable_count": actionable_count,
            "high_priority_count": high_priority_count,
            "first_seen": first_seen_s,
            "source_days": len(rec["source_dates"]),
            "metric_mode": "proxy_from_trigger_score",
        }
        pattern_rows.append(row)

        symbol_stats[symbol]["patterns"] = int(symbol_stats[symbol]["patterns"]) + 1
        symbol_stats[symbol]["sample_count"] = int(symbol_stats[symbol]["sample_count"]) + sample_count
        symbol_stats[symbol]["actionable_count"] = int(symbol_stats[symbol]["actionable_count"]) + actionable_count
        symbol_stats[symbol]["high_priority_count"] = int(
            symbol_stats[symbol]["high_priority_count"]
        ) + high_priority_count

    pattern_rows.sort(
        key=lambda x: (
            str(x["symbol"]),
            -int(x["sample_count"]),
            -float(x["expectancy"]),
            str(x["trigger_combo"]),
        )
    )

    pattern_file = output_dir / f"pattern_templates_{date_str}.csv"
    pattern_cols = [
        "pattern_id",
        "symbol",
        "regime",
        "trigger_combo",
        "sample_count",
        "hit_rate",
        "expectancy",
        "last_seen",
        "decay_score",
        "avg_score",
        "actionable_count",
        "high_priority_count",
        "first_seen",
        "source_days",
        "metric_mode",
    ]
    write_csv(pattern_file, pattern_rows, pattern_cols)

    overall_status = "PASS"
    if not target_file_present:
        overall_status = "FAIL"
    elif source_files_error:
        overall_status = "FAIL"

    summary = {
        "run_ts": dt.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST"),
        "base_dir": str(base_dir),
        "date": date_str,
        "validated_symbols": symbols,
        "history_days": history_days,
        "scan_dates": scan_dates,
        "min_sample_count": min_samples,
        "decay_half_life_days": args.decay_half_life_days,
        "score_neutral": args.score_neutral,
        "overall_status": overall_status,
        "rows_scanned": rows_scanned,
        "score_parse_fail_rows": score_parse_fail_rows,
        "ts_parse_fail_rows": ts_parse_fail_rows,
        "source_files_used": source_files_used,
        "source_files_missing": source_files_missing,
        "source_files_error": source_files_error,
        "target_file_present": target_file_present,
        "pattern_count": len(pattern_rows),
        "pattern_file": str(pattern_file),
        "symbol_stats": symbol_stats,
    }
    summary_file = output_dir / f"pattern_templates_summary_{date_str}.json"
    write_json(summary_file, summary)

    print(f"[forensics_build_pattern_templates] date={date_str}")
    print(f"[forensics_build_pattern_templates] validated_symbols={','.join(symbols)}")
    print(f"[forensics_build_pattern_templates] scan_dates={','.join(scan_dates)}")
    print(
        f"[forensics_build_pattern_templates] result={overall_status} "
        f"patterns={len(pattern_rows)} rows_scanned={rows_scanned}"
    )
    print(f"[forensics_build_pattern_templates] pattern_file={pattern_file}")
    print(f"[forensics_build_pattern_templates] summary_json={summary_file}")

    if args.fail_on_errors and overall_status != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import math
import re
import statistics
import sys
from collections import defaultdict
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
            "Detect market regimes and turning points from reconstructed forensics timelines."
        )
    )
    p.add_argument("--base-dir", default="postmortem", help="Base folder containing date subfolders")
    p.add_argument("--date", default="", help="Date folder in YYYY-MM-DD (default: latest available)")
    p.add_argument("--symbols", default="SENSEX,NIFTY50", help="Comma-separated symbol allowlist")
    p.add_argument(
        "--timeline-dir",
        default="",
        help="Directory containing forensics_timeline_<SYMBOL>_<DATE>.csv (default: <base>/<date>)",
    )
    p.add_argument(
        "--output-dir",
        default="",
        help="Output directory (default: <base>/<date>)",
    )
    p.add_argument("--window-minutes", type=int, default=5, help="Lookback minutes for trend move")
    p.add_argument("--atr-window", type=int, default=5, help="ATR lookback on minute bars")
    p.add_argument("--trend-atr-mult", type=float, default=1.2, help="Trend threshold multiplier on ATR")
    p.add_argument("--min-segment-minutes", type=int, default=2, help="Minimum segment duration to retain")
    p.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit non-zero if any symbol data is missing or parse quality is too low.",
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


def parse_ts(ts_raw: str, date_raw: str, time_raw: str) -> Optional[dt.datetime]:
    ts_s = (ts_raw or "").strip()
    if ts_s:
        try:
            return dt.datetime.strptime(ts_s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    d = (date_raw or "").strip()
    t = (time_raw or "").strip()
    if not d or not t:
        return None
    if len(t) == 5:
        t = f"{t}:00"
    try:
        return dt.datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


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


def load_symbol_input(
    base_date_dir: Path,
    timeline_dir: Path,
    symbol: str,
    date_str: str,
) -> Tuple[List[Dict[str, str]], str, Dict[str, int]]:
    """
    Prefer canonical timeline. Fallback to decision_journal.csv if timeline missing
    or lacks spot column.
    """
    metrics = {
        "timeline_rows": 0,
        "decision_rows": 0,
        "used_timeline_rows": 0,
        "used_decision_rows": 0,
    }

    timeline_path = timeline_dir / f"forensics_timeline_{symbol}_{date_str}.csv"
    t_headers, t_rows, t_err = read_csv(timeline_path)
    if not t_err:
        metrics["timeline_rows"] = len(t_rows)
        has_spot = "spot" in t_headers
        if has_spot:
            usable = [r for r in t_rows if (r.get("source", "") == "decision_journal")]
            if usable:
                metrics["used_timeline_rows"] = len(usable)
                return usable, "", metrics

    dec_path = base_date_dir / symbol / "decision_journal.csv"
    d_headers, d_rows, d_err = read_csv(dec_path)
    if d_err:
        if t_err:
            return [], f"timeline:{t_err}; decision:{d_err}", metrics
        return [], f"decision:{d_err}", metrics
    metrics["decision_rows"] = len(d_rows)
    metrics["used_decision_rows"] = len(d_rows)

    # normalize into timeline-like rows
    normalized: List[Dict[str, str]] = []
    for r in d_rows:
        ts = parse_ts("", r.get("date", ""), r.get("time", ""))
        if ts is None:
            continue
        normalized.append(
            {
                "ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "date": r.get("date", ""),
                "time": ts.strftime("%H:%M:%S"),
                "symbol": r.get("symbol", "") or symbol,
                "source": "decision_journal",
                "spot": r.get("spot", ""),
                "vix": r.get("vix", ""),
                "net_pcr": r.get("net_pcr", ""),
                "max_pain_dist": r.get("max_pain_dist", ""),
                "action": r.get("action", ""),
                "status": r.get("status", ""),
                "reason": r.get("reason", ""),
            }
        )
    return normalized, "", metrics


def build_minute_bars(rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, object]], int]:
    by_minute: Dict[dt.datetime, Dict[str, object]] = {}
    parse_fail = 0

    for row in rows:
        ts = parse_ts(row.get("ts", ""), row.get("date", ""), row.get("time", ""))
        spot = to_float(row.get("spot", ""))
        if ts is None or spot is None:
            parse_fail += 1
            continue
        minute = ts.replace(second=0, microsecond=0)
        vix = to_float(row.get("vix", ""))
        pcr = to_float(row.get("net_pcr", ""))

        bar = by_minute.get(minute)
        if bar is None:
            by_minute[minute] = {
                "minute": minute,
                "open": spot,
                "high": spot,
                "low": spot,
                "close": spot,
                "spot_values": [spot],
                "vix_values": [vix] if vix is not None else [],
                "pcr_values": [pcr] if pcr is not None else [],
                "row_count": 1,
            }
        else:
            bar["high"] = max(float(bar["high"]), spot)
            bar["low"] = min(float(bar["low"]), spot)
            bar["close"] = spot
            bar["spot_values"].append(spot)
            if vix is not None:
                bar["vix_values"].append(vix)
            if pcr is not None:
                bar["pcr_values"].append(pcr)
            bar["row_count"] = int(bar["row_count"]) + 1

    minute_bars = [by_minute[k] for k in sorted(by_minute)]
    for bar in minute_bars:
        vix_vals = bar["vix_values"]
        pcr_vals = bar["pcr_values"]
        bar["avg_vix"] = statistics.mean(vix_vals) if vix_vals else None
        bar["avg_net_pcr"] = statistics.mean(pcr_vals) if pcr_vals else None
        bar["median_spot"] = statistics.median(bar["spot_values"]) if bar["spot_values"] else None
    return minute_bars, parse_fail


def rolling_mean(values: List[float], idx: int, window: int) -> Optional[float]:
    if idx < 0:
        return None
    start = max(0, idx - window + 1)
    chunk = values[start : idx + 1]
    if not chunk:
        return None
    return sum(chunk) / len(chunk)


def compute_indicators(
    bars: List[Dict[str, object]],
    window_minutes: int,
    atr_window: int,
    trend_atr_mult: float,
) -> List[Dict[str, object]]:
    if not bars:
        return []

    trs: List[float] = []
    closes: List[float] = [float(b["close"]) for b in bars]

    for i, b in enumerate(bars):
        high = float(b["high"])
        low = float(b["low"])
        prev_close = closes[i - 1] if i > 0 else float(b["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    atr_list: List[Optional[float]] = []
    for i in range(len(bars)):
        atr_list.append(rolling_mean(trs, i, atr_window))

    valid_atr = [x for x in atr_list if x is not None]
    if valid_atr:
        q33 = statistics.quantiles(valid_atr, n=3)[0]
        q66 = statistics.quantiles(valid_atr, n=3)[1]
    else:
        q33 = 0.0
        q66 = 0.0

    out: List[Dict[str, object]] = []
    for i, b in enumerate(bars):
        close = closes[i]
        lookback_idx = max(0, i - window_minutes)
        move = close - closes[lookback_idx]
        atr = atr_list[i] if atr_list[i] is not None else 0.0
        atr_safe = atr if atr > 1e-9 else 1e-9
        ratio = abs(move) / atr_safe

        if abs(move) >= trend_atr_mult * atr and move > 0:
            regime = "TREND_UP"
        elif abs(move) >= trend_atr_mult * atr and move < 0:
            regime = "TREND_DOWN"
        else:
            regime = "SIDEWAYS"

        if atr <= q33:
            vol_state = "LOW_VOL"
        elif atr <= q66:
            vol_state = "MID_VOL"
        else:
            vol_state = "HIGH_VOL"

        if regime == "SIDEWAYS":
            confidence = int(max(45, min(90, 75 - ratio * 15)))
        else:
            confidence = int(max(55, min(99, 50 + ratio * 20)))

        row = dict(b)
        row.update(
            {
                "atr": atr,
                "move_window": move,
                "move_atr_ratio": ratio,
                "regime": regime,
                "vol_state": vol_state,
                "confidence": confidence,
            }
        )
        out.append(row)

    return out


def merge_small_segments(
    rows: List[Dict[str, object]],
    min_segment_minutes: int,
) -> List[Dict[str, object]]:
    if not rows:
        return rows

    # first pass segments
    segments: List[Tuple[int, int, str]] = []
    start = 0
    curr = rows[0]["regime"]
    for i in range(1, len(rows)):
        if rows[i]["regime"] != curr:
            segments.append((start, i - 1, str(curr)))
            start = i
            curr = rows[i]["regime"]
    segments.append((start, len(rows) - 1, str(curr)))

    # merge short segments into neighbors
    for seg_idx, (s, e, _reg) in enumerate(segments):
        dur = e - s + 1
        if dur >= min_segment_minutes:
            continue
        prev_reg = segments[seg_idx - 1][2] if seg_idx > 0 else None
        next_reg = segments[seg_idx + 1][2] if seg_idx < len(segments) - 1 else None
        replacement = prev_reg or next_reg or rows[s]["regime"]
        for i in range(s, e + 1):
            rows[i]["regime"] = replacement

    return rows


def build_regime_segments(rows: List[Dict[str, object]], symbol: str, date_str: str) -> List[Dict[str, object]]:
    if not rows:
        return []
    segments: List[Dict[str, object]] = []
    start = 0
    curr = rows[0]["regime"]
    for i in range(1, len(rows)):
        if rows[i]["regime"] != curr:
            segments.append(_segment_row(rows, start, i - 1, symbol, date_str))
            start = i
            curr = rows[i]["regime"]
    segments.append(_segment_row(rows, start, len(rows) - 1, symbol, date_str))
    return segments


def _segment_row(rows: List[Dict[str, object]], s: int, e: int, symbol: str, date_str: str) -> Dict[str, object]:
    chunk = rows[s : e + 1]
    start_row = chunk[0]
    end_row = chunk[-1]
    start_spot = float(start_row["close"])
    end_spot = float(end_row["close"])
    duration = len(chunk)
    change_pct = ((end_spot - start_spot) / start_spot * 100.0) if abs(start_spot) > 1e-9 else 0.0
    vix_vals = [x["avg_vix"] for x in chunk if x.get("avg_vix") is not None]
    pcr_vals = [x["avg_net_pcr"] for x in chunk if x.get("avg_net_pcr") is not None]
    conf_vals = [int(x["confidence"]) for x in chunk]
    vol_states = [str(x["vol_state"]) for x in chunk]
    vol_state = max(set(vol_states), key=vol_states.count)

    return {
        "symbol": symbol,
        "date": date_str,
        "start_time": start_row["minute"].strftime("%H:%M:%S"),
        "end_time": end_row["minute"].strftime("%H:%M:%S"),
        "regime": str(start_row["regime"]),
        "vol_state": vol_state,
        "confidence": int(round(sum(conf_vals) / len(conf_vals))),
        "duration_min": duration,
        "start_spot": round(start_spot, 2),
        "end_spot": round(end_spot, 2),
        "spot_change_pct": round(change_pct, 4),
        "avg_vix": round(statistics.mean(vix_vals), 4) if vix_vals else "",
        "avg_net_pcr": round(statistics.mean(pcr_vals), 4) if pcr_vals else "",
        "source_rows": int(sum(int(x["row_count"]) for x in chunk)),
    }


def build_turning_points(
    regime_segments: List[Dict[str, object]],
    minute_rows: List[Dict[str, object]],
    symbol: str,
    date_str: str,
) -> List[Dict[str, object]]:
    tps: List[Dict[str, object]] = []

    # Regime transition based turning points
    for i in range(1, len(regime_segments)):
        prev = regime_segments[i - 1]
        cur = regime_segments[i]
        prev_reg = str(prev["regime"])
        cur_reg = str(cur["regime"])
        if prev_reg == cur_reg:
            continue

        if prev_reg.startswith("TREND") and cur_reg.startswith("TREND"):
            tp_type = "REVERSAL"
        elif prev_reg == "SIDEWAYS" and cur_reg.startswith("TREND"):
            tp_type = "BREAKOUT"
        elif prev_reg.startswith("TREND") and cur_reg == "SIDEWAYS":
            tp_type = "EXHAUSTION"
        else:
            tp_type = "TRANSITION"

        prev_vix = to_float(str(prev.get("avg_vix", "")))
        cur_vix = to_float(str(cur.get("avg_vix", "")))
        prev_pcr = to_float(str(prev.get("avg_net_pcr", "")))
        cur_pcr = to_float(str(cur.get("avg_net_pcr", "")))

        confirmations = ["regime_flip"]
        if prev_vix is not None and cur_vix is not None and abs(cur_vix - prev_vix) >= 0.2:
            confirmations.append("vix_shift")
        if prev_pcr is not None and cur_pcr is not None and abs(cur_pcr - prev_pcr) >= 0.05:
            confirmations.append("pcr_shift")
        if int(prev["duration_min"]) >= 3 and int(cur["duration_min"]) >= 3:
            confirmations.append("sustained_blocks")

        strength = abs(float(prev["spot_change_pct"])) + abs(float(cur["spot_change_pct"]))
        tps.append(
            {
                "symbol": symbol,
                "date": date_str,
                "time": str(cur["start_time"]),
                "tp_type": tp_type,
                "strength": round(strength, 4),
                "confirmations": len(confirmations),
                "context": ",".join(confirmations),
                "from_regime": prev_reg,
                "to_regime": cur_reg,
            }
        )

    # Local extremum turning points (simple 3-point rule with ATR filter)
    closes = [float(r["close"]) for r in minute_rows]
    atrs = [float(r["atr"]) for r in minute_rows]
    for i in range(1, len(minute_rows) - 1):
        c_prev, c_cur, c_next = closes[i - 1], closes[i], closes[i + 1]
        atr = atrs[i] if atrs[i] > 1e-9 else 1e-9
        t_time = minute_rows[i]["minute"].strftime("%H:%M:%S")
        if c_cur > c_prev and c_cur > c_next and (c_cur - max(c_prev, c_next)) >= 0.6 * atr:
            tps.append(
                {
                    "symbol": symbol,
                    "date": date_str,
                    "time": t_time,
                    "tp_type": "LOCAL_PEAK",
                    "strength": round((c_cur - max(c_prev, c_next)) / atr, 4),
                    "confirmations": 1,
                    "context": "local_extrema",
                    "from_regime": minute_rows[i].get("regime", ""),
                    "to_regime": "",
                }
            )
        elif c_cur < c_prev and c_cur < c_next and (min(c_prev, c_next) - c_cur) >= 0.6 * atr:
            tps.append(
                {
                    "symbol": symbol,
                    "date": date_str,
                    "time": t_time,
                    "tp_type": "LOCAL_TROUGH",
                    "strength": round((min(c_prev, c_next) - c_cur) / atr, 4),
                    "confirmations": 1,
                    "context": "local_extrema",
                    "from_regime": minute_rows[i].get("regime", ""),
                    "to_regime": "",
                }
            )

    tps.sort(key=lambda x: (x["time"], x["tp_type"]))
    return tps


def write_csv(path: Path, rows: List[Dict[str, object]], cols: List[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in cols})


def write_summary(path: Path, payload: Dict[str, object]) -> None:
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

    timeline_dir = Path(args.timeline_dir) if args.timeline_dir.strip() else date_dir
    output_dir = Path(args.output_dir) if args.output_dir.strip() else date_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    present_symbols = sorted([d.name.upper() for d in date_dir.iterdir() if d.is_dir()])
    skipped_symbols = [s for s in present_symbols if s not in set(symbols)]
    missing_symbol_folders = [s for s in symbols if s not in present_symbols]

    combined_regimes: List[Dict[str, object]] = []
    combined_turning_points: List[Dict[str, object]] = []
    symbol_results: Dict[str, Dict[str, object]] = {}
    error_count = 0

    for symbol in symbols:
        rows, load_err, load_metrics = load_symbol_input(date_dir, timeline_dir, symbol, date_str)
        if load_err:
            symbol_results[symbol] = {
                "status": "FAIL",
                "error": load_err,
                "load_metrics": load_metrics,
            }
            error_count += 1
            continue

        minute_bars, parse_fail = build_minute_bars(rows)
        if len(minute_bars) < 10:
            symbol_results[symbol] = {
                "status": "FAIL",
                "error": f"insufficient minute bars: {len(minute_bars)}",
                "parse_fail_rows": parse_fail,
                "load_metrics": load_metrics,
            }
            error_count += 1
            continue

        minute_rows = compute_indicators(
            minute_bars,
            window_minutes=max(1, args.window_minutes),
            atr_window=max(1, args.atr_window),
            trend_atr_mult=max(0.1, args.trend_atr_mult),
        )
        minute_rows = merge_small_segments(minute_rows, max(1, args.min_segment_minutes))
        regime_segments = build_regime_segments(minute_rows, symbol, date_str)
        turning_points = build_turning_points(regime_segments, minute_rows, symbol, date_str)

        regime_file = output_dir / f"forensics_regime_table_{symbol}_{date_str}.csv"
        tp_file = output_dir / f"forensics_turning_points_{symbol}_{date_str}.csv"
        write_csv(
            regime_file,
            regime_segments,
            [
                "symbol",
                "date",
                "start_time",
                "end_time",
                "regime",
                "vol_state",
                "confidence",
                "duration_min",
                "start_spot",
                "end_spot",
                "spot_change_pct",
                "avg_vix",
                "avg_net_pcr",
                "source_rows",
            ],
        )
        write_csv(
            tp_file,
            turning_points,
            [
                "symbol",
                "date",
                "time",
                "tp_type",
                "strength",
                "confirmations",
                "context",
                "from_regime",
                "to_regime",
            ],
        )

        combined_regimes.extend(regime_segments)
        combined_turning_points.extend(turning_points)

        symbol_results[symbol] = {
            "status": "PASS",
            "parse_fail_rows": parse_fail,
            "minute_bars": len(minute_bars),
            "regime_segments": len(regime_segments),
            "turning_points": len(turning_points),
            "regime_file": str(regime_file),
            "turning_points_file": str(tp_file),
            "load_metrics": load_metrics,
        }

    combined_regimes.sort(key=lambda x: (x["symbol"], x["start_time"]))
    combined_turning_points.sort(key=lambda x: (x["symbol"], x["time"], x["tp_type"]))

    regime_table_file = output_dir / f"regime_table_{date_str}.csv"
    turning_points_file = output_dir / f"turning_points_{date_str}.csv"
    write_csv(
        regime_table_file,
        combined_regimes,
        [
            "symbol",
            "date",
            "start_time",
            "end_time",
            "regime",
            "vol_state",
            "confidence",
            "duration_min",
            "start_spot",
            "end_spot",
            "spot_change_pct",
            "avg_vix",
            "avg_net_pcr",
            "source_rows",
        ],
    )
    write_csv(
        turning_points_file,
        combined_turning_points,
        [
            "symbol",
            "date",
            "time",
            "tp_type",
            "strength",
            "confirmations",
            "context",
            "from_regime",
            "to_regime",
        ],
    )

    overall_status = "PASS"
    for s in symbol_results.values():
        if s.get("status") != "PASS":
            overall_status = "FAIL"
            break

    summary = {
        "run_ts": dt.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST"),
        "base_dir": str(base_dir),
        "date": date_str,
        "validated_symbols": symbols,
        "present_symbols": present_symbols,
        "skipped_symbols": skipped_symbols,
        "missing_symbol_folders": missing_symbol_folders,
        "overall_status": overall_status,
        "error_count": error_count,
        "window_minutes": args.window_minutes,
        "atr_window": args.atr_window,
        "trend_atr_mult": args.trend_atr_mult,
        "min_segment_minutes": args.min_segment_minutes,
        "symbol_results": symbol_results,
        "regime_table_file": str(regime_table_file),
        "turning_points_file": str(turning_points_file),
    }
    summary_file = output_dir / f"regime_summary_{date_str}.json"
    write_summary(summary_file, summary)

    print(f"[forensics_detect_regimes] date={date_str}")
    print(f"[forensics_detect_regimes] validated_symbols={','.join(symbols)}")
    print(f"[forensics_detect_regimes] present_symbols={','.join(present_symbols) or '-'}")
    print(f"[forensics_detect_regimes] skipped_symbols={','.join(skipped_symbols) or '-'}")
    print(
        f"[forensics_detect_regimes] result={overall_status} "
        f"regime_rows={len(combined_regimes)} turning_points={len(combined_turning_points)}"
    )
    print(f"[forensics_detect_regimes] regime_table={regime_table_file}")
    print(f"[forensics_detect_regimes] turning_points={turning_points_file}")
    print(f"[forensics_detect_regimes] summary_json={summary_file}")

    if args.fail_on_errors and overall_status != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

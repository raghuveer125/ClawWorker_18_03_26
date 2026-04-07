#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import re
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
        description="Generate trigger_signals from regime_table and turning_points outputs."
    )
    p.add_argument("--base-dir", default="postmortem", help="Base folder containing date subfolders")
    p.add_argument("--date", default="", help="Date folder in YYYY-MM-DD (default: latest available)")
    p.add_argument("--symbols", default="SENSEX,NIFTY50", help="Comma-separated symbol allowlist")
    p.add_argument(
        "--input-dir",
        default="",
        help="Input directory containing regime/turning files (default: <base>/<date>)",
    )
    p.add_argument(
        "--output-dir",
        default="",
        help="Output directory (default: <base>/<date>)",
    )
    p.add_argument(
        "--min-action-score",
        type=float,
        default=60.0,
        help="Score threshold to tag trigger as actionable",
    )
    p.add_argument(
        "--min-output-score",
        type=float,
        default=0.0,
        help="Drop triggers below this score",
    )
    p.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit non-zero when required inputs are missing or parse fails occur.",
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
            for i, raw in enumerate(reader, start=2):
                row: Dict[str, str] = {}
                for h in headers:
                    row[h] = (raw.get(h, "") or "").strip()
                row["_source_line"] = str(i)
                rows.append(row)
        return headers, rows, ""
    except Exception as exc:
        return [], [], f"parse_error:{type(exc).__name__}"


def time_to_seconds(t: str) -> Optional[int]:
    raw = (t or "").strip()
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) == 2:
        raw = f"{raw}:00"
        parts = raw.split(":")
    if len(parts) != 3:
        return None
    try:
        h = int(parts[0]); m = int(parts[1]); s = int(parts[2])
        return h * 3600 + m * 60 + s
    except Exception:
        return None


def find_regime_context(
    regimes: List[Dict[str, str]],
    t_sec: int,
) -> Dict[str, str]:
    fallback = {
        "regime": "",
        "vol_state": "",
        "confidence": "",
        "duration_min": "",
        "start_time": "",
        "end_time": "",
    }
    if not regimes:
        return fallback

    best = None
    best_dist = None
    for r in regimes:
        s = time_to_seconds(r.get("start_time", ""))
        e = time_to_seconds(r.get("end_time", ""))
        if s is None or e is None:
            continue
        if s <= t_sec <= e:
            return {
                "regime": r.get("regime", ""),
                "vol_state": r.get("vol_state", ""),
                "confidence": r.get("confidence", ""),
                "duration_min": r.get("duration_min", ""),
                "start_time": r.get("start_time", ""),
                "end_time": r.get("end_time", ""),
            }
        dist = min(abs(t_sec - s), abs(t_sec - e))
        if best is None or (best_dist is not None and dist < best_dist):
            best = r
            best_dist = dist

    if best is None:
        return fallback
    return {
        "regime": best.get("regime", ""),
        "vol_state": best.get("vol_state", ""),
        "confidence": best.get("confidence", ""),
        "duration_min": best.get("duration_min", ""),
        "start_time": best.get("start_time", ""),
        "end_time": best.get("end_time", ""),
    }


def classify_trigger(tp_type: str, from_regime: str, to_regime: str) -> Tuple[str, str]:
    tp = (tp_type or "").strip().upper()
    from_r = (from_regime or "").strip().upper()
    to_r = (to_regime or "").strip().upper()

    if tp == "BREAKOUT":
        if to_r == "TREND_UP":
            return "BREAKOUT_UP", "BULLISH"
        if to_r == "TREND_DOWN":
            return "BREAKOUT_DOWN", "BEARISH"
        return "BREAKOUT", "WATCH"
    if tp == "REVERSAL":
        if to_r == "TREND_UP":
            return "REVERSAL_UP", "BULLISH"
        if to_r == "TREND_DOWN":
            return "REVERSAL_DOWN", "BEARISH"
        return "REVERSAL", "WATCH"
    if tp == "EXHAUSTION":
        if from_r == "TREND_UP":
            return "UPTREND_EXHAUSTION", "REDUCE_LONG_RISK"
        if from_r == "TREND_DOWN":
            return "DOWNTREND_EXHAUSTION", "REDUCE_SHORT_RISK"
        return "EXHAUSTION", "WATCH"
    if tp == "LOCAL_PEAK":
        return "LOCAL_PEAK_ALERT", "TAKE_PROFIT_OR_HEDGE"
    if tp == "LOCAL_TROUGH":
        return "LOCAL_TROUGH_ALERT", "WATCH_BOUNCE"
    return tp if tp else "TRANSITION_ALERT", "WATCH"


def compute_score(
    trigger_type: str,
    tp_type: str,
    strength: float,
    confirmations: int,
    regime_conf: float,
    vol_state: str,
) -> float:
    base_map = {
        "BREAKOUT": 54.0,
        "REVERSAL": 60.0,
        "EXHAUSTION": 50.0,
        "LOCAL_PEAK": 46.0,
        "LOCAL_TROUGH": 46.0,
    }
    base = base_map.get(tp_type, 44.0)

    if tp_type in ("LOCAL_PEAK", "LOCAL_TROUGH"):
        strength_bonus = min(24.0, max(0.0, strength) * 18.0)
    else:
        strength_bonus = min(28.0, max(0.0, strength) * 30.0)

    conf_bonus = min(18.0, max(0, confirmations) * 4.5)
    regime_bonus = (regime_conf - 60.0) * 0.35

    vol_adj = 0.0
    v = (vol_state or "").upper()
    if "BREAKOUT" in trigger_type or "REVERSAL" in trigger_type:
        if v == "HIGH_VOL":
            vol_adj += 3.0
        elif v == "LOW_VOL":
            vol_adj -= 2.0
    elif "LOCAL_" in trigger_type and v == "HIGH_VOL":
        vol_adj -= 2.0

    score = base + strength_bonus + conf_bonus + regime_bonus + vol_adj
    return max(0.0, min(100.0, score))


def priority_from_score(score: float) -> str:
    if score >= 80:
        return "HIGH"
    if score >= 65:
        return "MEDIUM"
    return "LOW"


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

    input_dir = Path(args.input_dir) if args.input_dir.strip() else date_dir
    output_dir = Path(args.output_dir) if args.output_dir.strip() else date_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    regime_path = input_dir / f"regime_table_{date_str}.csv"
    tp_path = input_dir / f"turning_points_{date_str}.csv"
    _rh, regime_rows, regime_err = read_csv(regime_path)
    _th, tp_rows, tp_err = read_csv(tp_path)

    errors: List[str] = []
    if regime_err:
        errors.append(f"regime_table: {regime_err}")
    if tp_err:
        errors.append(f"turning_points: {tp_err}")
    if errors:
        print(f"ERROR: {', '.join(errors)}", file=sys.stderr)
        return 1 if args.fail_on_errors else 0

    regimes_by_symbol: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in regime_rows:
        sym = (r.get("symbol", "") or "").upper()
        if sym:
            regimes_by_symbol[sym].append(r)

    triggers: List[Dict[str, object]] = []
    symbol_stats: Dict[str, Dict[str, object]] = {}

    for symbol in symbols:
        symbol_tp = [r for r in tp_rows if (r.get("symbol", "") or "").upper() == symbol]
        symbol_reg = regimes_by_symbol.get(symbol, [])
        count_all = 0
        count_out = 0
        count_actionable = 0

        for tp in symbol_tp:
            t_sec = time_to_seconds(tp.get("time", ""))
            if t_sec is None:
                continue

            ctx = find_regime_context(symbol_reg, t_sec)
            tp_type = (tp.get("tp_type", "") or "").upper()
            from_regime = tp.get("from_regime", "")
            to_regime = tp.get("to_regime", "")
            trigger_type, recommendation = classify_trigger(tp_type, from_regime, to_regime)

            strength = to_float(tp.get("strength", "")) or 0.0
            confirmations = int(to_float(tp.get("confirmations", "")) or 0)
            regime_conf = to_float(ctx.get("confidence", "")) or 60.0
            score = compute_score(
                trigger_type=trigger_type,
                tp_type=tp_type,
                strength=strength,
                confirmations=confirmations,
                regime_conf=regime_conf,
                vol_state=ctx.get("vol_state", ""),
            )

            count_all += 1
            if score < args.min_output_score:
                continue

            priority = priority_from_score(score)
            actionable = score >= args.min_action_score
            if actionable:
                count_actionable += 1

            context = (
                f"tp={tp_type};from={from_regime or '-'};to={to_regime or '-'};"
                f"regime={ctx.get('regime','') or '-'};vol={ctx.get('vol_state','') or '-'};"
                f"reg_conf={int(regime_conf)};tp_ctx={tp.get('context','')}"
            )

            triggers.append(
                {
                    "symbol": symbol,
                    "date": tp.get("date", date_str),
                    "time": tp.get("time", ""),
                    "trigger_type": trigger_type,
                    "context": context,
                    "score": round(score, 2),
                    "priority": priority,
                    "actionable": "Y" if actionable else "N",
                    "recommended_action": recommendation,
                    "tp_type": tp_type,
                    "strength": round(strength, 4),
                    "confirmations": confirmations,
                    "from_regime": from_regime,
                    "to_regime": to_regime,
                    "regime": ctx.get("regime", ""),
                    "vol_state": ctx.get("vol_state", ""),
                    "regime_confidence": int(regime_conf),
                    "regime_duration_min": ctx.get("duration_min", ""),
                    "regime_window": f"{ctx.get('start_time','')}-{ctx.get('end_time','')}",
                    "source_context": tp.get("context", ""),
                }
            )
            count_out += 1

        symbol_stats[symbol] = {
            "turning_points_in": count_all,
            "triggers_out": count_out,
            "actionable_triggers": count_actionable,
        }

    triggers.sort(key=lambda x: (x["symbol"], x["time"], -float(x["score"])))

    cols = [
        "symbol",
        "date",
        "time",
        "trigger_type",
        "context",
        "score",
        "priority",
        "actionable",
        "recommended_action",
        "tp_type",
        "strength",
        "confirmations",
        "from_regime",
        "to_regime",
        "regime",
        "vol_state",
        "regime_confidence",
        "regime_duration_min",
        "regime_window",
        "source_context",
    ]
    trigger_file = output_dir / f"trigger_signals_{date_str}.csv"
    write_csv(trigger_file, triggers, cols)

    summary = {
        "run_ts": dt.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST"),
        "base_dir": str(base_dir),
        "date": date_str,
        "validated_symbols": symbols,
        "min_action_score": args.min_action_score,
        "min_output_score": args.min_output_score,
        "overall_status": "PASS",
        "total_triggers": len(triggers),
        "actionable_triggers": sum(1 for x in triggers if x["actionable"] == "Y"),
        "trigger_file": str(trigger_file),
        "symbol_stats": symbol_stats,
    }
    summary_file = output_dir / f"trigger_summary_{date_str}.json"
    write_json(summary_file, summary)

    print(f"[forensics_generate_triggers] date={date_str}")
    print(f"[forensics_generate_triggers] validated_symbols={','.join(symbols)}")
    print(f"[forensics_generate_triggers] total_triggers={len(triggers)}")
    print(f"[forensics_generate_triggers] actionable_triggers={summary['actionable_triggers']}")
    print(f"[forensics_generate_triggers] trigger_file={trigger_file}")
    print(f"[forensics_generate_triggers] summary_json={summary_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

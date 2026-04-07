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
        description="Generate run_summary_<date>.md from forensics pipeline outputs."
    )
    p.add_argument("--base-dir", default="postmortem", help="Base folder containing date subfolders")
    p.add_argument("--date", default="", help="Date folder in YYYY-MM-DD (default: latest available)")
    p.add_argument("--symbols", default="SENSEX,NIFTY50", help="Comma-separated symbol allowlist")
    p.add_argument("--output-md", default="", help="Markdown output path (default: <base>/<date>/run_summary_<date>.md)")
    p.add_argument("--output-json", default="", help="JSON output path (default: <base>/<date>/run_summary_<date>.json)")
    p.add_argument("--top-patterns", type=int, default=8, help="Top N patterns to include in markdown")
    p.add_argument("--top-triggers", type=int, default=5, help="Top N trigger rows to include in markdown")
    p.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit non-zero when critical component files are missing or failed.",
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


def component_status(payload: Dict[str, object], err: str) -> str:
    if err == "missing":
        return "MISSING"
    if err:
        return "ERROR"
    st = str(payload.get("overall_status", "")).strip().upper()
    if st in ("PASS", "FAIL"):
        return st
    return "UNKNOWN"


def relpath(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except Exception:
        return str(path)


def top_trigger_rows(rows: List[Dict[str, str]], top_n: int) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    filtered = [r for r in rows if (r.get("actionable", "") or "").upper() == "Y"]
    if not filtered:
        filtered = rows

    def key(row: Dict[str, str]) -> Tuple[float, str]:
        return (to_float(row.get("score", "")) or 0.0, row.get("time", ""))

    for row in sorted(filtered, key=key, reverse=True)[: max(0, top_n)]:
        out.append(
            {
                "symbol": row.get("symbol", ""),
                "time": row.get("time", ""),
                "trigger_type": row.get("trigger_type", ""),
                "score": round(to_float(row.get("score", "")) or 0.0, 2),
                "priority": row.get("priority", ""),
                "action": row.get("recommended_action", ""),
            }
        )
    return out


def top_pattern_rows(rows: List[Dict[str, str]], top_n: int) -> List[Dict[str, object]]:
    def key(row: Dict[str, str]) -> Tuple[int, float]:
        return (
            int(to_float(row.get("sample_count", "")) or 0),
            to_float(row.get("expectancy", "")) or 0.0,
        )

    out: List[Dict[str, object]] = []
    for row in sorted(rows, key=key, reverse=True)[: max(0, top_n)]:
        out.append(
            {
                "symbol": row.get("symbol", ""),
                "regime": row.get("regime", ""),
                "trigger_combo": row.get("trigger_combo", ""),
                "sample_count": int(to_float(row.get("sample_count", "")) or 0),
                "hit_rate": round(to_float(row.get("hit_rate", "")) or 0.0, 2),
                "expectancy": round(to_float(row.get("expectancy", "")) or 0.0, 4),
                "decay_score": round(to_float(row.get("decay_score", "")) or 0.0, 4),
            }
        )
    return out


def write_markdown(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(text)


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

    output_md = Path(args.output_md) if args.output_md.strip() else (date_dir / f"run_summary_{date_str}.md")
    output_json = Path(args.output_json) if args.output_json.strip() else (date_dir / f"run_summary_{date_str}.json")
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    component_files: Dict[str, Path] = {
        "validation": date_dir / "forensics_file_validation_summary.json",
        "quality": date_dir / f"quality_summary_{date_str}.json",
        "timeline": date_dir / f"forensics_timeline_summary_{date_str}.json",
        "regime": date_dir / f"regime_summary_{date_str}.json",
        "trigger": date_dir / f"trigger_summary_{date_str}.json",
        "patterns": date_dir / f"pattern_templates_summary_{date_str}.json",
    }

    component_payloads: Dict[str, Dict[str, object]] = {}
    component_errors: Dict[str, str] = {}
    component_states: Dict[str, str] = {}
    for name, path in component_files.items():
        payload, e = read_json(path)
        component_payloads[name] = payload
        component_errors[name] = e
        component_states[name] = component_status(payload, e)

    regime_csv = date_dir / f"regime_table_{date_str}.csv"
    tp_csv = date_dir / f"turning_points_{date_str}.csv"
    trigger_csv = date_dir / f"trigger_signals_{date_str}.csv"
    pattern_csv = date_dir / f"pattern_templates_{date_str}.csv"

    _rh, regime_rows, regime_csv_err = read_csv(regime_csv)
    _th, tp_rows, tp_csv_err = read_csv(tp_csv)
    _gh, trigger_rows, trigger_csv_err = read_csv(trigger_csv)
    _ph, pattern_rows, pattern_csv_err = read_csv(pattern_csv)

    counts = {
        "regime_rows": len(regime_rows) if not regime_csv_err else 0,
        "turning_points": len(tp_rows) if not tp_csv_err else 0,
        "triggers": len(trigger_rows) if not trigger_csv_err else 0,
        "actionable_triggers": sum(
            1 for r in trigger_rows if (r.get("actionable", "") or "").upper() == "Y"
        ) if not trigger_csv_err else 0,
        "pattern_templates": len(pattern_rows) if not pattern_csv_err else 0,
    }

    top_triggers = top_trigger_rows(trigger_rows, args.top_triggers) if not trigger_csv_err else []
    top_patterns = top_pattern_rows(pattern_rows, args.top_patterns) if not pattern_csv_err else []

    symbol_snapshot: Dict[str, Dict[str, object]] = {}
    timeline_payload = component_payloads.get("timeline", {})
    regime_payload = component_payloads.get("regime", {})
    quality_payload = component_payloads.get("quality", {})
    trigger_payload = component_payloads.get("trigger", {})
    pattern_payload = component_payloads.get("patterns", {})

    for symbol in symbols:
        timeline_sym = ((timeline_payload.get("symbol_results", {}) or {}).get(symbol, {}) or {})
        regime_sym = ((regime_payload.get("symbol_results", {}) or {}).get(symbol, {}) or {})
        quality_sym = ((quality_payload.get("symbol_results", {}) or {}).get(symbol, {}) or {})
        trigger_sym = ((trigger_payload.get("symbol_stats", {}) or {}).get(symbol, {}) or {})
        pattern_sym = ((pattern_payload.get("symbol_stats", {}) or {}).get(symbol, {}) or {})
        symbol_snapshot[symbol] = {
            "quality_status": quality_sym.get("overall_status", ""),
            "timeline_rows": timeline_sym.get("timeline_rows_out", 0),
            "regime_segments": regime_sym.get("regime_segments", 0),
            "turning_points": regime_sym.get("turning_points", 0),
            "triggers": trigger_sym.get("triggers_out", 0),
            "actionable_triggers": trigger_sym.get("actionable_triggers", 0),
            "patterns": pattern_sym.get("patterns", 0),
        }

    statuses = list(component_states.values())
    if any(s in ("FAIL", "ERROR") for s in statuses):
        overall_status = "FAIL"
    elif any(s in ("MISSING", "UNKNOWN") for s in statuses):
        overall_status = "PARTIAL"
    else:
        overall_status = "PASS"

    produced_files: List[str] = []
    for p in [
        date_dir / "forensics_file_validation_report.csv",
        date_dir / "forensics_file_validation_summary.json",
        date_dir / f"quality_report_{date_str}.csv",
        date_dir / f"quality_summary_{date_str}.json",
        date_dir / f"forensics_timeline_combined_{date_str}.csv",
        date_dir / f"forensics_timeline_summary_{date_str}.json",
        regime_csv,
        tp_csv,
        trigger_csv,
        date_dir / f"trigger_summary_{date_str}.json",
        pattern_csv,
        date_dir / f"pattern_templates_summary_{date_str}.json",
        output_md,
        output_json,
    ]:
        if p.exists():
            produced_files.append(relpath(p, base_dir))

    run_ts = dt.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")

    md_lines: List[str] = []
    md_lines.append(f"# Forensics Run Summary - {date_str}")
    md_lines.append("")
    md_lines.append(f"Generated: {run_ts}")
    md_lines.append(f"Validated symbols: {', '.join(symbols)}")
    md_lines.append(f"Overall status: **{overall_status}**")
    md_lines.append("")
    md_lines.append("## Component Status")
    md_lines.append("")
    md_lines.append("| Component | Status | File |")
    md_lines.append("|---|---|---|")
    for name in ["validation", "quality", "timeline", "regime", "trigger", "patterns"]:
        p = component_files[name]
        md_lines.append(
            f"| {name} | {component_states[name]} | `{relpath(p, base_dir)}` |"
        )
    md_lines.append("")
    md_lines.append("## Key Counts")
    md_lines.append("")
    md_lines.append(f"- regime_rows: {counts['regime_rows']}")
    md_lines.append(f"- turning_points: {counts['turning_points']}")
    md_lines.append(f"- triggers: {counts['triggers']}")
    md_lines.append(f"- actionable_triggers: {counts['actionable_triggers']}")
    md_lines.append(f"- pattern_templates: {counts['pattern_templates']}")
    md_lines.append("")
    md_lines.append("## Symbol Snapshot")
    md_lines.append("")
    md_lines.append("| Symbol | Quality | Timeline Rows | Regime Segments | Turning Points | Triggers | Actionable | Patterns |")
    md_lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for symbol in symbols:
        snap = symbol_snapshot[symbol]
        md_lines.append(
            f"| {symbol} | {snap['quality_status'] or '-'} | {snap['timeline_rows']} | "
            f"{snap['regime_segments']} | {snap['turning_points']} | {snap['triggers']} | "
            f"{snap['actionable_triggers']} | {snap['patterns']} |"
        )
    md_lines.append("")
    md_lines.append("## Top Patterns")
    md_lines.append("")
    if top_patterns:
        md_lines.append("| Symbol | Regime | Trigger Combo | Samples | Hit Rate | Expectancy | Decay |")
        md_lines.append("|---|---|---|---:|---:|---:|---:|")
        for row in top_patterns:
            md_lines.append(
                f"| {row['symbol']} | {row['regime']} | `{row['trigger_combo']}` | "
                f"{row['sample_count']} | {row['hit_rate']} | {row['expectancy']} | {row['decay_score']} |"
            )
    else:
        md_lines.append("- No pattern templates found.")
    md_lines.append("")
    md_lines.append("## Top Triggers")
    md_lines.append("")
    if top_triggers:
        md_lines.append("| Symbol | Time | Trigger Type | Score | Priority | Action |")
        md_lines.append("|---|---|---|---:|---|---|")
        for row in top_triggers:
            md_lines.append(
                f"| {row['symbol']} | {row['time']} | {row['trigger_type']} | "
                f"{row['score']} | {row['priority']} | {row['action']} |"
            )
    else:
        md_lines.append("- No trigger signals found.")
    md_lines.append("")
    md_lines.append("## Files Produced")
    md_lines.append("")
    if produced_files:
        for p in produced_files:
            md_lines.append(f"- `{p}`")
    else:
        md_lines.append("- No output files detected.")
    md_lines.append("")

    markdown = "\n".join(md_lines)
    write_markdown(output_md, markdown)

    summary = {
        "run_ts": run_ts,
        "base_dir": str(base_dir),
        "date": date_str,
        "validated_symbols": symbols,
        "overall_status": overall_status,
        "component_status": component_states,
        "component_errors": component_errors,
        "counts": counts,
        "symbol_snapshot": symbol_snapshot,
        "top_patterns": top_patterns,
        "top_triggers": top_triggers,
        "produced_files": produced_files,
        "run_summary_md": str(output_md),
    }
    write_json(output_json, summary)

    print(f"[forensics_generate_run_summary] date={date_str}")
    print(f"[forensics_generate_run_summary] validated_symbols={','.join(symbols)}")
    print(f"[forensics_generate_run_summary] result={overall_status}")
    print(f"[forensics_generate_run_summary] output_md={output_md}")
    print(f"[forensics_generate_run_summary] output_json={output_json}")

    if args.fail_on_errors and overall_status != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

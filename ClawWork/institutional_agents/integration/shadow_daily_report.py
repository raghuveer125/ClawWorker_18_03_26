from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _to_date_key(entry: Dict[str, Any]) -> str:
    explicit_date = entry.get("date")
    if explicit_date:
        return str(explicit_date)

    timestamp = entry.get("timestamp")
    if not timestamp:
        return "unknown"

    text = str(timestamp).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt.date().isoformat()
    except ValueError:
        return "unknown"


def _aggregate(entries: List[Dict[str, Any]], date_key: str) -> Dict[str, Any]:
    filtered = [item for item in entries if _to_date_key(item) == date_key]

    session_count = len(filtered)
    total_records = sum(int(item.get("record_count", 0)) for item in filtered)
    agree_count = sum(int(item.get("agree_count", 0)) for item in filtered)
    disagree_count = sum(int(item.get("disagree_count", 0)) for item in filtered)

    by_underlying: Dict[str, Counter] = defaultdict(Counter)
    for item in filtered:
        for row in item.get("records", []):
            if not isinstance(row, dict):
                continue
            underlying = str(row.get("underlying", "UNKNOWN")).upper()
            by_underlying[underlying][str(row.get("comparison_label", "unknown"))] += 1

    if total_records > 0:
        agree_pct = round((agree_count / total_records) * 100.0, 2)
        disagree_pct = round((disagree_count / total_records) * 100.0, 2)
    else:
        agree_pct = 0.0
        disagree_pct = 0.0

    underlying_summary = {
        key: {
            "agree": int(counts.get("agree", 0)),
            "disagree": int(counts.get("disagree", 0)),
            "other": int(sum(v for k, v in counts.items() if k not in {"agree", "disagree"})),
        }
        for key, counts in sorted(by_underlying.items())
    }

    return {
        "date": date_key,
        "session_count": session_count,
        "record_count": total_records,
        "agree_count": agree_count,
        "disagree_count": disagree_count,
        "agree_pct": agree_pct,
        "disagree_pct": disagree_pct,
        "underlying_summary": underlying_summary,
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate daily summary from institutional shadow JSONL logs")
    parser.add_argument("--shadow-log", required=True, help="Path to institutional_shadow.jsonl")
    parser.add_argument("--date", required=True, help="Target session date (YYYY-MM-DD)")
    parser.add_argument("--outdir", default="institutional_agents/reports", help="Output directory for summary artifact")
    parser.add_argument("--tag", default="integration_shadow", help="Artifact filename tag")
    args = parser.parse_args()

    log_path = Path(args.shadow_log)
    if not log_path.exists():
        print(json.dumps({"summary": None, "passed": False, "error": f"Missing shadow log: {log_path}"}, indent=2))
        return 2

    entries = _read_jsonl(log_path)
    summary = _aggregate(entries, date_key=args.date)

    outdir = Path(args.outdir)
    out_path = outdir / f"{args.tag}_{args.date}_daily_summary.json"
    _write_json(out_path, summary)

    print(json.dumps({"summary": str(out_path), "passed": True, "session_count": summary["session_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

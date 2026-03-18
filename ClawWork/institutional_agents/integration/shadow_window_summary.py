from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
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


def _in_range(day: str, start: str | None, end: str | None) -> bool:
    if not day:
        return False
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _consecutive_days(unique_days: List[str]) -> bool:
    if len(unique_days) <= 1:
        return True
    parsed = [date.fromisoformat(item) for item in unique_days]
    parsed.sort()
    for i in range(1, len(parsed)):
        if parsed[i] - parsed[i - 1] != timedelta(days=1):
            return False
    return True


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build multi-session shadow summary for gate evaluation")
    parser.add_argument("--shadow-log", required=True)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args()

    shadow_log = Path(args.shadow_log)
    if not shadow_log.exists():
        print(json.dumps({"summary": None, "passed": False, "error": f"Missing shadow log: {shadow_log}"}, indent=2))
        return 2

    rows = _read_jsonl(shadow_log)
    filtered = [item for item in rows if _in_range(str(item.get("date", "")), args.start_date, args.end_date)]

    record_count = sum(int(item.get("record_count", 0) or 0) for item in filtered)
    agree_count = sum(int(item.get("agree_count", 0) or 0) for item in filtered)
    disagree_count = sum(int(item.get("disagree_count", 0) or 0) for item in filtered)

    unique_dates = sorted({str(item.get("date")) for item in filtered if item.get("date")})
    session_count = len(unique_dates)
    consecutive_days_met = _consecutive_days(unique_dates)

    agree_pct = round((agree_count / record_count) * 100.0, 2) if record_count > 0 else 0.0
    disagree_pct = round((disagree_count / record_count) * 100.0, 2) if record_count > 0 else 0.0

    payload = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ"),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "session_count": session_count,
        "record_count": record_count,
        "agree_count": agree_count,
        "disagree_count": disagree_count,
        "agree_pct": agree_pct,
        "disagree_pct": disagree_pct,
        "unique_dates": unique_dates,
        "consecutive_days_met": consecutive_days_met,
    }

    out_path = Path(args.out_json)
    _write_json(out_path, payload)

    print(json.dumps({"summary": str(out_path), "passed": True, "session_count": session_count, "consecutive_days_met": consecutive_days_met}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_iso8601(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _extract_timestamps(payload: Any) -> List[str]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        rows = payload["rows"]
    else:
        rows = []

    return [str(item.get("timestamp", "")).strip() for item in rows if isinstance(item, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate shadow-mode comparison duration coverage")
    parser.add_argument("--shadow-input", required=True)
    parser.add_argument("--min-days", type=float, default=14.0)
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args()

    payload = _read_json(Path(args.shadow_input))
    raw_timestamps = [value for value in _extract_timestamps(payload) if value]

    parse_errors: List[str] = []
    timestamps: List[datetime] = []
    for item in raw_timestamps:
        try:
            timestamps.append(_parse_iso8601(item))
        except ValueError:
            parse_errors.append(item)

    issues: List[str] = []
    if parse_errors:
        issues.append(f"Invalid timestamp values: {parse_errors}")

    if len(timestamps) < 2:
        issues.append("At least two valid timestamped rows are required to measure duration.")
        span_days = 0.0
        start_ts = None
        end_ts = None
        trading_days = 0
    else:
        timestamps_sorted = sorted(timestamps)
        start_ts = timestamps_sorted[0]
        end_ts = timestamps_sorted[-1]
        span_days = (end_ts - start_ts).total_seconds() / 86400.0
        trading_days = len({ts.date().isoformat() for ts in timestamps_sorted})
        if span_days < float(args.min_days):
            issues.append(
                f"Duration below threshold: covered_days={span_days:.2f}, required_days={float(args.min_days):.2f}"
            )

    passed = len(issues) == 0
    result: Dict[str, Any] = {
        "passed": passed,
        "min_required_days": float(args.min_days),
        "covered_days": round(span_days, 6),
        "valid_rows": len(timestamps),
        "trading_days_covered": trading_days,
        "window_start": start_ts.isoformat() if start_ts else None,
        "window_end": end_ts.isoformat() if end_ts else None,
        "issues": issues,
    }

    if args.out_json:
        out_path = Path(args.out_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

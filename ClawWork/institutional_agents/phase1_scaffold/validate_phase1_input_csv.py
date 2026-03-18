from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

REQUIRED_COLUMNS = [
    "timestamp",
    "underlying",
    "ltp",
    "prev_close",
    "session",
    "daily_realized_pnl_pct",
    "bid_ask_spread_bps",
]

ALLOWED_UNDERLYINGS = {"NIFTY50", "BANKNIFTY", "SENSEX"}
ALLOWED_SESSIONS = {"OPEN", "MIDDAY", "CLOSE"}


def _to_float(value: Any) -> Tuple[bool, float]:
    try:
        return True, float(value)
    except (TypeError, ValueError):
        return False, 0.0


def _parse_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _load_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [row for row in reader]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 1 CSV input before backtest run")
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--min-rows", type=int, default=20)
    parser.add_argument("--min-trading-days", type=int, default=20)
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args()

    path = Path(args.input_csv)
    issues: List[str] = []

    if not path.exists():
        payload = {
            "passed": False,
            "input_csv": str(path),
            "issues": [f"Input CSV not found: {path}"],
        }
        print(json.dumps(payload, indent=2))
        return 2

    rows = _load_rows(path)
    header = rows[0].keys() if rows else []

    missing_columns = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing_columns:
        issues.append(f"Missing required columns: {missing_columns}")

    trading_days = set()
    underlying_counts: Dict[str, int] = {key: 0 for key in sorted(ALLOWED_UNDERLYINGS)}

    for idx, row in enumerate(rows, start=2):
        ts = str(row.get("timestamp", "")).strip()
        if not _parse_timestamp(ts):
            issues.append(f"Row {idx}: invalid timestamp '{ts}'")
        elif len(ts) >= 10:
            trading_days.add(ts[:10])

        underlying = str(row.get("underlying", "")).strip().upper()
        if underlying not in ALLOWED_UNDERLYINGS:
            issues.append(f"Row {idx}: invalid underlying '{underlying}'")
        else:
            underlying_counts[underlying] += 1

        session = str(row.get("session", "")).strip().upper()
        if session not in ALLOWED_SESSIONS:
            issues.append(f"Row {idx}: invalid session '{session}'")

        ok_ltp, ltp = _to_float(row.get("ltp"))
        ok_prev, prev_close = _to_float(row.get("prev_close"))
        ok_pnl, _ = _to_float(row.get("daily_realized_pnl_pct"))
        ok_spread, spread = _to_float(row.get("bid_ask_spread_bps"))

        if not ok_ltp or ltp <= 0:
            issues.append(f"Row {idx}: ltp must be > 0")
        if not ok_prev or prev_close <= 0:
            issues.append(f"Row {idx}: prev_close must be > 0")
        if not ok_pnl:
            issues.append(f"Row {idx}: daily_realized_pnl_pct must be numeric")
        if not ok_spread or spread < 0:
            issues.append(f"Row {idx}: bid_ask_spread_bps must be >= 0")

    if len(rows) < args.min_rows:
        issues.append(f"Row count below threshold: {len(rows)} < {args.min_rows}")

    if len(trading_days) < args.min_trading_days:
        issues.append(
            f"Trading-day coverage below threshold: {len(trading_days)} < {args.min_trading_days}"
        )

    payload = {
        "passed": len(issues) == 0,
        "input_csv": str(path),
        "row_count": len(rows),
        "min_rows_required": args.min_rows,
        "trading_days": len(trading_days),
        "min_trading_days_required": args.min_trading_days,
        "underlying_counts": underlying_counts,
        "issues": issues,
    }

    if args.out_json:
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

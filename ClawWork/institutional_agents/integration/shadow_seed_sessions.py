from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any, Dict, List

from institutional_agents.integration.shadow_adapter import run_shadow_adapter


def _build_date_list(start_date: str, sessions: int) -> List[str]:
    base = date.fromisoformat(start_date)
    return [(base + timedelta(days=offset)).isoformat() for offset in range(sessions)]


def _baseline_for_day(day_index: int) -> Dict[str, Any]:
    patterns = [
        {"signal": "BULLISH", "change_pct": 0.86, "confidence": 74, "reason": "seed bullish momentum"},
        {"signal": "BEARISH", "change_pct": -0.72, "confidence": 69, "reason": "seed bearish momentum"},
        {"signal": "NEUTRAL", "change_pct": 0.08, "confidence": 38, "reason": "seed range-bound"},
    ]
    row = patterns[day_index % len(patterns)]
    return {
        "success": True,
        "index_recommendations": [
            {
                "index": "NIFTY50",
                "signal": row["signal"],
                "change_pct": row["change_pct"],
                "confidence": row["confidence"],
                "reason": row["reason"],
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed deterministic institutional shadow sessions for integration testing")
    parser.add_argument("--signature", required=True, help="Agent signature used for log path")
    parser.add_argument("--data-path", required=True, help="Agent data path, e.g. livebench/data/agent_data/<signature>")
    parser.add_argument("--start-date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--sessions", type=int, default=5, help="Number of sessions to seed")
    args = parser.parse_args()

    if args.sessions <= 0:
        print(json.dumps({"seeded": 0, "passed": False, "error": "sessions must be > 0"}, indent=2))
        return 2

    os.environ["INSTITUTIONAL_ADAPTER_ENABLED"] = "true"
    os.environ["INSTITUTIONAL_SHADOW_MODE"] = "true"

    dates = _build_date_list(start_date=args.start_date, sessions=args.sessions)
    outputs: List[Dict[str, Any]] = []

    for idx, session_date in enumerate(dates):
        baseline = _baseline_for_day(idx)
        result = run_shadow_adapter(
            baseline_result=baseline,
            runtime_context={
                "signature": args.signature,
                "current_date": session_date,
                "data_path": args.data_path,
            },
        )
        outputs.append({"date": session_date, "status": result.get("status"), "record_count": result.get("record_count", 0)})

    log_path = os.path.join(args.data_path, "trading", "institutional_shadow.jsonl")
    payload = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ"),
        "seeded": len(outputs),
        "dates": dates,
        "log_path": log_path,
        "results": outputs,
        "passed": all(item.get("status") == "ok" for item in outputs),
    }

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

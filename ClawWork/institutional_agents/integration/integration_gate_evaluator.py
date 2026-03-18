from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def evaluate_gate(
    daily_summary: Dict[str, Any],
    observability: Dict[str, Any],
    rollback_payload: Dict[str, Any] | None,
    min_sessions: int,
    min_agree_pct: float,
    max_disagree_pct: float,
) -> Dict[str, Any]:
    session_count = _to_int(daily_summary.get("session_count", 0))
    agree_pct = _to_float(daily_summary.get("agree_pct", 0.0))
    disagree_pct = _to_float(daily_summary.get("disagree_pct", 0.0))

    alert_count = _to_int(observability.get("alert_count", 0))
    observability_status = str(observability.get("status", "")).upper()
    fallback_verified = bool(observability.get("fallback_verification", {}).get("verified", False))
    schema_mismatch_count = _to_int(observability.get("summary", {}).get("schema_mismatch_count", 0))

    rollback_passed = bool(rollback_payload.get("passed", False)) if rollback_payload else False

    performance_gate = session_count >= min_sessions and agree_pct >= min_agree_pct
    risk_gate = disagree_pct <= max_disagree_pct
    reliability_gate = (
        observability_status == "OK"
        and alert_count == 0
        and fallback_verified
        and schema_mismatch_count == 0
    )
    rollback_gate = rollback_passed

    checks = {
        "performance_gate_met": performance_gate,
        "risk_gate_met": risk_gate,
        "reliability_gate_met": reliability_gate,
        "rollback_gate_met": rollback_gate,
        "shadow_window_min_sessions_met": session_count >= min_sessions,
    }

    reasons: List[str] = [name for name, passed in checks.items() if not passed]

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        "passed": all(checks.values()),
        "checks": checks,
        "reasons": reasons,
        "metrics": {
            "session_count": session_count,
            "min_sessions": min_sessions,
            "agree_pct": agree_pct,
            "min_agree_pct": min_agree_pct,
            "disagree_pct": disagree_pct,
            "max_disagree_pct": max_disagree_pct,
            "observability_status": observability_status,
            "alert_count": alert_count,
            "fallback_verified": fallback_verified,
            "schema_mismatch_count": schema_mismatch_count,
            "rollback_test_passed": rollback_passed,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Step 4 integration gate status")
    parser.add_argument("--daily-summary-json", required=True)
    parser.add_argument("--observability-json", required=True)
    parser.add_argument("--rollback-json", default=None)
    parser.add_argument("--min-sessions", type=int, default=5)
    parser.add_argument("--min-agree-pct", type=float, default=60.0)
    parser.add_argument("--max-disagree-pct", type=float, default=40.0)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args()

    daily_summary = _read_json(Path(args.daily_summary_json))
    observability = _read_json(Path(args.observability_json))
    rollback_payload = _read_json(Path(args.rollback_json)) if args.rollback_json else None

    payload = evaluate_gate(
        daily_summary=daily_summary,
        observability=observability,
        rollback_payload=rollback_payload,
        min_sessions=int(args.min_sessions),
        min_agree_pct=float(args.min_agree_pct),
        max_disagree_pct=float(args.max_disagree_pct),
    )

    out_path = Path(args.out_json)
    _write_json(out_path, payload)

    print(json.dumps({"gate_report": str(out_path), "passed": payload.get("passed", False), "reasons": payload.get("reasons", [])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

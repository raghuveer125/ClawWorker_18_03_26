from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List


STAGES = [
    {"name": "stage1_5pct", "allocation_pct": 5},
    {"name": "stage2_25pct", "allocation_pct": 25},
    {"name": "stage3_50pct", "allocation_pct": 50},
    {"name": "stage4_100pct", "allocation_pct": 100},
]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _stage_index(stage_name: str) -> int:
    names = [item["name"] for item in STAGES]
    if stage_name not in names:
        raise ValueError(f"Unknown stage: {stage_name}. Expected one of {names}")
    return names.index(stage_name)


def _next_stage(current_stage: str) -> str | None:
    idx = _stage_index(current_stage)
    if idx >= len(STAGES) - 1:
        return None
    return STAGES[idx + 1]["name"]


def evaluate_stage(
    stage: str,
    gate_payload: Dict[str, Any],
    observability_payload: Dict[str, Any],
    critical_risk_incidents: int,
    reliability_regression: bool,
    unresolved_alerts: int,
) -> Dict[str, Any]:
    gate_passed = bool(gate_payload.get("passed", False))
    observability_ok = str(observability_payload.get("status", "")).upper() == "OK"

    exit_rules = {
        "no_critical_risk_incidents": int(critical_risk_incidents) == 0,
        "no_reliability_regression": not bool(reliability_regression),
        "no_unresolved_alerts": int(unresolved_alerts) == 0,
    }

    all_exit_rules_met = all(exit_rules.values())
    stage_allowed = gate_passed and observability_ok
    promotion_allowed = stage_allowed and all_exit_rules_met

    reasons: List[str] = []
    if not gate_passed:
        reasons.append("gate_not_passed")
    if not observability_ok:
        reasons.append("observability_not_ok")
    for key, ok in exit_rules.items():
        if not ok:
            reasons.append(key)

    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ"),
        "stage": stage,
        "allocation_pct": next(item["allocation_pct"] for item in STAGES if item["name"] == stage),
        "gate_passed": gate_passed,
        "observability_ok": observability_ok,
        "stage_allowed": stage_allowed,
        "exit_rules": exit_rules,
        "promotion_allowed": promotion_allowed,
        "next_stage": _next_stage(stage) if promotion_allowed else None,
        "reasons": reasons,
        "inputs": {
            "critical_risk_incidents": int(critical_risk_incidents),
            "reliability_regression": bool(reliability_regression),
            "unresolved_alerts": int(unresolved_alerts),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate staged rollout readiness and stage-exit rules")
    parser.add_argument("--gate-json", required=True)
    parser.add_argument("--observability-json", required=True)
    parser.add_argument("--stage", required=True, choices=[item["name"] for item in STAGES])
    parser.add_argument("--critical-risk-incidents", type=int, default=0)
    parser.add_argument("--reliability-regression", type=_to_bool, default=False)
    parser.add_argument("--unresolved-alerts", type=int, default=0)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args()

    gate_payload = _read_json(Path(args.gate_json))
    observability_payload = _read_json(Path(args.observability_json))

    result = evaluate_stage(
        stage=args.stage,
        gate_payload=gate_payload,
        observability_payload=observability_payload,
        critical_risk_incidents=int(args.critical_risk_incidents),
        reliability_regression=bool(args.reliability_regression),
        unresolved_alerts=int(args.unresolved_alerts),
    )

    out_path = Path(args.out_json)
    _write_json(out_path, result)

    print(
        json.dumps(
            {
                "rollout_report": str(out_path),
                "stage": result["stage"],
                "stage_allowed": result["stage_allowed"],
                "promotion_allowed": result["promotion_allowed"],
                "next_stage": result["next_stage"],
                "reasons": result["reasons"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

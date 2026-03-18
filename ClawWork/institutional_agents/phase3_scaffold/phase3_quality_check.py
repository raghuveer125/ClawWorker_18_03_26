from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


REQUIRED_RESULT_KEYS = [
    "underlying",
    "timestamp",
    "market_regime",
    "options_structure",
    "risk_officer",
    "consensus",
    "execution_plan",
    "memory_snapshot",
]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_schema(results: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    for idx, row in enumerate(results, start=1):
        for key in REQUIRED_RESULT_KEYS:
            if key not in row:
                issues.append(f"Result #{idx} missing key: {key}")
    return issues


def _check_confidence_and_scores(results: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    valid_conf = {"LOW", "MEDIUM", "HIGH"}

    for idx, row in enumerate(results, start=1):
        for block in ["market_regime", "options_structure", "risk_officer", "consensus", "execution_plan"]:
            confidence = row.get(block, {}).get("confidence")
            if confidence not in valid_conf:
                issues.append(f"Result #{idx} invalid confidence in {block}: {confidence}")

        weighted_score = row.get("consensus", {}).get("weighted_score")
        try:
            score_val = float(weighted_score)
        except (TypeError, ValueError):
            issues.append(f"Result #{idx} invalid consensus weighted_score: {weighted_score}")
            continue

        if not (0.0 <= score_val <= 100.0):
            issues.append(f"Result #{idx} weighted_score out of range: {score_val}")

    return issues


def _check_conflict_policy(results: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    for idx, row in enumerate(results, start=1):
        risk_veto = bool(row.get("risk_officer", {}).get("veto"))
        veto_applied = bool(row.get("consensus", {}).get("veto_applied"))
        consensus_action = row.get("consensus", {}).get("action")
        execution_action = row.get("execution_plan", {}).get("action")

        if risk_veto and not veto_applied:
            issues.append(f"Result #{idx} risk veto true but consensus veto_applied false")

        if veto_applied and consensus_action != "NO_TRADE":
            issues.append(f"Result #{idx} veto_applied true but consensus action is {consensus_action}")

        if consensus_action == "NO_TRADE" and execution_action != "NO_TRADE":
            issues.append(f"Result #{idx} NO_TRADE consensus but execution action is {execution_action}")

    return issues


def _check_memory_policy(results: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    for idx, row in enumerate(results, start=1):
        memory = row.get("memory_snapshot", {})
        policy = memory.get("retention_policy")
        if policy != "rolling_window_20":
            issues.append(f"Result #{idx} unexpected retention policy: {policy}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 3 quality gates")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args()

    report = _read_json(Path(args.report_json))
    results = report.get("results", [])

    schema_issues = _check_schema(results)
    score_issues = _check_confidence_and_scores(results)
    conflict_issues = _check_conflict_policy(results)
    memory_issues = _check_memory_policy(results)

    checks = {
        "output_schema_valid": {"passed": len(schema_issues) == 0, "issues": schema_issues},
        "scores_and_confidence_valid": {"passed": len(score_issues) == 0, "issues": score_issues},
        "conflict_policy_enforced": {"passed": len(conflict_issues) == 0, "issues": conflict_issues},
        "memory_policy_valid": {"passed": len(memory_issues) == 0, "issues": memory_issues},
    }

    passed = all(section["passed"] for section in checks.values())
    payload = {"passed": passed, "checks": checks}

    if args.out_json:
        out_path = Path(args.out_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

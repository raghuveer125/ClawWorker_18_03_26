from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


REQUIRED_RESULT_KEYS = ["input", "decision"]
REQUIRED_DECISION_KEYS = ["action", "confidence", "confidence_score", "position_size_multiplier", "rationale", "policy_tags"]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_schema(results: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    for idx, row in enumerate(results, start=1):
        for key in REQUIRED_RESULT_KEYS:
            if key not in row:
                issues.append(f"Result #{idx} missing key: {key}")
        decision = row.get("decision", {})
        for key in REQUIRED_DECISION_KEYS:
            if key not in decision:
                issues.append(f"Result #{idx} decision missing key: {key}")
    return issues


def _check_ranges(results: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    for idx, row in enumerate(results, start=1):
        decision = row.get("decision", {})
        score = decision.get("confidence_score")
        size = decision.get("position_size_multiplier")
        try:
            score_val = float(score)
            if not (0.0 <= score_val <= 100.0):
                issues.append(f"Result #{idx} confidence_score out of range: {score_val}")
        except (TypeError, ValueError):
            issues.append(f"Result #{idx} invalid confidence_score: {score}")

        try:
            size_val = float(size)
            if not (0.0 <= size_val <= 1.5):
                issues.append(f"Result #{idx} position_size_multiplier out of range: {size_val}")
        except (TypeError, ValueError):
            issues.append(f"Result #{idx} invalid position_size_multiplier: {size}")
    return issues


def _check_policy(results: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    for idx, row in enumerate(results, start=1):
        inp = row.get("input", {})
        decision = row.get("decision", {})
        tags = decision.get("policy_tags", {})

        if inp.get("event_window_active") and inp.get("event_name", "").upper() in {"RBI", "FED", "CPI", "EARNINGS"}:
            if decision.get("action") != "NO_TRADE":
                issues.append(f"Result #{idx} high-risk event should be NO_TRADE")
            if tags.get("gate") != "EVENT_BLOCK":
                issues.append(f"Result #{idx} high-risk event missing EVENT_BLOCK tag")

        current_exposure = float(inp.get("current_portfolio_exposure_pct", 0.0))
        max_exposure = float(inp.get("max_portfolio_exposure_pct", 0.0))
        if max_exposure > 0 and current_exposure >= max_exposure:
            if decision.get("action") != "NO_TRADE":
                issues.append(f"Result #{idx} exposure breach should force NO_TRADE")
            if tags.get("gate") != "PORTFOLIO_BLOCK":
                issues.append(f"Result #{idx} exposure breach missing PORTFOLIO_BLOCK tag")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 4 quality gates")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args()

    report = _read_json(Path(args.report_json))
    results = report.get("results", [])

    schema_issues = _check_schema(results)
    range_issues = _check_ranges(results)
    policy_issues = _check_policy(results)

    checks = {
        "output_schema_valid": {"passed": len(schema_issues) == 0, "issues": schema_issues},
        "score_and_sizing_valid": {"passed": len(range_issues) == 0, "issues": range_issues},
        "event_and_portfolio_policy_enforced": {"passed": len(policy_issues) == 0, "issues": policy_issues},
    }

    passed = all(section["passed"] for section in checks.values())
    payload = {"passed": passed, "checks": checks}

    if args.out_json:
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

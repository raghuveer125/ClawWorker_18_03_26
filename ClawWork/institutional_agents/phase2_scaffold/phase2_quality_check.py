from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


REQUIRED_RESULT_KEYS = [
    "underlying",
    "momentum_signal",
    "options_signal",
    "final_decision",
]


REQUIRED_OPTIONS_SIGNAL_KEYS = [
    "signal",
    "confidence",
    "preferred_strike_zone",
    "options_score",
    "momentum_score",
    "greeks_score",
    "volatility_score",
    "liquidity_score",
    "straddle_score",
    "weighted_components",
    "atm_straddle_price",
    "straddle_upper_band",
    "straddle_lower_band",
    "straddle_band_pct",
    "rationale",
    "liquidity_pass",
    "spread_pass",
]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_schema(results: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    for idx, row in enumerate(results, start=1):
        for key in REQUIRED_RESULT_KEYS:
            if key not in row:
                issues.append(f"Result #{idx} missing key: {key}")

        options_signal = row.get("options_signal", {})
        if not isinstance(options_signal, dict):
            issues.append(f"Result #{idx} options_signal is not an object")
            continue

        for key in REQUIRED_OPTIONS_SIGNAL_KEYS:
            if key not in options_signal:
                issues.append(f"Result #{idx} options_signal missing key: {key}")

    return issues


def _check_scores(results: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    for idx, row in enumerate(results, start=1):
        options_signal = row.get("options_signal", {})
        score = options_signal.get("options_score")
        try:
            score_val = float(score)
        except (TypeError, ValueError):
            issues.append(f"Result #{idx} invalid options_score: {score}")
            continue

        if not (0.0 <= score_val <= 100.0):
            issues.append(f"Result #{idx} options_score out of range: {score_val}")

        for component_name in [
            "momentum_score",
            "greeks_score",
            "volatility_score",
            "liquidity_score",
            "straddle_score",
        ]:
            component_value = options_signal.get(component_name)
            try:
                component_val = float(component_value)
            except (TypeError, ValueError):
                issues.append(f"Result #{idx} invalid {component_name}: {component_value}")
                continue

            if not (0.0 <= component_val <= 100.0):
                issues.append(f"Result #{idx} {component_name} out of range: {component_val}")

    return issues


def _check_guardrails(results: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    for idx, row in enumerate(results, start=1):
        options_signal = row.get("options_signal", {})
        final_action = row.get("final_decision", {}).get("action")

        liq_pass = options_signal.get("liquidity_pass")
        spread_pass = options_signal.get("spread_pass")

        if (liq_pass is False or spread_pass is False) and final_action != "NO_TRADE":
            issues.append(
                f"Result #{idx} guardrail mismatch: failed liquidity/spread but final action is {final_action}"
            )

    return issues


def _check_decision_scores(results: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    for idx, row in enumerate(results, start=1):
        final_decision = row.get("final_decision", {})
        if "final_weighted_score" not in final_decision:
            issues.append(f"Result #{idx} missing final_weighted_score")
        if "score_breakdown" not in final_decision:
            issues.append(f"Result #{idx} missing score_breakdown")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 2 quality gates")
    parser.add_argument("--report-json", required=True, help="Path to phase2 report json")
    parser.add_argument("--out-json", default=None, help="Optional output json path")
    args = parser.parse_args()

    report = _read_json(Path(args.report_json))
    results = report.get("results", [])

    schema_issues = _check_schema(results)
    score_issues = _check_scores(results)
    guardrail_issues = _check_guardrails(results)
    decision_score_issues = _check_decision_scores(results)

    checks = {
        "output_schema_valid": {"passed": len(schema_issues) == 0, "issues": schema_issues},
        "options_score_range_valid": {"passed": len(score_issues) == 0, "issues": score_issues},
        "guardrails_enforced": {"passed": len(guardrail_issues) == 0, "issues": guardrail_issues},
        "decision_layer_scores_present": {
            "passed": len(decision_score_issues) == 0,
            "issues": decision_score_issues,
        },
    }

    passed = all(section["passed"] for section in checks.values())

    payload = {
        "passed": passed,
        "checks": checks,
    }

    if args.out_json:
        out_json_path = Path(args.out_json)
        out_json_path.parent.mkdir(parents=True, exist_ok=True)
        out_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

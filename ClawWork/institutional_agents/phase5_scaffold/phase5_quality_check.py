from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 5 quality and gate consistency")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args()

    report = _read_json(Path(args.report_json))
    issues: List[str] = []

    flags = report.get("feature_flags", {})
    comparison = report.get("shadow_comparison", {})
    gate = report.get("go_live_gate", {})
    rollout = report.get("rollout_plan", [])

    if not flags.get("shadow_mode_enabled", False):
        issues.append("shadow_mode_enabled must be true for Phase 5 validation.")

    agreement_pct = comparison.get("agreement_pct")
    try:
        agreement_val = float(agreement_pct)
        if not (0.0 <= agreement_val <= 100.0):
            issues.append(f"agreement_pct out of range: {agreement_val}")
    except (TypeError, ValueError):
        issues.append(f"Invalid agreement_pct: {agreement_pct}")

    gate_passed = bool(gate.get("passed", False))
    checks = gate.get("checks", {})
    if gate_passed and not all(bool(v) for v in checks.values()):
        issues.append("Gate marked passed but one or more checks are false.")

    if not isinstance(rollout, list) or len(rollout) == 0:
        issues.append("rollout_plan must be a non-empty list.")

    for idx, stage in enumerate(rollout, start=1):
        if "allocation_pct" not in stage:
            issues.append(f"rollout_plan stage #{idx} missing allocation_pct")

    passed = len(issues) == 0
    payload = {
        "passed": passed,
        "checks": {
            "flags_valid": {"passed": "shadow_mode_enabled must be true for Phase 5 validation." not in issues},
            "shadow_metrics_valid": {"passed": not any("agreement_pct" in item for item in issues)},
            "go_live_gate_consistent": {"passed": "Gate marked passed but one or more checks are false." not in issues},
            "rollout_plan_valid": {"passed": not any("rollout_plan" in item for item in issues)},
        },
        "issues": issues,
    }

    if args.out_json:
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

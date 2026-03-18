from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Phase 2 exit criteria from baseline comparison output")
    parser.add_argument("--comparison-json", required=True, help="Path to phase2 baseline comparison json")
    parser.add_argument("--out-json", default=None, help="Optional path to write exit-gate result")
    args = parser.parse_args()

    comparison_payload = _read_json(Path(args.comparison_json))
    comparison = comparison_payload.get("comparison", {})
    phase1 = comparison_payload.get("phase1", {})
    phase2 = comparison_payload.get("phase2", {})

    false_signal_reduction_met = bool(comparison.get("false_signal_reduction_met") is True)
    risk_adjusted_improvement_met = bool(comparison.get("risk_adjusted_improvement_met") is True)

    has_usable_data = (
        comparison.get("status") == "ok"
        and phase1.get("evaluation_status") == "ok"
        and phase2.get("evaluation_status") == "ok"
    )

    passed = has_usable_data and false_signal_reduction_met and risk_adjusted_improvement_met

    if not has_usable_data:
        note = "Insufficient/unstable data for exit decision. Use real outcomes with enough actionable trades."
    elif passed:
        note = "Phase 2 exit criteria satisfied."
    else:
        note = "Phase 2 exit criteria not yet satisfied."

    payload = {
        "passed": passed,
        "criteria": {
            "false_signal_reduction_vs_phase1": false_signal_reduction_met,
            "improved_risk_adjusted_return": risk_adjusted_improvement_met,
        },
        "data_quality": {
            "comparison_status": comparison.get("status"),
            "phase1_evaluation_status": phase1.get("evaluation_status"),
            "phase2_evaluation_status": phase2.get("evaluation_status"),
        },
        "deltas": {
            "false_signal_delta_pct_points": comparison.get("false_signal_delta_pct_points"),
            "risk_adjusted_delta": comparison.get("risk_adjusted_delta"),
        },
        "note": note,
    }

    if args.out_json:
        out_path = Path(args.out_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from feature_flags import resolve_flags
from go_live_gates import GoLiveGateEvaluator
from rollout_policy import RolloutPlanner
from shadow_mode import ShadowComparator
from contracts import ShadowRow


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _to_rows(payload: List[Dict[str, Any]]) -> List[ShadowRow]:
    return [ShadowRow(**row) for row in payload if isinstance(row, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 5 controlled integration pipeline")
    parser.add_argument("--shadow-input", required=True)
    parser.add_argument("--flags-json", required=True)
    parser.add_argument("--gates-json", required=True)
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--tag", default="phase5_demo")
    args = parser.parse_args()

    shadow_payload = _read_json(Path(args.shadow_input))
    flags_payload = _read_json(Path(args.flags_json))
    gates_payload = _read_json(Path(args.gates_json))

    rows = _to_rows(shadow_payload if isinstance(shadow_payload, list) else [shadow_payload])
    flags = resolve_flags(flags_payload if isinstance(flags_payload, dict) else {})

    comparator = ShadowComparator()
    comparison = comparator.compare(rows)

    gate_eval = GoLiveGateEvaluator().evaluate(
        performance_threshold_met=bool(gates_payload.get("performance_threshold_met", False)),
        risk_threshold_met=bool(gates_payload.get("risk_threshold_met", False)),
        monitoring_active=bool(gates_payload.get("monitoring_active", False)),
        rollback_tested=bool(gates_payload.get("rollback_tested", False)),
        shadow_mode_min_days_met=bool(gates_payload.get("shadow_mode_min_days_met", False)),
    )

    rollout_plan = RolloutPlanner().evaluate(gate_passed=gate_eval.passed)

    result = {
        "run_tag": args.tag,
        "feature_flags": asdict(flags),
        "shadow_comparison": comparator.to_dict(comparison),
        "go_live_gate": asdict(gate_eval),
        "rollout_plan": rollout_plan,
    }

    outdir = Path(args.outdir)
    report_path = outdir / f"{args.tag}_report.json"
    _write_json(report_path, result)

    print(json.dumps({"report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

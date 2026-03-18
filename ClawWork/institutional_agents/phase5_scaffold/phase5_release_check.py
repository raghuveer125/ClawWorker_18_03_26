from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


REQUIRED_REPORT_KEYS = ["run_tag", "feature_flags", "shadow_comparison", "go_live_gate", "rollout_plan"]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 5 report artifact")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args()

    report_path = Path(args.report_json)
    errors: List[str] = []

    if not report_path.exists():
        errors.append(f"Missing report file: {report_path}")
        payload = {"passed": False, "errors": errors}
        if args.out_json:
            out = Path(args.out_json)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 2

    report = _read_json(report_path)
    for key in REQUIRED_REPORT_KEYS:
        if key not in report:
            errors.append(f"Missing key: {key}")

    passed = len(errors) == 0
    payload = {"passed": passed, "errors": errors, "report": str(report_path)}

    if args.out_json:
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

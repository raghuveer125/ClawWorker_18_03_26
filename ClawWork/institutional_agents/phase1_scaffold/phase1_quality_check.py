from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List


REQUIRED_INPUT_FIELDS = [
    "timestamp",
    "underlying",
    "ltp",
    "prev_close",
    "session",
    "daily_realized_pnl_pct",
    "bid_ask_spread_bps",
]

REQUIRED_DECISION_FIELDS = [
    "action",
    "confidence",
    "underlying",
    "preferred_strike",
    "stop_loss_pct",
    "target_pct",
    "rationale",
    "risk_checks",
    "model_version",
]

REQUIRED_RISK_CHECK_FIELDS = [
    "daily_loss_guard",
    "spread_guard",
    "data_quality_guard",
]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _compute_input_completeness(input_file: Path) -> float:
    with input_file.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    if not rows:
        return 0.0

    required_cells = len(rows) * len(REQUIRED_INPUT_FIELDS)
    present_cells = 0

    for row in rows:
        for field in REQUIRED_INPUT_FIELDS:
            value = row.get(field)
            if value is not None and str(value).strip() != "":
                present_cells += 1

    return (present_cells / required_cells) * 100.0 if required_cells else 0.0


def _check_output_schema(decisions: List[Dict[str, Any]]) -> tuple[bool, List[str]]:
    issues: List[str] = []

    for index, decision in enumerate(decisions, start=1):
        for field in REQUIRED_DECISION_FIELDS:
            if field not in decision:
                issues.append(f"Decision #{index} missing field: {field}")

    return len(issues) == 0, issues


def _check_risk_guardrails(decisions: List[Dict[str, Any]]) -> tuple[bool, List[str]]:
    issues: List[str] = []

    for index, decision in enumerate(decisions, start=1):
        risk_checks = decision.get("risk_checks")
        if not isinstance(risk_checks, dict):
            issues.append(f"Decision #{index} missing risk_checks object")
            continue

        for field in REQUIRED_RISK_CHECK_FIELDS:
            if field not in risk_checks:
                issues.append(f"Decision #{index} risk_checks missing: {field}")

    return len(issues) == 0, issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 1 quality gates from generated artifacts")
    parser.add_argument("--input-csv", required=True, help="Input CSV used for run")
    parser.add_argument("--pipeline-report-json", required=True, help="Path to *_pipeline_report.json")
    parser.add_argument("--min-completeness-pct", type=float, default=95.0)
    parser.add_argument("--out-json", default=None, help="Optional output json path")
    args = parser.parse_args()

    input_csv = Path(args.input_csv)
    pipeline_report_path = Path(args.pipeline_report_json)
    pipeline_report = _read_json(pipeline_report_path)
    decisions = pipeline_report.get("decisions", [])

    completeness_pct = _compute_input_completeness(input_csv)
    completeness_pass = completeness_pct >= args.min_completeness_pct

    schema_pass, schema_issues = _check_output_schema(decisions)
    guardrails_pass, guardrail_issues = _check_risk_guardrails(decisions)

    passed = completeness_pass and schema_pass and guardrails_pass

    payload = {
        "passed": passed,
        "checks": {
            "data_completeness": {
                "passed": completeness_pass,
                "value_pct": round(completeness_pct, 2),
                "required_min_pct": args.min_completeness_pct,
            },
            "output_schema_valid": {
                "passed": schema_pass,
                "issues": schema_issues,
            },
            "risk_guardrails_populated": {
                "passed": guardrails_pass,
                "issues": guardrail_issues,
            },
        },
    }

    if args.out_json:
        out_path = Path(args.out_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

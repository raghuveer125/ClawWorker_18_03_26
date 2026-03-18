from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


REQUIRED_SUFFIXES = [
    "_report.json",
    "_summary.json",
    "_report.md",
]


REQUIRED_REPORT_KEYS = [
    "run_tag",
    "input_file",
    "record_count",
    "summary",
    "results",
]


REQUIRED_SUMMARY_KEYS = [
    "run_tag",
    "record_count",
    "total_decisions",
    "final_action_counts",
    "options_signal_counts",
    "options_veto_pct",
    "average_options_score",
]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _missing_keys(payload: Dict[str, Any], required_keys: List[str]) -> List[str]:
    return [key for key in required_keys if key not in payload]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 2 artifacts for a given tag")
    parser.add_argument("--tag", required=True, help="Run tag used for phase2 outputs")
    parser.add_argument("--outdir", default="reports", help="Artifacts directory")
    parser.add_argument("--out-json", default=None, help="Optional path to persist validation payload")
    args = parser.parse_args()

    outdir = Path(args.outdir)

    missing_files: List[str] = []
    checked_files: List[str] = []
    validation_errors: List[str] = []

    for suffix in REQUIRED_SUFFIXES:
        path = outdir / f"{args.tag}{suffix}"
        if path.exists():
            checked_files.append(str(path))
        else:
            missing_files.append(str(path))

    report_path = outdir / f"{args.tag}_report.json"
    if report_path.exists():
        report_payload = _read_json(report_path)
        missing_report = _missing_keys(report_payload, REQUIRED_REPORT_KEYS)
        for key in missing_report:
            validation_errors.append(f"Missing key in report json: {key}")

    summary_path = outdir / f"{args.tag}_summary.json"
    if summary_path.exists():
        summary_payload = _read_json(summary_path)
        missing_summary = _missing_keys(summary_payload, REQUIRED_SUMMARY_KEYS)
        for key in missing_summary:
            validation_errors.append(f"Missing key in summary json: {key}")

    passed = len(missing_files) == 0 and len(validation_errors) == 0

    payload = {
        "tag": args.tag,
        "outdir": str(outdir),
        "passed": passed,
        "checked_files": checked_files,
        "missing_files": missing_files,
        "validation_errors": validation_errors,
    }

    if args.out_json:
        out_json_path = Path(args.out_json)
        out_json_path.parent.mkdir(parents=True, exist_ok=True)
        out_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

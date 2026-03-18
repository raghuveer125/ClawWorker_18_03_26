from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


REQUIRED_SUFFIXES = [
    "_sweep_threshold_sweep_summary.json",
    "_sweep_threshold_sweep_ranked.json",
    "_sweep_threshold_sweep_summary.md",
    "_pipeline_report.json",
    "_pipeline_summary.json",
    "_pipeline_report.md",
]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_summary_json(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ["run_tag", "best_config", "top_results", "total_configs"]:
        if key not in payload:
            errors.append(f"Missing key in sweep summary: {key}")
    return errors


def _validate_pipeline_report_json(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ["run_tag", "summary", "decisions", "record_count"]:
        if key not in payload:
            errors.append(f"Missing key in pipeline report: {key}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 1 release artifacts for a given tag")
    parser.add_argument("--tag", required=True, help="Run tag used in phase1_master_runner")
    parser.add_argument("--outdir", default="reports", help="Artifacts output directory")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    missing_files: List[str] = []
    existing_files: List[Path] = []

    for suffix in REQUIRED_SUFFIXES:
        file_path = outdir / f"{args.tag}{suffix}"
        if file_path.exists():
            existing_files.append(file_path)
        else:
            missing_files.append(str(file_path))

    validation_errors: List[str] = []

    sweep_summary_path = outdir / f"{args.tag}_sweep_threshold_sweep_summary.json"
    if sweep_summary_path.exists():
        validation_errors.extend(_validate_summary_json(_read_json(sweep_summary_path)))

    pipeline_report_path = outdir / f"{args.tag}_pipeline_report.json"
    if pipeline_report_path.exists():
        validation_errors.extend(_validate_pipeline_report_json(_read_json(pipeline_report_path)))

    passed = not missing_files and not validation_errors

    payload = {
        "tag": args.tag,
        "outdir": str(outdir),
        "passed": passed,
        "checked_files": [str(path) for path in existing_files],
        "missing_files": missing_files,
        "validation_errors": validation_errors,
    }

    print(json.dumps(payload, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

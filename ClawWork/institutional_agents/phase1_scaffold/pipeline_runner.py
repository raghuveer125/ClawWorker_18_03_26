from __future__ import annotations

import argparse
import json
from pathlib import Path

from batch_runner import main as batch_main
from report_generator import main as report_main


def run_batch(input_path: str, outdir: str, tag: str) -> dict:
    report_path = Path(outdir) / f"{tag}_report.json"
    summary_path = Path(outdir) / f"{tag}_summary.json"

    # Call batch runner internals by invoking through CLI-compatible args
    import sys

    original_argv = sys.argv[:]
    try:
        sys.argv = [
            "batch_runner.py",
            "--input",
            input_path,
            "--outdir",
            outdir,
            "--tag",
            tag,
        ]
        batch_main()
    finally:
        sys.argv = original_argv

    return {
        "report": str(report_path),
        "summary": str(summary_path),
    }


def run_markdown(report_json: str, out_md: str | None = None) -> dict:
    import sys

    original_argv = sys.argv[:]
    try:
        args = ["report_generator.py", "--report-json", report_json]
        if out_md:
            args.extend(["--out-md", out_md])
        sys.argv = args
        report_main()
    finally:
        sys.argv = original_argv

    md_path = out_md if out_md else str(Path(report_json).with_suffix(".md"))
    return {"markdown_report": md_path}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Phase 1 pipeline: batch backtest + markdown report generation"
    )
    parser.add_argument("--input", required=True, help="Path to input dataset (.json or .csv)")
    parser.add_argument("--outdir", default="reports", help="Output directory for artifacts")
    parser.add_argument("--tag", default="run", help="Run tag used for output filenames")
    parser.add_argument(
        "--out-md",
        default=None,
        help="Optional custom markdown output path (default: <outdir>/<tag>_report.md)",
    )
    args = parser.parse_args()

    batch_result = run_batch(args.input, args.outdir, args.tag)
    markdown_result = run_markdown(batch_result["report"], args.out_md)

    payload = {
        "pipeline": "phase1",
        "batch": batch_result,
        "report": markdown_result,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

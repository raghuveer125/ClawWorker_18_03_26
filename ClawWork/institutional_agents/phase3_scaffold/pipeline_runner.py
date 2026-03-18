from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from batch_runner import main as batch_main
from report_generator import main as report_main


def _run_cli(main_fn, argv: list[str]) -> None:
    old_argv = sys.argv[:]
    try:
        sys.argv = argv
        main_fn()
    finally:
        sys.argv = old_argv


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 3 pipeline: batch + markdown")
    parser.add_argument("--input", required=True)
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--tag", default="phase3_demo")
    args = parser.parse_args()

    _run_cli(
        batch_main,
        [
            "batch_runner.py",
            f"--input={args.input}",
            f"--outdir={args.outdir}",
            f"--tag={args.tag}",
        ],
    )

    report_json = str(Path(args.outdir) / f"{args.tag}_report.json")
    _run_cli(report_main, ["report_generator.py", f"--report-json={report_json}"])

    print(
        json.dumps(
            {
                "pipeline": "phase3",
                "report_json": report_json,
                "summary_json": str(Path(args.outdir) / f"{args.tag}_summary.json"),
                "report_md": str(Path(args.outdir) / f"{args.tag}_report.md"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

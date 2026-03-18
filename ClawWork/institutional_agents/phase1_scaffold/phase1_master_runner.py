from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any

from pipeline_runner import main as pipeline_main
from sweep_report_generator import main as sweep_report_main
from threshold_sweep import main as threshold_sweep_main


def _run_cli(main_fn, argv: list[str]) -> None:
    original_argv = sys.argv[:]
    try:
        sys.argv = argv
        main_fn()
    finally:
        sys.argv = original_argv


def run_threshold_sweep(
    input_path: str,
    outdir: str,
    tag: str,
    bullish_values: str,
    bearish_values: str,
    strong_values: str,
    max_veto_pct: float,
    top_k: int,
) -> Dict[str, str]:
    sweep_tag = f"{tag}_sweep"
    _run_cli(
        threshold_sweep_main,
        [
            "threshold_sweep.py",
            f"--input={input_path}",
            f"--outdir={outdir}",
            f"--tag={sweep_tag}",
            f"--bullish-values={bullish_values}",
            f"--bearish-values={bearish_values}",
            f"--strong-move-values={strong_values}",
            f"--max-veto-pct={max_veto_pct}",
            f"--top-k={top_k}",
        ],
    )

    summary = str(Path(outdir) / f"{sweep_tag}_threshold_sweep_summary.json")
    ranked = str(Path(outdir) / f"{sweep_tag}_threshold_sweep_ranked.json")
    return {"summary": summary, "ranked": ranked}


def run_sweep_report(summary_json: str, ranked_json: str, out_md: str | None = None) -> Dict[str, str]:
    argv = [
        "sweep_report_generator.py",
        "--summary-json",
        summary_json,
        "--ranked-json",
        ranked_json,
    ]
    if out_md:
        argv.extend(["--out-md", out_md])

    _run_cli(sweep_report_main, argv)

    md_path = out_md if out_md else str(Path(summary_json).with_suffix(".md"))
    return {"markdown_report": md_path}


def run_pipeline(input_path: str, outdir: str, tag: str) -> Dict[str, str]:
    pipeline_tag = f"{tag}_pipeline"
    _run_cli(
        pipeline_main,
        [
            "pipeline_runner.py",
            "--input",
            input_path,
            "--outdir",
            outdir,
            "--tag",
            pipeline_tag,
        ],
    )

    return {
        "report_json": str(Path(outdir) / f"{pipeline_tag}_report.json"),
        "summary_json": str(Path(outdir) / f"{pipeline_tag}_summary.json"),
        "report_md": str(Path(outdir) / f"{pipeline_tag}_report.md"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run complete Phase 1 workflow in one command"
    )
    parser.add_argument("--input", required=True, help="Dataset path (.csv or .json)")
    parser.add_argument("--outdir", default="reports", help="Output directory")
    parser.add_argument("--tag", default="phase1", help="Base tag for all artifacts")

    parser.add_argument("--bullish-values", default="0.3,0.4,0.5,0.6")
    parser.add_argument("--bearish-values", default="-0.3,-0.4,-0.5,-0.6")
    parser.add_argument("--strong-move-values", default="0.8,1.0")
    parser.add_argument("--max-veto-pct", type=float, default=30.0)
    parser.add_argument("--top-k", type=int, default=5)

    args = parser.parse_args()

    sweep_outputs = run_threshold_sweep(
        input_path=args.input,
        outdir=args.outdir,
        tag=args.tag,
        bullish_values=args.bullish_values,
        bearish_values=args.bearish_values,
        strong_values=args.strong_move_values,
        max_veto_pct=args.max_veto_pct,
        top_k=args.top_k,
    )

    sweep_report = run_sweep_report(
        summary_json=sweep_outputs["summary"],
        ranked_json=sweep_outputs["ranked"],
        out_md=None,
    )

    pipeline_outputs = run_pipeline(
        input_path=args.input,
        outdir=args.outdir,
        tag=args.tag,
    )

    payload: Dict[str, Any] = {
        "workflow": "phase1_master",
        "input": args.input,
        "outdir": args.outdir,
        "tag": args.tag,
        "sweep_outputs": sweep_outputs,
        "sweep_report": sweep_report,
        "pipeline_outputs": pipeline_outputs,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

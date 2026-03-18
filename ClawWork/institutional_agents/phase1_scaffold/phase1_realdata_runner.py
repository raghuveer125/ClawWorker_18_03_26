from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from fyers_to_phase1_csv import main as export_main
from phase1_master_runner import main as master_main
from validate_phase1_input_csv import main as validate_main


def _run_cli(main_fn, argv: list[str]) -> int:
    original_argv = sys.argv[:]
    try:
        sys.argv = argv
        return int(main_fn())
    finally:
        sys.argv = original_argv


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Phase 1 real-data workflow: FYERS export -> input validation -> master pipeline"
    )
    parser.add_argument("--from-date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--resolution", default="D", help="FYERS resolution (default: D)")
    parser.add_argument(
        "--underlyings",
        default="NIFTY50,BANKNIFTY,SENSEX",
        help="Comma-separated underlyings",
    )
    parser.add_argument("--min-rows", type=int, default=20)
    parser.add_argument("--min-trading-days", type=int, default=20)
    parser.add_argument("--daily-realized-pnl-pct", type=float, default=0.0)
    parser.add_argument("--spread-bps", type=float, default=25.0)
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--tag", default="phase1_20day")
    parser.add_argument("--out-csv", default=None)
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args()

    outdir = Path(args.outdir)
    csv_path = Path(args.out_csv) if args.out_csv else outdir / f"{args.tag}_realdata_input.csv"
    validation_json = outdir / f"{args.tag}_input_validation.json"
    summary_json = Path(args.out_json) if args.out_json else outdir / f"{args.tag}_realdata_run_summary.json"

    export_rc = _run_cli(
        export_main,
        [
            "fyers_to_phase1_csv.py",
            f"--from-date={args.from_date}",
            f"--to-date={args.to_date}",
            f"--resolution={args.resolution}",
            f"--underlyings={args.underlyings}",
            f"--daily-realized-pnl-pct={args.daily_realized_pnl_pct}",
            f"--spread-bps={args.spread_bps}",
            f"--min-rows={args.min_rows}",
            f"--out-csv={csv_path}",
        ],
    )

    validate_rc = _run_cli(
        validate_main,
        [
            "validate_phase1_input_csv.py",
            f"--input-csv={csv_path}",
            f"--min-rows={args.min_rows}",
            f"--min-trading-days={args.min_trading_days}",
            f"--out-json={validation_json}",
        ],
    )

    pipeline_rc = 2
    if validate_rc == 0:
        pipeline_rc = _run_cli(
            master_main,
            [
                "phase1_master_runner.py",
                f"--input={csv_path}",
                f"--outdir={outdir}",
                f"--tag={args.tag}",
            ],
        )

    final_summary = {
        "workflow": "phase1_realdata_runner",
        "tag": args.tag,
        "from_date": args.from_date,
        "to_date": args.to_date,
        "resolution": args.resolution,
        "underlyings": args.underlyings,
        "paths": {
            "input_csv": str(csv_path),
            "validation_json": str(validation_json),
            "pipeline_summary_json": str(outdir / f"{args.tag}_pipeline_summary.json"),
            "pipeline_report_json": str(outdir / f"{args.tag}_pipeline_report.json"),
            "final_report_md": str(outdir / f"{args.tag}_final_report.md"),
        },
        "exit_codes": {
            "export": export_rc,
            "validate": validate_rc,
            "pipeline": pipeline_rc,
        },
        "passed": export_rc == 0 and validate_rc == 0 and pipeline_rc == 0,
    }

    if validation_json.exists():
        try:
            final_summary["validation"] = _read_json(validation_json)
        except Exception as exc:  # noqa: BLE001
            final_summary["validation_read_error"] = str(exc)

    pipeline_summary_path = outdir / f"{args.tag}_pipeline_summary.json"
    if pipeline_summary_path.exists():
        try:
            final_summary["pipeline_summary"] = _read_json(pipeline_summary_path)
        except Exception as exc:  # noqa: BLE001
            final_summary["pipeline_summary_read_error"] = str(exc)

    _write_json(summary_json, final_summary)
    print(json.dumps(final_summary, indent=2))

    return 0 if final_summary["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

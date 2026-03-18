from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from contracts import MomentumSignal, OptionChainInput, OptionRow
from decision_layer import DecisionLayer
from metrics import summarize_results
from options_analyst import OptionsAnalyst


def _to_rows(raw_rows: List[Dict[str, Any]]) -> List[OptionRow]:
    return [OptionRow(**row) for row in raw_rows]


def _to_chain_input(item: Dict[str, Any]) -> OptionChainInput:
    return OptionChainInput(
        underlying=item["underlying"],
        underlying_change_pct=float(item["underlying_change_pct"]),
        iv_percentile=float(item["iv_percentile"]),
        straddle_breakout_direction=item["straddle_breakout_direction"],
        straddle_band_pct=float(item.get("straddle_band_pct", 12.0)),
        rows=_to_rows(item.get("rows", [])),
    )


def _load_dataset(path: Path) -> List[OptionChainInput]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("Input must be a JSON object or array of objects.")
    return [_to_chain_input(item) for item in raw if isinstance(item, dict)]


def _build_momentum_signal(change_pct: float) -> MomentumSignal:
    if change_pct >= 0.4:
        return MomentumSignal(action="BUY_CALL", confidence="MEDIUM")
    if change_pct <= -0.4:
        return MomentumSignal(action="BUY_PUT", confidence="MEDIUM")
    return MomentumSignal(action="NO_TRADE", confidence="LOW")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 2 batch options analysis")
    parser.add_argument("--input", required=True, help="Path to JSON dataset")
    parser.add_argument("--outdir", default="reports", help="Output directory")
    parser.add_argument("--tag", default="phase2", help="Run tag")
    args = parser.parse_args()

    dataset = _load_dataset(Path(args.input))
    analyst = OptionsAnalyst()
    decision_layer = DecisionLayer()

    results: List[Dict[str, Any]] = []
    for item in dataset:
        momentum = _build_momentum_signal(item.underlying_change_pct)
        options_signal = analyst.analyze(item)
        final_decision = decision_layer.merge(momentum, options_signal)

        results.append(
            {
                "underlying": item.underlying,
                "momentum_signal": asdict(momentum),
                "options_signal": asdict(options_signal),
                "final_decision": asdict(final_decision),
            }
        )

    summary = summarize_results(results)

    outdir = Path(args.outdir)
    report_path = outdir / f"{args.tag}_report.json"
    summary_path = outdir / f"{args.tag}_summary.json"

    _write_json(
        report_path,
        {
            "run_tag": args.tag,
            "input_file": args.input,
            "record_count": len(dataset),
            "summary": summary,
            "results": results,
        },
    )
    _write_json(summary_path, {"run_tag": args.tag, "record_count": len(dataset), **summary})

    print(json.dumps({"report": str(report_path), "summary": str(summary_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

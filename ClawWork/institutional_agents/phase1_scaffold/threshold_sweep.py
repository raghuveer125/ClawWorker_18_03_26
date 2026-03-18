from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from batch_runner import load_dataset, write_json
from config import SignalConfig
from metrics import summarize_decisions
from signal_engine import SignalEngine


def _parse_float_list(raw: str) -> List[float]:
    values: List[float] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        values.append(float(token))
    if not values:
        raise ValueError("At least one value is required.")
    return values


def _evaluate_config(
    dataset,
    bullish_threshold: float,
    bearish_threshold: float,
    strong_move_threshold: float,
) -> Dict[str, Any]:
    config = SignalConfig(
        bullish_threshold=bullish_threshold,
        bearish_threshold=bearish_threshold,
        strong_move_threshold=strong_move_threshold,
        model_version=(
            f"phase1_sweep_b{bullish_threshold:.2f}_"
            f"s{strong_move_threshold:.2f}_"
            f"br{bearish_threshold:.2f}"
        ),
    )
    engine = SignalEngine(config)

    decisions = [asdict(engine.decide(item)) for item in dataset]
    summary = summarize_decisions(decisions)

    action_counts = summary.get("action_counts", {})
    confidence_counts = summary.get("confidence_counts", {})

    active_count = int(action_counts.get("BUY_CALL", 0)) + int(action_counts.get("BUY_PUT", 0))
    veto_count = int(summary.get("risk_veto_count", 0))
    veto_pct = float(summary.get("risk_veto_pct", 0.0))
    high_medium_conf = int(confidence_counts.get("HIGH", 0)) + int(confidence_counts.get("MEDIUM", 0))

    # Light heuristic score for ranking
    score = round(active_count - (0.5 * veto_count) + (0.1 * high_medium_conf), 4)

    return {
        "config": {
            "bullish_threshold": bullish_threshold,
            "bearish_threshold": bearish_threshold,
            "strong_move_threshold": strong_move_threshold,
        },
        "summary": summary,
        "derived": {
            "active_count": active_count,
            "veto_count": veto_count,
            "veto_pct": veto_pct,
            "high_medium_confidence_count": high_medium_conf,
            "score": score,
        },
    }


def _rank_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        results,
        key=lambda row: (
            row["derived"]["score"],
            row["derived"]["active_count"],
            -row["derived"]["veto_pct"],
            row["derived"]["high_medium_confidence_count"],
        ),
        reverse=True,
    )


def _pick_best(
    ranked: List[Dict[str, Any]],
    max_veto_pct: float,
) -> Dict[str, Any] | None:
    for row in ranked:
        if row["derived"]["veto_pct"] <= max_veto_pct:
            return row
    return ranked[0] if ranked else None


def _build_grid(
    bullish_values: List[float],
    bearish_values: List[float],
    strong_move_values: List[float],
) -> List[Tuple[float, float, float]]:
    grid: List[Tuple[float, float, float]] = []
    for bullish in bullish_values:
        for bearish in bearish_values:
            if bullish <= 0 or bearish >= 0:
                continue
            if abs(bullish) > 10 or abs(bearish) > 10:
                continue
            for strong in strong_move_values:
                if strong <= 0:
                    continue
                grid.append((bullish, bearish, strong))
    if not grid:
        raise ValueError("Threshold grid is empty after validation.")
    return grid


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sweep Phase 1 thresholds and rank candidate configs"
    )
    parser.add_argument("--input", required=True, help="Path to dataset (.json or .csv)")
    parser.add_argument("--outdir", default="reports", help="Output directory")
    parser.add_argument("--tag", default="sweep", help="Run tag")
    parser.add_argument("--bullish-values", default="0.3,0.4,0.5,0.6")
    parser.add_argument("--bearish-values", default="-0.3,-0.4,-0.5,-0.6")
    parser.add_argument("--strong-move-values", default="0.8,1.0")
    parser.add_argument("--max-veto-pct", type=float, default=30.0)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    dataset = load_dataset(Path(args.input))

    bullish_values = _parse_float_list(args.bullish_values)
    bearish_values = _parse_float_list(args.bearish_values)
    strong_move_values = _parse_float_list(args.strong_move_values)

    grid = _build_grid(bullish_values, bearish_values, strong_move_values)

    results: List[Dict[str, Any]] = []
    for bullish, bearish, strong in grid:
        results.append(
            _evaluate_config(
                dataset=dataset,
                bullish_threshold=bullish,
                bearish_threshold=bearish,
                strong_move_threshold=strong,
            )
        )

    ranked = _rank_results(results)
    best = _pick_best(ranked, args.max_veto_pct)

    top_k = max(args.top_k, 1)

    payload = {
        "run_tag": args.tag,
        "input_file": args.input,
        "record_count": len(dataset),
        "evaluated_at": datetime.utcnow().isoformat() + "Z",
        "selection_policy": {
            "objective": "maximize score with veto guard",
            "max_veto_pct": args.max_veto_pct,
        },
        "total_configs": len(ranked),
        "best_config": best,
        "top_results": ranked[:top_k],
    }

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    summary_file = outdir / f"{args.tag}_threshold_sweep_summary.json"
    ranked_file = outdir / f"{args.tag}_threshold_sweep_ranked.json"

    write_json(summary_file, payload)
    write_json(
        ranked_file,
        {
            "run_tag": args.tag,
            "total_configs": len(ranked),
            "ranked_results": ranked,
        },
    )

    print(
        json.dumps(
            {
                "summary": str(summary_file),
                "ranked": str(ranked_file),
                "total_configs": len(ranked),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

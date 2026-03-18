from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Tuple


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_existing_path(raw_path: str, preferred_dirs: List[Path]) -> Path:
    candidate = Path(raw_path)
    if candidate.exists():
        return candidate

    attempted: List[Path] = [candidate]
    for base_dir in preferred_dirs:
        joined = base_dir / raw_path
        attempted.append(joined)
        if joined.exists():
            return joined

    attempted_str = "\n".join(f"- {str(item)}" for item in attempted)
    raise FileNotFoundError(
        f"Unable to locate file for input '{raw_path}'. Tried:\n{attempted_str}"
    )


def _read_outcomes(path: Optional[Path]) -> List[Dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Outcomes file must be a JSON array: {path}")
    return [row for row in payload if isinstance(row, dict)]


def _build_evaluation_pairs(predictions: List[Dict[str, Any]], outcomes: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    if not outcomes:
        return []

    has_indexed = any("prediction_index" in row for row in outcomes)
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

    if has_indexed:
        by_index = {int(pred["index"]): pred for pred in predictions}
        for row in outcomes:
            raw_index = row.get("prediction_index")
            if raw_index is None:
                continue
            try:
                idx = int(raw_index)
            except (TypeError, ValueError):
                continue
            pred = by_index.get(idx)
            if pred is not None:
                pairs.append((pred, row))
        return pairs

    paired_count = min(len(predictions), len(outcomes))
    for idx in range(paired_count):
        pairs.append((predictions[idx], outcomes[idx]))
    return pairs


def _load_phase1_predictions(path: Path) -> List[Dict[str, Any]]:
    report = _read_json(path)
    rows = report.get("decisions", [])
    predictions: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        predictions.append(
            {
                "index": index,
                "action": row.get("action", "NO_TRADE"),
                "confidence": row.get("confidence", "LOW"),
                "underlying": row.get("underlying"),
            }
        )
    return predictions


def _load_phase2_predictions(path: Path) -> List[Dict[str, Any]]:
    report = _read_json(path)
    rows = report.get("results", [])
    predictions: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        final_decision = row.get("final_decision", {})
        predictions.append(
            {
                "index": index,
                "action": final_decision.get("action", "NO_TRADE"),
                "confidence": final_decision.get("confidence", "LOW"),
                "underlying": row.get("underlying"),
            }
        )
    return predictions


def _signed_trade_return(action: str, realized_underlying_return_pct: float) -> float:
    if action == "BUY_CALL":
        return realized_underlying_return_pct
    if action == "BUY_PUT":
        return -realized_underlying_return_pct
    return 0.0


def _analyze_predictions(predictions: List[Dict[str, Any]], outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    actionable = [row for row in predictions if row.get("action") in {"BUY_CALL", "BUY_PUT"}]
    total = len(predictions)
    actionable_count = len(actionable)
    no_trade_count = sum(1 for row in predictions if row.get("action") == "NO_TRADE")

    base_metrics: Dict[str, Any] = {
        "total_records": total,
        "actionable_signals": actionable_count,
        "no_trade_signals": no_trade_count,
        "actionable_rate_pct": round((100.0 * actionable_count / total), 2) if total else 0.0,
    }

    if not outcomes:
        base_metrics.update(
            {
                "false_signal_rate_pct": None,
                "risk_adjusted_return": None,
                "average_trade_return_pct": None,
                "evaluation_rows": 0,
                "evaluation_status": "insufficient_data",
                "evaluation_note": "No outcomes file supplied.",
            }
        )
        return base_metrics

    pairs = _build_evaluation_pairs(predictions, outcomes)
    paired_count = len(pairs)
    false_signals = 0
    trade_returns: List[float] = []

    for pred, outcome in pairs:

        action = str(pred.get("action", "NO_TRADE"))
        actual_action = str(outcome.get("actual_action", "NO_TRADE"))
        realized_return = float(outcome.get("realized_underlying_return_pct", 0.0))

        if action in {"BUY_CALL", "BUY_PUT"}:
            if action != actual_action:
                false_signals += 1
            trade_returns.append(_signed_trade_return(action, realized_return))

    actionable_for_eval = sum(1 for pred, _ in pairs if pred.get("action") in {"BUY_CALL", "BUY_PUT"})
    false_signal_rate = (100.0 * false_signals / actionable_for_eval) if actionable_for_eval else 0.0

    avg_trade_return = mean(trade_returns) if trade_returns else 0.0
    return_std = pstdev(trade_returns) if len(trade_returns) > 1 else 0.0

    if actionable_for_eval < 2:
        risk_adjusted: Optional[float] = None
        evaluation_status = "insufficient_data"
        evaluation_note = "Need at least 2 actionable evaluated trades for risk-adjusted return."
    elif return_std == 0.0:
        risk_adjusted = None
        evaluation_status = "insufficient_dispersion"
        evaluation_note = "Actionable trade returns have zero variance; risk-adjusted return undefined."
    else:
        risk_adjusted = avg_trade_return / return_std
        evaluation_status = "ok"
        evaluation_note = "Evaluation computed from paired outcome rows."

    base_metrics.update(
        {
            "false_signal_rate_pct": round(false_signal_rate, 2),
            "risk_adjusted_return": round(risk_adjusted, 4) if risk_adjusted is not None else None,
            "average_trade_return_pct": round(avg_trade_return, 4),
            "evaluation_rows": paired_count,
            "evaluation_status": evaluation_status,
            "evaluation_note": f"{evaluation_note} (rows used: {paired_count})",
        }
    )
    return base_metrics


def _render_markdown(payload: Dict[str, Any]) -> str:
    p1 = payload["phase1"]
    p2 = payload["phase2"]
    cmp = payload["comparison"]

    return "\n".join(
        [
            f"# Phase 1 vs Phase 2 Baseline Comparison ({payload['tag']})",
            "",
            "## Inputs",
            f"- Phase 1 report: {payload['phase1_report']}",
            f"- Phase 2 report: {payload['phase2_report']}",
            f"- Phase 1 outcomes: {payload.get('phase1_outcomes') or 'not provided'}",
            f"- Phase 2 outcomes: {payload.get('phase2_outcomes') or 'not provided'}",
            "",
            "## Phase Metrics",
            f"- Phase 1 false-signal rate (%): {p1['false_signal_rate_pct']}",
            f"- Phase 2 false-signal rate (%): {p2['false_signal_rate_pct']}",
            f"- Phase 1 risk-adjusted return: {p1['risk_adjusted_return']}",
            f"- Phase 2 risk-adjusted return: {p2['risk_adjusted_return']}",
            "",
            "## Exit Criteria",
            f"- False-signal reduction vs Phase 1: {cmp['false_signal_reduction_met']}",
            f"- Improved risk-adjusted return: {cmp['risk_adjusted_improvement_met']}",
            f"- False-signal delta (pct points): {cmp['false_signal_delta_pct_points']}",
            f"- Risk-adjusted delta: {cmp['risk_adjusted_delta']}",
            "",
            "## Notes",
            "- False-signal rate uses actionable decisions only (BUY_CALL, BUY_PUT).",
            "- `actual_action` in outcomes should be BUY_CALL, BUY_PUT, or NO_TRADE.",
            "- `realized_underlying_return_pct` is signed underlying move after signal horizon.",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Phase 2 against Phase 1 baseline")
    parser.add_argument("--phase1-report", required=True)
    parser.add_argument("--phase2-report", required=True)
    parser.add_argument("--phase1-outcomes", default=None)
    parser.add_argument("--phase2-outcomes", default=None)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default=None)
    parser.add_argument("--tag", default="phase2_vs_phase1")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    phase2_reports_dir = script_dir / "reports"
    phase1_reports_dir = script_dir.parent / "phase1_scaffold" / "reports"

    phase1_report_path = _resolve_existing_path(
        args.phase1_report,
        [phase1_reports_dir, phase2_reports_dir, script_dir],
    )
    phase2_report_path = _resolve_existing_path(
        args.phase2_report,
        [phase2_reports_dir, script_dir, phase1_reports_dir],
    )

    phase1_predictions = _load_phase1_predictions(phase1_report_path)
    phase2_predictions = _load_phase2_predictions(phase2_report_path)

    phase1_outcomes = (
        _read_outcomes(
            _resolve_existing_path(
                args.phase1_outcomes,
                [script_dir, phase2_reports_dir, phase1_reports_dir],
            )
        )
        if args.phase1_outcomes
        else []
    )
    phase2_outcomes = (
        _read_outcomes(
            _resolve_existing_path(
                args.phase2_outcomes,
                [script_dir, phase2_reports_dir, phase1_reports_dir],
            )
        )
        if args.phase2_outcomes
        else []
    )

    phase1_metrics = _analyze_predictions(phase1_predictions, phase1_outcomes)
    phase2_metrics = _analyze_predictions(phase2_predictions, phase2_outcomes)

    if (
        phase1_metrics["false_signal_rate_pct"] is None
        or phase2_metrics["false_signal_rate_pct"] is None
        or phase1_metrics["risk_adjusted_return"] is None
        or phase2_metrics["risk_adjusted_return"] is None
    ):
        comparison = {
            "false_signal_reduction_met": None,
            "risk_adjusted_improvement_met": None,
            "false_signal_delta_pct_points": None,
            "risk_adjusted_delta": None,
            "status": "insufficient_data",
            "note": "Provide both phase outcomes files to evaluate exit criteria.",
        }
        exit_code = 0
    else:
        false_signal_delta = round(
            float(phase1_metrics["false_signal_rate_pct"]) - float(phase2_metrics["false_signal_rate_pct"]),
            2,
        )
        risk_adjusted_delta = round(
            float(phase2_metrics["risk_adjusted_return"]) - float(phase1_metrics["risk_adjusted_return"]),
            4,
        )
        comparison = {
            "false_signal_reduction_met": false_signal_delta > 0.0,
            "risk_adjusted_improvement_met": risk_adjusted_delta > 0.0,
            "false_signal_delta_pct_points": false_signal_delta,
            "risk_adjusted_delta": risk_adjusted_delta,
            "status": "ok",
            "note": "Computed from supplied outcomes files.",
        }
        exit_code = 0

    payload = {
        "tag": args.tag,
        "phase1_report": str(phase1_report_path),
        "phase2_report": str(phase2_report_path),
        "phase1_outcomes": args.phase1_outcomes,
        "phase2_outcomes": args.phase2_outcomes,
        "phase1": phase1_metrics,
        "phase2": phase2_metrics,
        "comparison": comparison,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    out_md = Path(args.out_md) if args.out_md else out_json.with_suffix(".md")
    out_md.write_text(_render_markdown(payload), encoding="utf-8")

    print(json.dumps({"comparison_json": str(out_json), "comparison_md": str(out_md)}, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from contracts import Phase4Input
from decision_engine import Phase4DecisionEngine


def _load_dataset(path: Path) -> List[Phase4Input]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("Input must be JSON object or array")
    return [Phase4Input(**row) for row in payload if isinstance(row, dict)]


def _summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    buy_call = sum(1 for row in rows if row.get("decision", {}).get("action") == "BUY_CALL")
    buy_put = sum(1 for row in rows if row.get("decision", {}).get("action") == "BUY_PUT")
    no_trade = sum(1 for row in rows if row.get("decision", {}).get("action") == "NO_TRADE")
    event_blocks = sum(1 for row in rows if row.get("decision", {}).get("policy_tags", {}).get("gate") == "EVENT_BLOCK")
    portfolio_blocks = sum(
        1 for row in rows if row.get("decision", {}).get("policy_tags", {}).get("gate") == "PORTFOLIO_BLOCK"
    )

    return {
        "total_records": total,
        "action_counts": {"BUY_CALL": buy_call, "BUY_PUT": buy_put, "NO_TRADE": no_trade},
        "event_block_count": event_blocks,
        "event_block_pct": round((100.0 * event_blocks / total), 2) if total else 0.0,
        "portfolio_block_count": portfolio_blocks,
        "portfolio_block_pct": round((100.0 * portfolio_blocks / total), 2) if total else 0.0,
    }


def _write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 4 batch decision flow")
    parser.add_argument("--input", required=True)
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--tag", default="phase4_demo")
    args = parser.parse_args()

    dataset = _load_dataset(Path(args.input))
    engine = Phase4DecisionEngine()

    results: List[Dict[str, Any]] = []
    for item in dataset:
        decision = engine.evaluate(item)
        results.append({"input": asdict(item), "decision": asdict(decision)})

    summary = _summary(results)
    outdir = Path(args.outdir)
    report_path = outdir / f"{args.tag}_report.json"
    summary_path = outdir / f"{args.tag}_summary.json"

    _write(
        report_path,
        {
            "run_tag": args.tag,
            "input_file": args.input,
            "record_count": len(dataset),
            "summary": summary,
            "results": results,
        },
    )
    _write(summary_path, {"run_tag": args.tag, **summary})

    print(json.dumps({"report": str(report_path), "summary": str(summary_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

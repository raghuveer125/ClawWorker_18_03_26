from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from contracts import Phase3Input
from runner import run_single


def _load_dataset(path: Path) -> List[Phase3Input]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("Input must be a JSON object or array")
    return [Phase3Input(**row) for row in payload if isinstance(row, dict)]


def _summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    buy_call = sum(1 for row in rows if row.get("consensus", {}).get("action") == "BUY_CALL")
    buy_put = sum(1 for row in rows if row.get("consensus", {}).get("action") == "BUY_PUT")
    no_trade = sum(1 for row in rows if row.get("consensus", {}).get("action") == "NO_TRADE")
    veto = sum(1 for row in rows if row.get("consensus", {}).get("veto_applied") is True)

    return {
        "total_records": total,
        "consensus_counts": {
            "BUY_CALL": buy_call,
            "BUY_PUT": buy_put,
            "NO_TRADE": no_trade,
        },
        "veto_count": veto,
        "veto_pct": round((100.0 * veto / total), 2) if total else 0.0,
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 3 batch orchestration")
    parser.add_argument("--input", required=True)
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--tag", default="phase3_demo")
    args = parser.parse_args()

    dataset = _load_dataset(Path(args.input))
    results: List[Dict[str, Any]] = []
    for row in dataset:
        result = run_single(row)
        result_payload = asdict(result)
        result_payload["underlying"] = row.underlying
        result_payload["timestamp"] = row.timestamp
        results.append(result_payload)

    summary = _summary(results)
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
    _write_json(summary_path, {"run_tag": args.tag, **summary})

    print(json.dumps({"report": str(report_path), "summary": str(summary_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

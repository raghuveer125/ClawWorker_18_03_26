from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from config import SignalConfig
from metrics import summarize_decisions
from models import MarketInput
from signal_engine import SignalEngine


SUPPORTED_INPUT_EXT = {".json", ".csv"}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "timestamp": str(row.get("timestamp", "")),
        "underlying": row.get("underlying", "NIFTY50"),
        "ltp": _to_float(row.get("ltp")),
        "prev_close": _to_float(row.get("prev_close")),
        "session": row.get("session", "OPEN"),
        "daily_realized_pnl_pct": _to_float(row.get("daily_realized_pnl_pct")),
        "bid_ask_spread_bps": _to_float(row.get("bid_ask_spread_bps")),
    }


def _load_json(path: Path) -> List[Dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("JSON input must be an object or list of objects")
    return [_normalize_row(item) for item in raw if isinstance(item, dict)]


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for item in reader:
            rows.append(_normalize_row(item))
    return rows


def load_dataset(path: Path) -> List[MarketInput]:
    if path.suffix.lower() not in SUPPORTED_INPUT_EXT:
        supported = ", ".join(sorted(SUPPORTED_INPUT_EXT))
        raise ValueError(f"Unsupported input extension: {path.suffix}. Use one of: {supported}")

    normalized = _load_json(path) if path.suffix.lower() == ".json" else _load_csv(path)
    return [MarketInput(**row) for row in normalized]


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run batch paper backtest for Phase 1 scaffold")
    parser.add_argument("--input", required=True, help="Path to input dataset (.json or .csv)")
    parser.add_argument("--outdir", default="reports", help="Output directory for results")
    parser.add_argument("--tag", default="run", help="Run tag used in output filenames")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.outdir)

    dataset = load_dataset(input_path)
    engine = SignalEngine(SignalConfig())

    decisions = [asdict(engine.decide(item)) for item in dataset]
    summary = summarize_decisions(decisions)

    payload = {
        "run_tag": args.tag,
        "input_file": str(input_path),
        "record_count": len(dataset),
        "summary": summary,
        "decisions": decisions,
    }

    report_file = output_dir / f"{args.tag}_report.json"
    write_json(report_file, payload)

    quick_summary = {
        "run_tag": args.tag,
        "record_count": len(dataset),
        **summary,
    }
    quick_summary_file = output_dir / f"{args.tag}_summary.json"
    write_json(quick_summary_file, quick_summary)

    print(json.dumps({"report": str(report_file), "summary": str(quick_summary_file)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

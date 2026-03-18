#!/usr/bin/env python3
"""
Replay runner for the scalping engine.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[2]
SCALPING_ROOT = REPO_ROOT / "engines" / "scalping"
if str(SCALPING_ROOT) not in sys.path:
    sys.path.insert(0, str(SCALPING_ROOT))

from scalping.config import IndexType, ScalpingConfig
from scalping.engine import ScalpingEngine
from scalping.replay_reporting import ReplayDiagnosticsTracker, safe_float


DEFAULT_DATASET = (
    REPO_ROOT
    / "fyersN7"
    / "fyers-2026-03-05"
    / "postmortem"
    / "2026-03-16"
    / "SENSEX"
    / "decision_journal.csv"
)


def _load_row_count(csv_path: Path) -> int:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))

async def run_replay(csv_path: Path) -> Dict[str, Any]:
    config = ScalpingConfig(indices=[IndexType.SENSEX])
    engine = ScalpingEngine(
        config=config,
        dry_run=True,
        replay_mode=True,
        replay_csv_path=str(csv_path),
        replay_interval_ms=200,
    )

    cycle_latencies_ms: List[float] = []
    diagnostics = ReplayDiagnosticsTracker()

    await engine.start()
    try:
        while engine.has_replay_remaining():
            cycle_start = time.perf_counter()
            results = await engine.run_cycle()
            cycle_latencies_ms.append((time.perf_counter() - cycle_start) * 1000.0)

            if results.get("status") == "replay_complete":
                break

            diagnostics.observe_cycle(engine.context, results)
    finally:
        await engine.stop()

    trades = list(engine.context.data.get("executed_trades", []))
    closed_trades = [trade for trade in trades if trade.get("status") == "closed"]
    capital_state = engine.context.data.get("capital_state", {})

    diagnostics_report = diagnostics.build_report(trades, safe_float(capital_state.get("total_pnl")))
    report = {
        "dataset": str(csv_path),
        "total_rows": _load_row_count(csv_path),
        "total_cycles": engine.cycle_count,
        "signals_detected": diagnostics_report["stage_totals"]["total_strike_selections"],
        "signals_after_quality": diagnostics_report["stage_totals"]["total_quality_pass"],
        "signals_after_liquidity": diagnostics_report["stage_totals"]["total_liquidity_pass"],
        "trades_executed": len(trades),
        "simulated_pnl": safe_float(capital_state.get("total_pnl")),
        "execution_latency": round(sum(cycle_latencies_ms) / len(cycle_latencies_ms), 2) if cycle_latencies_ms else 0.0,
        "profit_factor": diagnostics_report["profit_factor"],
        "win_rate": diagnostics_report["win_rate"],
    }
    report.update(diagnostics_report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scalping engine replay test")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="Path to decision_journal.csv")
    args = parser.parse_args()

    csv_path = Path(args.dataset)
    if not csv_path.exists():
        print(json.dumps({"error": f"Dataset not found: {csv_path}"}, indent=2))
        return 1

    metrics = asyncio.run(run_replay(csv_path))
    print(metrics["diagnostic_report"])
    print(json.dumps(metrics, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

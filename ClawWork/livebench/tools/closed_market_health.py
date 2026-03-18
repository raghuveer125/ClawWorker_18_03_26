"""
Closed-market project audit for ClawWork_FyersN7.

Runs repeatable dry checks that do not require live market data:
- full unittest discovery
- Python bytecode compilation
- optional frontend build
- historical SENSEX candle replay across active bots
"""

import argparse
import csv
import io
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Tuple


TOOL_ROOT = Path(__file__).resolve().parent
LIVEBENCH_ROOT = TOOL_ROOT.parent
APP_ROOT = LIVEBENCH_ROOT.parent
PROJECT_ROOT = APP_ROOT.parent
POSTMORTEM_ROOT = PROJECT_ROOT / "fyersN7" / "fyers-2026-03-05" / "postmortem"
FRONTEND_ROOT = APP_ROOT / "frontend"


def run_command(name: str, cmd: List[str], cwd: Path) -> Dict[str, Any]:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return {
        "name": name,
        "command": cmd,
        "cwd": str(cwd),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
        "passed": completed.returncode == 0,
    }


def find_reference_csv() -> Path:
    candidates = sorted(POSTMORTEM_ROOT.glob("*/SENSEX/sensex_1m_ohlcv_*.csv"))
    if not candidates:
        raise FileNotFoundError("No SENSEX 1m OHLCV CSV found under postmortem")
    return candidates[0]


def _build_market_data(rows: List[Dict[str, str]], index: int) -> Dict[str, Any]:
    current = rows[index]
    previous = rows[index - 1]
    day_open = float(rows[0]["open"])
    close = float(current["close"])
    prev_close = float(previous["close"])
    return {
        "open": float(current["open"]),
        "high": float(current["high"]),
        "low": float(current["low"]),
        "close": close,
        "ltp": close,
        "prev_close": prev_close,
        "change_pct": ((close - day_open) / day_open) * 100 if day_open else 0.0,
        "momentum": ((close - prev_close) / prev_close) * 100 if prev_close else 0.0,
        "volume": int(float(current.get("volume", 0) or 0)),
        "timestamp": current.get("timestamp_ist") or f"{current.get('date')} {current.get('time')}",
    }


def run_historical_bot_replay(csv_path: Path) -> Dict[str, Any]:
    if str(LIVEBENCH_ROOT) not in sys.path:
        sys.path.insert(0, str(LIVEBENCH_ROOT))

    from bots.base import SharedMemory
    from bots.ensemble import EnsembleCoordinator
    from bots.ict_sniper import ICTSniperBot
    from bots.momentum_scalper import MomentumScalperBot
    from bots.oi_analyst import OIAnalystBot
    from bots.regime_hunter import RegimeHunterBot
    from bots.reversal_hunter import ReversalHunterBot
    from bots.trend_follower import TrendFollowerBot
    from bots.volatility_trader import VolatilityTraderBot

    with open(csv_path, "r") as f:
        rows = list(csv.DictReader(f))

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            memory = SharedMemory(str(temp_root / "health_backtest_memory"))
            bots = [
                TrendFollowerBot(memory),
                ReversalHunterBot(memory),
                MomentumScalperBot(memory),
                OIAnalystBot(memory),
                VolatilityTraderBot(memory),
                ICTSniperBot(memory),
                RegimeHunterBot(memory),
            ]

            counts = {bot.name: 0 for bot in bots}
            for index in range(5, len(rows)):
                market_data = _build_market_data(rows, index)
                for bot in bots:
                    try:
                        signal = bot.analyze("SENSEX", market_data, None)
                    except TypeError:
                        signal = bot.analyze("SENSEX", market_data)
                    if signal:
                        counts[bot.name] += 1

            ensemble = EnsembleCoordinator(SharedMemory(str(temp_root / "health_ensemble_memory")))
            ensemble_decisions = 0
            for index in range(20, len(rows)):
                market_data = _build_market_data(rows, index)
                decision = ensemble.analyze("SENSEX", market_data, None)
                if decision:
                    ensemble_decisions += 1

    return {
        "reference_csv": str(csv_path),
        "total_rows": len(rows),
        "bot_signal_counts": counts,
        "ensemble_decisions": ensemble_decisions,
        "notes": [
            "OIAnalyst and VolatilityTrader expect richer option/IV context than candle-only CSV replay provides.",
            "Zero ensemble decisions on candle-only replay is informational, not automatically a bug.",
        ],
    }


def collect_frontend_bundle_stats() -> List[Tuple[str, int]]:
    dist_assets = sorted((FRONTEND_ROOT / "dist" / "assets").glob("*.js"))
    return [(asset.name, asset.stat().st_size) for asset in dist_assets]


def run_closed_market_health(include_build: bool = True) -> Dict[str, Any]:
    report = {
        "project_root": str(PROJECT_ROOT),
        "checks": [],
        "historical_replay": {},
        "frontend_bundle_stats": [],
    }

    report["checks"].append(
        run_command("unittest_discovery", ["python3", "-m", "unittest", "discover", "-p", "test*.py"], APP_ROOT)
    )
    report["checks"].append(
        run_command(
            "compileall",
            ["python3", "-m", "compileall", "-q", str(APP_ROOT), str(PROJECT_ROOT / "shared_project_engine")],
            PROJECT_ROOT,
        )
    )

    if include_build:
        report["checks"].append(
            run_command("frontend_build", ["npm", "run", "build"], FRONTEND_ROOT)
        )
        if report["checks"][-1]["passed"]:
            report["frontend_bundle_stats"] = collect_frontend_bundle_stats()

    csv_path = find_reference_csv()
    report["historical_replay"] = run_historical_bot_replay(csv_path)
    report["passed"] = all(check["passed"] for check in report["checks"])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run closed-market project health checks")
    parser.add_argument("--skip-build", action="store_true", help="Skip frontend production build")
    parser.add_argument("--write-json", help="Write report JSON to a file")
    args = parser.parse_args()

    report = run_closed_market_health(include_build=not args.skip_build)

    if args.write_json:
        output_path = Path(args.write_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)

    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

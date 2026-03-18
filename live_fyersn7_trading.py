#!/usr/bin/env python3
"""
FyersN7 Live Trading Runner - Optimized Configuration

Uses the backtested optimal settings:
- 25/15 point exit strategy (69.7% win rate)
- REQUIRE_TREND_ALIGNMENT = True
- SENSEX and NIFTY50 indices

Usage:
    python live_fyersn7_trading.py --indices SENSEX NIFTY50 --capital 100000

This script:
1. Connects to FyersN7 live signals
2. Applies optimized filters (trend alignment)
3. Tracks positions with 25pt target / 15pt SL
4. Logs all trades for review
"""

import os
import sys
import json
import csv
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from ai_hub.layer0.adapters import FyersN7SignalAdapter, FyersN7Signal

IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("LiveFyersN7")

# Paths
LIVE_JOURNAL_DIR = PROJECT_ROOT / "fyersN7" / "fyers-2026-03-05" / "live"
STATE_FILE = LIVE_JOURNAL_DIR / "live_state.json"
TRADES_LOG = LIVE_JOURNAL_DIR / "trades.csv"

# Configuration from optimal backtest
CONFIG = {
    "target_points": 25,
    "sl_points": 15,
    "position_size_pct": 0.10,
    "max_positions": 3,
    "fee_per_trade": 80,
}


@dataclass
class LivePosition:
    """Open position in live trading."""
    id: str
    index: str
    side: str  # CE or PE
    strike: int
    entry_price: float
    entry_time: str
    sl_price: float
    target_price: float
    quantity: int = 1
    status: str = "OPEN"


@dataclass
class LiveState:
    """Live trading state."""
    initial_capital: float = 100000.0
    current_capital: float = 100000.0
    realized_pnl: float = 0.0
    total_fees: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    open_positions: List[Dict] = None
    last_update: str = ""

    def __post_init__(self):
        if self.open_positions is None:
            self.open_positions = []


def is_market_open() -> bool:
    """Check if Indian market is open."""
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_start <= now <= market_end


def load_state(capital: float) -> LiveState:
    """Load or create live state."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            state = LiveState(**data)
            return state
        except Exception as e:
            logger.warning(f"Error loading state: {e}")

    return LiveState(initial_capital=capital, current_capital=capital)


def save_state(state: LiveState):
    """Save live state."""
    LIVE_JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    state.last_update = datetime.now(IST).isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(asdict(state), f, indent=2)


def log_trade(trade: Dict):
    """Log trade to CSV."""
    LIVE_JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

    headers = [
        "timestamp", "index", "side", "strike", "action",
        "price", "pnl", "reason", "capital_after"
    ]

    file_exists = TRADES_LOG.exists()
    with open(TRADES_LOG, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: trade.get(k, "") for k in headers})


def get_live_signals(indices: List[str]) -> List[Dict]:
    """
    Get live signals from FyersN7.

    In production, this would connect to the live decision journal
    or WebSocket feed. For now, reads the latest signals.
    """
    signals = []
    today = datetime.now(IST).strftime("%Y-%m-%d")
    postmortem_base = PROJECT_ROOT / "fyersN7" / "fyers-2026-03-05" / "postmortem" / today

    for index in indices:
        journal_file = postmortem_base / index / "decision_journal.csv"
        if journal_file.exists():
            try:
                with open(journal_file, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                    if rows:
                        # Get most recent signal
                        latest = rows[-1]
                        latest["_index"] = index
                        signals.append(latest)
            except Exception as e:
                logger.warning(f"Error reading {journal_file}: {e}")

    return signals


def check_exit(position: Dict, current_price: float, target_pts: float, sl_pts: float) -> Tuple[bool, float, str]:
    """Check if position should be exited."""
    entry = position["entry_price"]
    side = position["side"]

    if side == "CE":
        points_moved = current_price - entry
        if points_moved >= target_pts:
            return True, current_price, "TARGET"
        elif points_moved <= -sl_pts:
            return True, current_price, "STOPLOSS"
    else:  # PE
        points_moved = entry - current_price
        if points_moved >= target_pts:
            return True, current_price, "TARGET"
        elif points_moved <= -sl_pts:
            return True, current_price, "STOPLOSS"

    return False, 0, ""


def run_live_trading(
    indices: List[str],
    capital: float,
    poll_interval: int = 5,
):
    """Run live trading loop."""

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║   FyersN7 Live Trading - Optimized Configuration                 ║
╠══════════════════════════════════════════════════════════════════╣
║   Strategy: Point-Based Exit (25pt target / 15pt SL)             ║
║   Filter: REQUIRE_TREND_ALIGNMENT = True                         ║
║   Backtest WR: 69.7% | Indices: {', '.join(indices):<25} ║
║   Capital: Rs {capital:,.0f}                                     ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    # Load state
    state = load_state(capital)
    logger.info(f"Loaded state: Capital={state.current_capital:,.0f}, PnL={state.realized_pnl:+,.0f}")

    # Create adapter with optimized settings
    adapter = FyersN7SignalAdapter(
        min_score=50,
        min_confidence=70,
        min_signal_gap=30,
        max_trades_per_hour=10,
    )

    logger.info("Adapter initialized with REQUIRE_TREND_ALIGNMENT=True")
    logger.info(f"Polling every {poll_interval}s. Press Ctrl+C to stop.")

    try:
        while True:
            now = datetime.now(IST)

            if not is_market_open():
                next_check = 60
                logger.info(f"Market closed. Next check in {next_check}s...")
                time.sleep(next_check)
                continue

            # Get live signals
            signals = get_live_signals(indices)

            for row in signals:
                index = row.get("_index", "")
                entry_price = float(row.get("entry", 0) or 0)

                if entry_price <= 0:
                    continue

                # Process through adapter (applies trend alignment filter)
                signal, should_trade, reasons, warnings = adapter.process_signal_row(row, index)

                # Check open positions for exit
                for pos in state.open_positions[:]:
                    if pos["index"] == index:
                        should_exit, exit_price, exit_reason = check_exit(
                            pos, entry_price,
                            CONFIG["target_points"],
                            CONFIG["sl_points"]
                        )

                        if should_exit:
                            # Calculate PnL
                            if pos["side"] == "CE":
                                pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
                            else:
                                pnl = (pos["entry_price"] - exit_price) * pos["quantity"]

                            pnl -= CONFIG["fee_per_trade"]

                            # Update state
                            state.realized_pnl += pnl
                            state.current_capital += pnl
                            state.total_trades += 1
                            state.total_fees += CONFIG["fee_per_trade"]

                            if pnl > 0:
                                state.winning_trades += 1
                                logger.info(f"WIN: {pos['side']} {index} | PnL: +{pnl:,.0f} | Reason: {exit_reason}")
                            else:
                                state.losing_trades += 1
                                logger.info(f"LOSS: {pos['side']} {index} | PnL: {pnl:,.0f} | Reason: {exit_reason}")

                            # Log trade
                            log_trade({
                                "timestamp": now.isoformat(),
                                "index": index,
                                "side": pos["side"],
                                "strike": pos["strike"],
                                "action": "EXIT",
                                "price": exit_price,
                                "pnl": pnl,
                                "reason": exit_reason,
                                "capital_after": state.current_capital,
                            })

                            state.open_positions.remove(pos)

                # Check for new entry
                if should_trade and len(state.open_positions) < CONFIG["max_positions"]:
                    # Check if already have position in this index
                    existing = [p for p in state.open_positions if p["index"] == index]
                    if not existing:
                        # Calculate position size
                        position_value = state.current_capital * CONFIG["position_size_pct"]
                        quantity = max(1, int(position_value / entry_price))

                        # Create position
                        new_pos = {
                            "id": f"L{state.total_trades + 1:04d}",
                            "index": index,
                            "side": signal.side,
                            "strike": signal.strike,
                            "entry_price": entry_price,
                            "entry_time": now.isoformat(),
                            "quantity": quantity,
                        }

                        state.open_positions.append(new_pos)
                        state.total_fees += CONFIG["fee_per_trade"]

                        logger.info(f"ENTRY: {signal.side} {index} @ {entry_price:.2f} | Reasons: {', '.join(reasons)}")

                        # Log trade
                        log_trade({
                            "timestamp": now.isoformat(),
                            "index": index,
                            "side": signal.side,
                            "strike": signal.strike,
                            "action": "ENTRY",
                            "price": entry_price,
                            "pnl": 0,
                            "reason": "; ".join(reasons),
                            "capital_after": state.current_capital,
                        })
                elif warnings:
                    # Log rejected signals
                    logger.debug(f"SKIP {index}: {', '.join(warnings)}")

            # Save state
            save_state(state)

            # Print status
            win_rate = state.winning_trades / max(state.total_trades, 1) * 100
            print(f"\r[{now.strftime('%H:%M:%S')}] Capital: Rs {state.current_capital:,.0f} | "
                  f"PnL: {state.realized_pnl:+,.0f} | WR: {win_rate:.1f}% | "
                  f"Open: {len(state.open_positions)} | Trades: {state.total_trades}",
                  end="", flush=True)

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n")
        logger.info("Stopping live trading...")

    # Final summary
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║   SESSION SUMMARY                                                ║
╠══════════════════════════════════════════════════════════════════╣
║   Final Capital: Rs {state.current_capital:>15,.0f}                          ║
║   Realized P&L:  Rs {state.realized_pnl:>+15,.0f}                          ║
║   Total Trades:  {state.total_trades:>15}                              ║
║   Win Rate:      {state.winning_trades / max(state.total_trades, 1) * 100:>14.1f}%                             ║
║   Open Positions: {len(state.open_positions):>14}                              ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    save_state(state)
    logger.info(f"State saved to {STATE_FILE}")


def main():
    parser = argparse.ArgumentParser(description="FyersN7 Live Trading Runner")
    parser.add_argument("--indices", nargs="+", default=["SENSEX", "NIFTY50"],
                       help="Indices to trade")
    parser.add_argument("--capital", type=float, default=100000,
                       help="Initial capital")
    parser.add_argument("--poll-interval", type=int, default=5,
                       help="Polling interval in seconds")

    args = parser.parse_args()

    run_live_trading(
        indices=args.indices,
        capital=args.capital,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    main()

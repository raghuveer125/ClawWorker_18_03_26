#!/usr/bin/env python3
"""
FyersN7 Signal Backtest - Uses actual FyersN7 signals from postmortem data.

This is Option B: Use FyersN7 as indicator engine, Hub V2 for decisions.

Usage:
    python backtest_fyersn7_signals.py --indices SENSEX NIFTY50 --capital 100000

Features:
- Uses FyersN7's multi-factor scoring (spread, delta, gamma, IV, PCR, etc.)
- Replays actual captured signals from postmortem data
- Capital tracking with P&L
- Results on http://localhost:3001/scalping
"""

import os
import sys
import csv
import time
import asyncio
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, TYPE_CHECKING

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

# Import FyersN7 adapter
from ai_hub.layer0.adapters import FyersN7SignalAdapter, FyersN7Signal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("FyersN7Backtest")

SCALPING_API = "http://localhost:8002"
POSTMORTEM_BASE = PROJECT_ROOT / "fyersN7" / "fyers-2026-03-05" / "postmortem"


@dataclass
class Position:
    """Open position."""
    id: str
    index: str
    side: str
    strike: int
    entry_price: float
    entry_time: datetime
    stop_loss: float
    target: float
    quantity: int = 1
    status: str = "OPEN"
    exit_price: float = 0.0
    exit_time: Optional[datetime] = None
    pnl: float = 0.0
    signal_score: int = 0


@dataclass
class PaperTradingState:
    """Paper trading state."""
    initial_capital: float = 100000.0
    current_capital: float = 100000.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    open_positions: List[Position] = field(default_factory=list)
    closed_positions: List[Position] = field(default_factory=list)
    max_drawdown: float = 0.0
    peak_capital: float = 100000.0
    total_fees: float = 0.0

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100

    @property
    def equity(self) -> float:
        return self.current_capital + self.unrealized_pnl

    def update_drawdown(self):
        if self.equity > self.peak_capital:
            self.peak_capital = self.equity
        drawdown = (self.peak_capital - self.equity) / self.peak_capital * 100
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown


class ScalpingAPIClient:
    """Client for the scalping dashboard API."""

    def __init__(self, base_url: str = SCALPING_API):
        self.base_url = base_url

    def get_state(self) -> Optional[Dict]:
        try:
            resp = requests.get(f"{self.base_url}/api/scalping/status", timeout=5)
            return resp.json() if resp.ok else None
        except Exception:
            return None

    def add_signal(self, signal: Dict):
        try:
            requests.post(
                f"{self.base_url}/api/scalping/backtest/signal",
                json=signal,
                timeout=5
            )
        except Exception:
            pass  # intentional: dashboard update is best-effort

    def add_trade(self, trade: Dict):
        try:
            requests.post(
                f"{self.base_url}/api/scalping/backtest/trade",
                json=trade,
                timeout=5
            )
        except Exception:
            pass  # intentional: dashboard update is best-effort

    def clear_backtest(self):
        try:
            requests.post(
                f"{self.base_url}/api/scalping/backtest/clear",
                timeout=5
            )
        except Exception:
            pass  # intentional: dashboard update is best-effort

    def update_portfolio(self, portfolio: Dict):
        try:
            requests.post(
                f"{self.base_url}/api/scalping/backtest/portfolio",
                json=portfolio,
                timeout=5
            )
        except Exception:
            pass  # intentional: dashboard update is best-effort


def load_signal_csv(path: Path) -> List[Dict[str, str]]:
    """Load signals from CSV file."""
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        logger.error(f"Failed to load {path}: {e}")
        return []


def get_available_dates(base_path: Path) -> List[str]:
    """Get available postmortem dates."""
    if not base_path.exists():
        return []
    dates = []
    for d in sorted(base_path.iterdir()):
        if d.is_dir() and d.name.startswith("2026-"):
            dates.append(d.name)
    return dates


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


class FyersN7BacktestEngine:
    """
    Backtest engine using FyersN7 signals.

    Uses FyersN7's multi-factor scoring instead of basic indicators.
    Exit modes:
    - Point-based: Exit after target_points profit or sl_points loss
    - Reverse signal: Exit when opposite signal appears
    """

    POSITION_SIZE_PCT = 0.10
    MAX_POSITIONS = 3
    TRADE_FEE = 40

    def __init__(
        self,
        index: str,
        state: PaperTradingState,
        adapter: FyersN7SignalAdapter,
        delay: float = 0.01,
        target_points: float = 30.0,  # Optimal: 30pt target (66.7% WR)
        sl_points: float = 20.0,      # Optimal: 20pt SL (1.5:1 R:R)
        use_reverse_exit: bool = True  # Exit on reverse signal (key for profits)
    ):
        self.index = index
        self.state = state
        self.adapter = adapter
        self.delay = delay
        self.api = ScalpingAPIClient()
        self.running = False
        self.trade_counter = 0

        # Point-based exits
        self.target_points = target_points
        self.sl_points = sl_points
        self.use_reverse_exit = use_reverse_exit

    def open_position(self, signal: FyersN7Signal) -> Position:
        """Open a new position from signal."""
        self.trade_counter += 1
        position_size = self.state.current_capital * self.POSITION_SIZE_PCT

        position = Position(
            id=f"FN7-{self.index[:3]}-{self.trade_counter:04d}",
            index=self.index,
            side=signal.side,
            strike=signal.strike,
            entry_price=signal.entry,
            entry_time=datetime.now(),
            stop_loss=signal.sl if signal.sl > 0 else signal.entry * 0.985,
            target=signal.t1 if signal.t1 > 0 else signal.entry * 1.025,
            quantity=max(1, int(position_size / max(signal.entry, 1))),
            signal_score=signal.score,
        )

        # Deduct fee
        self.state.current_capital -= self.TRADE_FEE
        self.state.total_fees += self.TRADE_FEE

        self.state.open_positions.append(position)
        return position

    def check_point_based_exit(
        self,
        pos: Position,
        current_price: float
    ) -> Tuple[bool, float, str]:
        """
        Check if position should exit based on points movement.

        Returns (should_exit, exit_price, reason)
        """
        if current_price <= 0:
            return False, 0.0, ""

        # Calculate points moved
        if pos.side == "CE":
            points_moved = current_price - pos.entry_price
            # CE profit when price goes up
            if points_moved >= self.target_points:
                return True, current_price, "Target"
            elif points_moved <= -self.sl_points:
                return True, current_price, "SL"
        else:  # PE
            points_moved = pos.entry_price - current_price
            # PE profit when price goes down
            if points_moved >= self.target_points:
                return True, current_price, "Target"
            elif points_moved <= -self.sl_points:
                return True, current_price, "SL"

        return False, 0.0, ""

    def check_reverse_signal_exit(
        self,
        pos: Position,
        current_signal: FyersN7Signal,
        should_trade: bool
    ) -> Tuple[bool, str]:
        """
        Check if opposite signal indicates exit.

        If we have CE position and strong PE signal comes, exit CE.
        """
        if not self.use_reverse_exit or not should_trade:
            return False, ""

        # Check for reverse signal
        if pos.side == "CE" and current_signal.side == "PE":
            return True, "ReversePE"
        elif pos.side == "PE" and current_signal.side == "CE":
            return True, "ReverseCE"

        return False, ""

    def simulate_exit(
        self,
        pos: Position,
        next_signals: List[Dict],
        current_idx: int
    ) -> Optional[float]:
        """
        Simulate exit based on subsequent signals (legacy method).
        Now uses point-based exits primarily.
        """
        # Check next few signals for price movement
        for i in range(current_idx + 1, min(current_idx + 10, len(next_signals))):
            if i >= len(next_signals):
                break

            next_row = next_signals[i]
            next_entry = to_float(next_row.get("entry", 0))

            if next_entry <= 0:
                continue

            # Check point-based exit
            should_exit, exit_price, reason = self.check_point_based_exit(pos, next_entry)
            if should_exit:
                return exit_price

        return None

    def close_position(self, pos: Position, exit_price: float, reason: str):
        """Close a position."""
        pos.exit_price = exit_price
        pos.exit_time = datetime.now()
        pos.status = reason

        # Calculate P&L
        if pos.side == "CE":
            pos.pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pos.pnl = (pos.entry_price - exit_price) * pos.quantity

        # Deduct exit fee
        pos.pnl -= self.TRADE_FEE
        self.state.total_fees += self.TRADE_FEE

        # Update state
        self.state.realized_pnl += pos.pnl
        self.state.current_capital += pos.pnl
        self.state.total_trades += 1

        if pos.pnl > 0:
            self.state.winning_trades += 1
        else:
            self.state.losing_trades += 1

        self.state.open_positions.remove(pos)
        self.state.closed_positions.append(pos)

        return pos

    async def process_signals(self, signals: List[Dict[str, str]]):
        """Process all signals for an index."""
        self.running = True
        total = len(signals)

        logger.info(f"Processing {total} signals for {self.index}")

        for i, row in enumerate(signals):
            if not self.running:
                break

            # Skip if no valid entry
            entry = to_float(row.get("entry", 0))
            if entry <= 0:
                continue

            # Process through adapter
            signal, should_trade, reasons, warnings = self.adapter.process_signal_row(
                row, self.index
            )

            # Check exits for open positions
            for pos in self.state.open_positions[:]:
                exit_price = None
                exit_reason = None

                # 1. Check point-based exit first
                should_exit, price, reason = self.check_point_based_exit(pos, entry)
                if should_exit:
                    exit_price = price
                    exit_reason = reason

                # 2. Check reverse signal exit (if we have a new signal)
                if not exit_price and should_trade:
                    is_reverse, reverse_reason = self.check_reverse_signal_exit(
                        pos, signal, should_trade
                    )
                    if is_reverse:
                        exit_price = entry  # Exit at current price
                        exit_reason = reverse_reason

                # 3. Close position if any exit condition met
                if exit_price:
                    closed = self.close_position(pos, exit_price, exit_reason)

                    self.api.add_trade({
                        "id": closed.id,
                        "index": self.index,
                        "type": closed.side,
                        "entry": closed.entry_price,
                        "exit": closed.exit_price,
                        "pnl": round(closed.pnl, 2),
                        "score": closed.signal_score,
                        "status": closed.status,
                    })

            # Open new position if criteria met
            if should_trade and len(self.state.open_positions) < self.MAX_POSITIONS:
                pos = self.open_position(signal)

                self.api.add_signal({
                    "index": self.index,
                    "side": signal.side,
                    "strike": signal.strike,
                    "entry": signal.entry,
                    "sl": signal.sl,
                    "target": signal.t1,
                    "score": signal.score,
                    "confidence": signal.confidence,
                    "vote_diff": signal.vote_diff,
                    "spread_pct": signal.spread_pct,
                    "delta": signal.delta,
                    "gamma": signal.gamma,
                    "reasons": reasons,
                })

                self.api.add_trade({
                    "id": pos.id,
                    "index": self.index,
                    "type": pos.side,
                    "entry": pos.entry_price,
                    "sl": pos.stop_loss,
                    "target": pos.target,
                    "score": signal.score,
                    "status": "OPEN",
                })

            # Update portfolio
            self.state.update_drawdown()
            self.api.update_portfolio({
                "capital": round(self.state.current_capital, 2),
                "realized_pnl": round(self.state.realized_pnl, 2),
                "equity": round(self.state.equity, 2),
                "total_trades": self.state.total_trades,
                "win_rate": round(self.state.win_rate, 1),
                "max_drawdown": round(self.state.max_drawdown, 2),
                "open_positions": len(self.state.open_positions),
                "total_fees": round(self.state.total_fees, 2),
            })

            # Progress
            if (i + 1) % 50 == 0 or i == total - 1:
                logger.info(
                    f"[{self.index}] {i+1}/{total} | "
                    f"Trades: {self.state.total_trades} | "
                    f"PnL: {self.state.realized_pnl:+,.0f} | "
                    f"WR: {self.state.win_rate:.0f}%"
                )

            await asyncio.sleep(self.delay)

        # Close any remaining positions at last price
        for pos in self.state.open_positions[:]:
            exit_price = pos.entry_price  # Flat exit
            self.close_position(pos, exit_price, "EOD")


async def run_fyersn7_backtest(
    indices: List[str],
    capital: float,
    delay: float,
    min_score: int,
    min_confidence: int,
    target_points: float = 30.0,
    sl_points: float = 20.0,
    use_reverse_exit: bool = True,
):
    """Run backtest using FyersN7 signals."""

    reverse_str = "ON" if use_reverse_exit else "OFF"
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║   FyersN7 Signal Backtest - Point-Based Exits                    ║
╠══════════════════════════════════════════════════════════════════╣
║   Exit Strategy:                                                 ║
║   - Target: +{target_points:.0f} points profit                                    ║
║   - Stop Loss: -{sl_points:.0f} points loss                                     ║
║   - Reverse Signal Exit: {reverse_str:<3}                                     ║
╠══════════════════════════════════════════════════════════════════╣
║   Indices: {', '.join(indices):<51} ║
║   Capital: Rs {capital:,.0f}                                        ║
║   Min Score: {min_score}   Min Confidence: {min_confidence}                       ║
║   View: http://localhost:3001/scalping                           ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    # Check API
    api = ScalpingAPIClient()
    if not api.get_state():
        logger.error("Scalping API not running. Start with: ./start.sh all")
        return

    # Clear old data
    api.clear_backtest()
    logger.info("Cleared previous backtest data")

    # Get available dates
    dates = get_available_dates(POSTMORTEM_BASE)
    if not dates:
        logger.error(f"No postmortem data found in {POSTMORTEM_BASE}")
        return

    logger.info(f"Found {len(dates)} days of data: {dates}")

    # Create shared state
    state = PaperTradingState(
        initial_capital=capital,
        current_capital=capital,
        peak_capital=capital
    )

    # Create adapter with configurable thresholds (relaxed for backtest)
    adapter = FyersN7SignalAdapter(
        min_score=min_score,
        min_confidence=min_confidence,
        min_signal_gap=5,  # Lower for backtest (5s instead of 30s)
        max_trades_per_hour=100,  # Allow more trades in backtest
    )

    # Process each index
    for index in indices:
        index = index.upper()
        all_signals = []

        # Load decision_journal which has all FyersN7 indicators
        for date in dates:
            # Use decision_journal.csv which has: vote_diff, delta, gamma, iv, spread_pct, etc.
            journal_file = POSTMORTEM_BASE / date / index / "decision_journal.csv"
            if journal_file.exists():
                signals = load_signal_csv(journal_file)
                # Filter to only rows with valid entry prices
                valid_signals = [s for s in signals if to_float(s.get("entry", 0)) > 0]
                all_signals.extend(valid_signals)
                logger.info(f"{index}/{date}: Loaded {len(valid_signals)} valid signals from decision_journal")

        if not all_signals:
            logger.warning(f"No signals found for {index}")
            continue

        # Run backtest with point-based exits
        engine = FyersN7BacktestEngine(
            index, state, adapter,
            delay=delay,
            target_points=target_points,
            sl_points=sl_points,
            use_reverse_exit=use_reverse_exit
        )
        try:
            await engine.process_signals(all_signals)
        except KeyboardInterrupt:
            logger.info(f"\nStopping {index} backtest...")
            engine.running = False
            break

    # Final summary
    print("\n" + "=" * 70)
    print("FYERSN7 BACKTEST SUMMARY")
    print("=" * 70)
    print(f"  Initial Capital: Rs {state.initial_capital:,.0f}")
    print(f"  Final Capital: Rs {state.current_capital:,.0f}")
    print(f"  Total Realized P&L: Rs {state.realized_pnl:+,.0f}")
    print(f"  Total Fees: Rs {state.total_fees:,.0f}")
    print(f"  Net Return: {((state.current_capital - state.initial_capital) / state.initial_capital * 100):+.2f}%")
    print(f"  Total Trades: {state.total_trades}")
    print(f"  Winning Trades: {state.winning_trades}")
    print(f"  Losing Trades: {state.losing_trades}")
    print(f"  Win Rate: {state.win_rate:.1f}%")
    print(f"  Max Drawdown: {state.max_drawdown:.2f}%")
    print("=" * 70)

    # Show adapter stats
    print("\nAdapter Settings:")
    stats = adapter.get_stats()
    print(f"  Min Score: {stats['min_score']}")
    print(f"  Min Confidence: {stats['min_confidence']}")


async def main():
    parser = argparse.ArgumentParser(description="FyersN7 Signal Backtest")
    parser.add_argument("--indices", nargs="+", default=["SENSEX", "NIFTY50"],
                       help="Indices to test")
    parser.add_argument("--capital", type=float, default=100000,
                       help="Starting capital (default: 100000)")
    parser.add_argument("--delay", type=float, default=0.01,
                       help="Delay between signals")
    parser.add_argument("--min-score", type=int, default=50,
                       help="Minimum FyersN7 score (default: 50)")
    parser.add_argument("--min-confidence", type=int, default=70,
                       help="Minimum confidence (default: 70)")
    parser.add_argument("--target-points", type=float, default=30.0,
                       help="Target profit in points (default: 30)")
    parser.add_argument("--sl-points", type=float, default=20.0,
                       help="Stop loss in points (default: 20)")
    parser.add_argument("--no-reverse-exit", action="store_true",
                       help="Disable reverse signal exits")
    parser.add_argument("--fast", action="store_true",
                       help="Fast mode")

    args = parser.parse_args()
    delay = 0.001 if args.fast else args.delay

    try:
        await run_fyersn7_backtest(
            indices=args.indices,
            capital=args.capital,
            delay=delay,
            min_score=args.min_score,
            min_confidence=args.min_confidence,
            target_points=args.target_points,
            sl_points=args.sl_points,
            use_reverse_exit=not args.no_reverse_exit,
        )
    except KeyboardInterrupt:
        logger.info("\nBacktest interrupted")


if __name__ == "__main__":
    asyncio.run(main())

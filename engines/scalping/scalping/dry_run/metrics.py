"""Metrics engine — tracks win rate, R:R, slippage, PnL over time."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from .position_manager import PositionManager, SimulatedPosition


class MetricsEngine:
    """Computes real-time and session-level trading metrics."""

    def __init__(self, position_manager: PositionManager) -> None:
        self.pm = position_manager
        self._pnl_snapshots: List[Dict[str, Any]] = []
        self._slippage_costs: List[float] = []

    def record_slippage(self, slippage_cost: float) -> None:
        self._slippage_costs.append(slippage_cost)

    def snapshot_pnl(self) -> None:
        self._pnl_snapshots.append({
            "timestamp": datetime.now().isoformat(),
            "realized": self.pm.total_realized_pnl,
            "unrealized": self.pm.total_unrealized_pnl,
            "total": self.pm.daily_pnl,
            "open_positions": len(self.pm.get_open_positions()),
        })

    def compute(self) -> Dict[str, Any]:
        closed = self.pm.get_all_closed()
        open_positions = self.pm.get_open_positions()

        wins = [p for p in closed if p.realized_pnl > 0]
        losses = [p for p in closed if p.realized_pnl <= 0]

        total_trades = len(closed)
        win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0

        avg_win = sum(p.realized_pnl for p in wins) / len(wins) if wins else 0
        avg_loss = sum(p.realized_pnl for p in losses) / len(losses) if losses else 0

        expected_rr_list = []
        actual_rr_list = []
        for p in closed:
            risk = abs(p.entry_price - p.sl_price) * p.filled_qty if p.sl_price > 0 else 0
            reward = abs(p.target_price - p.entry_price) * p.filled_qty if p.target_price > 0 else 0
            if risk > 0:
                expected_rr_list.append(reward / risk)
            actual_reward = p.realized_pnl
            if risk > 0:
                actual_rr_list.append(actual_reward / risk)

        total_slippage = sum(self._slippage_costs)
        avg_slippage = total_slippage / max(len(self._slippage_costs), 1)

        return {
            "session_time": datetime.now().isoformat(),
            "total_trades": total_trades,
            "open_positions": len(open_positions),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 1),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0,
            "expected_rr_avg": round(sum(expected_rr_list) / max(len(expected_rr_list), 1), 2),
            "actual_rr_avg": round(sum(actual_rr_list) / max(len(actual_rr_list), 1), 2),
            "realized_pnl": round(self.pm.total_realized_pnl, 2),
            "unrealized_pnl": round(self.pm.total_unrealized_pnl, 2),
            "total_pnl": round(self.pm.daily_pnl, 2),
            "total_slippage_cost": round(total_slippage, 2),
            "avg_slippage_per_trade": round(avg_slippage, 2),
            "slippage_loss_pct": round(total_slippage / max(abs(self.pm.total_realized_pnl), 1) * 100, 1) if self.pm.total_realized_pnl != 0 else 0,
            "pnl_curve": self._pnl_snapshots[-20:],
        }

    def print_summary(self) -> None:
        m = self.compute()
        print(f"\n{'='*60}")
        print(f"  DRY-RUN METRICS SUMMARY")
        print(f"{'='*60}")
        print(f"  Trades: {m['total_trades']} | Open: {m['open_positions']}")
        print(f"  Wins: {m['wins']} | Losses: {m['losses']} | Win Rate: {m['win_rate_pct']}%")
        print(f"  Avg Win: ₹{m['avg_win']:.2f} | Avg Loss: ₹{m['avg_loss']:.2f}")
        print(f"  Profit Factor: {m['profit_factor']}")
        print(f"  Expected R:R: {m['expected_rr_avg']} | Actual R:R: {m['actual_rr_avg']}")
        print(f"  Realized P&L: ₹{m['realized_pnl']:.2f}")
        print(f"  Unrealized P&L: ₹{m['unrealized_pnl']:.2f}")
        print(f"  Total P&L: ₹{m['total_pnl']:.2f}")
        print(f"  Slippage Loss: ₹{m['total_slippage_cost']:.2f} ({m['slippage_loss_pct']}%)")
        print(f"{'='*60}\n")

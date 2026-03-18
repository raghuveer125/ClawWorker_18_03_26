"""
Exit Optimizer Agent - 3-Dimensional TP/SL/Hold Time Optimizer.

Agent 20: ExitOptimizerAgent

Purpose:
Discover the optimal combination of:
- Take Profit (TP) points
- Stop Loss (SL) points
- Maximum Hold Time (minutes)

Many scalping edges come from TIME exits, not just price targets.
This agent runs after market close and optimizes exit parameters per regime.

Key Insight:
- Trades often move quickly in first 5-10 minutes
- After that, theta decay or reversal starts
- Time-based exits can significantly improve profitability
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
import json
from pathlib import Path

from ..base import BaseBot, BotContext, BotResult, BotStatus
from ..config import ScalpingConfig


@dataclass
class ExitConfig:
    """Optimized exit configuration."""
    tp_points: float
    sl_points: float
    max_hold_minutes: int
    regime: str


@dataclass
class OptimizationResult:
    """Result of a single TP/SL/HoldTime combination test."""
    tp: float
    sl: float
    hold_minutes: int
    regime: str
    # Metrics
    total_trades: int
    winning_trades: int
    losing_trades: int
    profit_factor: float
    win_rate: float
    expectancy: float
    max_drawdown: float
    avg_hold_time: float
    total_pnl: float
    # Weighted score
    score: float


@dataclass
class RegimeExitTable:
    """Optimized exit parameters per regime."""
    trending_bullish: ExitConfig
    trending_bearish: ExitConfig
    range_bound: ExitConfig
    volatile_expansion: ExitConfig
    volatile_contraction: ExitConfig
    expiry_pinning: ExitConfig


class ExitOptimizerAgent(BaseBot):
    """
    Agent 20: Exit Optimizer Agent

    3-Dimensional optimization of TP × SL × Hold Time.

    Optimization Grid:
    - TP: 20-50 points (step 5)
    - SL: 10-30 points (step 5)
    - Hold: 5-30 minutes (step 5)

    Scoring Formula:
    score = (profit_factor * 0.35) + (expectancy * 0.25) +
            (win_rate * 0.15) - (drawdown * 0.25)

    Target Metrics:
    - Profit Factor > 1.8
    - Win Rate > 58%
    - Max Drawdown < 12%

    Runs after market close to optimize next day's parameters.
    """

    BOT_TYPE = "exit_optimizer"
    REQUIRES_LLM = False  # Pure numerical optimization

    # Optimization grid defaults
    TP_RANGE = (20, 50, 5)   # min, max, step
    SL_RANGE = (10, 30, 5)   # min, max, step
    HOLD_RANGE = (5, 30, 5)  # min, max, step (minutes)

    # Scoring weights
    WEIGHT_PROFIT_FACTOR = 0.35
    WEIGHT_EXPECTANCY = 0.25
    WEIGHT_WIN_RATE = 0.15
    WEIGHT_DRAWDOWN = 0.25

    # Target thresholds
    MIN_PROFIT_FACTOR = 1.5
    TARGET_PROFIT_FACTOR = 1.8
    MIN_WIN_RATE = 0.50
    TARGET_WIN_RATE = 0.58
    MAX_DRAWDOWN = 0.12  # 12%
    MIN_TRADES_FOR_OPTIMIZATION = 20

    # Regime mapping
    REGIMES = [
        "TRENDING_BULLISH",
        "TRENDING_BEARISH",
        "RANGE_BOUND",
        "VOLATILE_EXPANSION",
        "VOLATILE_CONTRACTION",
        "EXPIRY_PINNING",
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._optimization_history: List[Dict] = []
        self._current_exit_table: Dict[str, ExitConfig] = {}
        self._last_optimization: Optional[datetime] = None

        # Default exit configs per regime
        self._default_configs = {
            "TRENDING_BULLISH": ExitConfig(40, 20, 20, "TRENDING_BULLISH"),
            "TRENDING_BEARISH": ExitConfig(40, 20, 20, "TRENDING_BEARISH"),
            "RANGE_BOUND": ExitConfig(25, 15, 9, "RANGE_BOUND"),
            "VOLATILE_EXPANSION": ExitConfig(50, 25, 15, "VOLATILE_EXPANSION"),
            "VOLATILE_CONTRACTION": ExitConfig(20, 12, 7, "VOLATILE_CONTRACTION"),
            "EXPIRY_PINNING": ExitConfig(20, 15, 10, "EXPIRY_PINNING"),
        }
        self._current_exit_table = self._default_configs.copy()

    def get_description(self) -> str:
        return "3D Exit Optimizer - optimizes TP/SL/HoldTime per regime"

    async def execute(self, context: BotContext) -> BotResult:
        """
        Run exit parameter optimization.

        Should be called after market close with historical trade data.
        """
        config = context.data.get("config", ScalpingConfig())

        # Get historical trades for optimization
        historical_trades = context.data.get("historical_trades", [])
        recent_trades = context.data.get("recent_trades", [])
        all_trades = historical_trades + recent_trades

        if len(all_trades) < self.MIN_TRADES_FOR_OPTIMIZATION:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SUCCESS,
                output={
                    "optimized": False,
                    "reason": f"Insufficient trades ({len(all_trades)} < {self.MIN_TRADES_FOR_OPTIMIZATION})",
                    "current_exit_table": self._format_exit_table(),
                },
                metrics={"trades_available": len(all_trades)},
            )

        # Run optimization for each regime
        optimization_results: Dict[str, List[OptimizationResult]] = {}
        best_configs: Dict[str, ExitConfig] = {}

        for regime in self.REGIMES:
            # Filter trades by regime
            regime_trades = [t for t in all_trades if t.get("regime") == regime]

            if len(regime_trades) < 10:
                # Not enough trades for this regime, use defaults
                best_configs[regime] = self._default_configs[regime]
                continue

            # Run 3D optimization
            results = self._optimize_regime(regime, regime_trades)
            optimization_results[regime] = results

            if results:
                # Get best result
                best = max(results, key=lambda r: r.score)
                best_configs[regime] = ExitConfig(
                    tp_points=best.tp,
                    sl_points=best.sl,
                    max_hold_minutes=best.hold_minutes,
                    regime=regime,
                )
            else:
                best_configs[regime] = self._default_configs[regime]

        # Update current exit table
        self._current_exit_table = best_configs
        self._last_optimization = datetime.now()

        # Store in context for other agents
        context.data["exit_optimizer_table"] = self._format_exit_table()
        context.data["regime_exit_configs"] = {
            regime: {
                "tp": cfg.tp_points,
                "sl": cfg.sl_points,
                "hold_minutes": cfg.max_hold_minutes,
            }
            for regime, cfg in best_configs.items()
        }

        # Calculate improvement estimate
        improvement = self._estimate_improvement(all_trades, best_configs)

        # Get top 10 overall configurations
        all_results = []
        for regime_results in optimization_results.values():
            all_results.extend(regime_results)
        all_results.sort(key=lambda r: r.score, reverse=True)
        top_10 = all_results[:10]

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "optimized": True,
                "trades_analyzed": len(all_trades),
                "regimes_optimized": len([r for r in optimization_results if optimization_results.get(r)]),
                "exit_table": self._format_exit_table(),
                "top_10_configs": [self._format_result(r) for r in top_10],
                "expected_improvement": improvement,
                "optimization_time": self._last_optimization.isoformat() if self._last_optimization else None,
            },
            metrics={
                "trades_analyzed": len(all_trades),
                "configurations_tested": sum(len(r) for r in optimization_results.values()),
                "avg_profit_factor": sum(r.profit_factor for r in top_10) / len(top_10) if top_10 else 0,
            },
        )

    def _optimize_regime(
        self, regime: str, trades: List[Dict]
    ) -> List[OptimizationResult]:
        """
        Run 3D grid search for optimal TP/SL/HoldTime.
        """
        results = []

        tp_min, tp_max, tp_step = self.TP_RANGE
        sl_min, sl_max, sl_step = self.SL_RANGE
        hold_min, hold_max, hold_step = self.HOLD_RANGE

        # Grid search
        for tp in range(tp_min, tp_max + 1, tp_step):
            for sl in range(sl_min, sl_max + 1, sl_step):
                for hold in range(hold_min, hold_max + 1, hold_step):
                    result = self._backtest_config(regime, trades, tp, sl, hold)
                    if result:
                        results.append(result)

        return results

    def _backtest_config(
        self,
        regime: str,
        trades: List[Dict],
        tp: float,
        sl: float,
        hold_minutes: int,
    ) -> Optional[OptimizationResult]:
        """
        Backtest a specific TP/SL/HoldTime configuration on historical trades.
        """
        if not trades:
            return None

        wins = 0
        losses = 0
        total_profit = 0.0
        total_loss = 0.0
        total_pnl = 0.0
        max_drawdown = 0.0
        peak_pnl = 0.0
        hold_times = []

        for trade in trades:
            # Simulate exit with this config
            entry_price = trade.get("entry_price", 0)
            high_price = trade.get("high_during_trade", entry_price + tp)
            low_price = trade.get("low_during_trade", entry_price - sl)
            actual_hold = trade.get("hold_minutes", trade.get("hold_sec", 0) / 60)
            side = trade.get("side", "CE")

            # Determine exit
            if side in ["CE", "BUY", "LONG"]:
                # Long position
                hit_tp = high_price >= entry_price + tp
                hit_sl = low_price <= entry_price - sl
            else:
                # Short position (PE)
                hit_tp = low_price <= entry_price - tp
                hit_sl = high_price >= entry_price + sl

            hit_time = actual_hold >= hold_minutes

            # Determine outcome
            if hit_tp and not hit_sl:
                pnl = tp
                wins += 1
                total_profit += tp
            elif hit_sl:
                pnl = -sl
                losses += 1
                total_loss += sl
            elif hit_time:
                # Time exit - use actual PnL or estimate
                actual_pnl = trade.get("net_pnl", trade.get("pnl", 0))
                if actual_pnl > 0:
                    pnl = min(actual_pnl, tp * 0.5)  # Partial profit
                    wins += 1
                    total_profit += pnl
                else:
                    pnl = max(actual_pnl, -sl * 0.5)  # Partial loss
                    losses += 1
                    total_loss += abs(pnl)
            else:
                # Use actual outcome
                actual_pnl = trade.get("net_pnl", trade.get("pnl", 0))
                pnl = actual_pnl
                if pnl > 0:
                    wins += 1
                    total_profit += pnl
                else:
                    losses += 1
                    total_loss += abs(pnl)

            total_pnl += pnl
            hold_times.append(min(actual_hold, hold_minutes))

            # Track drawdown
            peak_pnl = max(peak_pnl, total_pnl)
            drawdown = (peak_pnl - total_pnl) / peak_pnl if peak_pnl > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)

        # Calculate metrics
        total_trades = wins + losses
        if total_trades == 0:
            return None

        profit_factor = total_profit / total_loss if total_loss > 0 else 10.0
        win_rate = wins / total_trades
        expectancy = total_pnl / total_trades
        avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0

        # Calculate weighted score
        # Normalize metrics
        pf_score = min(profit_factor / 3.0, 1.0)  # Cap at PF 3.0
        wr_score = win_rate
        exp_score = min(max(expectancy / 50, -1), 1)  # Normalize to -1 to 1
        dd_score = 1 - min(max_drawdown, 1.0)  # Invert drawdown

        score = (
            pf_score * self.WEIGHT_PROFIT_FACTOR +
            exp_score * self.WEIGHT_EXPECTANCY +
            wr_score * self.WEIGHT_WIN_RATE +
            dd_score * self.WEIGHT_DRAWDOWN
        )

        return OptimizationResult(
            tp=tp,
            sl=sl,
            hold_minutes=hold_minutes,
            regime=regime,
            total_trades=total_trades,
            winning_trades=wins,
            losing_trades=losses,
            profit_factor=round(profit_factor, 2),
            win_rate=round(win_rate, 3),
            expectancy=round(expectancy, 2),
            max_drawdown=round(max_drawdown, 3),
            avg_hold_time=round(avg_hold, 1),
            total_pnl=round(total_pnl, 2),
            score=round(score, 4),
        )

    def _estimate_improvement(
        self, trades: List[Dict], optimized_configs: Dict[str, ExitConfig]
    ) -> Dict[str, Any]:
        """
        Estimate improvement from optimization vs current performance.
        """
        # Calculate current performance
        current_pnl = sum(t.get("net_pnl", t.get("pnl", 0)) for t in trades)
        current_wins = sum(1 for t in trades if t.get("result", "").lower() == "win")
        current_total = len(trades)
        current_win_rate = current_wins / current_total if current_total > 0 else 0

        # Estimate optimized performance (simplified)
        estimated_pnl = 0
        estimated_wins = 0

        for trade in trades:
            regime = trade.get("regime", "RANGE_BOUND")
            config = optimized_configs.get(regime, self._default_configs.get(regime))

            if config:
                entry = trade.get("entry_price", 0)
                side = trade.get("side", "CE")
                high = trade.get("high_during_trade", entry + config.tp_points)
                low = trade.get("low_during_trade", entry - config.sl_points)

                if side in ["CE", "BUY", "LONG"]:
                    if high >= entry + config.tp_points:
                        estimated_pnl += config.tp_points
                        estimated_wins += 1
                    elif low <= entry - config.sl_points:
                        estimated_pnl -= config.sl_points
                    else:
                        estimated_pnl += trade.get("net_pnl", 0) * 0.8
                        if trade.get("result", "").lower() == "win":
                            estimated_wins += 1
                else:
                    if low <= entry - config.tp_points:
                        estimated_pnl += config.tp_points
                        estimated_wins += 1
                    elif high >= entry + config.sl_points:
                        estimated_pnl -= config.sl_points
                    else:
                        estimated_pnl += trade.get("net_pnl", 0) * 0.8
                        if trade.get("result", "").lower() == "win":
                            estimated_wins += 1

        estimated_win_rate = estimated_wins / current_total if current_total > 0 else 0

        pnl_improvement = ((estimated_pnl - current_pnl) / abs(current_pnl) * 100
                          ) if current_pnl != 0 else 0
        wr_improvement = (estimated_win_rate - current_win_rate) * 100

        return {
            "current_pnl": round(current_pnl, 2),
            "estimated_pnl": round(estimated_pnl, 2),
            "pnl_improvement_pct": round(pnl_improvement, 1),
            "current_win_rate": round(current_win_rate * 100, 1),
            "estimated_win_rate": round(estimated_win_rate * 100, 1),
            "win_rate_improvement_pct": round(wr_improvement, 1),
        }

    def _format_exit_table(self) -> Dict[str, Dict]:
        """Format exit table for output."""
        return {
            regime: {
                "tp": cfg.tp_points,
                "sl": cfg.sl_points,
                "hold_minutes": cfg.max_hold_minutes,
            }
            for regime, cfg in self._current_exit_table.items()
        }

    def _format_result(self, result: OptimizationResult) -> Dict:
        """Format optimization result for output."""
        return {
            "regime": result.regime,
            "tp": result.tp,
            "sl": result.sl,
            "hold": result.hold_minutes,
            "profit_factor": result.profit_factor,
            "win_rate": f"{result.win_rate * 100:.1f}%",
            "expectancy": result.expectancy,
            "max_drawdown": f"{result.max_drawdown * 100:.1f}%",
            "score": result.score,
            "trades": result.total_trades,
        }

    def get_exit_config(self, regime: str) -> ExitConfig:
        """
        Get optimized exit config for a regime.

        Called by EntryAgent/ExitAgent during live trading.
        """
        return self._current_exit_table.get(
            regime,
            self._default_configs.get(regime, ExitConfig(30, 15, 10, regime))
        )

    def get_exit_table(self) -> Dict[str, ExitConfig]:
        """Get full exit configuration table."""
        return self._current_exit_table.copy()

    def should_exit_by_time(self, regime: str, hold_minutes: float) -> bool:
        """
        Check if position should exit based on time.

        Called by ExitAgent during position monitoring.
        """
        config = self.get_exit_config(regime)
        return hold_minutes >= config.max_hold_minutes

    def save_optimization(self, path: Optional[str] = None):
        """Save optimization results to disk."""
        if path is None:
            path = Path(__file__).parent.parent / "data" / "exit_optimization.json"

        data = {
            "last_optimization": self._last_optimization.isoformat() if self._last_optimization else None,
            "exit_table": self._format_exit_table(),
            "history": self._optimization_history[-100:],  # Keep last 100
        }

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load_optimization(self, path: Optional[str] = None):
        """Load previous optimization results."""
        if path is None:
            path = Path(__file__).parent.parent / "data" / "exit_optimization.json"

        if not Path(path).exists():
            return

        try:
            with open(path, "r") as f:
                data = json.load(f)

            # Restore exit table
            table = data.get("exit_table", {})
            for regime, cfg in table.items():
                self._current_exit_table[regime] = ExitConfig(
                    tp_points=cfg["tp"],
                    sl_points=cfg["sl"],
                    max_hold_minutes=cfg["hold_minutes"],
                    regime=regime,
                )

            if data.get("last_optimization"):
                self._last_optimization = datetime.fromisoformat(data["last_optimization"])

            self._optimization_history = data.get("history", [])

        except Exception as e:
            print(f"[ExitOptimizer] Failed to load optimization: {e}")

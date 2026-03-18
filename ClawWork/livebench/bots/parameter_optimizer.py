"""
Automated Parameter Optimizer - Self-Tuning Strategy Parameters

This module completes the adaptive learning loop by automatically
tuning bot strategy parameters based on trading performance.

Philosophy: "Small incremental improvements, never drastic changes"

Features:
1. Tracks parameter-outcome relationships
2. Suggests parameter adjustments based on performance
3. Uses evolutionary approach (small mutations)
4. Has safety bounds to prevent extreme values
5. Gradual optimization - no sudden changes
"""

import json
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict
import copy

LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPTIMIZER_DATA_DIR = LIVEBENCH_ROOT / "data" / "optimizer"


@dataclass
class ParameterConfig:
    """Configuration for a tunable parameter"""
    name: str
    current_value: float
    min_value: float
    max_value: float
    step_size: float  # How much to adjust per optimization
    description: str = ""
    last_optimized: datetime = field(default_factory=datetime.now)
    optimization_count: int = 0


@dataclass
class ParameterPerformance:
    """Performance tracking for a parameter value"""
    parameter_name: str
    value: float
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    win_rate: float = 0.0


@dataclass
class OptimizationResult:
    """Result of an optimization run"""
    parameter_name: str
    old_value: float
    new_value: float
    reason: str
    expected_improvement: float
    timestamp: datetime = field(default_factory=datetime.now)


class ParameterOptimizer:
    """
    Automated Parameter Optimizer

    Continuously learns which parameter values perform better
    and gradually adjusts them to improve performance.

    Key Principles:
    1. Never make drastic changes (max 1 step per optimization)
    2. Require minimum trades before optimizing
    3. Only optimize parameters that show clear patterns
    4. Roll back if performance degrades
    """

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or DEFAULT_OPTIMIZER_DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Define tunable parameters for each bot (matching actual bot parameter names)
        self.bot_parameters: Dict[str, List[ParameterConfig]] = {
            "TrendFollower": [
                ParameterConfig("min_trend_pct", 0.3, 0.1, 0.8, 0.05,
                               "Minimum % move to consider a trend"),
                ParameterConfig("strong_trend_pct", 0.8, 0.4, 1.5, 0.1,
                               "Strong trend threshold"),
                ParameterConfig("momentum_threshold", 0.1, 0.02, 0.3, 0.02,
                               "Momentum must be positive"),
                ParameterConfig("stop_loss_pct", 1.5, 0.8, 3.0, 0.25,
                               "Stop loss percentage"),
                ParameterConfig("target_pct", 3.0, 1.5, 5.0, 0.25,
                               "Target profit percentage"),
            ],
            "MomentumScalper": [
                ParameterConfig("min_momentum", 0.08, 0.03, 0.2, 0.01,
                               "Minimum momentum to trigger signal"),
                ParameterConfig("strong_momentum", 0.2, 0.1, 0.5, 0.02,
                               "Threshold for strong momentum"),
                ParameterConfig("min_change_pct", 0.1, 0.05, 0.3, 0.02,
                               "Minimum change percentage"),
                ParameterConfig("cooldown_seconds", 30, 10, 120, 10,
                               "Cooldown between signals"),
                ParameterConfig("quick_target_pct", 1.5, 0.8, 3.0, 0.25,
                               "Quick scalp target percentage"),
                ParameterConfig("stop_loss_pct", 1.0, 0.5, 2.0, 0.25,
                               "Stop loss percentage"),
            ],
            "OIAnalyst": [
                ParameterConfig("bullish_pcr_threshold", 1.2, 1.0, 1.5, 0.05,
                               "PCR above this is bullish"),
                ParameterConfig("bearish_pcr_threshold", 0.8, 0.5, 1.0, 0.05,
                               "PCR below this is bearish"),
                ParameterConfig("oi_change_threshold", 5.0, 2.0, 15.0, 1.0,
                               "Significant OI change percentage"),
                ParameterConfig("stop_loss_pct", 1.5, 0.8, 3.0, 0.25,
                               "Stop loss percentage"),
                ParameterConfig("target_pct", 3.0, 1.5, 5.0, 0.25,
                               "Target profit percentage"),
            ],
            "VolatilityTrader": [
                ParameterConfig("low_iv_percentile", 45, 20, 60, 5,
                               "Low IV percentile threshold"),
                ParameterConfig("high_iv_percentile", 60, 50, 85, 5,
                               "High IV percentile threshold"),
                ParameterConfig("vol_breakout_threshold", 0.5, 0.2, 1.0, 0.1,
                               "Volatility breakout threshold"),
                ParameterConfig("stop_loss_pct", 2.0, 1.0, 3.5, 0.25,
                               "Stop loss percentage"),
                ParameterConfig("target_pct", 3.5, 2.0, 5.5, 0.25,
                               "Target profit percentage"),
            ],
            "ReversalHunter": [
                ParameterConfig("overbought_threshold", 1.0, 0.5, 2.0, 0.1,
                               "Overbought threshold percentage"),
                ParameterConfig("oversold_threshold", -1.0, -2.0, -0.5, 0.1,
                               "Oversold threshold percentage"),
                ParameterConfig("extreme_threshold", 1.5, 0.8, 2.5, 0.1,
                               "Extreme move threshold"),
                ParameterConfig("momentum_fade_threshold", -0.1, -0.3, -0.05, 0.02,
                               "Momentum fade detection threshold"),
                ParameterConfig("stop_loss_pct", 1.5, 0.8, 3.0, 0.25,
                               "Stop loss percentage"),
                ParameterConfig("target_pct", 3.0, 1.5, 5.0, 0.25,
                               "Target profit percentage"),
            ],
        }

        # Performance tracking per parameter value
        self.parameter_performance: Dict[str, Dict[float, ParameterPerformance]] = defaultdict(dict)

        # Trade history with parameters
        self.trade_parameter_history: List[Dict] = []

        # Optimization history
        self.optimization_history: List[OptimizationResult] = []

        # Configuration
        self.config = {
            "min_trades_for_optimization": 20,  # Need at least 20 trades
            "min_trades_per_value": 5,          # Need 5 trades at each value
            "optimization_interval_hours": 24,   # Optimize once per day
            "max_optimizations_per_run": 3,      # Max 3 parameters per run
            "performance_threshold": 0.1,        # 10% improvement needed
            "rollback_threshold": -0.15,         # Rollback if 15% worse
        }

        # Load persisted state
        self._load_state()

        print("[Optimizer] Parameter Optimizer initialized")

    def record_trade_with_params(
        self,
        bot_name: str,
        parameters: Dict[str, float],
        outcome: str,
        pnl: float,
        market_conditions: Dict[str, Any]
    ):
        """
        Record a trade outcome with the parameters that were used.

        Call this after every trade to build the parameter-performance database.
        """
        trade_record = {
            "timestamp": datetime.now().isoformat(),
            "bot_name": bot_name,
            "parameters": parameters,
            "outcome": outcome,
            "pnl": pnl,
            "market_conditions": market_conditions,
        }

        self.trade_parameter_history.append(trade_record)

        # Update parameter performance tracking
        for param_name, param_value in parameters.items():
            key = f"{bot_name}.{param_name}"

            if param_value not in self.parameter_performance[key]:
                self.parameter_performance[key][param_value] = ParameterPerformance(
                    parameter_name=key,
                    value=param_value
                )

            perf = self.parameter_performance[key][param_value]
            perf.trades += 1
            perf.total_pnl += pnl

            if outcome == "WIN":
                perf.wins += 1
            elif outcome == "LOSS":
                perf.losses += 1

            perf.avg_pnl = perf.total_pnl / perf.trades if perf.trades > 0 else 0
            perf.win_rate = (perf.wins / perf.trades * 100) if perf.trades > 0 else 0

        # Periodically save state
        if len(self.trade_parameter_history) % 10 == 0:
            self._save_state()

    def optimize(self, bot_name: str = None) -> List[OptimizationResult]:
        """
        Run optimization for specified bot or all bots.

        Returns list of parameter adjustments made.
        """
        results = []
        bots_to_optimize = [bot_name] if bot_name else list(self.bot_parameters.keys())

        for bot in bots_to_optimize:
            if bot not in self.bot_parameters:
                continue

            bot_results = self._optimize_bot(bot)
            results.extend(bot_results)

            if len(results) >= self.config["max_optimizations_per_run"]:
                break

        # Save optimization history
        self.optimization_history.extend(results)
        self._save_state()

        return results

    def _optimize_bot(self, bot_name: str) -> List[OptimizationResult]:
        """Optimize parameters for a specific bot"""
        results = []

        for param_config in self.bot_parameters[bot_name]:
            key = f"{bot_name}.{param_config.name}"

            # Check if we have enough data
            total_trades = sum(
                perf.trades for perf in self.parameter_performance.get(key, {}).values()
            )

            if total_trades < self.config["min_trades_for_optimization"]:
                continue

            # Analyze performance at different values
            best_value, best_performance = self._find_best_value(key, param_config)

            if best_value is None:
                continue

            # Check if significantly better than current
            current_perf = self.parameter_performance.get(key, {}).get(param_config.current_value)

            if current_perf and best_performance:
                current_win_rate = current_perf.win_rate
                best_win_rate = best_performance.win_rate

                improvement = (best_win_rate - current_win_rate) / max(current_win_rate, 1)

                if improvement >= self.config["performance_threshold"]:
                    # Calculate new value (move towards best, but not all the way)
                    direction = 1 if best_value > param_config.current_value else -1
                    new_value = param_config.current_value + (direction * param_config.step_size)

                    # Clamp to bounds
                    new_value = max(param_config.min_value, min(param_config.max_value, new_value))

                    if new_value != param_config.current_value:
                        result = OptimizationResult(
                            parameter_name=f"{bot_name}.{param_config.name}",
                            old_value=param_config.current_value,
                            new_value=new_value,
                            reason=f"Win rate improvement: {current_win_rate:.1f}% → {best_win_rate:.1f}%",
                            expected_improvement=improvement,
                        )

                        # Apply the change
                        param_config.current_value = new_value
                        param_config.last_optimized = datetime.now()
                        param_config.optimization_count += 1

                        results.append(result)
                        print(f"[Optimizer] {bot_name}.{param_config.name}: {result.old_value} → {result.new_value}")

        return results

    def _find_best_value(
        self,
        key: str,
        param_config: ParameterConfig
    ) -> Tuple[Optional[float], Optional[ParameterPerformance]]:
        """Find the best performing value for a parameter"""
        performances = self.parameter_performance.get(key, {})

        if not performances:
            return None, None

        # Filter to values with enough trades
        valid_performances = [
            (value, perf) for value, perf in performances.items()
            if perf.trades >= self.config["min_trades_per_value"]
        ]

        if not valid_performances:
            return None, None

        # Sort by win rate, then by avg PnL
        valid_performances.sort(
            key=lambda x: (x[1].win_rate, x[1].avg_pnl),
            reverse=True
        )

        best_value, best_perf = valid_performances[0]
        return best_value, best_perf

    def suggest_parameters(self, bot_name: str) -> Dict[str, float]:
        """
        Get current optimized parameters for a bot.

        Returns dict of parameter_name -> value
        """
        if bot_name not in self.bot_parameters:
            return {}

        return {
            param.name: param.current_value
            for param in self.bot_parameters[bot_name]
        }

    def apply_to_bot(self, bot: Any, bot_name: str):
        """
        Apply optimized parameters to a bot instance.

        Updates the bot's parameters dict with optimized values.
        """
        if bot_name not in self.bot_parameters:
            return

        optimized = self.suggest_parameters(bot_name)

        if hasattr(bot, 'parameters'):
            for param_name, value in optimized.items():
                if param_name in bot.parameters:
                    old_value = bot.parameters[param_name]
                    bot.parameters[param_name] = value
                    if old_value != value:
                        print(f"[Optimizer] Applied {bot_name}.{param_name}: {old_value} → {value}")

    def get_optimization_report(self) -> Dict[str, Any]:
        """Get comprehensive optimization report"""
        report = {
            "total_trades_analyzed": len(self.trade_parameter_history),
            "optimizations_performed": len(self.optimization_history),
            "bots": {},
        }

        for bot_name, params in self.bot_parameters.items():
            bot_report = {
                "parameters": {},
                "total_optimizations": 0,
            }

            for param in params:
                key = f"{bot_name}.{param.name}"
                performances = self.parameter_performance.get(key, {})

                param_report = {
                    "current_value": param.current_value,
                    "bounds": [param.min_value, param.max_value],
                    "optimization_count": param.optimization_count,
                    "values_tested": len(performances),
                    "best_value": None,
                    "best_win_rate": 0,
                }

                if performances:
                    best_value, best_perf = self._find_best_value(key, param)
                    if best_perf:
                        param_report["best_value"] = best_value
                        param_report["best_win_rate"] = best_perf.win_rate

                bot_report["parameters"][param.name] = param_report
                bot_report["total_optimizations"] += param.optimization_count

            report["bots"][bot_name] = bot_report

        # Recent optimizations
        report["recent_optimizations"] = [
            {
                "parameter": opt.parameter_name,
                "change": f"{opt.old_value} → {opt.new_value}",
                "reason": opt.reason,
                "timestamp": opt.timestamp.isoformat(),
            }
            for opt in self.optimization_history[-10:]
        ]

        return report

    def explore_parameter(self, bot_name: str, param_name: str) -> float:
        """
        Get a slightly varied parameter value for exploration.

        Used during trading to occasionally test different values.
        """
        if bot_name not in self.bot_parameters:
            return None

        for param in self.bot_parameters[bot_name]:
            if param.name == param_name:
                # 20% chance to explore
                if random.random() < 0.2:
                    # Small random variation
                    variation = random.uniform(-param.step_size, param.step_size)
                    new_value = param.current_value + variation
                    new_value = max(param.min_value, min(param.max_value, new_value))
                    return new_value
                return param.current_value

        return None

    def rollback_parameter(self, bot_name: str, param_name: str):
        """Rollback a parameter to its previous value if performance degraded"""
        # Find the last optimization for this parameter
        for opt in reversed(self.optimization_history):
            if opt.parameter_name == f"{bot_name}.{param_name}":
                # Rollback
                for param in self.bot_parameters.get(bot_name, []):
                    if param.name == param_name:
                        print(f"[Optimizer] ROLLBACK {bot_name}.{param_name}: {param.current_value} → {opt.old_value}")
                        param.current_value = opt.old_value
                        return

    def _save_state(self):
        """Persist optimizer state"""
        state = {
            "bot_parameters": {
                bot_name: [asdict(p) for p in params]
                for bot_name, params in self.bot_parameters.items()
            },
            "parameter_performance": {
                key: {
                    str(value): asdict(perf)
                    for value, perf in performances.items()
                }
                for key, performances in self.parameter_performance.items()
            },
            "optimization_history": [
                asdict(opt) for opt in self.optimization_history[-100:]  # Keep last 100
            ],
            "config": self.config,
            "last_saved": datetime.now().isoformat(),
        }

        with open(self.data_dir / "optimizer_state.json", 'w') as f:
            json.dump(state, f, indent=2, default=str)

    def _load_state(self):
        """Load persisted state"""
        state_file = self.data_dir / "optimizer_state.json"

        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)

                # Restore bot parameters
                for bot_name, params in state.get("bot_parameters", {}).items():
                    if bot_name in self.bot_parameters:
                        for i, param_dict in enumerate(params):
                            if i < len(self.bot_parameters[bot_name]):
                                self.bot_parameters[bot_name][i].current_value = param_dict.get(
                                    "current_value",
                                    self.bot_parameters[bot_name][i].current_value
                                )
                                self.bot_parameters[bot_name][i].optimization_count = param_dict.get(
                                    "optimization_count", 0
                                )

                # Restore performance data
                for key, performances in state.get("parameter_performance", {}).items():
                    for value_str, perf_dict in performances.items():
                        value = float(value_str)
                        self.parameter_performance[key][value] = ParameterPerformance(
                            parameter_name=perf_dict["parameter_name"],
                            value=value,
                            trades=perf_dict["trades"],
                            wins=perf_dict["wins"],
                            losses=perf_dict["losses"],
                            total_pnl=perf_dict["total_pnl"],
                            avg_pnl=perf_dict["avg_pnl"],
                            win_rate=perf_dict["win_rate"],
                        )

                print(f"[Optimizer] State loaded - {len(self.parameter_performance)} parameters tracked")

            except Exception as e:
                print(f"[Optimizer] Error loading state: {e}")

    def reset_bot_parameters(self, bot_name: str):
        """Reset a bot's parameters to defaults"""
        if bot_name in self.bot_parameters:
            # Re-initialize with default values (matching actual bot defaults)
            defaults = {
                "TrendFollower": {
                    "min_trend_pct": 0.3, "strong_trend_pct": 0.8,
                    "momentum_threshold": 0.1, "stop_loss_pct": 1.5, "target_pct": 3.0
                },
                "MomentumScalper": {
                    "min_momentum": 0.08, "strong_momentum": 0.2,
                    "min_change_pct": 0.1, "cooldown_seconds": 30,
                    "quick_target_pct": 1.5, "stop_loss_pct": 1.0
                },
                "OIAnalyst": {
                    "bullish_pcr_threshold": 1.2, "bearish_pcr_threshold": 0.8,
                    "oi_change_threshold": 5.0, "stop_loss_pct": 1.5, "target_pct": 3.0
                },
                "VolatilityTrader": {
                    "low_iv_percentile": 45, "high_iv_percentile": 60,
                    "vol_breakout_threshold": 0.5, "stop_loss_pct": 2.0, "target_pct": 3.5
                },
                "ReversalHunter": {
                    "overbought_threshold": 1.0, "oversold_threshold": -1.0,
                    "extreme_threshold": 1.5, "momentum_fade_threshold": -0.1,
                    "stop_loss_pct": 1.5, "target_pct": 3.0
                },
            }

            if bot_name in defaults:
                for param in self.bot_parameters[bot_name]:
                    if param.name in defaults[bot_name]:
                        param.current_value = defaults[bot_name][param.name]
                        param.optimization_count = 0
                print(f"[Optimizer] Reset {bot_name} parameters to defaults")

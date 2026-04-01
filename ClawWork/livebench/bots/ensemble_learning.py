"""
Ensemble Learning Mixin - Learning, Optimization, Backtest Validation

Methods for ML data access, parameter optimization, daily resets,
and trade-triggered backtest validation.
"""

import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class LearningMixin:
    """Mixin providing learning, optimization, and backtest validation methods.

    Expects the host class to expose:
        self.bots, self.bot_map, self.config, self.memory,
        self.deep_learning, self.ml_extractor, self.ml_bot,
        self.risk_controller, self.risk_controller_active,
        self.parameter_optimizer, self.optimizer_active,
        self.capital_allocator, self.allocator_active,
        self.drift_detector, self.drift_active,
        self.mtf_engine, self.mtf_active,
        self.backtest_validation_active,
        self.trades_since_backtest, self.parameter_snapshots,
        self.last_backtest_results,
    """

    # ------------------------------------------------------------------
    # ML / Deep Learning accessors
    # ------------------------------------------------------------------

    def get_learning_insights(self) -> Dict:
        """Get insights from deep learning"""
        return self.deep_learning.get_learning_summary()

    def get_ml_statistics(self) -> Dict:
        """Get ML training data statistics"""
        return self.ml_extractor.get_statistics()

    def export_ml_training_data(self, filepath: str = None):
        """Export ML training data to CSV"""
        self.ml_extractor.export_to_csv(filepath)

    def get_ml_training_data(self):
        """Get ML training data as X, y arrays"""
        return self.ml_extractor.get_training_data()

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    def reset_daily(self):
        """Reset daily counters (call at market open)"""
        self.daily_trades = []
        self.daily_pnl = 0

        # Reset adaptive risk controller
        if self.risk_controller_active and self.risk_controller:
            self.risk_controller.reset_daily()

        # Reset capital allocator daily tracking
        if self.allocator_active and self.capital_allocator:
            self.capital_allocator.reset_daily()

        # Rebalance capital based on current regime
        if self.allocator_active and self.capital_allocator:
            regime = "UNKNOWN"
            if hasattr(self, '_current_inst_analysis') and self._current_inst_analysis:
                regime = self._current_inst_analysis.regime.value
            self.capital_allocator.rebalance_sleeves(regime)

        # Run automated parameter optimization (learns from previous days)
        if self.optimizer_active and self.parameter_optimizer:
            print("[Ensemble] Running daily parameter optimization...")
            results = self.run_optimization()
            if results:
                print(f"[Optimizer] Applied {len(results)} parameter improvements:")
                for r in results:
                    print(f"  - {r['parameter']}: {r['old_value']} -> {r['new_value']}")
            else:
                print("[Optimizer] No optimizations needed (insufficient data or no improvements found)")

    # ------------------------------------------------------------------
    # Parameter optimization
    # ------------------------------------------------------------------

    def run_optimization(self, bot_name: str = None) -> List[Dict]:
        """
        Run parameter optimization for one or all bots.

        Call periodically (e.g., daily) or after significant trade history builds up.
        Returns list of optimization changes made.
        """
        if not self.optimizer_active or not self.parameter_optimizer:
            return []

        results = self.parameter_optimizer.optimize(bot_name)

        # Apply new parameters to bots
        if results:
            self._apply_optimized_parameters()

        return [
            {
                "parameter": r.parameter_name,
                "old_value": r.old_value,
                "new_value": r.new_value,
                "reason": r.reason,
            }
            for r in results
        ]

    def get_optimization_report(self) -> Dict[str, Any]:
        """Get comprehensive parameter optimization report"""
        if not self.optimizer_active or not self.parameter_optimizer:
            return {"enabled": False}

        return self.parameter_optimizer.get_optimization_report()

    def _apply_optimized_parameters(self):
        """Apply optimized parameters to all bots"""
        if not self.optimizer_active or not self.parameter_optimizer:
            return

        for bot in self.bots:
            self.parameter_optimizer.apply_to_bot(bot, bot.name)

    def _get_bot_parameters(self, bot_name: str) -> Dict[str, float]:
        """Get current parameters from a bot for tracking"""
        if bot_name not in self.bot_map:
            return {}

        bot = self.bot_map[bot_name]
        if hasattr(bot, 'parameters'):
            return dict(bot.parameters)
        return {}

    # ------------------------------------------------------------------
    # Parameter snapshots (backtest validation support)
    # ------------------------------------------------------------------

    def _save_parameter_snapshot(self, bot_name: str):
        """Save current bot parameters as known-good snapshot"""
        bot = self.bot_map.get(bot_name)
        if not bot:
            return

        # Save bot-agnostic parameters (if bot has config/params attribute)
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "performance": {
                "win_rate": bot.performance.win_rate,
                "total_pnl": bot.performance.total_pnl,
                "weight": bot.performance.weight,
                "total_trades": bot.performance.total_trades,
            },
        }

        # Save bot-specific parameters (if they exist)
        if hasattr(bot, "config"):
            snapshot["config"] = asdict(bot.config) if hasattr(bot.config, "__dataclass_fields__") else vars(bot.config)
        elif hasattr(bot, "params"):
            snapshot["params"] = bot.params.copy() if isinstance(bot.params, dict) else vars(bot.params)

        self.parameter_snapshots[bot_name] = snapshot
        logger.info(f"Saved parameter snapshot for {bot_name}")

    def _restore_parameter_snapshot(self, bot_name: str):
        """Restore bot parameters from snapshot"""
        snapshot = self.parameter_snapshots.get(bot_name)
        if not snapshot:
            logger.warning(f"No snapshot found for {bot_name}")
            return

        bot = self.bot_map.get(bot_name)
        if not bot:
            return

        # Restore bot-specific parameters
        if "config" in snapshot and hasattr(bot, "config"):
            for key, value in snapshot["config"].items():
                if hasattr(bot.config, key):
                    setattr(bot.config, key, value)
        elif "params" in snapshot and hasattr(bot, "params"):
            if isinstance(bot.params, dict):
                bot.params.update(snapshot["params"])
            else:
                for key, value in snapshot["params"].items():
                    if hasattr(bot.params, key):
                        setattr(bot.params, key, value)

        logger.info(f"Restored parameter snapshot for {bot_name} from {snapshot['timestamp']}")

    # ------------------------------------------------------------------
    # Trade-triggered backtest validation
    # ------------------------------------------------------------------

    def _run_backtest_validation(self, bot_name: str, index: str):
        """
        Validate learned parameters via quick backtest

        Process:
        1. Run backtest with current (learned) parameters
        2. Compare to baseline/last known-good
        3. If better: Update baseline and save snapshot
        4. If worse: Optionally revert to last known-good
        """
        try:
            try:
                from backtesting.backtest import Backtester
            except ImportError:
                from livebench.backtesting.backtest import Backtester

            logger.info(f"[Backtest Validation] Triggering for {bot_name} after {self.trades_since_backtest[bot_name]} trades")

            # Run quick backtest
            backtester = Backtester()
            result = backtester.run_backtest(
                index=index,
                days=self.config.backtest_validation_days,
                resolution="5"
            )

            # Extract bot-specific results from backtest
            bot_trades = backtester.bot_trades.get(bot_name, [])
            if not bot_trades:
                logger.warning(f"No trades for {bot_name} in backtest")
                self.trades_since_backtest[bot_name] = 0
                return

            # Calculate bot performance from backtest
            winning_trades = [t for t in bot_trades if t.get("pnl", 0) > 0]
            current_win_rate = len(winning_trades) / len(bot_trades) if bot_trades else 0
            current_avg_pnl = sum(t.get("pnl", 0) for t in bot_trades) / len(bot_trades) if bot_trades else 0
            current_total_pnl = sum(t.get("pnl", 0) for t in bot_trades)

            # Get baseline (from drift detector or last backtest)
            baseline = self.drift_detector.baselines.get(bot_name) if self.drift_active else None
            last_result = self.last_backtest_results.get(bot_name, {})

            baseline_win_rate = baseline.win_rate if baseline else last_result.get("win_rate", 0.5)
            baseline_avg_return = baseline.avg_return if baseline else last_result.get("avg_pnl", 0)

            # Compare performance
            win_rate_delta = current_win_rate - baseline_win_rate
            pnl_delta = current_avg_pnl - baseline_avg_return

            logger.info(f"[Backtest Validation] {bot_name}: "
                       f"WinRate {current_win_rate:.1%} (baseline {baseline_win_rate:.1%}, \u0394{win_rate_delta:+.1%}), "
                       f"AvgPnL {current_avg_pnl:.0f} (baseline {baseline_avg_return:.0f}, \u0394{pnl_delta:+.0f})")

            # Decision: Keep or revert?
            improvement = win_rate_delta + (pnl_delta / 100)  # Combine metrics

            if improvement >= self.config.backtest_min_improvement:
                # IMPROVED - Update baseline and save snapshot
                logger.info(f"[Backtest Validation] {bot_name} IMPROVED ({improvement:+.1%}) - Updating baseline")

                # Update drift detector baseline
                if self.drift_active and self.drift_detector:
                    self.drift_detector.register_baseline(
                        bot_name=bot_name,
                        backtest_results={
                            "win_rate": current_win_rate,
                            "avg_return": current_avg_pnl / 100,  # Convert to decimal
                            "sharpe_ratio": 1.0,  # Could calculate properly
                            "max_drawdown": 0.10,
                            "profit_factor": current_total_pnl / max(1, abs(sum(t.get("pnl", 0) for t in bot_trades if t.get("pnl", 0) < 0))),
                        }
                    )

                # Save new snapshot
                self._save_parameter_snapshot(bot_name)

                # Store result
                self.last_backtest_results[bot_name] = {
                    "win_rate": current_win_rate,
                    "avg_pnl": current_avg_pnl,
                    "total_pnl": current_total_pnl,
                    "trades": len(bot_trades),
                    "timestamp": datetime.now().isoformat(),
                }

            elif improvement < -self.config.backtest_min_improvement and self.config.backtest_revert_on_failure:
                # DEGRADED - Revert to last known-good
                logger.warning(f"[Backtest Validation] {bot_name} DEGRADED ({improvement:+.1%}) - REVERTING parameters")
                self._restore_parameter_snapshot(bot_name)

            else:
                # NO SIGNIFICANT CHANGE - Keep current
                logger.info(f"[Backtest Validation] {bot_name} NO CHANGE ({improvement:+.1%}) - Keeping current params")

            # Reset counter
            self.trades_since_backtest[bot_name] = 0

        except Exception as e:
            logger.error(f"[Backtest Validation] Error validating {bot_name}: {e}")
            # Reset counter anyway to avoid repeated failures
            self.trades_since_backtest[bot_name] = 0

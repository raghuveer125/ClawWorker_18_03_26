"""
Ensemble Status Mixin - Status Reporting, Leaderboard, Bot Management

Methods for querying bot status, ensemble statistics, leaderboard,
hedge fund reporting, and bot enable/disable management.
"""

import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List

from .model_drift_detector import ModelStatus

logger = logging.getLogger(__name__)


class StatusMixin:
    """Mixin providing status reporting and bot management methods.

    Expects the host class to expose:
        self.bots, self.bot_map, self.config, self.memory,
        self.disabled_bots, self.regime_weights, self.current_regime,
        self.active_positions, self.daily_trades, self.daily_pnl,
        self.ensemble_performance,
        self.ml_bot, self.ml_bot_active,
        self.llm_bot, self.llm_bot_active,
        self.veto_layer, self.veto_active,
        self.mtf_engine, self.mtf_active,
        self.risk_controller, self.risk_controller_active,
        self.institutional_layer, self.institutional_active,
        self.capital_allocator, self.allocator_active,
        self.drift_detector, self.drift_active,
        self.execution_engine, self.execution_active,
        self.deep_learning,
    """

    # ------------------------------------------------------------------
    # Bot status
    # ------------------------------------------------------------------

    def get_bot_status(self) -> List[Dict[str, Any]]:
        """Get status of all bots"""
        status = [bot.to_dict() for bot in self.bots]

        # Add ML bot status
        ml_status = self.ml_bot.get_status()
        status.append({
            "name": self.ml_bot.name,
            "description": self.ml_bot.description,
            "is_ml_bot": True,
            **ml_status,
        })

        # Add LLM bot status (TRUE AI reasoning)
        if self.llm_bot:
            status.append({
                "name": self.llm_bot.name,
                "description": self.llm_bot.description,
                "is_llm_bot": True,
                "enabled": self.llm_bot_active,
                "model": getattr(self.llm_bot, 'model', 'unknown'),
                "performance": {
                    "total_signals": self.llm_bot.performance.total_signals,
                    "total_trades": self.llm_bot.performance.total_trades,
                    "wins": self.llm_bot.performance.wins,
                    "losses": self.llm_bot.performance.losses,
                    "win_rate": self.llm_bot.performance.win_rate,
                    "total_pnl": self.llm_bot.performance.total_pnl,
                    "weight": self.llm_bot.performance.weight,
                },
                "recent_signals": [
                    {
                        "index": s.index,
                        "signal_type": s.signal_type.value,
                        "confidence": s.confidence,
                        "reasoning": s.reasoning[:100] + "..." if len(s.reasoning) > 100 else s.reasoning,
                    }
                    for s in self.llm_bot.recent_signals[-5:]
                ] if hasattr(self.llm_bot, 'recent_signals') else [],
            })

        # Add Veto Layer status (capital protection)
        if self.veto_layer:
            veto_stats = self.veto_layer.get_stats()
            status.append({
                "name": "VetoLayer",
                "description": "LLM-powered signal filter for capital protection",
                "is_veto_layer": True,
                "enabled": self.veto_active,
                "model": getattr(self.veto_layer, 'model', 'gpt-4o-mini'),
                "stats": veto_stats,
            })

        return status

    # ------------------------------------------------------------------
    # Ensemble statistics
    # ------------------------------------------------------------------

    def get_ensemble_stats(self) -> Dict[str, Any]:
        """Get ensemble-level statistics"""
        total = self.ensemble_performance["wins"] + self.ensemble_performance["losses"]
        win_rate = self.ensemble_performance["wins"] / total * 100 if total > 0 else 0

        # Get learning summary
        learning_summary = self.deep_learning.get_learning_summary()

        # Get veto layer stats
        veto_stats = self.veto_layer.get_stats() if self.veto_active and self.veto_layer else None

        return {
            **self.ensemble_performance,
            "win_rate": round(win_rate, 1),
            "active_positions": len(self.active_positions),
            "daily_trades": len(self.daily_trades),
            "daily_pnl": round(self.daily_pnl, 2),
            "current_regime": self.current_regime.value if self.current_regime else None,
            "bot_count": len(self.bots) + 1 + (1 if self.llm_bot_active else 0),  # +1 for ML, +1 for LLM
            "ml_bot_status": self.ml_bot.get_status(),
            "veto_layer": {
                "enabled": self.veto_active,
                "stats": veto_stats,
            },
            "learning": learning_summary,
            "bots": [
                {
                    "name": bot.name,
                    "weight": round(bot.get_weight(), 2),
                    "regime_weight": round(self.regime_weights.get(bot.name, 1.0), 2),
                    "win_rate": round(bot.performance.win_rate, 1),
                    "total_trades": bot.performance.total_trades,
                }
                for bot in self.bots
            ] + [{
                "name": self.ml_bot.name,
                "weight": 1.5 if self.ml_bot.is_trained else 0.0,
                "regime_weight": 1.0,
                "win_rate": self.ml_bot.metadata.accuracy if self.ml_bot.metadata else 0,
                "total_trades": self.ml_bot.metadata.training_samples if self.ml_bot.metadata else 0,
                "is_ml": True,
                "trained": self.ml_bot.is_trained,
            }]
        }

    # ------------------------------------------------------------------
    # Leaderboard
    # ------------------------------------------------------------------

    def get_leaderboard(self) -> List[Dict[str, Any]]:
        """Get bot leaderboard sorted by performance"""
        bot_stats = []
        for bot in self.bots:
            perf = bot.performance
            score = (
                (perf.win_rate / 100) * perf.profit_factor * perf.weight
                if perf.total_trades >= 5 else 0
            )
            bot_stats.append({
                "name": bot.name,
                "description": bot.description,
                "win_rate": round(perf.win_rate, 1),
                "profit_factor": round(perf.profit_factor, 2),
                "total_trades": perf.total_trades,
                "total_pnl": round(perf.total_pnl, 2),
                "weight": round(perf.weight, 2),
                "regime_weight": round(self.regime_weights.get(bot.name, 1.0), 2),
                "score": round(score, 3),
            })

        return sorted(bot_stats, key=lambda x: x["score"], reverse=True)

    # ------------------------------------------------------------------
    # MTF / Risk / Safety status
    # ------------------------------------------------------------------

    def get_mtf_status(self) -> Dict[str, Any]:
        """Get Multi-Timeframe engine status for all indices"""
        if self.mtf_active and self.mtf_engine:
            return self.mtf_engine.get_status()
        return {"enabled": False}

    def get_risk_status(self) -> Dict[str, Any]:
        """Get current adaptive risk controller status"""
        if self.risk_controller_active and self.risk_controller:
            return self.risk_controller.get_status()
        return {"enabled": False}

    def get_safety_status(self) -> Dict[str, Any]:
        """Get current safety/protection status"""
        return {
            "capital_preservation_mode": getattr(self, 'capital_preservation_mode', True),
            "mtf_active": self.mtf_active,
            "mtf_mode": self.mtf_engine.config.get("mode", "balanced") if self.mtf_active else None,
            "veto_active": self.veto_active,
            "disabled_bots": list(getattr(self, 'disabled_bots', set())),
            "active_bots": [
                {"name": bot.name, "weight": bot.performance.weight}
                for bot in self.bots
                if bot.name not in getattr(self, 'disabled_bots', set())
            ],
            "min_confidence_required": 70 if getattr(self, 'capital_preservation_mode', True) else 50,
            "min_bots_required": 2 if getattr(self, 'capital_preservation_mode', True) else 1,
        }

    # ------------------------------------------------------------------
    # Bot enable / disable
    # ------------------------------------------------------------------

    def enable_bot(self, bot_name: str):
        """Re-enable a disabled bot"""
        if bot_name in self.disabled_bots:
            self.disabled_bots.remove(bot_name)
            print(f"[Ensemble] {bot_name} RE-ENABLED")

            # Reset weight to minimum viable
            if bot_name in self.bot_map:
                self.bot_map[bot_name].performance.weight = 0.5
        else:
            print(f"[Ensemble] {bot_name} was not disabled")

    def disable_bot(self, bot_name: str, reason: str = "manual"):
        """Disable a bot from contributing signals"""
        self.disabled_bots.add(bot_name)
        print(f"[Ensemble] {bot_name} DISABLED (reason: {reason})")

        # Set weight to 0
        if bot_name in self.bot_map:
            self.bot_map[bot_name].performance.weight = 0.0

    # ------------------------------------------------------------------
    # Hedge fund grade reporting
    # ------------------------------------------------------------------

    def get_capital_allocation_status(self) -> Dict[str, Any]:
        """Get current capital allocation status across strategy sleeves"""
        if not self.allocator_active or not self.capital_allocator:
            return {"enabled": False}

        return self.capital_allocator.get_allocation_report()

    def get_drift_report(self) -> Dict[str, Any]:
        """Get model drift detection report for all bots"""
        if not self.drift_active or not self.drift_detector:
            return {"enabled": False}

        return self.drift_detector.get_drift_report()

    def get_execution_report(self) -> Dict[str, Any]:
        """Get execution quality report"""
        if not self.execution_active or not self.execution_engine:
            return {"enabled": False}

        return self.execution_engine.get_execution_report()

    def get_hedge_fund_status(self) -> Dict[str, Any]:
        """Get comprehensive hedge fund grade system status"""
        return {
            "timestamp": datetime.now().isoformat(),
            "institutional_layer": {
                "enabled": self.institutional_active,
                "current_regime": self._current_inst_analysis.regime.value if hasattr(self, '_current_inst_analysis') and self._current_inst_analysis else None,
                "trading_condition": self._current_inst_analysis.trading_condition.value if hasattr(self, '_current_inst_analysis') and self._current_inst_analysis else None,
            } if self.institutional_active else {"enabled": False},
            "capital_allocator": self.get_capital_allocation_status(),
            "drift_detector": {
                "enabled": self.drift_active,
                "quarantined_bots": [
                    bot for bot in self.drift_detector.model_status
                    if self.drift_detector.model_status[bot] == ModelStatus.QUARANTINED
                ] if self.drift_active and self.drift_detector else [],
            } if self.drift_active else {"enabled": False},
            "execution_engine": {
                "enabled": self.execution_active,
                "summary": self.get_execution_report().get("summary", {}) if self.execution_active else {},
            } if self.execution_active else {"enabled": False},
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Convert ensemble state to dictionary"""
        return {
            "config": asdict(self.config),
            "performance": self.ensemble_performance,
            "active_positions": self.active_positions,
            "daily_trades_count": len(self.daily_trades),
            "current_regime": self.current_regime.value if self.current_regime else None,
            "learning_summary": self.deep_learning.get_learning_summary(),
            "bots": self.get_bot_status(),
        }

    def save_state(self, filepath: str = None):
        """Save ensemble state to file"""
        self._save_state()

    def load_state(self, filepath: str = None):
        """Load ensemble state from file"""
        self._load_state()

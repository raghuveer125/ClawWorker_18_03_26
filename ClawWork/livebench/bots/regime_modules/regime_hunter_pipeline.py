"""
Regime Hunter Pipeline - Hybrid Module Integration

Main pipeline that integrates:
- VolatilityModule: Risk assessment
- SentimentModule: Institutional bias
- TrendModule: Price direction

Provides unified regime detection with modular control.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .volatility_module import VolatilityModule, VolatilityLevel, VolatilityOutput
from .sentiment_module import SentimentModule, SentimentBias, SentimentOutput
from .trend_module import TrendModule, TrendDirection, TrendOutput

DEFAULT_BOT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "bots"


class RegimeState(Enum):
    """Overall market regime"""
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING_BULLISH = "RANGING_BULLISH"
    RANGING_BEARISH = "RANGING_BEARISH"
    RANGING_NEUTRAL = "RANGING_NEUTRAL"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    BREAKOUT_UP = "BREAKOUT_UP"
    BREAKOUT_DOWN = "BREAKOUT_DOWN"
    COMPRESSED = "COMPRESSED"
    UNKNOWN = "UNKNOWN"


@dataclass
class PipelineConfig:
    """Configuration for the pipeline"""
    # Module weights (for final decision)
    volatility_weight: float = 1.0
    sentiment_weight: float = 1.0
    trend_weight: float = 1.0

    # Decision thresholds
    min_confidence: float = 50.0  # Minimum confidence to act
    consensus_required: float = 0.6  # % of modules must agree

    # Risk settings
    max_risk_multiplier: float = 1.5
    min_risk_multiplier: float = 0.25

    # Index-specific overrides
    index_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def get_index_config(self, index: str, param: str, default: Any) -> Any:
        """Get index-specific config or default"""
        if index in self.index_configs:
            return self.index_configs[index].get(param, default)
        return default


@dataclass
class PipelineDecision:
    """Final decision from the pipeline"""
    regime: RegimeState
    confidence: float
    action: str  # BUY_CE, BUY_PE, NO_TRADE, REDUCE_SIZE
    position_bias: str  # LONG, SHORT, NEUTRAL
    risk_multiplier: float

    # Module outputs
    volatility: VolatilityOutput
    sentiment: SentimentOutput
    trend: TrendOutput

    # Consensus info
    modules_agreeing: int
    total_modules: int
    consensus_level: float

    # Actionable info
    entry_side: str  # CE, PE, NONE
    stop_distance_pct: float
    target_distance_pct: float
    reasoning: str
    warnings: List[str]

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["regime"] = self.regime.value
        d["volatility"] = self.volatility.to_dict()
        d["sentiment"] = self.sentiment.to_dict()
        d["trend"] = self.trend.to_dict()
        return d


class RegimeHunterPipeline:
    """
    Main pipeline integrating all regime detection modules.

    Usage:
        pipeline = RegimeHunterPipeline()
        decision = pipeline.analyze("NIFTY50", market_data)

        if decision.action != "NO_TRADE":
            # Execute trade with decision.entry_side
            # Use decision.risk_multiplier for position sizing
    """

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.getenv(
            "BOT_DATA_DIR",
            str(DEFAULT_BOT_DATA_DIR)
        )) / "regime_pipeline"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize modules
        self.volatility = VolatilityModule(data_dir=str(self.data_dir.parent))
        self.sentiment = SentimentModule(data_dir=str(self.data_dir.parent))
        self.trend = TrendModule(data_dir=str(self.data_dir.parent))

        # Pipeline configuration
        self.config = self._load_config()

        # Decision history
        self.decision_history: List[PipelineDecision] = []

        # Performance tracking
        self.stats = self._load_stats()

    def _load_config(self) -> PipelineConfig:
        """Load pipeline configuration"""
        config_file = self.data_dir / "pipeline_config.json"
        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    data = json.load(f)
                    return PipelineConfig(**data)
            except (json.JSONDecodeError, TypeError):
                pass
        return PipelineConfig()

    def save_config(self):
        """Save pipeline configuration"""
        config_file = self.data_dir / "pipeline_config.json"
        with open(config_file, "w") as f:
            json.dump(asdict(self.config), f, indent=2)

    def _load_stats(self) -> Dict[str, Any]:
        """Load pipeline statistics"""
        stats_file = self.data_dir / "pipeline_stats.json"
        if stats_file.exists():
            try:
                with open(stats_file, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                pass
        return {
            "total_decisions": 0,
            "trades_suggested": 0,
            "by_regime": {},
            "by_index": {},
        }

    def _save_stats(self):
        """Save pipeline statistics"""
        stats_file = self.data_dir / "pipeline_stats.json"
        with open(stats_file, "w") as f:
            json.dump(self.stats, f, indent=2)

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        historical_data: Optional[List[Dict]] = None
    ) -> PipelineDecision:
        """
        Main analysis method - runs all modules and produces final decision.

        Args:
            index: Index name (NIFTY50, BANKNIFTY, etc.)
            market_data: Current market data
            historical_data: Recent historical data

        Returns:
            PipelineDecision with regime, action, and all module outputs
        """
        warnings = []

        # Run each module
        vol_output = self.volatility.analyze(index, market_data, historical_data)
        sent_output = self.sentiment.analyze(index, market_data, historical_data)
        trend_output = self.trend.analyze(index, market_data, historical_data)

        # Collect warnings
        if vol_output.warning:
            warnings.append(f"VOLATILITY: {vol_output.warning}")

        # Determine overall regime
        regime = self._determine_regime(vol_output, sent_output, trend_output)

        # Calculate consensus
        consensus_info = self._calculate_consensus(vol_output, sent_output, trend_output)

        # Determine action
        action, position_bias, entry_side = self._determine_action(
            regime, vol_output, sent_output, trend_output, consensus_info
        )

        # Calculate risk multiplier
        risk_multiplier = self._calculate_risk_multiplier(
            vol_output, consensus_info["level"]
        )

        # Calculate stop/target distances
        stop_pct, target_pct = self._calculate_stop_target(
            vol_output, trend_output, regime
        )

        # Calculate overall confidence
        confidence = self._calculate_confidence(
            vol_output, sent_output, trend_output, consensus_info
        )

        # Generate reasoning
        reasoning = self._generate_reasoning(
            regime, vol_output, sent_output, trend_output, action
        )

        # Build decision
        decision = PipelineDecision(
            regime=regime,
            confidence=confidence,
            action=action,
            position_bias=position_bias,
            risk_multiplier=risk_multiplier,
            volatility=vol_output,
            sentiment=sent_output,
            trend=trend_output,
            modules_agreeing=consensus_info["agreeing"],
            total_modules=3,
            consensus_level=consensus_info["level"],
            entry_side=entry_side,
            stop_distance_pct=stop_pct,
            target_distance_pct=target_pct,
            reasoning=reasoning,
            warnings=warnings,
        )

        # Update stats
        self._update_stats(decision, index)

        # Store in history
        self.decision_history.append(decision)
        if len(self.decision_history) > 500:
            self.decision_history.pop(0)

        return decision

    def _determine_regime(
        self,
        vol: VolatilityOutput,
        sent: SentimentOutput,
        trend: TrendOutput
    ) -> RegimeState:
        """Determine overall market regime from module outputs"""

        # High volatility overrides everything
        if vol.level == VolatilityLevel.EXTREME:
            return RegimeState.HIGH_VOLATILITY

        # Compressed volatility - potential breakout
        if vol.level == VolatilityLevel.COMPRESSED:
            # Check trend for breakout direction hint
            if trend.direction in [TrendDirection.STRONG_UP, TrendDirection.UP]:
                return RegimeState.COMPRESSED  # Bullish breakout expected
            elif trend.direction in [TrendDirection.STRONG_DOWN, TrendDirection.DOWN]:
                return RegimeState.COMPRESSED  # Bearish breakout expected
            return RegimeState.COMPRESSED

        # Breakout detection
        if trend.phase.value == "BREAKOUT":
            if trend.direction in [TrendDirection.STRONG_UP, TrendDirection.UP]:
                return RegimeState.BREAKOUT_UP
            elif trend.direction in [TrendDirection.STRONG_DOWN, TrendDirection.DOWN]:
                return RegimeState.BREAKOUT_DOWN

        # Trending markets
        if trend.strength >= 60:
            if trend.direction in [TrendDirection.STRONG_UP, TrendDirection.UP]:
                return RegimeState.TRENDING_BULLISH
            elif trend.direction in [TrendDirection.STRONG_DOWN, TrendDirection.DOWN]:
                return RegimeState.TRENDING_BEARISH

        # Ranging markets - use sentiment for bias
        if trend.direction == TrendDirection.SIDEWAYS or trend.strength < 40:
            if sent.bias in [SentimentBias.STRONG_BULLISH, SentimentBias.BULLISH]:
                return RegimeState.RANGING_BULLISH
            elif sent.bias in [SentimentBias.STRONG_BEARISH, SentimentBias.BEARISH]:
                return RegimeState.RANGING_BEARISH
            else:
                return RegimeState.RANGING_NEUTRAL

        # Weak trends with sentiment
        if trend.direction in [TrendDirection.UP]:
            if sent.bias in [SentimentBias.BULLISH, SentimentBias.STRONG_BULLISH]:
                return RegimeState.TRENDING_BULLISH
            return RegimeState.RANGING_BULLISH

        if trend.direction in [TrendDirection.DOWN]:
            if sent.bias in [SentimentBias.BEARISH, SentimentBias.STRONG_BEARISH]:
                return RegimeState.TRENDING_BEARISH
            return RegimeState.RANGING_BEARISH

        return RegimeState.UNKNOWN

    def _calculate_consensus(
        self,
        vol: VolatilityOutput,
        sent: SentimentOutput,
        trend: TrendOutput
    ) -> Dict[str, Any]:
        """Calculate how many modules agree on direction"""
        bullish_votes = 0
        bearish_votes = 0
        neutral_votes = 0

        # Volatility vote (neutral, but affects via risk)
        # Volatility doesn't vote on direction, just risk

        # Sentiment vote
        if sent.position_bias == "LONG":
            bullish_votes += 1
        elif sent.position_bias == "SHORT":
            bearish_votes += 1
        else:
            neutral_votes += 1

        # Trend vote
        if trend.direction in [TrendDirection.STRONG_UP, TrendDirection.UP]:
            bullish_votes += 1
        elif trend.direction in [TrendDirection.STRONG_DOWN, TrendDirection.DOWN]:
            bearish_votes += 1
        else:
            neutral_votes += 1

        total_directional = bullish_votes + bearish_votes + neutral_votes
        max_votes = max(bullish_votes, bearish_votes, neutral_votes)

        direction = "NEUTRAL"
        if bullish_votes > bearish_votes and bullish_votes > neutral_votes:
            direction = "BULLISH"
        elif bearish_votes > bullish_votes and bearish_votes > neutral_votes:
            direction = "BEARISH"

        return {
            "bullish": bullish_votes,
            "bearish": bearish_votes,
            "neutral": neutral_votes,
            "direction": direction,
            "agreeing": max_votes,
            "level": max_votes / total_directional if total_directional > 0 else 0,
        }

    def _determine_action(
        self,
        regime: RegimeState,
        vol: VolatilityOutput,
        sent: SentimentOutput,
        trend: TrendOutput,
        consensus: Dict[str, Any]
    ) -> tuple[str, str, str]:
        """
        Determine trading action based on regime and module outputs.

        Returns: (action, position_bias, entry_side)
        """
        # No trade in extreme volatility unless experienced
        if regime == RegimeState.HIGH_VOLATILITY:
            return "REDUCE_SIZE", "NEUTRAL", "NONE"

        # Compressed - wait for breakout confirmation
        if regime == RegimeState.COMPRESSED:
            return "WAIT_BREAKOUT", "NEUTRAL", "NONE"

        # Unknown regime - no trade
        if regime == RegimeState.UNKNOWN:
            return "NO_TRADE", "NEUTRAL", "NONE"

        # Check consensus threshold
        if consensus["level"] < self.config.consensus_required:
            # Low consensus - reduce confidence
            if consensus["level"] < 0.4:
                return "NO_TRADE", "NEUTRAL", "NONE"

        # Bullish regimes
        if regime in [RegimeState.TRENDING_BULLISH, RegimeState.BREAKOUT_UP]:
            return "BUY_CE", "LONG", "CE"

        if regime == RegimeState.RANGING_BULLISH:
            # Only trade at support in ranging
            return "BUY_CE_AT_SUPPORT", "LONG", "CE"

        # Bearish regimes
        if regime in [RegimeState.TRENDING_BEARISH, RegimeState.BREAKOUT_DOWN]:
            return "BUY_PE", "SHORT", "PE"

        if regime == RegimeState.RANGING_BEARISH:
            # Only trade at resistance in ranging
            return "BUY_PE_AT_RESISTANCE", "SHORT", "PE"

        # Neutral ranging
        if regime == RegimeState.RANGING_NEUTRAL:
            return "NO_TRADE", "NEUTRAL", "NONE"

        return "NO_TRADE", "NEUTRAL", "NONE"

    def _calculate_risk_multiplier(
        self,
        vol: VolatilityOutput,
        consensus_level: float
    ) -> float:
        """Calculate position size multiplier"""
        # Start with volatility-based multiplier
        risk_mult = vol.risk_multiplier

        # Adjust for consensus
        if consensus_level >= 0.8:
            risk_mult *= 1.1  # Higher confidence = slightly larger
        elif consensus_level < 0.5:
            risk_mult *= 0.7  # Low consensus = smaller

        # Apply bounds
        return max(
            self.config.min_risk_multiplier,
            min(self.config.max_risk_multiplier, risk_mult)
        )

    def _calculate_stop_target(
        self,
        vol: VolatilityOutput,
        trend: TrendOutput,
        regime: RegimeState
    ) -> tuple[float, float]:
        """Calculate stop loss and target percentages"""
        # Base on volatility
        base_stop = 0.5  # 0.5% default

        if vol.level == VolatilityLevel.EXTREME:
            base_stop = 1.5
        elif vol.level == VolatilityLevel.HIGH:
            base_stop = 1.0
        elif vol.level == VolatilityLevel.LOW:
            base_stop = 0.3

        # Adjust for regime
        if regime in [RegimeState.TRENDING_BULLISH, RegimeState.TRENDING_BEARISH]:
            # Trending: wider stops, larger targets
            stop_pct = base_stop * 1.2
            target_pct = base_stop * 2.5
        elif regime in [RegimeState.BREAKOUT_UP, RegimeState.BREAKOUT_DOWN]:
            # Breakout: wider stops for volatility
            stop_pct = base_stop * 1.5
            target_pct = base_stop * 3.0
        else:
            # Ranging: tighter stops and targets
            stop_pct = base_stop * 0.8
            target_pct = base_stop * 1.5

        return round(stop_pct, 2), round(target_pct, 2)

    def _calculate_confidence(
        self,
        vol: VolatilityOutput,
        sent: SentimentOutput,
        trend: TrendOutput,
        consensus: Dict[str, Any]
    ) -> float:
        """Calculate overall pipeline confidence"""
        # Weighted average of module confidences
        vol_weight = self.volatility.get_weight() * self.config.volatility_weight
        sent_weight = self.sentiment.get_weight() * self.config.sentiment_weight
        trend_weight = self.trend.get_weight() * self.config.trend_weight

        total_weight = vol_weight + sent_weight + trend_weight

        weighted_conf = (
            vol.confidence * vol_weight +
            sent.confidence * sent_weight +
            trend.confidence * trend_weight
        ) / total_weight if total_weight > 0 else 50

        # Adjust for consensus
        consensus_factor = 0.7 + (consensus["level"] * 0.3)
        final_conf = weighted_conf * consensus_factor

        return min(95, max(20, final_conf))

    def _generate_reasoning(
        self,
        regime: RegimeState,
        vol: VolatilityOutput,
        sent: SentimentOutput,
        trend: TrendOutput,
        action: str
    ) -> str:
        """Generate human-readable reasoning"""
        parts = [
            f"REGIME: {regime.value}",
            f"VOLATILITY: {vol.level.value} (VIX: {vol.vix}, Range: {vol.range_pct}%)",
            f"SENTIMENT: {sent.bias.value} (PCR: {sent.pcr}, Signal: {sent.institutional_signal})",
            f"TREND: {trend.direction.value} (Strength: {trend.strength:.0f}, Phase: {trend.phase.value})",
            f"ACTION: {action}",
        ]
        return " | ".join(parts)

    def _update_stats(self, decision: PipelineDecision, index: str):
        """Update pipeline statistics"""
        self.stats["total_decisions"] += 1

        if decision.action not in ["NO_TRADE", "WAIT_BREAKOUT"]:
            self.stats["trades_suggested"] += 1

        # By regime
        regime_key = decision.regime.value
        if regime_key not in self.stats["by_regime"]:
            self.stats["by_regime"][regime_key] = 0
        self.stats["by_regime"][regime_key] += 1

        # By index
        if index not in self.stats["by_index"]:
            self.stats["by_index"][index] = 0
        self.stats["by_index"][index] += 1

        self._save_stats()

    # === Module Control Methods ===

    def enable_module(self, module_name: str):
        """Enable a specific module"""
        if module_name == "volatility":
            self.volatility.enable()
        elif module_name == "sentiment":
            self.sentiment.enable()
        elif module_name == "trend":
            self.trend.enable()

    def disable_module(self, module_name: str):
        """Disable a specific module"""
        if module_name == "volatility":
            self.volatility.disable()
        elif module_name == "sentiment":
            self.sentiment.disable()
        elif module_name == "trend":
            self.trend.disable()

    def set_module_weight(self, module_name: str, weight: float):
        """Set weight for a specific module"""
        if module_name == "volatility":
            self.config.volatility_weight = weight
        elif module_name == "sentiment":
            self.config.sentiment_weight = weight
        elif module_name == "trend":
            self.config.trend_weight = weight
        self.save_config()

    def set_index_override(self, index: str, module: str, param: str, value: Any):
        """Set index-specific override for a module"""
        if module == "volatility":
            self.volatility.set_index_override(index, param, value)
        elif module == "sentiment":
            self.sentiment.set_index_override(index, param, value)
        elif module == "trend":
            self.trend.set_index_override(index, param, value)

    def configure_for_expiry(self):
        """Configure pipeline for expiry day trading"""
        # Increase volatility and sentiment weights
        self.config.volatility_weight = 1.5
        self.config.sentiment_weight = 1.5
        self.config.trend_weight = 0.7
        self.save_config()

    def configure_for_normal(self):
        """Configure pipeline for normal trading"""
        self.config.volatility_weight = 1.0
        self.config.sentiment_weight = 1.0
        self.config.trend_weight = 1.0
        self.save_config()

    def get_module_status(self) -> Dict[str, Any]:
        """Get status of all modules"""
        return {
            "volatility": {
                "enabled": self.volatility.is_enabled(),
                "weight": self.volatility.get_weight() * self.config.volatility_weight,
                "performance": self.volatility.performance.accuracy,
            },
            "sentiment": {
                "enabled": self.sentiment.is_enabled(),
                "weight": self.sentiment.get_weight() * self.config.sentiment_weight,
                "performance": self.sentiment.performance.accuracy,
            },
            "trend": {
                "enabled": self.trend.is_enabled(),
                "weight": self.trend.get_weight() * self.config.trend_weight,
                "performance": self.trend.performance.accuracy,
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert pipeline state to dictionary"""
        return {
            "config": asdict(self.config),
            "stats": self.stats,
            "modules": self.get_module_status(),
            "recent_decisions": [d.to_dict() for d in self.decision_history[-10:]],
        }


# === Convenience function for quick analysis ===

def quick_regime_check(index: str, market_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Quick regime check without maintaining pipeline state.

    Usage:
        result = quick_regime_check("NIFTY50", market_data)
        print(result["regime"], result["action"])
    """
    pipeline = RegimeHunterPipeline()
    decision = pipeline.analyze(index, market_data)

    return {
        "regime": decision.regime.value,
        "action": decision.action,
        "confidence": decision.confidence,
        "risk_multiplier": decision.risk_multiplier,
        "entry_side": decision.entry_side,
        "reasoning": decision.reasoning,
        "warnings": decision.warnings,
    }

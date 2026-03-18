"""
Regime Hunter Pipeline - Hybrid Module Architecture

A modular approach to market regime detection with three specialized modules:
- VolatilityModule: Analyzes VIX, Range%, ATR for risk assessment
- SentimentModule: Analyzes PCR, OI patterns for institutional bias
- TrendModule: Analyzes price action, momentum for direction

Each module can be:
- Independently tuned per index
- Enabled/disabled based on market conditions
- Weighted differently for final regime decision
"""

from .base_module import (
    BaseModule,
    ModuleOutput,
    ModuleConfig,
    ModulePerformance,
)
from .volatility_module import VolatilityModule, VolatilityLevel, VolatilityOutput
from .sentiment_module import SentimentModule, SentimentBias, SentimentOutput
from .trend_module import TrendModule, TrendDirection, TrendOutput
from .regime_hunter_pipeline import (
    RegimeHunterPipeline,
    PipelineConfig,
    PipelineDecision,
    RegimeState,
)

__all__ = [
    # Base
    "BaseModule",
    "ModuleOutput",
    "ModuleConfig",
    "ModulePerformance",
    # Volatility
    "VolatilityModule",
    "VolatilityLevel",
    "VolatilityOutput",
    # Sentiment
    "SentimentModule",
    "SentimentBias",
    "SentimentOutput",
    # Trend
    "TrendModule",
    "TrendDirection",
    "TrendOutput",
    # Pipeline
    "RegimeHunterPipeline",
    "PipelineConfig",
    "PipelineDecision",
    "RegimeState",
]

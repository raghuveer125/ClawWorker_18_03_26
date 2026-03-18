"""
Institutional-Grade Multi-Bot Ensemble Trading System

A self-learning ensemble of specialized trading bots with:
- Persistent pattern recognition (all learning on disk)
- Market regime detection and adaptation
- Institutional trading rules (time filters, risk management)
- Performance-based weight adjustment

Bots:
- TrendFollower: Rides established trends with moving averages
- ReversalHunter: Catches mean reversion at extremes
- MomentumScalper: Quick scalps on momentum bursts
- OIAnalyst: Follows institutional money via OI/PCR
- VolatilityTrader: Trades volatility cycles
- MLPredictor: Machine learning model (auto-activates after training)

Learning:
- DeepLearningEngine: Persistent pattern discovery
- RegimeDetector: Market condition adaptation
- MLTradingBot: Self-training ML model (needs 500+ trades)
"""

from .base import (
    TradingBot,
    BotSignal,
    BotDecision,
    TradeRecord,
    BotPerformance,
    SharedMemory,
    SignalType,
    OptionType,
)
from .trend_follower import TrendFollowerBot
from .reversal_hunter import ReversalHunterBot
from .momentum_scalper import MomentumScalperBot
from .oi_analyst import OIAnalystBot
from .volatility_trader import VolatilityTraderBot
from .deep_learning import DeepLearningEngine, TradeContext, Pattern
from .regime_detector import RegimeDetector, MarketRegime, RegimeAnalysis
from .ml_features import MLFeatureExtractor, MLFeatures
from .ml_bot import MLTradingBot, train_model
from .llm_trading_bot import LLMTradingBot
from .regime_hunter import RegimeHunterBot
from .llm_veto import LLMVetoLayer, VetoDecision
from .multi_timeframe import MultiTimeframeEngine, TimeframeAlignment, Trend, MTFAnalysis
from .adaptive_risk_controller import AdaptiveRiskController, RiskLevel, TradeOutcome
from .parameter_optimizer import ParameterOptimizer
from .institutional_risk_layer import (
    InstitutionalRiskLayer,
    TradingCondition,
    MarketRegime as InstMarketRegime,
    RegimeAnalysis as InstRegimeAnalysis,
    BotExpectancy,
    PortfolioExposure,
    DecisionQuality,
)
from .capital_allocator import (
    InstitutionalCapitalAllocator,
    StrategySleeve,
    SleeveAllocation,
    CapitalDecision,
    DrawdownState,
)
from .model_drift_detector import (
    ModelDriftDetector,
    DriftSeverity,
    ModelStatus,
    DriftType,
    DriftAlert,
    ModelHealth,
)
from .execution_engine import (
    ExecutionEngine,
    ExecutionStrategy,
    LiquidityLevel,
    ExecutionUrgency,
    SlippageEstimate,
    MarketImpact,
    ExecutionPlan,
    ExecutionQuality,
)
from .ensemble import EnsembleCoordinator, EnsembleConfig

# Regime Hunter Pipeline (Hybrid Module Architecture)
from .regime_modules import (
    # Base
    BaseModule,
    ModuleOutput,
    ModuleConfig,
    ModulePerformance,
    # Volatility Module
    VolatilityModule,
    VolatilityLevel,
    VolatilityOutput,
    # Sentiment Module
    SentimentModule,
    SentimentBias,
    SentimentOutput,
    # Trend Module
    TrendModule,
    TrendDirection,
    TrendOutput,
    # Pipeline
    RegimeHunterPipeline,
    PipelineConfig,
    PipelineDecision,
    RegimeState,
)

__all__ = [
    # Base classes
    "TradingBot",
    "BotSignal",
    "BotDecision",
    "TradeRecord",
    "BotPerformance",
    "SharedMemory",
    "SignalType",
    "OptionType",
    # Bots
    "TrendFollowerBot",
    "ReversalHunterBot",
    "MomentumScalperBot",
    "OIAnalystBot",
    "VolatilityTraderBot",
    "RegimeHunterBot",
    # Deep Learning
    "DeepLearningEngine",
    "TradeContext",
    "Pattern",
    # Regime Detection
    "RegimeDetector",
    "MarketRegime",
    "RegimeAnalysis",
    # ML Features
    "MLFeatureExtractor",
    "MLFeatures",
    # ML Trading Bot
    "MLTradingBot",
    "train_model",
    # LLM Trading Bot
    "LLMTradingBot",
    # LLM Veto Layer
    "LLMVetoLayer",
    "VetoDecision",
    # Multi-Timeframe Analysis
    "MultiTimeframeEngine",
    "TimeframeAlignment",
    "Trend",
    "MTFAnalysis",
    # Adaptive Risk Controller (Independent Learning Layer)
    "AdaptiveRiskController",
    "RiskLevel",
    "TradeOutcome",
    # Parameter Optimizer (Self-Tuning Parameters)
    "ParameterOptimizer",
    # Institutional Risk Layer (Hedge Fund Grade)
    "InstitutionalRiskLayer",
    "TradingCondition",
    "InstMarketRegime",
    "InstRegimeAnalysis",
    "BotExpectancy",
    "PortfolioExposure",
    "DecisionQuality",
    # Capital Allocator (Multi-Strategy Hedge Fund Grade)
    "InstitutionalCapitalAllocator",
    "StrategySleeve",
    "SleeveAllocation",
    "CapitalDecision",
    "DrawdownState",
    # Model Drift Detector (Model Risk Governance)
    "ModelDriftDetector",
    "DriftSeverity",
    "ModelStatus",
    "DriftType",
    "DriftAlert",
    "ModelHealth",
    # Execution Engine (Market Impact & Slippage Intelligence)
    "ExecutionEngine",
    "ExecutionStrategy",
    "LiquidityLevel",
    "ExecutionUrgency",
    "SlippageEstimate",
    "MarketImpact",
    "ExecutionPlan",
    "ExecutionQuality",
    # Ensemble
    "EnsembleCoordinator",
    "EnsembleConfig",
    # Regime Hunter Pipeline (Hybrid Module Architecture)
    "BaseModule",
    "ModuleOutput",
    "ModuleConfig",
    "ModulePerformance",
    "VolatilityModule",
    "VolatilityLevel",
    "VolatilityOutput",
    "SentimentModule",
    "SentimentBias",
    "SentimentOutput",
    "TrendModule",
    "TrendDirection",
    "TrendOutput",
    "RegimeHunterPipeline",
    "PipelineConfig",
    "PipelineDecision",
    "RegimeState",
]

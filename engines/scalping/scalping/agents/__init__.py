"""
Scalping Agents - 21 specialized AI agents for autonomous scalping.

Architecture:
- Safety Layer (1 agent): KillSwitch - runs FIRST, can halt entire pipeline
- Data Layer (4 agents): Feed, OptionChain, Futures, LatencyGuardian
- Analysis Layer (5 agents): MarketRegime, Structure, Momentum, TrapDetector, StrikeSelector
- Quality Gate (1 agent): SignalQuality - filters weak signals before execution
- Risk Layer (3 agents): LiquidityMonitor, RiskGuardian, CorrelationGuard
- Execution Layer (4 agents): MetaAllocator, Entry, Exit, PositionManager
- Learning Layer (3 agents): QuantLearner, StrategyOptimizer, ExitOptimizer
"""

from .kill_switch_agent import KillSwitchAgent
from .data_agents import DataFeedAgent, OptionChainAgent, FuturesAgent
from .analysis_agents import (
    StructureAgent,
    MomentumAgent,
    TrapDetectorAgent,
    StrikeSelectorAgent,
)
from .volatility_surface_agent import VolatilitySurfaceAgent
from .dealer_pressure_agent import DealerPressureAgent
from .signal_quality_agent import SignalQualityAgent
from .execution_agents import (
    EntryAgent,
    ExitAgent,
    PositionManagerAgent,
    RiskGuardianAgent,
)
from .learning_agents import QuantLearnerAgent, StrategyOptimizerAgent
from .exit_optimizer_agent import ExitOptimizerAgent
from .meta_agents import MetaAllocatorAgent, CorrelationGuardAgent
from .infrastructure_agents import (
    LatencyGuardianAgent,
    LiquidityMonitorAgent,
    MarketRegimeAgent,
)

__all__ = [
    # Safety Layer (1) - runs FIRST
    "KillSwitchAgent",
    # Data Layer (4)
    "DataFeedAgent",
    "OptionChainAgent",
    "FuturesAgent",
    "LatencyGuardianAgent",
    # Analysis Layer (5)
    "MarketRegimeAgent",
    "StructureAgent",
    "MomentumAgent",
    "TrapDetectorAgent",
    "StrikeSelectorAgent",
    "VolatilitySurfaceAgent",
    "DealerPressureAgent",
    # Quality Gate (1) - filters signals before execution
    "SignalQualityAgent",
    # Risk Layer (3)
    "LiquidityMonitorAgent",
    "RiskGuardianAgent",
    "CorrelationGuardAgent",
    # Execution Layer (4)
    "MetaAllocatorAgent",
    "EntryAgent",
    "ExitAgent",
    "PositionManagerAgent",
    # Learning Layer (3)
    "QuantLearnerAgent",
    "StrategyOptimizerAgent",
    "ExitOptimizerAgent",
]

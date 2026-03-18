"""
Scalping System - Quant-Style Learning Scalping for Index Options

A 15-agent autonomous system for NIFTY/BANKNIFTY/SENSEX options scalping.

Architecture:
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SCALPING COMMAND CENTER                               │
├─────────────────────────────────────────────────────────────────────────────┤
│  DATA LAYER           │  ANALYSIS LAYER       │  EXECUTION LAYER            │
│  ─────────────        │  ──────────────       │  ────────────────           │
│  1. DataFeedAgent     │  5. StructureAgent    │  9. EntryAgent              │
│  2. OptionChainAgent  │  6. MomentumAgent     │  10. ExitAgent              │
│  3. FuturesAgent      │  7. TrapDetector      │  11. PositionManager        │
│  4. HistoryAgent      │  8. StrikeSelector    │  12. RiskGuardian           │
├─────────────────────────────────────────────────────────────────────────────┤
│  LEARNING LAYER                              │  META LAYER                  │
│  ──────────────                              │  ──────────────              │
│  13. QuantLearner (ML training)              │  14. MetaAllocator           │
│  14. StrategyOptimizer (nightly backtest)    │  15. CorrelationGuard        │
│  15. AlphaMonitor (edge decay)               │                              │
└─────────────────────────────────────────────────────────────────────────────┘

Target: ₹10-₹25 far OTM options that can expand to ₹40-₹100+
"""

from .agents import (
    # Data Layer
    DataFeedAgent,
    OptionChainAgent,
    FuturesAgent,
    # Analysis Layer
    StructureAgent,
    MomentumAgent,
    TrapDetectorAgent,
    StrikeSelectorAgent,
    # Execution Layer
    EntryAgent,
    ExitAgent,
    PositionManagerAgent,
    RiskGuardianAgent,
    # Learning Layer
    QuantLearnerAgent,
    StrategyOptimizerAgent,
    # Meta Layer
    MetaAllocatorAgent,
    CorrelationGuardAgent,
)

from .engine import ScalpingEngine
from .config import ScalpingConfig, IndexConfig

__all__ = [
    "ScalpingEngine",
    "ScalpingConfig",
    "IndexConfig",
    # Agents
    "DataFeedAgent",
    "OptionChainAgent",
    "FuturesAgent",
    "StructureAgent",
    "MomentumAgent",
    "TrapDetectorAgent",
    "StrikeSelectorAgent",
    "EntryAgent",
    "ExitAgent",
    "PositionManagerAgent",
    "RiskGuardianAgent",
    "QuantLearnerAgent",
    "StrategyOptimizerAgent",
    "MetaAllocatorAgent",
    "CorrelationGuardAgent",
]

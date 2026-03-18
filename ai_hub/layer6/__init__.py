"""Layer 6 - Learning Army + Genetic AI.

Features:
- Quant learning from trade outcomes
- Strategy parameter optimization
- Genetic evolution of strategies
- Safety gates (risk filter, deployment gate)
- Feedback loop from live trading
"""
from .learning import (
    QuantLearnerAgent,
    TradeOutcome,
    LearningInsight,
    StrategyOptimizerAgent,
    OptimizationResult,
)
from .genetic import (
    StrategyGenome,
    GeneType,
    PopulationManager,
    FitnessEvaluator,
    FitnessResult,
    SelectionEngine,
    SelectionMethod,
    MutationEngine,
    GeneticPipeline,
    EvolutionResult,
)
from .safety import (
    RiskFilter,
    RiskCheckResult,
    DeploymentGate,
    DeploymentDecision,
)
from .feedback import FeedbackLoop, FeedbackEntry

__all__ = [
    # Learning
    "QuantLearnerAgent",
    "TradeOutcome",
    "LearningInsight",
    "StrategyOptimizerAgent",
    "OptimizationResult",
    # Genetic
    "StrategyGenome",
    "GeneType",
    "PopulationManager",
    "FitnessEvaluator",
    "FitnessResult",
    "SelectionEngine",
    "SelectionMethod",
    "MutationEngine",
    "GeneticPipeline",
    "EvolutionResult",
    # Safety
    "RiskFilter",
    "RiskCheckResult",
    "DeploymentGate",
    "DeploymentDecision",
    # Feedback
    "FeedbackLoop",
    "FeedbackEntry",
]

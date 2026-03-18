"""
AI Engineering Hub - Self-Improving AI Trading System

Architecture:
  L0 (Data) → L1 (Goal) → L2 (Reason) → Synapse → L3 (Workers) →
  L4 (Execute) → L5 (Improve) → L6 (Learn + Genetic) → back to L0

Layers:
- layer0: Data pipeline (market data, signals)
- layer1: Goal orchestrator (goal definition, decomposition)
- layer2: Reasoning engine (context, memory, chain-of-thought)
- synapse: Neural message bus (inter-layer communication)
- layer3: Worker army orchestrator (registration, dispatch, health)
- layer4: Execution grid (parallel execution, monitoring, errors)
- layer5: Self-improvement (weak worker ID, patches, validation)
- layer6: Learning + Genetic AI (quant learning, evolution, safety)
"""

__version__ = "2.0.0"
__author__ = "AI Engineering Hub"

# Layer imports (lazy to avoid circular dependencies)
def get_layer0():
    from . import layer0
    return layer0

def get_layer1():
    from . import layer1
    return layer1

def get_layer2():
    from . import layer2
    return layer2

def get_synapse():
    from . import synapse
    return synapse

def get_layer3():
    from . import layer3
    return layer3

def get_layer4():
    from . import layer4
    return layer4

def get_layer5():
    from . import layer5
    return layer5

def get_layer6():
    from . import layer6
    return layer6

# Quick access to key components
def create_synapse():
    """Create a new Synapse message bus."""
    from .synapse import Synapse
    return Synapse()

def create_goal_manager():
    """Create goal manager."""
    from .layer1 import GoalManager
    return GoalManager()

def create_reasoning_engine():
    """Create reasoning engine (chain-of-thought pipeline)."""
    from .layer2.reasoning import CoTPipeline
    return CoTPipeline()

def create_worker_registry():
    """Create worker registry."""
    from .layer3 import WorkerRegistry
    return WorkerRegistry()

def create_genetic_pipeline(population_size=50):
    """Create genetic evolution pipeline."""
    from .layer6 import GeneticPipeline
    return GeneticPipeline(population_size=population_size)

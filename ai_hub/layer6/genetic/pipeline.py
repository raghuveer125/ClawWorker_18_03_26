"""
Genetic Pipeline - Orchestrates the evolution process.

Combines population, selection, crossover, mutation, and fitness
into a complete evolutionary optimization system.
"""

import time
import logging
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field

from .genome import StrategyGenome, create_scalping_genome
from .population import PopulationManager
from .fitness import FitnessEvaluator
from .selection import SelectionEngine, SelectionMethod
from .mutation import MutationEngine

logger = logging.getLogger(__name__)


@dataclass
class EvolutionResult:
    """Result of evolution run."""
    best_genome: StrategyGenome
    best_fitness: float
    generations: int
    total_evaluations: int
    improvement: float  # From initial to final
    duration_seconds: float
    history: List[Dict[str, float]] = field(default_factory=list)


class GeneticPipeline:
    """
    Complete genetic algorithm pipeline.

    Orchestrates:
    - Population initialization
    - Fitness evaluation
    - Selection
    - Crossover and mutation
    - Generation progression
    """

    AGENT_TYPE = "genetic_pipeline"

    DEFAULT_GENERATIONS = 50
    STAGNATION_LIMIT = 10  # Stop if no improvement for N generations
    DIVERSITY_INJECTION_RATE = 0.05  # 5% random injection

    def __init__(
        self,
        population_size: int = 50,
        genome_factory: Optional[Callable[[], StrategyGenome]] = None,
        backtester: Optional[Callable] = None,
        selection_method: SelectionMethod = SelectionMethod.TOURNAMENT,
    ):
        self._genome_factory = genome_factory or create_scalping_genome

        # Components
        self._population = PopulationManager(
            population_size=population_size,
            genome_factory=self._genome_factory,
        )
        self._fitness = FitnessEvaluator(backtester=backtester)
        self._selection = SelectionEngine(method=selection_method)
        self._mutation = MutationEngine()

        # State
        self._current_generation = 0
        self._best_fitness_ever = float("-inf")
        self._stagnation_count = 0
        self._evolution_history: List[Dict[str, float]] = []

    def set_backtester(self, backtester: Callable):
        """Set backtester for fitness evaluation."""
        self._fitness.set_backtester(backtester)

    def initialize(self, seed_genomes: Optional[List[StrategyGenome]] = None):
        """Initialize population."""
        self._population.initialize(seed_genomes)
        self._current_generation = 0
        self._best_fitness_ever = float("-inf")
        self._stagnation_count = 0
        self._evolution_history = []

    def evolve(
        self,
        generations: Optional[int] = None,
        callback: Optional[Callable[[int, Dict], None]] = None,
    ) -> EvolutionResult:
        """
        Run evolution for specified generations.

        Args:
            generations: Number of generations (default: DEFAULT_GENERATIONS)
            callback: Optional callback(generation, stats) for progress

        Returns:
            EvolutionResult
        """
        max_gen = generations or self.DEFAULT_GENERATIONS
        start_time = time.time()
        total_evals = 0

        # Get initial fitness
        initial_fitness = 0.0

        for gen in range(max_gen):
            self._current_generation = gen

            # Evaluate population
            population = self._population.get_population()
            results = self._fitness.evaluate_population(population)
            total_evals += len(results)

            # Track best
            gen_best = self._population.get_best()
            gen_fitness = gen_best.fitness if gen_best else 0

            if gen == 0:
                initial_fitness = gen_fitness

            # Check for improvement
            if gen_fitness > self._best_fitness_ever:
                self._best_fitness_ever = gen_fitness
                self._stagnation_count = 0
            else:
                self._stagnation_count += 1

            # Record history
            stats = self._population.get_stats()
            stats["stagnation"] = self._stagnation_count
            self._evolution_history.append(stats)

            # Callback
            if callback:
                callback(gen, stats)

            # Early stopping
            if self._stagnation_count >= self.STAGNATION_LIMIT:
                logger.info(f"Early stopping at generation {gen} (stagnation)")
                break

            # Selection
            num_offspring = self._population.population_size - int(
                self._population.population_size * self._population.ELITE_RATIO
            )
            parents = self._selection.select_parents(population, num_offspring)

            # Create offspring
            offspring = self._mutation.create_offspring(parents)

            # Diversity injection if stagnating
            if self._stagnation_count > 3:
                inject_count = max(1, int(len(offspring) * self.DIVERSITY_INJECTION_RATE))
                diverse = self._mutation.inject_diversity(
                    population,
                    inject_count,
                    self._genome_factory(),
                )
                offspring.extend(diverse)
                offspring = offspring[:num_offspring]  # Trim to size

            # Evolve to next generation
            self._population.evolve(offspring, preserve_elite=True)

        # Final result
        best = self._population.get_best()
        improvement = (
            (self._best_fitness_ever - initial_fitness) / abs(initial_fitness)
            if initial_fitness != 0 else 0
        )

        return EvolutionResult(
            best_genome=best or self._genome_factory(),
            best_fitness=self._best_fitness_ever,
            generations=self._current_generation + 1,
            total_evaluations=total_evals,
            improvement=improvement,
            duration_seconds=time.time() - start_time,
            history=self._evolution_history,
        )

    def get_best_genome(self) -> Optional[StrategyGenome]:
        """Get current best genome."""
        return self._population.get_best()

    def get_elite_genomes(self, count: int = 5) -> List[StrategyGenome]:
        """Get top N genomes."""
        return self._population.get_elite(count)

    def get_stats(self) -> Dict:
        """Get pipeline statistics."""
        return {
            "generation": self._current_generation,
            "best_fitness": self._best_fitness_ever,
            "stagnation": self._stagnation_count,
            "population": self._population.get_stats(),
            "evaluator": self._fitness.get_stats(),
        }

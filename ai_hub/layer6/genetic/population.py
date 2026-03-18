"""
Population Manager - Manages population of strategy genomes.

Features:
- Population initialization
- Generation tracking
- Elite preservation
"""

import logging
from typing import Callable, Dict, List, Optional
from dataclasses import dataclass, field

from .genome import StrategyGenome, create_scalping_genome

logger = logging.getLogger(__name__)


@dataclass
class PopulationStats:
    """Statistics for a population."""
    generation: int
    population_size: int
    best_fitness: float
    avg_fitness: float
    worst_fitness: float
    diversity: float  # Measure of genetic diversity


class PopulationManager:
    """
    Manages a population of strategy genomes.

    Features:
    - Population initialization
    - Generation management
    - Statistics tracking
    """

    DEFAULT_POPULATION_SIZE = 50
    ELITE_RATIO = 0.1  # Top 10% preserved

    def __init__(
        self,
        population_size: int = DEFAULT_POPULATION_SIZE,
        genome_factory: Optional[Callable[[], StrategyGenome]] = None,
    ):
        self.population_size = population_size
        self._genome_factory = genome_factory or create_scalping_genome
        self._population: List[StrategyGenome] = []
        self._generation = 0
        self._history: List[PopulationStats] = []

    def initialize(self, seed_genomes: Optional[List[StrategyGenome]] = None):
        """
        Initialize population.

        Args:
            seed_genomes: Optional seed genomes to include
        """
        self._population = []

        # Add seeds
        if seed_genomes:
            for genome in seed_genomes[:self.population_size]:
                self._population.append(genome)

        # Fill remaining with random genomes
        while len(self._population) < self.population_size:
            genome = self._genome_factory()
            # Randomize genes
            mutated = genome.mutate()
            mutated.generation = 0
            self._population.append(mutated)

        self._generation = 0
        logger.info(f"Initialized population with {len(self._population)} genomes")

    def get_population(self) -> List[StrategyGenome]:
        """Get current population."""
        return list(self._population)

    def set_fitness(self, genome_id: str, fitness: float):
        """Set fitness for a genome."""
        for genome in self._population:
            if genome.genome_id == genome_id:
                genome.fitness = fitness
                break

    def get_elite(self, count: Optional[int] = None) -> List[StrategyGenome]:
        """Get top N genomes by fitness."""
        count = count or max(1, int(self.population_size * self.ELITE_RATIO))

        # Sort by fitness (evaluated genomes only)
        evaluated = [g for g in self._population if g.fitness is not None]
        evaluated.sort(key=lambda g: g.fitness or 0, reverse=True)

        return evaluated[:count]

    def get_best(self) -> Optional[StrategyGenome]:
        """Get best genome."""
        elite = self.get_elite(1)
        return elite[0] if elite else None

    def evolve(
        self,
        offspring: List[StrategyGenome],
        preserve_elite: bool = True,
    ):
        """
        Replace population with new generation.

        Args:
            offspring: New genomes for next generation
            preserve_elite: Whether to preserve top performers
        """
        new_population = []

        # Preserve elite
        if preserve_elite:
            elite = self.get_elite()
            for genome in elite:
                # Clone elite without mutating
                elite_copy = StrategyGenome(
                    strategy_type=genome.strategy_type,
                    genes=dict(genome.genes),  # Keep genes
                    generation=self._generation + 1,
                    parent_ids=[genome.genome_id],
                    fitness=None,  # Reset fitness for re-evaluation
                )
                new_population.append(elite_copy)

        # Add offspring
        for genome in offspring:
            if len(new_population) >= self.population_size:
                break
            new_population.append(genome)

        # Fill any remaining slots
        while len(new_population) < self.population_size:
            new_population.append(self._genome_factory().mutate())

        # Record stats before replacing
        self._record_stats()

        # Replace population
        self._population = new_population[:self.population_size]
        self._generation += 1

        logger.info(
            f"Generation {self._generation}: {len(new_population)} genomes"
        )

    def _record_stats(self):
        """Record population statistics."""
        evaluated = [g for g in self._population if g.fitness is not None]

        if not evaluated:
            return

        fitnesses = [g.fitness for g in evaluated]

        stats = PopulationStats(
            generation=self._generation,
            population_size=len(self._population),
            best_fitness=max(fitnesses),
            avg_fitness=sum(fitnesses) / len(fitnesses),
            worst_fitness=min(fitnesses),
            diversity=self._calculate_diversity(),
        )

        self._history.append(stats)

    def _calculate_diversity(self) -> float:
        """Calculate genetic diversity (0-1)."""
        if len(self._population) < 2:
            return 0.0

        # Simple: count unique parent lineages
        parent_sets = set()
        for genome in self._population:
            parent_sets.add(tuple(sorted(genome.parent_ids)))

        return len(parent_sets) / len(self._population)

    def get_stats(self) -> Dict:
        """Get current population stats."""
        evaluated = [g for g in self._population if g.fitness is not None]

        if not evaluated:
            return {
                "generation": self._generation,
                "population_size": len(self._population),
                "evaluated": 0,
            }

        fitnesses = [g.fitness for g in evaluated]

        return {
            "generation": self._generation,
            "population_size": len(self._population),
            "evaluated": len(evaluated),
            "best_fitness": max(fitnesses),
            "avg_fitness": sum(fitnesses) / len(fitnesses),
            "diversity": self._calculate_diversity(),
        }

    def get_history(self) -> List[PopulationStats]:
        """Get evolution history."""
        return list(self._history)

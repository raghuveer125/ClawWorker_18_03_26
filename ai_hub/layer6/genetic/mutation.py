"""
Mutation Engine - Applies mutations and crossover.

Features:
- Configurable mutation rates
- Adaptive mutation
- Multiple crossover strategies
"""

import random
import logging
from typing import List, Tuple

from .genome import StrategyGenome, Gene

logger = logging.getLogger(__name__)


class MutationEngine:
    """
    Applies genetic operators to genomes.

    Features:
    - Mutation with adaptive rates
    - Crossover operators
    - Diversity injection
    """

    DEFAULT_MUTATION_RATE = 0.1
    DEFAULT_CROSSOVER_RATE = 0.7

    def __init__(
        self,
        mutation_rate: float = DEFAULT_MUTATION_RATE,
        crossover_rate: float = DEFAULT_CROSSOVER_RATE,
    ):
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate

    def create_offspring(
        self,
        parents: List[Tuple[StrategyGenome, StrategyGenome]],
    ) -> List[StrategyGenome]:
        """
        Create offspring from parent pairs.

        Args:
            parents: List of (parent1, parent2) tuples

        Returns:
            List of offspring genomes
        """
        offspring = []

        for parent1, parent2 in parents:
            # Crossover
            if random.random() < self.crossover_rate:
                child = StrategyGenome.crossover(parent1, parent2)
            else:
                # Clone one parent
                child = parent1.mutate() if random.random() < 0.5 else parent2.mutate()

            # Mutate
            child = self._mutate(child)
            offspring.append(child)

        return offspring

    def _mutate(self, genome: StrategyGenome) -> StrategyGenome:
        """Apply mutation to genome."""
        for gene_name, gene in genome.genes.items():
            if random.random() < self.mutation_rate:
                genome.genes[gene_name] = gene.mutate()

        return genome

    def inject_diversity(
        self,
        population: List[StrategyGenome],
        count: int,
        template_genome: StrategyGenome,
    ) -> List[StrategyGenome]:
        """
        Inject random genomes for diversity.

        Args:
            population: Current population
            count: Number of random genomes to inject
            template_genome: Template for gene structure

        Returns:
            New genomes to add
        """
        new_genomes = []

        for _ in range(count):
            new_genome = StrategyGenome(
                strategy_type=template_genome.strategy_type,
                generation=max(g.generation for g in population) if population else 0,
            )

            # Copy gene structure with random values
            for name, gene in template_genome.genes.items():
                new_gene = Gene(
                    name=name,
                    gene_type=gene.gene_type,
                    value=gene.value,
                    min_value=gene.min_value,
                    max_value=gene.max_value,
                    choices=gene.choices,
                    mutation_rate=gene.mutation_rate,
                )
                # Heavily mutate
                new_gene = new_gene.mutate()
                new_gene = new_gene.mutate()  # Double mutation for more variation
                new_genome.genes[name] = new_gene

            new_genomes.append(new_genome)

        return new_genomes

    def adaptive_mutation(
        self,
        genome: StrategyGenome,
        generation: int,
        stagnation_count: int,
    ) -> StrategyGenome:
        """
        Apply adaptive mutation based on evolution progress.

        Increases mutation rate when evolution stagnates.

        Args:
            genome: Genome to mutate
            generation: Current generation
            stagnation_count: Generations without improvement

        Returns:
            Mutated genome
        """
        # Increase mutation rate with stagnation
        adaptive_rate = self.mutation_rate * (1 + stagnation_count * 0.1)
        adaptive_rate = min(adaptive_rate, 0.5)  # Cap at 50%

        for gene_name, gene in genome.genes.items():
            if random.random() < adaptive_rate:
                # Stronger mutation when stagnating
                mutated = gene.mutate()
                if stagnation_count > 5:
                    mutated = mutated.mutate()  # Double mutate
                genome.genes[gene_name] = mutated

        return genome

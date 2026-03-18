"""
Selection Engine - Selects parents for reproduction.

Implements various selection strategies.
"""

import random
import logging
from enum import Enum
from typing import List, Optional, Tuple

from .genome import StrategyGenome

logger = logging.getLogger(__name__)


class SelectionMethod(Enum):
    """Selection methods."""
    TOURNAMENT = "tournament"
    ROULETTE = "roulette"
    RANK = "rank"
    ELITIST = "elitist"


class SelectionEngine:
    """
    Selects parents for reproduction.

    Features:
    - Multiple selection methods
    - Configurable selection pressure
    """

    TOURNAMENT_SIZE = 3

    def __init__(self, method: SelectionMethod = SelectionMethod.TOURNAMENT):
        self.method = method

    def select_parents(
        self,
        population: List[StrategyGenome],
        count: int,
    ) -> List[Tuple[StrategyGenome, StrategyGenome]]:
        """
        Select parent pairs for reproduction.

        Args:
            population: Current population
            count: Number of parent pairs to select

        Returns:
            List of (parent1, parent2) tuples
        """
        # Filter to evaluated genomes only
        evaluated = [g for g in population if g.fitness is not None]

        if len(evaluated) < 2:
            logger.warning("Not enough evaluated genomes for selection")
            return []

        pairs = []
        for _ in range(count):
            parent1 = self._select_one(evaluated)
            parent2 = self._select_one(evaluated, exclude=parent1)
            pairs.append((parent1, parent2))

        return pairs

    def _select_one(
        self,
        population: List[StrategyGenome],
        exclude: Optional[StrategyGenome] = None,
    ) -> StrategyGenome:
        """Select one parent."""
        candidates = [g for g in population if g != exclude]

        if not candidates:
            return population[0]

        if self.method == SelectionMethod.TOURNAMENT:
            return self._tournament_select(candidates)
        elif self.method == SelectionMethod.ROULETTE:
            return self._roulette_select(candidates)
        elif self.method == SelectionMethod.RANK:
            return self._rank_select(candidates)
        else:  # ELITIST
            return self._elitist_select(candidates)

    def _tournament_select(self, population: List[StrategyGenome]) -> StrategyGenome:
        """Tournament selection."""
        tournament_size = min(self.TOURNAMENT_SIZE, len(population))
        tournament = random.sample(population, tournament_size)
        return max(tournament, key=lambda g: g.fitness or 0)

    def _roulette_select(self, population: List[StrategyGenome]) -> StrategyGenome:
        """Roulette wheel selection (fitness-proportionate)."""
        # Shift to positive
        min_fitness = min(g.fitness or 0 for g in population)
        shifted = [(g, (g.fitness or 0) - min_fitness + 0.01) for g in population]

        total = sum(f for _, f in shifted)
        pick = random.uniform(0, total)

        current = 0
        for genome, fitness in shifted:
            current += fitness
            if current >= pick:
                return genome

        return population[-1]

    def _rank_select(self, population: List[StrategyGenome]) -> StrategyGenome:
        """Rank-based selection."""
        # Sort by fitness
        sorted_pop = sorted(population, key=lambda g: g.fitness or 0)

        # Assign ranks (1 = worst, N = best)
        ranks = list(range(1, len(sorted_pop) + 1))
        total_rank = sum(ranks)

        pick = random.uniform(0, total_rank)
        current = 0

        for genome, rank in zip(sorted_pop, ranks):
            current += rank
            if current >= pick:
                return genome

        return sorted_pop[-1]

    def _elitist_select(self, population: List[StrategyGenome]) -> StrategyGenome:
        """Elitist selection (always picks from top)."""
        sorted_pop = sorted(population, key=lambda g: g.fitness or 0, reverse=True)
        # Pick from top 20%
        elite_size = max(1, len(sorted_pop) // 5)
        return random.choice(sorted_pop[:elite_size])

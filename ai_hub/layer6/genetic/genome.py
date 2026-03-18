"""
Strategy Genome - Encodes strategy parameters as genes.

Each genome represents a trading strategy configuration.
"""

import uuid
import random
import logging
from enum import Enum
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class GeneType(Enum):
    """Types of genes."""
    FLOAT = "float"
    INT = "int"
    BOOL = "bool"
    CHOICE = "choice"


@dataclass
class Gene:
    """A single gene in the genome."""
    name: str
    gene_type: GeneType
    value: Any
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    choices: Optional[List[Any]] = None
    mutation_rate: float = 0.1  # Per-gene mutation rate

    def mutate(self) -> "Gene":
        """Create a mutated copy of this gene."""
        new_gene = Gene(
            name=self.name,
            gene_type=self.gene_type,
            value=self.value,
            min_value=self.min_value,
            max_value=self.max_value,
            choices=self.choices,
            mutation_rate=self.mutation_rate,
        )

        if random.random() > self.mutation_rate:
            return new_gene  # No mutation

        # Apply mutation based on type
        if self.gene_type == GeneType.FLOAT:
            # Gaussian mutation
            range_size = (self.max_value or 1.0) - (self.min_value or 0.0)
            delta = random.gauss(0, range_size * 0.1)
            new_val = self.value + delta
            new_gene.value = max(
                self.min_value or float("-inf"),
                min(self.max_value or float("inf"), new_val)
            )

        elif self.gene_type == GeneType.INT:
            # Integer mutation
            range_size = int((self.max_value or 100) - (self.min_value or 0))
            delta = random.randint(-max(1, range_size // 10), max(1, range_size // 10))
            new_val = int(self.value) + delta
            new_gene.value = max(
                int(self.min_value or 0),
                min(int(self.max_value or 100), new_val)
            )

        elif self.gene_type == GeneType.BOOL:
            # Flip
            new_gene.value = not self.value

        elif self.gene_type == GeneType.CHOICE:
            # Random choice
            if self.choices:
                new_gene.value = random.choice(self.choices)

        return new_gene


@dataclass
class StrategyGenome:
    """
    Complete genome for a trading strategy.

    Encodes all tunable parameters as genes.
    """
    genome_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    strategy_type: str = "default"
    genes: Dict[str, Gene] = field(default_factory=dict)
    generation: int = 0
    parent_ids: List[str] = field(default_factory=list)
    fitness: Optional[float] = None
    created_at: float = field(default_factory=lambda: __import__("time").time())

    def add_gene(self, gene: Gene):
        """Add a gene to the genome."""
        self.genes[gene.name] = gene

    def get_value(self, gene_name: str) -> Any:
        """Get value of a gene."""
        gene = self.genes.get(gene_name)
        return gene.value if gene else None

    def set_value(self, gene_name: str, value: Any):
        """Set value of a gene."""
        if gene_name in self.genes:
            self.genes[gene_name].value = value

    def to_params(self) -> Dict[str, Any]:
        """Convert genome to parameter dict."""
        return {name: gene.value for name, gene in self.genes.items()}

    def mutate(self) -> "StrategyGenome":
        """Create a mutated copy of this genome."""
        new_genome = StrategyGenome(
            strategy_type=self.strategy_type,
            generation=self.generation + 1,
            parent_ids=[self.genome_id],
        )

        for name, gene in self.genes.items():
            new_genome.genes[name] = gene.mutate()

        return new_genome

    @classmethod
    def crossover(cls, parent1: "StrategyGenome", parent2: "StrategyGenome") -> "StrategyGenome":
        """Create offspring from two parents."""
        child = StrategyGenome(
            strategy_type=parent1.strategy_type,
            generation=max(parent1.generation, parent2.generation) + 1,
            parent_ids=[parent1.genome_id, parent2.genome_id],
        )

        # Uniform crossover
        all_genes = set(parent1.genes.keys()) | set(parent2.genes.keys())
        for name in all_genes:
            gene1 = parent1.genes.get(name)
            gene2 = parent2.genes.get(name)

            if gene1 and gene2:
                # Both parents have gene - pick one randomly
                child.genes[name] = Gene(
                    name=name,
                    gene_type=gene1.gene_type,
                    value=gene1.value if random.random() < 0.5 else gene2.value,
                    min_value=gene1.min_value,
                    max_value=gene1.max_value,
                    choices=gene1.choices,
                    mutation_rate=gene1.mutation_rate,
                )
            elif gene1:
                child.genes[name] = Gene(
                    name=name,
                    gene_type=gene1.gene_type,
                    value=gene1.value,
                    min_value=gene1.min_value,
                    max_value=gene1.max_value,
                    choices=gene1.choices,
                    mutation_rate=gene1.mutation_rate,
                )
            elif gene2:
                child.genes[name] = Gene(
                    name=name,
                    gene_type=gene2.gene_type,
                    value=gene2.value,
                    min_value=gene2.min_value,
                    max_value=gene2.max_value,
                    choices=gene2.choices,
                    mutation_rate=gene2.mutation_rate,
                )

        return child

    def __repr__(self) -> str:
        return f"StrategyGenome({self.genome_id}, gen={self.generation}, fitness={self.fitness})"


def create_scalping_genome() -> StrategyGenome:
    """Create a default scalping strategy genome."""
    genome = StrategyGenome(strategy_type="scalping")

    # Entry parameters
    genome.add_gene(Gene(
        name="vwap_band_multiplier",
        gene_type=GeneType.FLOAT,
        value=1.5,
        min_value=0.5,
        max_value=3.0,
    ))
    genome.add_gene(Gene(
        name="fvg_threshold",
        gene_type=GeneType.FLOAT,
        value=0.002,
        min_value=0.0005,
        max_value=0.01,
    ))
    genome.add_gene(Gene(
        name="rsi_oversold",
        gene_type=GeneType.INT,
        value=30,
        min_value=15,
        max_value=40,
    ))
    genome.add_gene(Gene(
        name="rsi_overbought",
        gene_type=GeneType.INT,
        value=70,
        min_value=60,
        max_value=85,
    ))

    # Exit parameters
    genome.add_gene(Gene(
        name="profit_target_pct",
        gene_type=GeneType.FLOAT,
        value=0.005,
        min_value=0.002,
        max_value=0.02,
    ))
    genome.add_gene(Gene(
        name="stop_loss_pct",
        gene_type=GeneType.FLOAT,
        value=0.003,
        min_value=0.001,
        max_value=0.01,
    ))
    genome.add_gene(Gene(
        name="max_hold_seconds",
        gene_type=GeneType.INT,
        value=300,
        min_value=60,
        max_value=900,
    ))

    # Risk parameters
    genome.add_gene(Gene(
        name="position_size_pct",
        gene_type=GeneType.FLOAT,
        value=0.02,
        min_value=0.005,
        max_value=0.05,
    ))
    genome.add_gene(Gene(
        name="use_trailing_stop",
        gene_type=GeneType.BOOL,
        value=True,
    ))
    genome.add_gene(Gene(
        name="entry_mode",
        gene_type=GeneType.CHOICE,
        value="aggressive",
        choices=["conservative", "moderate", "aggressive"],
    ))

    return genome

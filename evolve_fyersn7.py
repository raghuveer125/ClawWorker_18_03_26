#!/usr/bin/env python3
"""
FyersN7 Genetic Evolution - Auto-optimize signal thresholds.

Uses Hub V2's genetic algorithm to evolve optimal parameters for
the FyersN7 signal adapter.

Usage:
    python evolve_fyersn7.py --generations 20 --population 20
"""

import os
import sys
import csv
import time
import argparse
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from ai_hub.layer6.genetic.genome import StrategyGenome, Gene, GeneType
from ai_hub.layer6.genetic.pipeline import GeneticPipeline
from ai_hub.layer0.adapters import FyersN7SignalAdapter, FyersN7Signal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("FyersN7Evolution")

POSTMORTEM_BASE = PROJECT_ROOT / "fyersN7" / "fyers-2026-03-05" / "postmortem"


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def load_signal_csv(path: Path) -> List[Dict[str, str]]:
    """Load signals from CSV file."""
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        return []


def create_fyersn7_genome() -> StrategyGenome:
    """Create a FyersN7-specific genome with tunable parameters."""
    genome = StrategyGenome(strategy_type="fyersn7")

    # Entry thresholds
    genome.add_gene(Gene(
        name="min_score",
        gene_type=GeneType.INT,
        value=50,
        min_value=30,
        max_value=90,
        mutation_rate=0.2,
    ))
    genome.add_gene(Gene(
        name="min_confidence",
        gene_type=GeneType.INT,
        value=70,
        min_value=50,
        max_value=95,
        mutation_rate=0.2,
    ))
    genome.add_gene(Gene(
        name="min_vote_diff",
        gene_type=GeneType.FLOAT,
        value=3.0,
        min_value=1.0,
        max_value=8.0,
        mutation_rate=0.15,
    ))
    genome.add_gene(Gene(
        name="max_spread_pct",
        gene_type=GeneType.FLOAT,
        value=2.5,
        min_value=0.5,
        max_value=5.0,
        mutation_rate=0.15,
    ))
    genome.add_gene(Gene(
        name="min_delta",
        gene_type=GeneType.FLOAT,
        value=0.03,
        min_value=0.01,
        max_value=0.20,
        mutation_rate=0.15,
    ))
    genome.add_gene(Gene(
        name="min_gamma",
        gene_type=GeneType.FLOAT,
        value=0.0003,
        min_value=0.0001,
        max_value=0.0020,
        mutation_rate=0.15,
    ))

    # Risk/reward multipliers
    genome.add_gene(Gene(
        name="sl_multiplier",
        gene_type=GeneType.FLOAT,
        value=1.0,
        min_value=0.7,
        max_value=1.5,
        mutation_rate=0.2,
    ))
    genome.add_gene(Gene(
        name="target_multiplier",
        gene_type=GeneType.FLOAT,
        value=1.0,
        min_value=0.8,
        max_value=2.0,
        mutation_rate=0.2,
    ))

    # Trade frequency
    genome.add_gene(Gene(
        name="min_signal_gap",
        gene_type=GeneType.INT,
        value=30,
        min_value=5,
        max_value=120,
        mutation_rate=0.15,
    ))
    genome.add_gene(Gene(
        name="max_trades_per_hour",
        gene_type=GeneType.INT,
        value=10,
        min_value=3,
        max_value=30,
        mutation_rate=0.15,
    ))

    return genome


class FyersN7BacktestRunner:
    """Runs fast backtests for fitness evaluation."""

    POSITION_SIZE_PCT = 0.10
    TRADE_FEE = 40

    def __init__(self, indices: List[str] = None, capital: float = 100000):
        self.indices = indices or ["SENSEX", "NIFTY50"]
        self.initial_capital = capital
        self._signals_cache: Dict[str, List[Dict]] = {}
        self._load_signals()

    def _load_signals(self):
        """Pre-load all signals for speed."""
        logger.info("Loading signals for evolution...")
        dates = self._get_dates()

        for index in self.indices:
            all_signals = []
            for date in dates:
                journal_file = POSTMORTEM_BASE / date / index / "decision_journal.csv"
                if journal_file.exists():
                    signals = load_signal_csv(journal_file)
                    valid = [s for s in signals if to_float(s.get("entry", 0)) > 0]
                    all_signals.extend(valid)

            self._signals_cache[index] = all_signals
            logger.info(f"Loaded {len(all_signals)} signals for {index}")

    def _get_dates(self) -> List[str]:
        """Get available dates."""
        if not POSTMORTEM_BASE.exists():
            return []
        return sorted([
            d.name for d in POSTMORTEM_BASE.iterdir()
            if d.is_dir() and d.name.startswith("2026-")
        ])

    def run_backtest(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run fast backtest with given parameters.

        Returns metrics dict.
        """
        # Create fresh adapter (no cooldown for backtest)
        adapter = FyersN7SignalAdapter(
            min_score=int(params.get("min_score", 50)),
            min_confidence=int(params.get("min_confidence", 70)),
            min_signal_gap=1,  # No cooldown in backtest
            max_trades_per_hour=1000,  # No limit in backtest
        )

        # Override adapter thresholds
        adapter.MIN_VOTE_DIFF = params.get("min_vote_diff", 3.0)
        adapter.MAX_SPREAD_PCT = params.get("max_spread_pct", 2.5)
        adapter.MIN_DELTA = params.get("min_delta", 0.03)
        adapter.MIN_GAMMA = params.get("min_gamma", 0.0003)

        # Reset any cooldowns
        adapter._cooldowns = {}

        sl_mult = params.get("sl_multiplier", 1.0)
        target_mult = params.get("target_multiplier", 1.0)

        # Backtest state
        capital = self.initial_capital
        total_trades = 0
        winning_trades = 0
        losing_trades = 0
        total_pnl = 0.0
        max_drawdown = 0.0
        peak_capital = capital
        daily_returns = []

        # Process each index
        for index in self.indices:
            signals = self._signals_cache.get(index, [])
            open_position = None

            for i, row in enumerate(signals):
                # Process through adapter
                signal, should_trade, _, _ = adapter.process_signal_row(row, index)

                # Check exit for open position
                if open_position:
                    next_entry = to_float(row.get("entry", 0))
                    if next_entry > 0:
                        # Simple exit check
                        sl = open_position["sl"] * sl_mult
                        target = open_position["target"] * target_mult
                        side = open_position["side"]

                        if side == "CE":
                            if next_entry <= sl or next_entry >= target:
                                pnl = (next_entry - open_position["entry"]) - self.TRADE_FEE
                                total_pnl += pnl
                                capital += pnl
                                total_trades += 1
                                if pnl > 0:
                                    winning_trades += 1
                                else:
                                    losing_trades += 1
                                open_position = None
                        else:  # PE
                            if next_entry >= sl or next_entry <= target:
                                pnl = (open_position["entry"] - next_entry) - self.TRADE_FEE
                                total_pnl += pnl
                                capital += pnl
                                total_trades += 1
                                if pnl > 0:
                                    winning_trades += 1
                                else:
                                    losing_trades += 1
                                open_position = None

                    # Update drawdown
                    if capital > peak_capital:
                        peak_capital = capital
                    dd = (peak_capital - capital) / peak_capital
                    if dd > max_drawdown:
                        max_drawdown = dd

                # Open new position if criteria met
                if should_trade and open_position is None:
                    entry = signal.entry
                    sl_price = signal.sl if signal.sl > 0 else entry * (0.985 if signal.side == "CE" else 1.015)
                    target_price = signal.t1 if signal.t1 > 0 else entry * (1.025 if signal.side == "CE" else 0.975)

                    open_position = {
                        "side": signal.side,
                        "entry": entry,
                        "sl": sl_price,
                        "target": target_price,
                    }

            # Close any open position at end
            if open_position:
                total_trades += 1
                losing_trades += 1  # EOD exit counted as loss

        # Calculate metrics
        win_rate = winning_trades / max(total_trades, 1)

        avg_win = (total_pnl / winning_trades) if winning_trades > 0 else 0
        avg_loss = abs(total_pnl / losing_trades) if losing_trades > 0 else 1

        profit_factor = (winning_trades * avg_win) / max(losing_trades * avg_loss, 1) if avg_loss > 0 else 1.0

        # Simple Sharpe approximation
        returns_pct = total_pnl / self.initial_capital
        sharpe = returns_pct / max(max_drawdown, 0.01) if returns_pct > 0 else returns_pct

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe,
            "profit_factor": profit_factor,
            "daily_returns": daily_returns,
            "backtest_hours": 30,  # ~5 days * 6 hours
        }


def run_evolution(
    generations: int = 20,
    population_size: int = 20,
    indices: List[str] = None,
    capital: float = 100000,
):
    """Run genetic evolution to optimize FyersN7 parameters."""

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║   FyersN7 Genetic Evolution                                      ║
╠══════════════════════════════════════════════════════════════════╣
║   Evolving optimal parameters for FyersN7 signal adapter         ║
║   Generations: {generations:<4}  Population: {population_size:<4}                          ║
║   Indices: {', '.join(indices or ['SENSEX', 'NIFTY50']):<50} ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    # Create backtester
    backtester = FyersN7BacktestRunner(indices=indices, capital=capital)

    # Create pipeline
    pipeline = GeneticPipeline(
        population_size=population_size,
        genome_factory=create_fyersn7_genome,
        backtester=backtester.run_backtest,
    )

    # Initialize
    pipeline.initialize()

    # Progress callback
    def on_progress(gen: int, stats: Dict):
        logger.info(
            f"Gen {gen+1:2d}/{generations} | "
            f"Best: {stats.get('best_fitness', 0):.4f} | "
            f"Avg: {stats.get('avg_fitness', 0):.4f} | "
            f"Stagnation: {stats.get('stagnation', 0)}"
        )

    # Run evolution
    logger.info("Starting evolution...")
    start_time = time.time()

    result = pipeline.evolve(generations=generations, callback=on_progress)

    elapsed = time.time() - start_time

    # Results
    print("\n" + "=" * 70)
    print("EVOLUTION COMPLETE")
    print("=" * 70)
    print(f"  Generations: {result.generations}")
    print(f"  Total Evaluations: {result.total_evaluations}")
    print(f"  Duration: {elapsed:.1f}s")
    print(f"  Best Fitness: {result.best_fitness:.4f}")
    print(f"  Improvement: {result.improvement*100:.1f}%")
    print()

    # Best parameters
    best_params = result.best_genome.to_params()
    print("OPTIMAL PARAMETERS:")
    print("-" * 40)
    for name, value in sorted(best_params.items()):
        if isinstance(value, float):
            print(f"  {name}: {value:.4f}")
        else:
            print(f"  {name}: {value}")
    print("=" * 70)

    # Run final backtest with best params
    print("\nFINAL BACKTEST WITH OPTIMAL PARAMS:")
    print("-" * 40)
    final_result = backtester.run_backtest(best_params)
    print(f"  Total Trades: {final_result['total_trades']}")
    print(f"  Win Rate: {final_result['winning_trades']/max(final_result['total_trades'],1)*100:.1f}%")
    print(f"  Total P&L: Rs {final_result['total_pnl']:+,.0f}")
    print(f"  Max Drawdown: {final_result['max_drawdown']*100:.1f}%")
    print(f"  Profit Factor: {final_result['profit_factor']:.2f}")
    print("=" * 70)

    return result, best_params


def main():
    parser = argparse.ArgumentParser(description="FyersN7 Genetic Evolution")
    parser.add_argument("--generations", type=int, default=20,
                       help="Number of generations (default: 20)")
    parser.add_argument("--population", type=int, default=20,
                       help="Population size (default: 20)")
    parser.add_argument("--indices", nargs="+", default=["SENSEX", "NIFTY50"],
                       help="Indices to optimize for")
    parser.add_argument("--capital", type=float, default=100000,
                       help="Starting capital")

    args = parser.parse_args()

    try:
        run_evolution(
            generations=args.generations,
            population_size=args.population,
            indices=args.indices,
            capital=args.capital,
        )
    except KeyboardInterrupt:
        logger.info("\nEvolution interrupted")


if __name__ == "__main__":
    main()

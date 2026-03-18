"""
Feedback Loop - Closes the loop from execution back to learning.

Collects live trading results and feeds them back to:
- QuantLearnerAgent for pattern learning
- GeneticPipeline for fitness updates
"""

import time
import logging
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class FeedbackEntry:
    """Single feedback entry from live trading."""
    entry_id: str
    genome_id: str
    trade_id: str
    outcome: str  # "win", "loss", "scratch"
    pnl: float
    pnl_percent: float
    entry_price: float
    exit_price: float
    hold_duration: float
    regime: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


class FeedbackLoop:
    """
    Collects and processes feedback from live trading.

    Features:
    - Buffered collection
    - Aggregation by genome/strategy
    - Triggers learning updates
    """

    BUFFER_SIZE = 100
    MIN_FEEDBACK_FOR_UPDATE = 10

    def __init__(self, synapse=None):
        self._synapse = synapse
        self._buffer: deque = deque(maxlen=self.BUFFER_SIZE)
        self._by_genome: Dict[str, List[FeedbackEntry]] = {}
        self._update_callbacks: List[Callable] = []
        self._entry_counter = 0

    def register_callback(self, callback: Callable[[List[FeedbackEntry]], None]):
        """Register callback for when feedback batch is ready."""
        self._update_callbacks.append(callback)

    def record(
        self,
        genome_id: str,
        trade_id: str,
        outcome: str,
        pnl: float,
        pnl_percent: float,
        entry_price: float,
        exit_price: float,
        hold_duration: float,
        regime: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Record feedback from a trade."""
        self._entry_counter += 1

        entry = FeedbackEntry(
            entry_id=f"fb_{self._entry_counter:06d}",
            genome_id=genome_id,
            trade_id=trade_id,
            outcome=outcome,
            pnl=pnl,
            pnl_percent=pnl_percent,
            entry_price=entry_price,
            exit_price=exit_price,
            hold_duration=hold_duration,
            regime=regime,
            metadata=metadata or {},
        )

        self._buffer.append(entry)

        # Index by genome
        if genome_id not in self._by_genome:
            self._by_genome[genome_id] = []
        self._by_genome[genome_id].append(entry)

        logger.debug(f"Recorded feedback {entry.entry_id}: {outcome} {pnl_percent:.2%}")

        # Check if we should trigger update
        if len(self._buffer) >= self.MIN_FEEDBACK_FOR_UPDATE:
            self._trigger_update()

    def _trigger_update(self):
        """Trigger learning update with buffered feedback."""
        if not self._update_callbacks:
            return

        batch = list(self._buffer)

        for callback in self._update_callbacks:
            try:
                callback(batch)
            except Exception as e:
                logger.error(f"Feedback callback error: {e}")

    def get_genome_performance(self, genome_id: str) -> Dict:
        """Get performance summary for a genome."""
        entries = self._by_genome.get(genome_id, [])

        if not entries:
            return {"genome_id": genome_id, "trades": 0}

        wins = sum(1 for e in entries if e.outcome == "win")
        losses = sum(1 for e in entries if e.outcome == "loss")
        total_pnl = sum(e.pnl for e in entries)

        return {
            "genome_id": genome_id,
            "trades": len(entries),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / len(entries) if entries else 0,
            "total_pnl": total_pnl,
            "avg_pnl": total_pnl / len(entries) if entries else 0,
        }

    def get_regime_breakdown(self, genome_id: str) -> Dict[str, Dict]:
        """Get performance breakdown by regime."""
        entries = self._by_genome.get(genome_id, [])

        by_regime: Dict[str, Dict] = {}
        for entry in entries:
            regime = entry.regime
            if regime not in by_regime:
                by_regime[regime] = {"wins": 0, "losses": 0, "pnl": 0}

            by_regime[regime]["pnl"] += entry.pnl
            if entry.outcome == "win":
                by_regime[regime]["wins"] += 1
            elif entry.outcome == "loss":
                by_regime[regime]["losses"] += 1

        return by_regime

    def clear_genome(self, genome_id: str):
        """Clear feedback for a genome."""
        if genome_id in self._by_genome:
            del self._by_genome[genome_id]

    def get_stats(self) -> Dict:
        """Get feedback loop statistics."""
        total = len(self._buffer)
        wins = sum(1 for e in self._buffer if e.outcome == "win")

        return {
            "buffer_size": total,
            "genomes_tracked": len(self._by_genome),
            "win_rate": wins / total if total > 0 else 0,
            "total_entries": self._entry_counter,
        }

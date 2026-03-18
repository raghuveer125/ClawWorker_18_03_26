"""
Memory Agent - Stores and retrieves past experiences for reasoning.

Maintains memory of:
- Past goal outcomes (success/failure)
- Failure patterns and root causes
- Successful strategies by market regime
- Parameter configurations that worked
"""

import json
import time
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict

logger = logging.getLogger(__name__)


class MemoryType(Enum):
    """Types of memories stored."""
    GOAL_OUTCOME = "goal_outcome"
    FAILURE_PATTERN = "failure_pattern"
    SUCCESS_STRATEGY = "success_strategy"
    PARAMETER_CONFIG = "parameter_config"
    MARKET_INSIGHT = "market_insight"
    REGIME_BEHAVIOR = "regime_behavior"


@dataclass
class MemoryEntry:
    """A single memory entry."""
    memory_id: str
    memory_type: MemoryType
    timestamp: float
    content: Dict[str, Any]
    tags: List[str] = field(default_factory=list)
    relevance_score: float = 1.0
    access_count: int = 0
    last_accessed: float = 0

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["memory_type"] = self.memory_type.value
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "MemoryEntry":
        d["memory_type"] = MemoryType(d["memory_type"])
        return cls(**d)


class MemoryAgent:
    """
    Manages long-term memory for reasoning.

    Provides:
    - Storage of goal outcomes and patterns
    - Retrieval of relevant memories by context
    - Pattern detection from historical data
    - Memory consolidation and pruning
    """

    AGENT_TYPE = "memory"

    MAX_MEMORIES = 10000
    RELEVANCE_DECAY = 0.99  # Per day

    def __init__(self, storage_path: Optional[Path] = None):
        """
        Initialize memory agent.

        Args:
            storage_path: Path to persist memories (optional)
        """
        self.storage_path = storage_path
        self.memories: Dict[str, MemoryEntry] = {}
        self.index_by_type: Dict[MemoryType, List[str]] = defaultdict(list)
        self.index_by_tag: Dict[str, List[str]] = defaultdict(list)

        if storage_path and storage_path.exists():
            self._load_memories()

    def store(
        self,
        memory_type: MemoryType,
        content: Dict[str, Any],
        tags: Optional[List[str]] = None,
        relevance: float = 1.0,
    ) -> str:
        """
        Store a new memory.

        Args:
            memory_type: Type of memory
            content: Memory content
            tags: Tags for indexing
            relevance: Initial relevance score

        Returns:
            Memory ID
        """
        import hashlib

        memory_id = hashlib.sha256(
            f"{time.time()}{memory_type.value}{json.dumps(content, sort_keys=True)}".encode()
        ).hexdigest()[:12]

        entry = MemoryEntry(
            memory_id=memory_id,
            memory_type=memory_type,
            timestamp=time.time(),
            content=content,
            tags=tags or [],
            relevance_score=relevance,
        )

        self.memories[memory_id] = entry
        self.index_by_type[memory_type].append(memory_id)

        for tag in entry.tags:
            self.index_by_tag[tag].append(memory_id)

        # Prune if needed
        if len(self.memories) > self.MAX_MEMORIES:
            self._prune_memories()

        logger.debug(f"Stored memory {memory_id}: type={memory_type.value}")
        return memory_id

    def store_goal_outcome(
        self,
        goal_id: str,
        goal_type: str,
        success: bool,
        pnl: Optional[float] = None,
        regime: Optional[str] = None,
        parameters: Optional[Dict] = None,
        failure_reason: Optional[str] = None,
    ) -> str:
        """Store outcome of a goal execution."""
        content = {
            "goal_id": goal_id,
            "goal_type": goal_type,
            "success": success,
            "pnl": pnl,
            "regime": regime,
            "parameters": parameters or {},
            "failure_reason": failure_reason,
        }

        tags = [goal_type, regime or "unknown_regime"]
        if success:
            tags.append("success")
        else:
            tags.append("failure")

        return self.store(
            MemoryType.GOAL_OUTCOME,
            content,
            tags,
            relevance=1.5 if not success else 1.0  # Failures more important
        )

    def store_failure_pattern(
        self,
        pattern_name: str,
        conditions: Dict[str, Any],
        failure_type: str,
        occurrences: int = 1,
        mitigation: Optional[str] = None,
    ) -> str:
        """Store a detected failure pattern."""
        content = {
            "pattern_name": pattern_name,
            "conditions": conditions,
            "failure_type": failure_type,
            "occurrences": occurrences,
            "mitigation": mitigation,
        }

        return self.store(
            MemoryType.FAILURE_PATTERN,
            content,
            tags=[failure_type, pattern_name],
            relevance=2.0  # High importance
        )

    def store_success_strategy(
        self,
        strategy_name: str,
        regime: str,
        parameters: Dict[str, Any],
        win_rate: float,
        avg_pnl: float,
        sample_size: int,
    ) -> str:
        """Store a successful strategy configuration."""
        content = {
            "strategy_name": strategy_name,
            "regime": regime,
            "parameters": parameters,
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "sample_size": sample_size,
        }

        return self.store(
            MemoryType.SUCCESS_STRATEGY,
            content,
            tags=[strategy_name, regime, "successful"],
            relevance=1.0 + (win_rate * sample_size / 100)  # Higher for proven strategies
        )

    def recall(
        self,
        memory_type: Optional[MemoryType] = None,
        tags: Optional[List[str]] = None,
        limit: int = 10,
        min_relevance: float = 0.1,
    ) -> List[MemoryEntry]:
        """
        Recall memories by type and/or tags.

        Args:
            memory_type: Filter by type
            tags: Filter by tags (OR logic)
            limit: Maximum memories to return
            min_relevance: Minimum relevance threshold

        Returns:
            List of matching memories, sorted by relevance
        """
        candidates = set()

        if memory_type:
            candidates.update(self.index_by_type.get(memory_type, []))
        elif tags:
            for tag in tags:
                candidates.update(self.index_by_tag.get(tag, []))
        else:
            candidates = set(self.memories.keys())

        # Filter by tags if both type and tags specified
        if memory_type and tags:
            tag_matches = set()
            for tag in tags:
                tag_matches.update(self.index_by_tag.get(tag, []))
            candidates = candidates.intersection(tag_matches)

        # Get entries and filter
        entries = []
        for mid in candidates:
            entry = self.memories.get(mid)
            if entry and entry.relevance_score >= min_relevance:
                entries.append(entry)

        # Sort by relevance (with recency boost)
        now = time.time()
        entries.sort(
            key=lambda e: e.relevance_score * (1 + 1 / (1 + (now - e.timestamp) / 86400)),
            reverse=True
        )

        # Update access stats
        result = entries[:limit]
        for entry in result:
            entry.access_count += 1
            entry.last_accessed = now

        return result

    def recall_for_context(
        self,
        goal_type: str,
        regime: Optional[str] = None,
        targets: Optional[List[str]] = None,
    ) -> Dict[str, List[MemoryEntry]]:
        """
        Recall all relevant memories for a given context.

        Returns organized memories for reasoning.
        """
        tags = [goal_type]
        if regime:
            tags.append(regime)
        if targets:
            tags.extend(targets)

        result = {
            "past_outcomes": self.recall(MemoryType.GOAL_OUTCOME, tags, limit=5),
            "failure_patterns": self.recall(MemoryType.FAILURE_PATTERN, tags, limit=3),
            "success_strategies": self.recall(MemoryType.SUCCESS_STRATEGY, tags, limit=3),
            "regime_insights": self.recall(MemoryType.REGIME_BEHAVIOR, [regime] if regime else None, limit=2),
        }

        return result

    def get_failure_rate(
        self,
        goal_type: str,
        regime: Optional[str] = None,
        lookback_days: int = 7,
    ) -> Tuple[float, int]:
        """
        Calculate failure rate for goal type.

        Returns:
            (failure_rate, sample_size)
        """
        cutoff = time.time() - (lookback_days * 86400)

        outcomes = self.recall(MemoryType.GOAL_OUTCOME, [goal_type], limit=100)
        recent = [o for o in outcomes if o.timestamp > cutoff]

        if regime:
            recent = [o for o in recent if o.content.get("regime") == regime]

        if not recent:
            return 0.0, 0

        failures = sum(1 for o in recent if not o.content.get("success"))
        return failures / len(recent), len(recent)

    def detect_patterns(self, memory_type: MemoryType = MemoryType.GOAL_OUTCOME) -> List[Dict]:
        """
        Detect patterns in stored memories.

        Returns list of detected patterns.
        """
        patterns = []
        memories = self.recall(memory_type, limit=100)

        if memory_type == MemoryType.GOAL_OUTCOME:
            # Group by regime and goal type
            by_regime = defaultdict(list)
            for m in memories:
                key = (m.content.get("regime"), m.content.get("goal_type"))
                by_regime[key].append(m)

            for (regime, goal_type), items in by_regime.items():
                if len(items) >= 3:
                    success_rate = sum(1 for i in items if i.content.get("success")) / len(items)
                    patterns.append({
                        "type": "regime_performance",
                        "regime": regime,
                        "goal_type": goal_type,
                        "success_rate": success_rate,
                        "sample_size": len(items),
                    })

        return patterns

    def _prune_memories(self):
        """Remove low-relevance old memories."""
        # Apply time decay
        now = time.time()
        for entry in self.memories.values():
            days_old = (now - entry.timestamp) / 86400
            entry.relevance_score *= (self.RELEVANCE_DECAY ** days_old)

        # Remove lowest relevance
        if len(self.memories) > self.MAX_MEMORIES:
            sorted_entries = sorted(
                self.memories.values(),
                key=lambda e: e.relevance_score
            )
            to_remove = sorted_entries[:len(self.memories) - self.MAX_MEMORIES + 100]

            for entry in to_remove:
                self._remove_memory(entry.memory_id)

        logger.info(f"Pruned memories, remaining: {len(self.memories)}")

    def _remove_memory(self, memory_id: str):
        """Remove a memory and its index entries."""
        entry = self.memories.pop(memory_id, None)
        if entry:
            if memory_id in self.index_by_type[entry.memory_type]:
                self.index_by_type[entry.memory_type].remove(memory_id)
            for tag in entry.tags:
                if memory_id in self.index_by_tag[tag]:
                    self.index_by_tag[tag].remove(memory_id)

    def _load_memories(self):
        """Load memories from storage."""
        if not self.storage_path:
            return

        try:
            with open(self.storage_path, "r") as f:
                data = json.load(f)

            for d in data.get("memories", []):
                entry = MemoryEntry.from_dict(d)
                self.memories[entry.memory_id] = entry
                self.index_by_type[entry.memory_type].append(entry.memory_id)
                for tag in entry.tags:
                    self.index_by_tag[tag].append(entry.memory_id)

            logger.info(f"Loaded {len(self.memories)} memories from storage")

        except Exception as e:
            logger.error(f"Error loading memories: {e}")

    def save(self):
        """Save memories to storage."""
        if not self.storage_path:
            return

        try:
            data = {
                "memories": [m.to_dict() for m in self.memories.values()],
                "saved_at": time.time(),
            }

            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "w") as f:
                json.dump(data, f, indent=2)

            logger.info(f"Saved {len(self.memories)} memories")

        except Exception as e:
            logger.error(f"Error saving memories: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        return {
            "total_memories": len(self.memories),
            "by_type": {t.value: len(ids) for t, ids in self.index_by_type.items()},
            "unique_tags": len(self.index_by_tag),
        }

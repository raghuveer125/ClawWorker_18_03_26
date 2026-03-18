"""
Debate Cache - Avoid repeated LLM reasoning calls.

Used by learning agents (QuantLearner, StrategyOptimizer) to cache debate results.
TTL: 24 hours by default.

Benefits:
- Avoids repeated LLM calls for same conditions
- Speeds up learning cycles
- Lowers API costs
"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass
class CacheEntry:
    """A single cache entry."""
    key: str
    result: Dict[str, Any]
    confidence: float
    created_at: str
    expires_at: str
    hit_count: int = 0


class DebateCache:
    """
    LRU cache for debate results with TTL.

    Features:
    - Hash-based key generation from conditions
    - Configurable TTL (default 24h)
    - In-memory + optional file persistence
    - Hit rate tracking for optimization
    """

    def __init__(
        self,
        ttl_hours: float = 24.0,
        max_entries: int = 1000,
        persist_path: Optional[str] = None,
    ):
        self.ttl = timedelta(hours=ttl_hours)
        self.max_entries = max_entries
        self.persist_path = persist_path

        self._cache: Dict[str, CacheEntry] = {}
        self._hits = 0
        self._misses = 0

        # Load from disk if exists
        if persist_path:
            self._load_from_disk()

    def _generate_key(self, conditions: Dict[str, Any]) -> str:
        """Generate a unique key from debate conditions."""
        # Sort keys for consistent hashing
        sorted_str = json.dumps(conditions, sort_keys=True, default=str)
        return hashlib.sha256(sorted_str.encode()).hexdigest()[:16]

    def get(self, conditions: Dict[str, Any]) -> Optional[Tuple[Dict, float]]:
        """
        Get cached debate result if exists and not expired.

        Returns:
            Tuple of (result, confidence) or None if miss
        """
        key = self._generate_key(conditions)
        entry = self._cache.get(key)

        if entry is None:
            self._misses += 1
            return None

        # Check expiry
        expires = datetime.fromisoformat(entry.expires_at)
        if datetime.now() > expires:
            del self._cache[key]
            self._misses += 1
            return None

        # Cache hit
        entry.hit_count += 1
        self._hits += 1
        return (entry.result, entry.confidence)

    def set(
        self,
        conditions: Dict[str, Any],
        result: Dict[str, Any],
        confidence: float,
    ) -> str:
        """
        Cache a debate result.

        Returns:
            The cache key
        """
        key = self._generate_key(conditions)
        now = datetime.now()

        entry = CacheEntry(
            key=key,
            result=result,
            confidence=confidence,
            created_at=now.isoformat(),
            expires_at=(now + self.ttl).isoformat(),
        )

        self._cache[key] = entry

        # Evict oldest if over limit
        if len(self._cache) > self.max_entries:
            self._evict_oldest()

        # Persist if enabled
        if self.persist_path:
            self._save_to_disk()

        return key

    def _evict_oldest(self):
        """Remove oldest entries when cache is full."""
        if not self._cache:
            return

        # Sort by created_at and remove oldest 10%
        sorted_entries = sorted(
            self._cache.items(),
            key=lambda x: x[1].created_at
        )

        to_remove = max(1, len(sorted_entries) // 10)
        for key, _ in sorted_entries[:to_remove]:
            del self._cache[key]

    def invalidate(self, conditions: Dict[str, Any]) -> bool:
        """Invalidate a specific cache entry."""
        key = self._generate_key(conditions)
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self):
        """Clear all cache entries."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0

        return {
            "entries": len(self._cache),
            "max_entries": self.max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_pct": round(hit_rate, 2),
            "ttl_hours": self.ttl.total_seconds() / 3600,
        }

    def _save_to_disk(self):
        """Persist cache to disk."""
        if not self.persist_path:
            return

        try:
            path = Path(self.persist_path)
            path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                key: {
                    "key": e.key,
                    "result": e.result,
                    "confidence": e.confidence,
                    "created_at": e.created_at,
                    "expires_at": e.expires_at,
                    "hit_count": e.hit_count,
                }
                for key, e in self._cache.items()
            }

            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[DebateCache] Failed to save: {e}")

    def _load_from_disk(self):
        """Load cache from disk."""
        if not self.persist_path:
            return

        try:
            path = Path(self.persist_path)
            if not path.exists():
                return

            with open(path, 'r') as f:
                data = json.load(f)

            now = datetime.now()
            for key, entry_data in data.items():
                # Skip expired entries
                expires = datetime.fromisoformat(entry_data["expires_at"])
                if now > expires:
                    continue

                self._cache[key] = CacheEntry(
                    key=entry_data["key"],
                    result=entry_data["result"],
                    confidence=entry_data["confidence"],
                    created_at=entry_data["created_at"],
                    expires_at=entry_data["expires_at"],
                    hit_count=entry_data.get("hit_count", 0),
                )

            print(f"[DebateCache] Loaded {len(self._cache)} entries from disk")
        except Exception as e:
            print(f"[DebateCache] Failed to load: {e}")


# Global cache instance for learning agents
_debate_cache: Optional[DebateCache] = None


def get_debate_cache() -> DebateCache:
    """Get or create the global debate cache."""
    global _debate_cache
    if _debate_cache is None:
        # Default path for persistence
        cache_path = os.environ.get(
            "DEBATE_CACHE_PATH",
            str(Path(__file__).parent.parent / "data" / "debate_cache.json")
        )
        _debate_cache = DebateCache(
            ttl_hours=24.0,
            max_entries=1000,
            persist_path=cache_path,
        )
    return _debate_cache


def should_trigger_debate(
    model_confidence: float,
    threshold: float = 0.65,
    conditions: Optional[Dict] = None,
) -> Tuple[bool, Optional[Dict]]:
    """
    Check if debate should be triggered based on confidence threshold.

    Implements the hedge fund pattern:
    - If confidence >= threshold: skip debate (decision is obvious)
    - If confidence < threshold: check cache, then trigger if needed

    Args:
        model_confidence: The model's confidence in its decision (0-1)
        threshold: Minimum confidence to skip debate (default 0.65)
        conditions: Optional conditions to check cache

    Returns:
        Tuple of (should_debate, cached_result)
    """
    # High confidence = skip debate
    if model_confidence >= threshold:
        return (False, None)

    # Low confidence - check cache first
    if conditions:
        cache = get_debate_cache()
        cached = cache.get(conditions)
        if cached:
            result, conf = cached
            return (False, result)  # Use cached result

    # No cache hit - trigger debate
    return (True, None)

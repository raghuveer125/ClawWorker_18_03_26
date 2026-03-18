from __future__ import annotations

from typing import Dict

from contracts import MemoryState


class AgentMemory:
    def __init__(self):
        self._states: Dict[str, MemoryState] = {
            "trend_strength": MemoryState(key="trend_strength", values=[]),
            "underlying_change_pct": MemoryState(key="underlying_change_pct", values=[]),
        }

    def update(self, trend_strength: float, underlying_change_pct: float) -> None:
        self._states["trend_strength"].append(trend_strength)
        self._states["underlying_change_pct"].append(underlying_change_pct)

    def snapshot(self) -> Dict[str, object]:
        trend_avg = self._states["trend_strength"].average()
        change_avg = self._states["underlying_change_pct"].average()
        return {
            "trend_strength_avg": round(trend_avg, 4) if trend_avg is not None else None,
            "underlying_change_pct_avg": round(change_avg, 4) if change_avg is not None else None,
            "window_size": len(self._states["trend_strength"].values),
            "retention_policy": "rolling_window_20",
        }

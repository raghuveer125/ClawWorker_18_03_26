"""
TradingEngine — abstract base class for all ClawWorker trading engines.

Every engine (scalping, fyersn7, clawwork-autotrader, etc.) should implement
this interface so they can be managed, monitored, and tested uniformly.

Usage:
    class ScalpingEngine(TradingEngine):
        async def start(self) -> None: ...
        async def stop(self) -> None: ...
        ...
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class EngineMode(Enum):
    """Operating mode for an engine."""
    IDLE = "idle"
    PAPER = "paper"
    LIVE = "live"
    REPLAY = "replay"


class EngineHealth(Enum):
    """Health status of an engine."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"


@dataclass
class EngineStatus:
    """Snapshot of engine runtime state."""
    engine_name: str
    mode: EngineMode
    health: EngineHealth
    uptime_seconds: float = 0.0
    trades_today: int = 0
    pnl_today: float = 0.0
    open_positions: int = 0
    last_signal_time: Optional[datetime] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class TradingEngine(ABC):
    """
    Abstract base class for all trading engines.

    Provides a uniform lifecycle interface:
        start() → get_status() → stop()

    And configuration access:
        get_config() → Dict of current configuration
        engine_name → human-readable name
    """

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Short identifier for this engine (e.g. 'scalping', 'fyersn7')."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """Start the engine. Idempotent — safe to call if already running."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully stop the engine. Idempotent."""
        ...

    @abstractmethod
    def get_status(self) -> EngineStatus:
        """Return a point-in-time snapshot of engine state."""
        ...

    @abstractmethod
    def get_config(self) -> Dict[str, Any]:
        """Return the current engine configuration as a flat dict."""
        ...

    def is_running(self) -> bool:
        """Convenience: True if engine is in an active mode."""
        status = self.get_status()
        return status.health not in (EngineHealth.STOPPED, EngineHealth.UNHEALTHY)

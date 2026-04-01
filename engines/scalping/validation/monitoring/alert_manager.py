"""
Alert management system for the scalping validation pipeline.

Supports three severity levels (CRITICAL, WARNING, INFO), per-alert
cooldown to prevent spam, and three output channels:

  1. Console (colour-coded)
  2. JSON log file (append)
  3. Webhook (async POST, non-blocking)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

from config.settings import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class AlertLevel(Enum):
    """Alert severity — drives console colour and routing decisions."""

    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass(frozen=True)
class Alert:
    """Immutable record of a fired alert."""

    level: AlertLevel
    category: str
    message: str
    component: str
    timestamp: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["level"] = self.level.value
        return result


# ---------------------------------------------------------------------------
# Console colour helpers (ANSI)
# ---------------------------------------------------------------------------

_COLOURS: Dict[AlertLevel, str] = {
    AlertLevel.CRITICAL: "\033[91m",  # red
    AlertLevel.WARNING: "\033[93m",   # yellow
    AlertLevel.INFO: "\033[94m",      # blue
}
_RESET = "\033[0m"


def _coloured(level: AlertLevel, text: str) -> str:
    return f"{_COLOURS.get(level, '')}{text}{_RESET}"


# ---------------------------------------------------------------------------
# AlertManager
# ---------------------------------------------------------------------------

_MAX_RECENT_ALERTS = 100


class AlertManager:
    """Central alert dispatcher with cooldown and multi-channel output.

    Usage::

        mgr = AlertManager(settings)
        await mgr.fire(AlertLevel.CRITICAL, "kafka", "No brokers available")
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cooldown_sec: float = settings.alert_cooldown_sec
        self._webhook_url: str = settings.webhook_url
        self._log_file: str = settings.log_file

        # (category, component) -> last_fire_epoch
        self._cooldowns: Dict[Tuple[str, str], float] = {}

        # Recent alerts ring buffer
        self._recent: Deque[Alert] = deque(maxlen=_MAX_RECENT_ALERTS)

        # Counters by severity
        self._counts: Dict[str, int] = {
            AlertLevel.CRITICAL.value: 0,
            AlertLevel.WARNING.value: 0,
            AlertLevel.INFO.value: 0,
        }

    # -- Public API ---------------------------------------------------------

    async def fire(
        self,
        level: AlertLevel,
        category: str,
        message: str,
        component: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[Alert]:
        """Fire an alert if the cooldown for *(category, component)* has elapsed.

        Returns the ``Alert`` if it was actually dispatched, or ``None``
        when suppressed by cooldown.
        """
        now = time.time()
        cooldown_key = (category, component)
        last_fired = self._cooldowns.get(cooldown_key, 0.0)

        if (now - last_fired) < self._cooldown_sec:
            return None

        self._cooldowns[cooldown_key] = now

        alert = Alert(
            level=level,
            category=category,
            message=message,
            component=component,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            details=details or {},
        )

        self._recent.append(alert)
        self._counts[level.value] = self._counts.get(level.value, 0) + 1

        # Dispatch to all channels concurrently
        await asyncio.gather(
            self._emit_console(alert),
            self._emit_log_file(alert),
            self._emit_webhook(alert),
            return_exceptions=True,
        )

        return alert

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """Return the most recent alerts (up to 100) as dicts."""
        return [a.to_dict() for a in self._recent]

    def get_alert_summary(self) -> Dict[str, int]:
        """Return total counts keyed by severity level."""
        return dict(self._counts)

    # -- Output channels -----------------------------------------------------

    async def _emit_console(self, alert: Alert) -> None:
        """Print colour-coded alert to stdout."""
        tag = _coloured(alert.level, f"[{alert.level.value}]")
        comp = f" [{alert.component}]" if alert.component else ""
        print(f"{tag}{comp} {alert.category}: {alert.message}")

    async def _emit_log_file(self, alert: Alert) -> None:
        """Append alert as a single JSON line to the configured log file."""
        try:
            log_dir = os.path.dirname(self._log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)

            line = json.dumps(alert.to_dict(), default=str) + "\n"
            with open(self._log_file, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            logger.exception("Failed to write alert to log file %s", self._log_file)

    async def _emit_webhook(self, alert: Alert) -> None:
        """POST alert JSON to the configured webhook URL (non-blocking).

        Silently ignores failures — alerts should never block the pipeline.
        """
        if not self._webhook_url:
            return

        try:
            import aiohttp  # optional dependency

            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await session.post(
                    self._webhook_url,
                    json=alert.to_dict(),
                    headers={"Content-Type": "application/json"},
                )
        except ImportError:
            # aiohttp not installed — fall back to a fire-and-forget urllib call
            # in a thread so we don't block the loop.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._post_sync, alert)
        except Exception:
            logger.debug("Webhook delivery failed (non-fatal)", exc_info=True)

    def _post_sync(self, alert: Alert) -> None:
        """Blocking POST via stdlib — used as fallback when aiohttp is absent."""
        import urllib.request

        try:
            payload = json.dumps(alert.to_dict(), default=str).encode("utf-8")
            req = urllib.request.Request(
                self._webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception:
            logger.debug("Webhook sync fallback failed (non-fatal)", exc_info=True)

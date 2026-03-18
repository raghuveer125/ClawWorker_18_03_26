import json
import os
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import fcntl


DEFAULT_AUTO_TRADER_STRATEGY_ID = "clawwork-autotrader"
DEFAULT_LEGACY_RUNNER_STRATEGY_ID = "legacy-ict-runner"


def normalize_strategy_id(value: Optional[str], default: str) -> str:
    """Normalize strategy ids so they are safe for filesystem paths."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip().lower())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("._-")
    return cleaned or default


def resolve_strategy_component_dir(root_dir: Path, strategy_id: str, component: str) -> Path:
    """Return a strategy-scoped directory for a given component."""
    return Path(root_dir) / strategy_id / component


class StrategyConflictError(RuntimeError):
    """Raised when another process already owns the requested strategy namespace."""


class StrategyRuntimeLock:
    """Cross-process lock that prevents multiple runtimes from claiming one strategy."""

    def __init__(self, runtime_dir: Path, strategy_id: str, component: str):
        self.runtime_dir = Path(runtime_dir)
        self.strategy_id = strategy_id
        self.component = component
        self.lock_path = self.runtime_dir / ".runtime.lock"
        self.metadata_path = self.runtime_dir / "runtime.json"
        self._handle = None

    def acquire(self, extra_metadata: Optional[Dict[str, Any]] = None) -> None:
        if self._handle is not None:
            return

        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            existing = self._read_metadata()
            details = []
            if existing.get("pid"):
                details.append(f"pid={existing['pid']}")
            if existing.get("component"):
                details.append(f"component={existing['component']}")
            if existing.get("started_at"):
                details.append(f"started_at={existing['started_at']}")
            suffix = f" ({', '.join(details)})" if details else ""
            raise StrategyConflictError(
                f"strategy '{self.strategy_id}' is already active{suffix}"
            ) from exc

        self._handle = handle
        metadata = {
            "strategy_id": self.strategy_id,
            "component": self.component,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": datetime.now().isoformat(),
            "runtime_dir": str(self.runtime_dir),
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        self.metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def _read_metadata(self) -> Dict[str, Any]:
        if not self.metadata_path.exists():
            return {}
        try:
            return json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

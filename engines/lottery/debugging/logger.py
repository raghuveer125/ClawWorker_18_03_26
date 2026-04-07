"""Structured JSON logger — TRACE/DEBUG/INFO/WARN/ERROR with cycle metadata.

Outputs JSON lines for machine parsing.
Supports custom TRACE level below DEBUG.
Log directory includes symbol for multi-instrument isolation.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import LogLevel, LotteryConfig

# Custom TRACE level (below DEBUG=10)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


class JsonFormatter(logging.Formatter):
    """JSON-structured log formatter for machine parsing."""

    def __init__(self, symbol: str = "") -> None:
        super().__init__()
        self._symbol = symbol

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "symbol": self._symbol,
            "module": record.module,
            "func": record.funcName,
            "msg": record.getMessage(),
        }

        # Attach extra structured data if present
        if hasattr(record, "cycle_id"):
            entry["cycle_id"] = record.cycle_id
        if hasattr(record, "snapshot_id"):
            entry["snapshot_id"] = record.snapshot_id
        if hasattr(record, "data"):
            entry["data"] = record.data

        if record.exc_info and record.exc_info[1]:
            entry["error"] = str(record.exc_info[1])
            entry["error_type"] = type(record.exc_info[1]).__name__

        return json.dumps(entry, default=str)


class PlainFormatter(logging.Formatter):
    """Simple human-readable formatter for terminal output."""

    def __init__(self, symbol: str = "") -> None:
        super().__init__()
        self._symbol = symbol

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        level = record.levelname[:4]
        sym = f"[{self._symbol}]" if self._symbol else ""
        return f"{ts} {level:4s} {sym} {record.getMessage()}"


_LOG_LEVEL_MAP = {
    LogLevel.TRACE: TRACE,
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARN: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
}


def setup_logger(
    config: LotteryConfig,
    symbol: str,
    name: str = "lottery",
) -> logging.Logger:
    """Configure the lottery pipeline logger.

    Creates:
    - Console handler (always, plain format)
    - File handler (if log_dir configured, JSON format)

    Args:
        config: Lottery config with logging settings.
        symbol: Instrument symbol for log isolation.
        name: Logger name.

    Returns:
        Configured logger instance.
    """
    log_cfg = config.logging
    level = _LOG_LEVEL_MAP.get(log_cfg.level, logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Clear existing handlers to avoid duplicates on re-init
    logger.handlers.clear()

    # Console handler — always plain text
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(PlainFormatter(symbol=symbol))
    logger.addHandler(console)

    # File handler — JSON if configured
    if log_cfg.log_dir:
        _ENGINE_ROOT = Path(__file__).resolve().parents[1]
        log_path = Path(log_cfg.log_dir)
        if not log_path.is_absolute():
            # Resolve relative to engine root, strip prefix if present
            path_str = str(log_path)
            for prefix in ("engines/lottery/", "engines\\lottery\\"):
                if path_str.startswith(prefix):
                    path_str = path_str[len(prefix):]
                    break
            log_path = _ENGINE_ROOT / path_str
        log_dir = log_path / symbol.upper()
        log_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = log_dir / f"lottery_{today}.jsonl"

        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(level)

        if log_cfg.json_output:
            file_handler.setFormatter(JsonFormatter(symbol=symbol))
        else:
            file_handler.setFormatter(PlainFormatter(symbol=symbol))

        logger.addHandler(file_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def log_cycle(
    logger: logging.Logger,
    cycle_id: str,
    snapshot_id: str,
    data: dict,
) -> None:
    """Log a structured cycle summary.

    Args:
        logger: The lottery logger.
        cycle_id: Current cycle ID.
        snapshot_id: Current snapshot ID.
        data: Structured cycle data dict.
    """
    record = logger.makeRecord(
        name=logger.name,
        level=logging.INFO,
        fn="",
        lno=0,
        msg=f"cycle {cycle_id}",
        args=(),
        exc_info=None,
    )
    record.cycle_id = cycle_id
    record.snapshot_id = snapshot_id
    record.data = data
    logger.handle(record)

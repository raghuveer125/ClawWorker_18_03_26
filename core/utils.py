"""
Shared utility functions for ClawWorker.

Consolidates to_float, to_int, parse_dt, ensure_csv, append_csv
that were previously duplicated across 15+ fyersN7 scripts.
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Type coercion ────────────────────────────────────────────────────────────

def to_float(v: Any, default: float = 0.0) -> float:
    """Convert *v* to float, returning *default* on failure."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def to_float_opt(v: Any) -> Optional[float]:
    """Convert *v* to float, returning None on failure (for forensics)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_int(v: Any, default: int = 0) -> int:
    """Convert *v* to int (via float), returning *default* on failure."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def to_int_opt(v: Any) -> Optional[int]:
    """Convert *v* to int, returning None on failure (for forensics)."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ── Datetime parsing ─────────────────────────────────────────────────────────

_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def parse_dt(date_s: str, time_s: str) -> Optional[dt.datetime]:
    """Parse a (date, time) pair into a datetime, or None on failure."""
    raw = f"{(date_s or '').strip()} {(time_s or '').strip()}".strip()
    for fmt in _DT_FORMATS:
        try:
            return dt.datetime.strptime(raw, fmt)
        except (ValueError, TypeError):
            pass
    return None


# ── CSV helpers ──────────────────────────────────────────────────────────────

def ensure_csv(path: str, headers: List[str]) -> None:
    """Create a CSV file with *headers* if it does not already exist."""
    if Path(path).exists():
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()


def append_csv(path: str, headers: List[str], row: Dict[str, Any]) -> None:
    """Append a single *row* to the CSV at *path*, creating the file if needed."""
    ensure_csv(path, headers)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writerow({k: row.get(k, "") for k in headers})

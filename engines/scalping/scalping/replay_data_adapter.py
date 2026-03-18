"""
Historical replay adapter for the scalping engine.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SYMBOL_MAP = {
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


@dataclass
class ReplaySymbolSnapshot:
    symbol: str
    raw_symbol: str
    timestamp: str
    spot: float
    vix: float
    fut_ltp: float
    fut_basis: float
    fut_basis_pct: float
    rows: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ReplaySnapshot:
    timestamp: str
    symbols: Dict[str, ReplaySymbolSnapshot]
    raw_rows: List[Dict[str, Any]]
    events: List[Dict[str, Any]] = field(default_factory=list)


class ReplayDataAdapter:
    """Sequentially replays decision journal rows grouped by timestamp."""

    def __init__(self, csv_path: str, interval_ms: int = 200):
        self.csv_path = Path(csv_path)
        self.interval_ms = interval_ms
        self._batches: List[ReplaySnapshot] = self._load_batches()
        self._cursor = 0

    def _load_batches(self) -> List[ReplaySnapshot]:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"Replay CSV not found: {self.csv_path}")

        grouped: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
        with self.csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                key = (str(row.get("date", "")).strip(), str(row.get("time", "")).strip())
                grouped.setdefault(key, []).append(row)

        batches: List[ReplaySnapshot] = []
        for (date_s, time_s), rows in sorted(grouped.items()):
            timestamp = f"{date_s}T{time_s}"
            symbols: Dict[str, ReplaySymbolSnapshot] = {}
            events: List[Dict[str, Any]] = []
            for row in rows:
                raw_symbol = str(row.get("symbol", "")).strip().upper()
                symbol = SYMBOL_MAP.get(raw_symbol, raw_symbol)
                events.append(
                    {
                        "event_type": "market_event",
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "spot": _to_float(row.get("spot"), 0.0),
                        "vix": _to_float(row.get("vix"), 15.0),
                        "bid": _to_float(row.get("bid"), 0.0),
                        "ask": _to_float(row.get("ask"), 0.0),
                        "volume": _to_int(row.get("volume"), 0),
                        "oi": _to_int(row.get("oi"), 0),
                        "fut_ltp": _to_float(row.get("fut_ltp"), 0.0),
                        "fut_basis": _to_float(row.get("fut_basis"), 0.0),
                    }
                )
                snapshot = symbols.get(symbol)
                if snapshot is None:
                    snapshot = ReplaySymbolSnapshot(
                        symbol=symbol,
                        raw_symbol=raw_symbol,
                        timestamp=timestamp,
                        spot=_to_float(row.get("spot"), 0.0),
                        vix=_to_float(row.get("vix"), 15.0),
                        fut_ltp=_to_float(row.get("fut_ltp"), 0.0),
                        fut_basis=_to_float(row.get("fut_basis"), 0.0),
                        fut_basis_pct=_to_float(row.get("fut_basis_pct"), 0.0),
                    )
                    symbols[symbol] = snapshot
                snapshot.rows.append(dict(row))

            batches.append(ReplaySnapshot(timestamp=timestamp, symbols=symbols, raw_rows=[dict(r) for r in rows], events=events))
        return batches

    def has_next(self) -> bool:
        return self._cursor < len(self._batches)

    def has_previous(self) -> bool:
        return self._cursor > 0

    def next_snapshot(self) -> Optional[ReplaySnapshot]:
        if not self.has_next():
            return None
        snapshot = self._batches[self._cursor]
        self._cursor += 1
        return snapshot

    def previous_snapshot(self) -> Optional[ReplaySnapshot]:
        if not self.has_previous():
            return None
        self._cursor -= 1
        return self._batches[self._cursor]

    def step(self, direction: int = 1) -> Optional[ReplaySnapshot]:
        if direction >= 0:
            return self.next_snapshot()
        return self.previous_snapshot()

    def rewind(self) -> None:
        self._cursor = 0

    def seek(self, index: int) -> None:
        if not self._batches:
            self._cursor = 0
            return
        self._cursor = max(0, min(int(index), len(self._batches) - 1))

    def remaining(self) -> int:
        return max(0, len(self._batches) - self._cursor)

    def total_batches(self) -> int:
        return len(self._batches)

    def current_index(self) -> int:
        return self._cursor

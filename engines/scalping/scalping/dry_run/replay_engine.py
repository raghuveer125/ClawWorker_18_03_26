"""Replay engine — reads historical data and replays at configurable speed."""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .market_data_producer import MarketDataProducer
from . import kafka_config as bus


class ReplayEngine:
    """Replays historical ticks from JSON/CSV files at configurable speed."""

    def __init__(self, speed: float = 1.0) -> None:
        self.speed = max(0.1, speed)
        self.producer = MarketDataProducer()
        self._ticks: List[Dict[str, Any]] = []
        self._position = 0
        self._paused = False
        self._stopped = False
        self.total_ticks = 0

    def load_json(self, path: str) -> int:
        data = json.loads(Path(path).read_text())
        if isinstance(data, list):
            self._ticks = data
        elif isinstance(data, dict):
            self._ticks = data.get("ticks", data.get("data", []))
        self.total_ticks = len(self._ticks)
        self._position = 0
        return self.total_ticks

    def load_csv(self, path: str) -> int:
        self._ticks = []
        with open(path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tick = {
                    "symbol": row.get("symbol", row.get("underlying", "")),
                    "strike": int(float(row.get("strike", 0) or 0)),
                    "option_type": row.get("option_type", row.get("type", "")),
                    "ltp": float(row.get("ltp", row.get("close", 0)) or 0),
                    "bid": float(row.get("bid", 0) or 0),
                    "ask": float(row.get("ask", 0) or 0),
                    "volume": int(float(row.get("volume", 0) or 0)),
                    "oi": int(float(row.get("oi", 0) or 0)),
                    "vix": float(row.get("vix", 15) or 15),
                    "timestamp": row.get("timestamp", row.get("time", "")),
                }
                if tick["ltp"] > 0:
                    self._ticks.append(tick)
        self.total_ticks = len(self._ticks)
        self._position = 0
        return self.total_ticks

    def replay(self, on_tick=None, on_complete=None) -> None:
        """Replay all ticks sequentially."""
        prev_ts = None
        while self._position < self.total_ticks and not self._stopped:
            if self._paused:
                time.sleep(0.1)
                continue

            tick = self._ticks[self._position]

            # Calculate delay based on timestamp gaps
            ts_str = tick.get("timestamp", "")
            current_ts = None
            if ts_str:
                try:
                    current_ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                except ValueError:
                    current_ts = None

            if prev_ts and current_ts and self.speed < 100:
                gap = (current_ts - prev_ts).total_seconds()
                delay = max(0, gap / self.speed)
                if delay > 0 and delay < 5:
                    time.sleep(delay)
            prev_ts = current_ts

            # Determine if spot or option tick
            if tick.get("strike"):
                self.producer.publish_option_tick(
                    underlying=tick.get("symbol", ""),
                    strike=tick["strike"],
                    option_type=tick.get("option_type", ""),
                    ltp=tick["ltp"],
                    bid=tick.get("bid", tick["ltp"] - 0.1),
                    ask=tick.get("ask", tick["ltp"] + 0.1),
                    volume=tick.get("volume", 0),
                    oi=tick.get("oi", 0),
                    delta=float(tick.get("delta", 0.2) or 0.2),
                    spread_pct=float(tick.get("spread_pct", 0.3) or 0.3),
                    timestamp=current_ts,
                )
            else:
                self.producer.publish_tick(
                    symbol=tick.get("symbol", ""),
                    ltp=tick["ltp"],
                    bid=tick.get("bid", 0),
                    ask=tick.get("ask", 0),
                    volume=tick.get("volume", 0),
                    oi=tick.get("oi", 0),
                    vix=float(tick.get("vix", 15) or 15),
                    timestamp=current_ts,
                )

            if on_tick:
                on_tick(tick, self._position, self.total_ticks)

            self._position += 1

        if on_complete and not self._stopped:
            on_complete()

    def step(self) -> Optional[Dict[str, Any]]:
        """Replay a single tick (for integration into existing engine loop)."""
        if self._position >= self.total_ticks:
            return None
        tick = self._ticks[self._position]
        self._position += 1
        return tick

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        self._stopped = True

    def seek(self, pct: float) -> None:
        self._position = min(self.total_ticks - 1, max(0, int(self.total_ticks * pct / 100)))

    @property
    def progress_pct(self) -> float:
        return (self._position / max(self.total_ticks, 1)) * 100

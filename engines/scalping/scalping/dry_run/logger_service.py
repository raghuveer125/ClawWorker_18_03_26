"""Structured decision logger — captures every trading decision as JSONL."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List

from . import kafka_config as bus


class LoggerService:
    """Subscribes to all bus topics and writes structured JSONL logs."""

    def __init__(self, log_dir: str = "logs/dry_run") -> None:
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._buffer: List[str] = []
        self._entry_count = 0

        for topic_key in bus.TOPICS:
            bus.subscribe(topic_key, self._on_message)

    def _on_message(self, message: Dict[str, Any]) -> None:
        record = {
            "ts": message.get("_ts", datetime.now().isoformat()),
            "topic": message.get("_topic", "unknown"),
            **{k: v for k, v in message.items() if not k.startswith("_")},
        }
        line = json.dumps(record, default=str)
        self._buffer.append(line)
        self._entry_count += 1
        if len(self._buffer) >= 50:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(self.log_dir, f"decisions_{date_str}.jsonl")
        try:
            with open(path, "a") as f:
                for line in self._buffer:
                    f.write(line + "\n")
            self._buffer.clear()
        except Exception:
            pass

    @property
    def total_logged(self) -> int:
        return self._entry_count

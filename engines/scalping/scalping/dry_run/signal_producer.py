"""Signal producer — generates entry signals from market data."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from . import kafka_config as bus


class SignalProducer:
    """Produces trading signals from market data ticks."""

    def __init__(self) -> None:
        self._tick_history: Dict[str, List[float]] = {}

    def on_tick(self, tick: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process a market data tick and optionally produce a signal."""
        if tick.get("event") != "option_tick":
            return None

        underlying = tick.get("underlying", "")
        strike = tick.get("strike", 0)
        option_type = tick.get("option_type", "")
        ltp = float(tick.get("ltp", 0) or 0)
        if ltp <= 0:
            return None

        key = f"{underlying}|{strike}|{option_type}"
        history = self._tick_history.setdefault(key, [])
        history.append(ltp)
        if len(history) > 20:
            self._tick_history[key] = history[-20:]

        if len(history) < 3:
            return None

        # Simple momentum signal: 3-tick trend
        trend_up = history[-1] > history[-2] > history[-3]
        trend_down = history[-1] < history[-2] < history[-3]
        if not trend_up and not trend_down:
            return None

        direction = "CE" if trend_up else "PE"
        if direction != option_type:
            return None

        bid = float(tick.get("bid", ltp - 0.1))
        ask = float(tick.get("ask", ltp + 0.1))
        spread = round(ask - bid, 2)

        signal = {
            "event": "signal",
            "symbol": underlying,
            "strike": strike,
            "option_type": option_type,
            "entry": ask,
            "premium": ltp,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "spread_pct": float(tick.get("spread_pct", 0.3)),
            "volume": tick.get("volume", 5000),
            "oi": tick.get("oi", 10000),
            "bid_qty": tick.get("volume", 5000),
            "ask_qty": int(tick.get("volume", 5000) * 0.6),
            "delta": tick.get("delta", 0.20),
            "quality_score": 0.72,
            "quality_grade": "B",
            "setup_tag": "B",
            "rr_ratio": 1.4,
            "conditions_met": ["structure_break", "futures_momentum"],
            "sl": round(ask * 0.75, 2),
            "t1": round(ask + max(4.0, ask * 0.35), 2),
            "timestamp": tick.get("timestamp", datetime.now().isoformat()),
        }

        bus.publish("signals", signal)
        return signal

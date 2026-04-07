"""
Mock data generator for the scalping validation pipeline.

Produces realistic fake market data across all scalping Kafka topics
and publishes it via ``kafka-python`` ``KafkaProducer``.  Designed for
end-to-end validation testing without a live market feed.

Indices and base prices:
  - NIFTY50   ~23 000
  - BANKNIFTY ~48 000
  - SENSEX    ~76 000
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
import uuid
from typing import Any, Dict, List, Optional

from config.settings import SCALPING_TOPICS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INDICES: Dict[str, float] = {
    "NIFTY50": 23_000.0,
    "BANKNIFTY": 48_000.0,
    "SENSEX": 76_000.0,
}

_OPTION_TYPES = ["CE", "PE"]
_SIGNAL_SIDES = ["BUY", "SELL"]
_TRADE_STATUSES = ["OPEN", "FILLED", "PARTIAL"]
_ANALYSIS_COMPONENTS = [
    "MarketRegime",
    "Structure",
    "Momentum",
    "TrapDetector",
    "StrikeDetector",
]
_SIGNAL_TYPES = [
    "regime_change",
    "breakout",
    "momentum_surge",
    "trap_detected",
    "strike_selected",
]


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class MockDataGenerator:
    """Generate realistic fake scalping data and publish to Kafka.

    Usage::

        gen = MockDataGenerator("localhost:9092")
        await gen.generate_and_publish(duration_seconds=60, tick_interval_ms=500)
    """

    def __init__(self, bootstrap_servers: str = "localhost:9092") -> None:
        self._bootstrap_servers = bootstrap_servers
        self._producer: Optional[Any] = None

        # Running prices (random-walk state)
        self._prices: Dict[str, float] = {k: v for k, v in _INDICES.items()}

        # Track generated signals so trades can reference them
        self._pending_signals: List[Dict[str, Any]] = []

    # -- Public API ---------------------------------------------------------

    async def generate_and_publish(
        self,
        duration_seconds: int = 60,
        tick_interval_ms: int = 500,
    ) -> None:
        """Generate fake data for all scalping topics for *duration_seconds*.

        A new message batch is published every *tick_interval_ms*.
        """
        self._producer = self._create_producer()
        if self._producer is None:
            logger.error("Could not create Kafka producer — aborting simulation")
            return

        logger.info(
            "Mock data generator started (duration=%ds, interval=%dms)",
            duration_seconds,
            tick_interval_ms,
        )

        interval_sec = tick_interval_ms / 1000.0
        end_time = time.time() + duration_seconds
        tick_count = 0

        try:
            while time.time() < end_time:
                tick_count += 1
                self._publish_tick(tick_count)
                await asyncio.sleep(interval_sec)
        finally:
            self._producer.flush(timeout=5)
            self._producer.close(timeout=5)
            logger.info("Mock data generator finished (%d ticks published)", tick_count)

    # -- Tick publishing -----------------------------------------------------

    def _publish_tick(self, tick_num: int) -> None:
        """Publish one round of messages across all topics."""
        for symbol, base_price in _INDICES.items():
            spot = self._walk_price(symbol)

            self._send(SCALPING_TOPICS["market_data"], self._generate_market_data(symbol, spot))
            self._send(SCALPING_TOPICS["option_chain"], self._generate_option_chain(symbol, spot))
            self._send(SCALPING_TOPICS["futures"], self._generate_futures(symbol, spot))

        # Analysis, signals, and trades at lower frequency
        if tick_num % 3 == 0:
            for index in _INDICES:
                self._send(SCALPING_TOPICS["analysis"], self._generate_analysis(index))

        if tick_num % 5 == 0:
            index = random.choice(list(_INDICES.keys()))
            spot = self._prices[index]
            signal = self._generate_signal(index, spot)
            self._send(SCALPING_TOPICS["signals"], signal)
            self._pending_signals.append(signal)

        if tick_num % 7 == 0 and self._pending_signals:
            signal = self._pending_signals.pop(0)
            self._send(SCALPING_TOPICS["trades"], self._generate_trade(signal))

        # Heartbeat every tick
        self._send(
            SCALPING_TOPICS["heartbeat"],
            {
                "component": "MockDataGenerator",
                "status": "healthy",
                "latency_ms": round(random.uniform(0.5, 5.0), 2),
                "timestamp": self._now_iso(),
            },
        )

    # -- Data generators -----------------------------------------------------

    def _generate_market_data(self, symbol: str, spot: float) -> Dict[str, Any]:
        spread = spot * random.uniform(0.0001, 0.0005)
        return {
            "symbol": symbol,
            "ltp": round(spot, 2),
            "bid": round(spot - spread / 2, 2),
            "ask": round(spot + spread / 2, 2),
            "volume": random.randint(100, 50_000),
            "timestamp": self._now_iso(),
            "source": "mock",
        }

    def _generate_option_chain(self, symbol: str, spot: float) -> Dict[str, Any]:
        atm_strike = round(spot / 100) * 100
        offset = random.choice([-200, -100, 0, 100, 200])
        strike = atm_strike + offset
        opt_type = random.choice(_OPTION_TYPES)
        moneyness = (spot - strike) / spot if opt_type == "CE" else (strike - spot) / spot

        delta = max(0.01, min(0.99, 0.5 + moneyness * 2)) * (1 if opt_type == "CE" else -1)
        premium = max(1.0, abs(spot - strike) * 0.3 + random.uniform(5, 50))

        return {
            "symbol": symbol,
            "strike": strike,
            "option_type": opt_type,
            "ltp": round(premium, 2),
            "delta": round(delta, 4),
            "gamma": round(random.uniform(0.001, 0.05), 4),
            "theta": round(-random.uniform(0.5, 10.0), 4),
            "vega": round(random.uniform(0.1, 5.0), 4),
            "oi": random.randint(1_000, 500_000),
            "volume": random.randint(10, 10_000),
            "bid": round(premium - random.uniform(0.5, 2.0), 2),
            "ask": round(premium + random.uniform(0.5, 2.0), 2),
            "timestamp": self._now_iso(),
        }

    def _generate_futures(self, symbol: str, spot: float) -> Dict[str, Any]:
        basis = spot * random.uniform(-0.002, 0.005)
        return {
            "symbol": symbol,
            "ltp": round(spot + basis, 2),
            "basis": round(basis, 2),
            "volume": random.randint(500, 100_000),
            "oi": random.randint(10_000, 1_000_000),
            "timestamp": self._now_iso(),
        }

    def _generate_analysis(self, index: str) -> Dict[str, Any]:
        return {
            "component": random.choice(_ANALYSIS_COMPONENTS),
            "signal_type": random.choice(_SIGNAL_TYPES),
            "confidence": round(random.uniform(0.3, 1.0), 3),
            "index": index,
            "timestamp": self._now_iso(),
        }

    def _generate_signal(self, index: str, spot: float) -> Dict[str, Any]:
        side = random.choice(_SIGNAL_SIDES)
        atm_strike = round(spot / 100) * 100
        # Use spot-relative entry/SL/target so strategy_validator can evaluate
        entry = round(spot, 2)

        if side == "BUY":
            sl = round(entry * 0.995, 2)        # 0.5% below
            target = round(entry * 1.008, 2)     # 0.8% above
        else:
            sl = round(entry * 1.005, 2)         # 0.5% above
            target = round(entry * 0.992, 2)     # 0.8% below

        return {
            "signal_id": str(uuid.uuid4())[:12],
            "index": index,
            "side": side,
            "strike": atm_strike,
            "entry_price": entry,
            "stop_loss": sl,
            "target": target,
            "confidence": round(random.uniform(0.5, 0.99), 3),
            "timestamp": self._now_iso(),
        }

    def _generate_trade(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        slippage = random.uniform(-0.5, 0.5)
        return {
            "trade_id": str(uuid.uuid4())[:12],
            "signal_id": signal["signal_id"],
            "index": signal["index"],
            "side": signal["side"],
            "strike": signal["strike"],
            "entry_price": round(signal["entry_price"] + slippage, 2),
            "quantity": random.choice([25, 50, 75, 100, 150]),
            "status": random.choice(_TRADE_STATUSES),
            "timestamp": self._now_iso(),
        }

    # -- Helpers ------------------------------------------------------------

    def _walk_price(self, symbol: str) -> float:
        """Advance the random walk for *symbol* and return the new price."""
        current = self._prices[symbol]
        # Brownian-ish step: ~0.01 % per tick
        step_pct = random.gauss(0, 0.0001)
        new_price = current * (1 + step_pct)
        self._prices[symbol] = new_price
        return new_price

    @staticmethod
    def _now_iso() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def _create_producer(self) -> Optional[Any]:
        """Create a ``KafkaProducer``, returning ``None`` on failure."""
        try:
            from kafka import KafkaProducer

            return KafkaProducer(
                bootstrap_servers=self._bootstrap_servers,
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                acks=1,
                retries=3,
            )
        except Exception:
            logger.exception("Failed to create Kafka producer")
            return None

    def _send(self, topic: str, message: Dict[str, Any]) -> None:
        """Send a message to Kafka, logging errors without raising."""
        if self._producer is None:
            return
        try:
            self._producer.send(topic, value=message)
        except Exception:
            logger.debug("Failed to send to %s (non-fatal)", topic, exc_info=True)

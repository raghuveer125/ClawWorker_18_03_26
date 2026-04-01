"""Market data producer — publishes live or synthetic ticks."""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import kafka_config as bus


class MarketDataProducer:
    """Produces market data ticks to the bus."""

    def __init__(self, symbols: Optional[List[str]] = None) -> None:
        self.symbols = symbols or ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX", "BSE:SENSEX-INDEX"]
        self._last_prices: Dict[str, float] = {}

    def publish_tick(self, symbol: str, ltp: float, bid: float = 0, ask: float = 0,
                     volume: int = 0, oi: int = 0, vix: float = 15.0,
                     timestamp: Optional[datetime] = None) -> None:
        tick = {
            "event": "tick",
            "symbol": symbol,
            "ltp": ltp,
            "bid": bid or round(ltp - 0.1, 2),
            "ask": ask or round(ltp + 0.1, 2),
            "volume": volume,
            "oi": oi,
            "vix": vix,
            "timestamp": (timestamp or datetime.now()).isoformat(),
        }
        self._last_prices[symbol] = ltp
        bus.publish("market_data", tick)

    def publish_option_tick(self, underlying: str, strike: int, option_type: str,
                            ltp: float, bid: float, ask: float, volume: int, oi: int,
                            delta: float = 0.2, spread_pct: float = 0.3,
                            timestamp: Optional[datetime] = None) -> None:
        tick = {
            "event": "option_tick",
            "underlying": underlying,
            "strike": strike,
            "option_type": option_type,
            "ltp": ltp,
            "bid": bid,
            "ask": ask,
            "spread": round(ask - bid, 2),
            "spread_pct": spread_pct,
            "volume": volume,
            "oi": oi,
            "delta": delta,
            "timestamp": (timestamp or datetime.now()).isoformat(),
        }
        key = f"{underlying}|{strike}|{option_type}"
        self._last_prices[key] = ltp
        bus.publish("market_data", tick)

    def generate_synthetic_tick(self, symbol: str, base_price: float, volatility: float = 0.001) -> float:
        last = self._last_prices.get(symbol, base_price)
        change = last * random.gauss(0, volatility)
        new_price = round(last + change, 2)
        new_price = max(0.05, new_price)
        self._last_prices[symbol] = new_price
        return new_price

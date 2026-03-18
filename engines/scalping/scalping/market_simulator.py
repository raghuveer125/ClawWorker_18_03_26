"""
Replay market simulator.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List


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


class MarketSimulator:
    """Adds small randomized movement to replay snapshots."""

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)

    def simulate_snapshot(self, snapshot: Any) -> Dict[str, Any]:
        spot_data: Dict[str, Dict[str, Any]] = {}
        option_rows: Dict[str, List[Dict[str, Any]]] = {}
        futures_data: Dict[str, Dict[str, Any]] = {}
        candles: Dict[str, Dict[str, Any]] = {}
        vix = 15.0

        for symbol, payload in snapshot.symbols.items():
            spot_price = payload.spot + self._rng.uniform(-5, 5)
            vix = payload.vix or vix
            spot_data[symbol] = {
                "symbol": symbol,
                "ltp": spot_price,
                "open": spot_price + self._rng.uniform(-8, 8),
                "high": spot_price + abs(self._rng.uniform(0, 12)),
                "low": spot_price - abs(self._rng.uniform(0, 12)),
                "prev_close": spot_price + self._rng.uniform(-10, 10),
                "volume": max(1, _to_int(sum(_to_float(r.get("volume"), 0.0) for r in payload.rows) / max(1, len(payload.rows)))),
                "vwap": spot_price + self._rng.uniform(-4, 4),
                "change_pct": self._rng.uniform(-0.8, 0.8),
                "timestamp": payload.timestamp,
            }

            option_rows[symbol] = []
            for row in payload.rows:
                base_entry = max(0.05, _to_float(row.get("entry"), 0.0))
                base_bid = _to_float(row.get("bid"), 0.0) or (base_entry * 0.99)
                base_ask = _to_float(row.get("ask"), 0.0) or max(base_bid + 0.05, base_entry * 1.01)
                sim_bid = max(0.05, base_bid * self._rng.uniform(0.98, 1.02))
                sim_ask = max(sim_bid + 0.05, base_ask * self._rng.uniform(0.98, 1.02))
                sim_volume = max(1, int(_to_float(row.get("volume"), 0.0) * self._rng.uniform(0.7, 1.4)))
                sim_oi = max(1, int(_to_float(row.get("oi"), 0.0) * self._rng.uniform(0.8, 1.2)))
                option_rows[symbol].append({
                    **row,
                    "spot": f"{spot_price:.2f}",
                    "entry": f"{base_entry * self._rng.uniform(0.98, 1.02):.2f}",
                    "bid": f"{sim_bid:.2f}",
                    "ask": f"{sim_ask:.2f}",
                    "spread_pct": f"{((sim_ask - sim_bid) / max(base_entry, 0.05)) * 100:.4f}",
                    "volume": str(sim_volume),
                    "oi": str(sim_oi),
                })

            futures_data[symbol] = {
                "symbol": payload.rows[0].get("fut_symbol", symbol),
                "ltp": payload.fut_ltp + self._rng.uniform(-5, 5),
                "open": payload.fut_ltp + self._rng.uniform(-8, 8),
                "high": payload.fut_ltp + abs(self._rng.uniform(0, 10)),
                "low": payload.fut_ltp - abs(self._rng.uniform(0, 10)),
                "volume": max(1, _to_int(sum(_to_float(r.get("volume"), 0.0) for r in payload.rows))),
                "oi": max(1, _to_int(sum(_to_float(r.get("oi"), 0.0) for r in payload.rows))),
                "spot_price": spot_price,
                "basis": payload.fut_basis,
                "basis_pct": payload.fut_basis_pct,
                "timestamp": payload.timestamp,
            }

            candles[symbol] = {
                "open": spot_price + self._rng.uniform(-3, 3),
                "high": spot_price + abs(self._rng.uniform(0, 6)),
                "low": spot_price - abs(self._rng.uniform(0, 6)),
                "close": spot_price,
                "volume": spot_data[symbol]["volume"],
                "timestamp": payload.timestamp,
            }

        return {
            "timestamp": snapshot.timestamp,
            "spot_data": spot_data,
            "option_rows": option_rows,
            "futures_data": futures_data,
            "candles": candles,
            "vix": vix,
            "raw_rows": snapshot.raw_rows,
        }

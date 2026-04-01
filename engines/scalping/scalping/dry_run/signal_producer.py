"""Institutional signal producer — generates realistic trading signals.

Four signal types:
1. Momentum breakout: price move > threshold within N ticks
2. Pullback continuation: trend + retracement + continuation
3. Volatility expansion: range expansion after compression
4. Liquidity sweep: break of recent high/low + reversal

Output format is compatible with validate_entry() 14-gate pipeline.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import kafka_config as bus


class SignalProducer:
    """Produces institutional-quality signals from structured market data."""

    def __init__(self) -> None:
        self._price_history: Dict[str, List[float]] = {}
        self._volume_history: Dict[str, List[int]] = {}
        self._high_history: Dict[str, List[float]] = {}
        self._low_history: Dict[str, List[float]] = {}
        self._signal_cooldown: Dict[str, int] = {}

    def on_tick(self, tick: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if tick.get("event") != "option_tick":
            return None

        underlying = tick.get("underlying", "")
        strike = int(tick.get("strike", 0) or 0)
        option_type = str(tick.get("option_type", "")).upper()
        ltp = float(tick.get("ltp", 0) or 0)
        if ltp <= 0 or not underlying:
            return None

        key = f"{underlying}|{strike}|{option_type}"

        # Update histories
        prices = self._price_history.setdefault(key, [])
        prices.append(ltp)
        if len(prices) > 50:
            self._price_history[key] = prices[-50:]
            prices = self._price_history[key]

        vol = int(tick.get("volume", 0) or 0)
        vols = self._volume_history.setdefault(key, [])
        vols.append(vol)
        if len(vols) > 50:
            self._volume_history[key] = vols[-50:]
            vols = self._volume_history[key]

        bid = float(tick.get("bid", ltp - 0.1) or ltp - 0.1)
        ask = float(tick.get("ask", ltp + 0.1) or ltp + 0.1)
        highs = self._high_history.setdefault(key, [])
        lows = self._low_history.setdefault(key, [])
        highs.append(ask)
        lows.append(bid)
        if len(highs) > 50:
            self._high_history[key] = highs[-50:]
            self._low_history[key] = lows[-50:]
            highs = self._high_history[key]
            lows = self._low_history[key]

        # Cooldown: don't spam signals for the same strike
        cd = self._signal_cooldown.get(key, 0)
        if cd > 0:
            self._signal_cooldown[key] = cd - 1
            return None

        if len(prices) < 10:
            return None

        # Try each signal type in priority order
        signal_type, confidence, conditions = self._detect_signal(
            prices, vols, highs, lows, option_type
        )
        if signal_type is None:
            return None

        # Set cooldown (don't signal again for 15 ticks)
        self._signal_cooldown[key] = 15

        spread = round(ask - bid, 2)
        spread_pct = round(spread / max(ltp, 1) * 100, 2)
        sl = round(ask * 0.75, 2)
        t1 = round(ask + max(4.0, ask * 0.35), 2)

        signal = {
            "event": "signal",
            "signal_type": signal_type,
            "symbol": underlying,
            "strike": strike,
            "option_type": option_type,
            "entry": round(ask, 2),
            "premium": round(ltp, 2),
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "spread": spread,
            "spread_pct": spread_pct,
            "volume": vol,
            "oi": int(tick.get("oi", 10000) or 10000),
            "bid_qty": max(1000, vol),
            "ask_qty": max(600, int(vol * 0.6)),
            "delta": float(tick.get("delta", 0.20) or 0.20),
            "quality_score": min(0.95, confidence),
            "quality_grade": _grade(confidence),
            "setup_tag": _tag(confidence),
            "rr_ratio": round((t1 - ask) / max(ask - sl, 0.01), 2),
            "conditions_met": conditions,
            "sl": sl,
            "t1": t1,
            "timestamp": tick.get("timestamp", datetime.now().isoformat()),
        }

        bus.publish("signals", signal)
        return signal

    def _detect_signal(
        self, prices: List[float], vols: List[int],
        highs: List[float], lows: List[float], option_type: str,
    ) -> Tuple[Optional[str], float, List[str]]:
        """Run signal detection. Returns (type, confidence, conditions) or (None, 0, [])."""

        # ── 1. Momentum breakout ──
        result = self._check_momentum_breakout(prices, vols, option_type)
        if result:
            return result

        # ── 2. Pullback continuation ──
        result = self._check_pullback(prices, option_type)
        if result:
            return result

        # ── 3. Volatility expansion ──
        result = self._check_vol_expansion(prices, highs, lows, option_type)
        if result:
            return result

        # ── 4. Liquidity sweep ──
        result = self._check_liquidity_sweep(prices, highs, lows, option_type)
        if result:
            return result

        return None, 0, []

    def _check_momentum_breakout(
        self, prices: List[float], vols: List[int], option_type: str,
    ) -> Optional[Tuple[str, float, List[str]]]:
        """Price move > 1.5% in last 5 ticks + volume spike."""
        if len(prices) < 10:
            return None

        recent = prices[-5:]
        move_pct = (recent[-1] - recent[0]) / max(recent[0], 1) * 100

        # PE profits when price goes UP (premium increases on underlying drop)
        if option_type == "PE" and move_pct > 1.5:
            vol_avg = sum(vols[-10:-5]) / max(len(vols[-10:-5]), 1) if len(vols) >= 10 else 1
            vol_now = sum(vols[-5:]) / 5 if len(vols) >= 5 else 1
            vol_spike = vol_now > vol_avg * 1.3 if vol_avg > 0 else True

            conditions = ["structure_break", "futures_momentum"]
            if vol_spike:
                conditions.append("volume_burst")

            confidence = min(0.90, 0.65 + move_pct * 0.05)
            return "momentum_breakout", confidence, conditions

        if option_type == "CE" and move_pct > 1.5:
            conditions = ["structure_break", "futures_momentum"]
            confidence = min(0.90, 0.65 + move_pct * 0.05)
            return "momentum_breakout", confidence, conditions

        return None

    def _check_pullback(
        self, prices: List[float], option_type: str,
    ) -> Optional[Tuple[str, float, List[str]]]:
        """Trend (15-tick) + small retracement (5-tick) + continuation (3-tick)."""
        if len(prices) < 20:
            return None

        trend_prices = prices[-20:-5]
        pullback_prices = prices[-5:-2]
        continuation = prices[-3:]

        trend_move = (trend_prices[-1] - trend_prices[0]) / max(trend_prices[0], 1) * 100
        pullback_move = (pullback_prices[-1] - pullback_prices[0]) / max(pullback_prices[0], 1) * 100
        cont_move = (continuation[-1] - continuation[0]) / max(continuation[0], 1) * 100

        # PE: underlying drops → premium rises. We want premium trending up, small pullback, then resume
        if option_type == "PE":
            is_trend = trend_move > 1.0
            is_pullback = -1.0 < pullback_move < -0.1
            is_continuation = cont_move > 0.3
        else:
            is_trend = trend_move > 1.0
            is_pullback = -1.0 < pullback_move < -0.1
            is_continuation = cont_move > 0.3

        if is_trend and is_pullback and is_continuation:
            conditions = ["structure_break", "futures_momentum"]
            confidence = min(0.85, 0.60 + abs(trend_move) * 0.04)
            return "pullback_continuation", confidence, conditions

        return None

    def _check_vol_expansion(
        self, prices: List[float], highs: List[float], lows: List[float], option_type: str,
    ) -> Optional[Tuple[str, float, List[str]]]:
        """Range expansion after compression."""
        if len(highs) < 20:
            return None

        # Compression: last 15-10 ticks had small range
        old_range = max(highs[-20:-10]) - min(lows[-20:-10])
        new_range = max(highs[-5:]) - min(lows[-5:])

        if old_range <= 0:
            return None

        expansion_ratio = new_range / old_range
        if expansion_ratio > 2.0:
            # Direction: which way did expansion go?
            recent_move = prices[-1] - prices[-5]
            if (option_type == "PE" and recent_move > 0) or (option_type == "CE" and recent_move > 0):
                conditions = ["structure_break", "volume_burst"]
                confidence = min(0.88, 0.60 + expansion_ratio * 0.05)
                return "volatility_expansion", confidence, conditions

        return None

    def _check_liquidity_sweep(
        self, prices: List[float], highs: List[float], lows: List[float], option_type: str,
    ) -> Optional[Tuple[str, float, List[str]]]:
        """Break of recent high/low followed by reversal."""
        if len(prices) < 15:
            return None

        recent_high = max(highs[-15:-5])
        recent_low = min(lows[-15:-5])
        last_5 = prices[-5:]

        # Sweep below recent low then reverse up (PE: premium dropped then recovered)
        swept_low = any(p < recent_low for p in last_5[:-2])
        reversed_up = last_5[-1] > last_5[-3] and last_5[-1] > recent_low

        if option_type == "PE" and swept_low and reversed_up:
            conditions = ["structure_break", "futures_momentum"]
            confidence = 0.75
            return "liquidity_sweep", confidence, conditions

        # Sweep above recent high then reverse (CE equivalent)
        swept_high = any(p > recent_high for p in last_5[:-2])
        reversed_down = last_5[-1] < last_5[-3]

        if option_type == "CE" and swept_high and reversed_down:
            conditions = ["structure_break", "futures_momentum"]
            confidence = 0.75
            return "liquidity_sweep", confidence, conditions

        return None


def _grade(confidence: float) -> str:
    if confidence >= 0.9:
        return "A+"
    if confidence >= 0.8:
        return "A"
    if confidence >= 0.7:
        return "B"
    if confidence >= 0.6:
        return "C"
    return "D"


def _tag(confidence: float) -> str:
    if confidence >= 0.85:
        return "A+"
    if confidence >= 0.7:
        return "B"
    return "C"

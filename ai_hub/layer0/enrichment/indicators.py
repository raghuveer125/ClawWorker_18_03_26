"""
Computed Indicators - Core indicator functions.

These are called by the IndicatorRegistry to enrich data.
Learning Army can request new indicators to be added here.
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class IndicatorResult:
    """Result of indicator computation."""
    name: str
    value: Any
    metadata: Dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


def compute_vwap(
    candles: List[Dict],
    params: Optional[Dict] = None
) -> IndicatorResult:
    """
    Compute Volume Weighted Average Price.

    VWAP = cumsum(price * volume) / cumsum(volume)

    Args:
        candles: List of candle dicts with 'close' and 'volume'
        params: Optional parameters (not used for VWAP)

    Returns:
        IndicatorResult with current VWAP value
    """
    if not candles:
        return IndicatorResult(name="vwap", value=0.0)

    cumulative_pv = 0.0
    cumulative_volume = 0.0

    for candle in candles:
        # Handle both dict and list formats
        if isinstance(candle, dict):
            close = float(candle.get("close", candle.get("c", 0)) or 0)
            volume = float(candle.get("volume", candle.get("v", 0)) or 0)
        elif isinstance(candle, (list, tuple)) and len(candle) >= 5:
            # [timestamp, open, high, low, close, volume]
            close = float(candle[4] or 0)
            volume = float(candle[5] if len(candle) > 5 else 0)
        else:
            continue

        cumulative_pv += close * volume
        cumulative_volume += volume

    vwap = cumulative_pv / cumulative_volume if cumulative_volume > 0 else 0.0

    return IndicatorResult(
        name="vwap",
        value=round(vwap, 2),
        metadata={
            "candle_count": len(candles),
            "total_volume": cumulative_volume,
        }
    )


def compute_fvg(
    candles: List[Dict],
    params: Optional[Dict] = None
) -> IndicatorResult:
    """
    Compute Fair Value Gaps (FVG).

    FVG occurs when:
    - Bullish: candle[i-1].high < candle[i+1].low (gap up)
    - Bearish: candle[i-1].low > candle[i+1].high (gap down)

    Args:
        candles: List of candle dicts with OHLC
        params: {
            "min_gap_pct": 0.1,  # Minimum gap percentage
            "lookback": 50       # How many candles to scan
        }

    Returns:
        IndicatorResult with list of FVG zones
    """
    params = params or {}
    min_gap_pct = float(params.get("min_gap_pct", 0.1))
    lookback = int(params.get("lookback", 50))

    if len(candles) < 3:
        return IndicatorResult(name="fvg_zones", value=[])

    zones = []
    scan_candles = candles[-lookback:] if len(candles) > lookback else candles

    for i in range(1, len(scan_candles) - 1):
        prev_candle = scan_candles[i - 1]
        curr_candle = scan_candles[i]
        next_candle = scan_candles[i + 1]

        # Extract OHLC
        def get_ohlc(c):
            if isinstance(c, dict):
                return {
                    "o": float(c.get("open", c.get("o", 0)) or 0),
                    "h": float(c.get("high", c.get("h", 0)) or 0),
                    "l": float(c.get("low", c.get("l", 0)) or 0),
                    "c": float(c.get("close", c.get("c", 0)) or 0),
                    "ts": c.get("timestamp", c.get("t", i)),
                }
            elif isinstance(c, (list, tuple)) and len(c) >= 5:
                return {"o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4]), "ts": c[0]}
            return {"o": 0, "h": 0, "l": 0, "c": 0, "ts": i}

        prev = get_ohlc(prev_candle)
        curr = get_ohlc(curr_candle)
        nxt = get_ohlc(next_candle)

        # Bullish FVG: gap between prev high and next low
        if prev["h"] < nxt["l"]:
            gap = nxt["l"] - prev["h"]
            gap_pct = (gap / curr["c"]) * 100 if curr["c"] > 0 else 0

            if gap_pct >= min_gap_pct:
                zones.append({
                    "type": "bullish",
                    "top": nxt["l"],
                    "bottom": prev["h"],
                    "gap_pct": round(gap_pct, 2),
                    "timestamp": curr["ts"],
                    "filled": False,
                })

        # Bearish FVG: gap between prev low and next high
        if prev["l"] > nxt["h"]:
            gap = prev["l"] - nxt["h"]
            gap_pct = (gap / curr["c"]) * 100 if curr["c"] > 0 else 0

            if gap_pct >= min_gap_pct:
                zones.append({
                    "type": "bearish",
                    "top": prev["l"],
                    "bottom": nxt["h"],
                    "gap_pct": round(gap_pct, 2),
                    "timestamp": curr["ts"],
                    "filled": False,
                })

    return IndicatorResult(
        name="fvg_zones",
        value=zones,
        metadata={
            "total_zones": len(zones),
            "bullish_zones": len([z for z in zones if z["type"] == "bullish"]),
            "bearish_zones": len([z for z in zones if z["type"] == "bearish"]),
        }
    )


def compute_spread(
    data: Dict,
    params: Optional[Dict] = None
) -> IndicatorResult:
    """
    Compute bid-ask spread.

    Args:
        data: Dict with 'bid' and 'ask' prices
        params: Not used

    Returns:
        IndicatorResult with spread value and percentage
    """
    bid = float(data.get("bid", 0) or 0)
    ask = float(data.get("ask", 0) or 0)

    if bid <= 0 or ask <= 0:
        return IndicatorResult(name="spread", value=0.0, metadata={"spread_pct": 0.0})

    spread = ask - bid
    mid_price = (bid + ask) / 2
    spread_pct = (spread / mid_price) * 100 if mid_price > 0 else 0

    return IndicatorResult(
        name="spread",
        value=round(spread, 2),
        metadata={
            "spread_pct": round(spread_pct, 4),
            "bid": bid,
            "ask": ask,
            "mid_price": round(mid_price, 2),
        }
    )


def compute_volume_profile(
    candles: List[Dict],
    params: Optional[Dict] = None
) -> IndicatorResult:
    """
    Compute volume profile with POC (Point of Control).

    Args:
        candles: List of candles with volume
        params: {"bins": 20}

    Returns:
        IndicatorResult with volume profile and POC
    """
    params = params or {}
    num_bins = int(params.get("bins", 20))

    if not candles:
        return IndicatorResult(name="volume_profile", value={})

    # Get price range
    prices = []
    for c in candles:
        if isinstance(c, dict):
            prices.append(float(c.get("high", c.get("h", 0)) or 0))
            prices.append(float(c.get("low", c.get("l", 0)) or 0))
        elif isinstance(c, (list, tuple)) and len(c) >= 4:
            prices.append(float(c[2]))  # high
            prices.append(float(c[3]))  # low

    if not prices:
        return IndicatorResult(name="volume_profile", value={})

    price_min = min(prices)
    price_max = max(prices)
    bin_size = (price_max - price_min) / num_bins if price_max > price_min else 1

    # Build volume profile
    profile = {i: 0.0 for i in range(num_bins)}

    for c in candles:
        if isinstance(c, dict):
            high = float(c.get("high", c.get("h", 0)) or 0)
            low = float(c.get("low", c.get("l", 0)) or 0)
            volume = float(c.get("volume", c.get("v", 0)) or 0)
        elif isinstance(c, (list, tuple)) and len(c) >= 6:
            high, low = float(c[2]), float(c[3])
            volume = float(c[5])
        else:
            continue

        # Distribute volume across price bins
        for price in [low, (low + high) / 2, high]:
            bin_idx = min(int((price - price_min) / bin_size), num_bins - 1)
            if 0 <= bin_idx < num_bins:
                profile[bin_idx] += volume / 3

    # Find POC
    poc_bin = max(profile, key=profile.get) if profile else 0
    poc_price = price_min + (poc_bin + 0.5) * bin_size

    return IndicatorResult(
        name="volume_profile",
        value={
            "profile": profile,
            "poc": round(poc_price, 2),
            "poc_volume": profile.get(poc_bin, 0),
        },
        metadata={
            "price_min": price_min,
            "price_max": price_max,
            "bin_size": bin_size,
        }
    )


def compute_order_imbalance(
    data: Dict,
    params: Optional[Dict] = None
) -> IndicatorResult:
    """
    Compute order imbalance from bid/ask volumes.

    Args:
        data: Dict with 'bid_volume' and 'ask_volume'
        params: Not used

    Returns:
        IndicatorResult with imbalance ratio (-1 to +1)
    """
    bid_vol = float(data.get("bid_volume", data.get("bid_qty", 0)) or 0)
    ask_vol = float(data.get("ask_volume", data.get("ask_qty", 0)) or 0)

    total = bid_vol + ask_vol
    if total <= 0:
        return IndicatorResult(name="order_imbalance", value=0.0)

    # Imbalance: +1 = all bids, -1 = all asks, 0 = balanced
    imbalance = (bid_vol - ask_vol) / total

    return IndicatorResult(
        name="order_imbalance",
        value=round(imbalance, 4),
        metadata={
            "bid_volume": bid_vol,
            "ask_volume": ask_vol,
            "total_volume": total,
            "bid_pct": round(bid_vol / total * 100, 2) if total > 0 else 0,
        }
    )


def compute_atr(
    candles: List[Dict],
    params: Optional[Dict] = None
) -> IndicatorResult:
    """
    Compute Average True Range (ATR).

    Args:
        candles: List of candles with OHLC
        params: {"period": 14}

    Returns:
        IndicatorResult with ATR value
    """
    params = params or {}
    period = int(params.get("period", 14))

    if len(candles) < 2:
        return IndicatorResult(name="atr", value=0.0)

    true_ranges = []
    prev_close = None

    for c in candles:
        if isinstance(c, dict):
            high = float(c.get("high", c.get("h", 0)) or 0)
            low = float(c.get("low", c.get("l", 0)) or 0)
            close = float(c.get("close", c.get("c", 0)) or 0)
        elif isinstance(c, (list, tuple)) and len(c) >= 5:
            high, low, close = float(c[2]), float(c[3]), float(c[4])
        else:
            continue

        if prev_close is not None:
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)

        prev_close = close

    if not true_ranges:
        return IndicatorResult(name="atr", value=0.0)

    # Simple average of last 'period' true ranges
    atr_values = true_ranges[-period:]
    atr = sum(atr_values) / len(atr_values)

    return IndicatorResult(
        name="atr",
        value=round(atr, 2),
        metadata={"period": period, "samples": len(atr_values)}
    )


# Registry of all available compute functions
INDICATOR_FUNCTIONS = {
    "compute_vwap": compute_vwap,
    "compute_fvg": compute_fvg,
    "compute_spread": compute_spread,
    "compute_volume_profile": compute_volume_profile,
    "compute_order_imbalance": compute_order_imbalance,
    "compute_atr": compute_atr,
}

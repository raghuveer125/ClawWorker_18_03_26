"""
Indicator Data Adapter - Provides calculated indicators for Hub V2.

This adapter sits on top of MarketDataAdapter and computes:
- Momentum indicators (mom_1m, mom_3m, volume_acceleration)
- Microstructure indicators (bid_ask_ratio, volume_delta)
- Liquidity distance indicators (gamma_wall, high_oi_distance)
- Greeks (delta, gamma, theta, vega)
- Market regime indicators

All indicators are provided in a standardized IndicatorSnapshot format.
"""

from __future__ import annotations

import math
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from .adapter import MarketDataAdapter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class MomentumIndicators:
    """Short-term momentum indicators for scalping."""
    mom_1m: float = 0.0       # 1-minute momentum (% change)
    mom_3m: float = 0.0       # 3-minute momentum (% change)
    mom_5m: float = 0.0       # 5-minute momentum (% change)
    volume_acceleration: float = 0.0  # Volume vs avg (ratio)
    momentum_direction: str = "neutral"  # "bullish", "bearish", "neutral"


@dataclass
class MicrostructureIndicators:
    """Order flow and microstructure indicators."""
    bid_ask_spread: float = 0.0      # Spread in points
    bid_ask_spread_pct: float = 0.0  # Spread as % of price
    bid_ask_ratio: float = 1.0       # Bid size / Ask size
    volume_delta: int = 0            # Buy volume - Sell volume (estimated)
    order_book_pressure: str = "neutral"  # "BUY", "SELL", "neutral"


@dataclass
class LiquidityIndicators:
    """Liquidity distance and trap indicators."""
    distance_to_max_pain: float = 0.0      # Points from max pain
    distance_to_gamma_wall: float = 0.0    # Points from highest gamma strike
    distance_to_high_oi_strike: float = 0.0  # Points from highest OI strike
    max_pain_strike: int = 0
    gamma_wall_strike: int = 0
    high_oi_strike: int = 0
    liquidity_zone: str = "safe"  # "safe", "caution", "danger"


@dataclass
class GreeksSnapshot:
    """Option Greeks at ATM strike."""
    delta_ce: float = 0.0
    delta_pe: float = 0.0
    gamma: float = 0.0
    theta_ce: float = 0.0
    theta_pe: float = 0.0
    vega: float = 0.0
    iv_ce: float = 0.0
    iv_pe: float = 0.0
    iv_skew: float = 0.0  # IV_PE - IV_CE


@dataclass
class MarketRegime:
    """Market regime classification."""
    trend: str = "ranging"      # "uptrend", "downtrend", "ranging"
    volatility: str = "normal"  # "low", "normal", "high", "extreme"
    range_state: str = "inside"  # "breakout", "breakdown", "inside"
    regime_score: float = 0.0   # -1 (bearish) to +1 (bullish)


@dataclass
class OptionFlowIndicators:
    """Option chain flow indicators."""
    pcr: float = 1.0            # Put-Call Ratio
    pcr_change: float = 0.0     # PCR change from previous
    ce_oi_total: int = 0
    pe_oi_total: int = 0
    ce_oi_change: float = 0.0   # % change
    pe_oi_change: float = 0.0   # % change
    oi_pattern: str = "neutral"  # "long_buildup", "short_covering", "long_unwinding", "short_buildup"
    vote_side: str = "neutral"  # "CE", "PE", "neutral"
    vote_diff: float = 0.0      # Strength of vote (0-10)


@dataclass
class IndicatorSnapshot:
    """Complete indicator snapshot for Hub V2 consumption."""
    # Metadata
    timestamp: float = field(default_factory=time.time)
    index: str = ""
    ltp: float = 0.0
    change_pct: float = 0.0

    # Indicator groups
    momentum: MomentumIndicators = field(default_factory=MomentumIndicators)
    microstructure: MicrostructureIndicators = field(default_factory=MicrostructureIndicators)
    liquidity: LiquidityIndicators = field(default_factory=LiquidityIndicators)
    greeks: GreeksSnapshot = field(default_factory=GreeksSnapshot)
    regime: MarketRegime = field(default_factory=MarketRegime)
    option_flow: OptionFlowIndicators = field(default_factory=OptionFlowIndicators)

    # Raw data for custom processing
    atm_strike: int = 0
    strikes_data: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "datetime": datetime.fromtimestamp(self.timestamp).isoformat(),
            "index": self.index,
            "ltp": self.ltp,
            "change_pct": self.change_pct,
            "atm_strike": self.atm_strike,
            "momentum": asdict(self.momentum),
            "microstructure": asdict(self.microstructure),
            "liquidity": asdict(self.liquidity),
            "greeks": asdict(self.greeks),
            "regime": asdict(self.regime),
            "option_flow": asdict(self.option_flow),
            "strikes_data": self.strikes_data,
        }


# ---------------------------------------------------------------------------
# Indicator Calculation Functions
# ---------------------------------------------------------------------------

def calculate_momentum(candles: List[Dict], current_price: float) -> MomentumIndicators:
    """
    Calculate momentum indicators from candle history.

    Args:
        candles: List of candles with 'close', 'volume' keys
        current_price: Current LTP

    Returns:
        MomentumIndicators
    """
    if not candles or len(candles) < 3:
        return MomentumIndicators()

    # Get prices at different intervals
    closes = [c.get("close", c.get("ltp", 0)) for c in candles]
    volumes = [c.get("volume", 0) for c in candles]

    # 1m momentum (last candle vs current)
    if len(closes) >= 1 and closes[-1] > 0:
        mom_1m = ((current_price - closes[-1]) / closes[-1]) * 100
    else:
        mom_1m = 0.0

    # 3m momentum
    if len(closes) >= 3 and closes[-3] > 0:
        mom_3m = ((current_price - closes[-3]) / closes[-3]) * 100
    else:
        mom_3m = 0.0

    # 5m momentum
    if len(closes) >= 5 and closes[-5] > 0:
        mom_5m = ((current_price - closes[-5]) / closes[-5]) * 100
    else:
        mom_5m = 0.0

    # Volume acceleration (current vs average)
    if len(volumes) >= 5:
        avg_volume = sum(volumes[-5:]) / 5
        current_volume = volumes[-1] if volumes else 0
        volume_acceleration = current_volume / avg_volume if avg_volume > 0 else 1.0
    else:
        volume_acceleration = 1.0

    # Determine direction
    if mom_3m > 0.1 and mom_1m > 0:
        direction = "bullish"
    elif mom_3m < -0.1 and mom_1m < 0:
        direction = "bearish"
    else:
        direction = "neutral"

    return MomentumIndicators(
        mom_1m=round(mom_1m, 4),
        mom_3m=round(mom_3m, 4),
        mom_5m=round(mom_5m, 4),
        volume_acceleration=round(volume_acceleration, 2),
        momentum_direction=direction,
    )


def calculate_microstructure(
    bid_price: float,
    ask_price: float,
    bid_qty: int,
    ask_qty: int,
    ltp: float,
    prev_volume: int = 0,
    current_volume: int = 0,
) -> MicrostructureIndicators:
    """
    Calculate microstructure indicators.

    Args:
        bid_price: Best bid price
        ask_price: Best ask price
        bid_qty: Bid quantity
        ask_qty: Ask quantity
        ltp: Last traded price
        prev_volume: Previous total volume
        current_volume: Current total volume

    Returns:
        MicrostructureIndicators
    """
    # Spread calculations
    spread = ask_price - bid_price if ask_price > bid_price else 0.0
    spread_pct = (spread / ltp * 100) if ltp > 0 else 0.0

    # Bid/Ask ratio
    bid_ask_ratio = bid_qty / ask_qty if ask_qty > 0 else 1.0

    # Volume delta (simplified - estimate from price direction)
    volume_delta = current_volume - prev_volume

    # Order book pressure
    if bid_ask_ratio > 1.5:
        pressure = "BUY"
    elif bid_ask_ratio < 0.67:
        pressure = "SELL"
    else:
        pressure = "neutral"

    return MicrostructureIndicators(
        bid_ask_spread=round(spread, 2),
        bid_ask_spread_pct=round(spread_pct, 4),
        bid_ask_ratio=round(bid_ask_ratio, 2),
        volume_delta=volume_delta,
        order_book_pressure=pressure,
    )


def calculate_liquidity_distance(
    ltp: float,
    option_chain: List[Dict],
    strike_gap: int = 50,
) -> LiquidityIndicators:
    """
    Calculate liquidity distance indicators.

    Args:
        ltp: Current spot price
        option_chain: Option chain data with 'strike', 'ce_oi', 'pe_oi', 'gamma'
        strike_gap: Gap between strikes

    Returns:
        LiquidityIndicators
    """
    if not option_chain:
        return LiquidityIndicators()

    # Find max pain (strike where total premium loss is minimum)
    max_oi = 0
    max_oi_strike = 0
    max_gamma = 0.0
    gamma_wall_strike = 0

    # Calculate total OI at each strike
    for strike_data in option_chain:
        strike = strike_data.get("strike", 0)
        ce_oi = strike_data.get("ce_oi", 0)
        pe_oi = strike_data.get("pe_oi", 0)
        gamma = abs(strike_data.get("gamma", 0))

        total_oi = ce_oi + pe_oi

        if total_oi > max_oi:
            max_oi = total_oi
            max_oi_strike = strike

        if gamma > max_gamma:
            max_gamma = gamma
            gamma_wall_strike = strike

    # Calculate max pain (simplified - use highest OI strike)
    # In reality, max pain calculation is more complex
    max_pain_strike = max_oi_strike

    # Calculate distances
    distance_to_max_pain = ltp - max_pain_strike if max_pain_strike > 0 else 0.0
    distance_to_gamma_wall = ltp - gamma_wall_strike if gamma_wall_strike > 0 else 0.0
    distance_to_high_oi = ltp - max_oi_strike if max_oi_strike > 0 else 0.0

    # Determine liquidity zone
    abs_dist = abs(distance_to_max_pain)
    if abs_dist <= strike_gap:
        zone = "danger"  # Near max pain - potential pinning
    elif abs_dist <= strike_gap * 3:
        zone = "caution"
    else:
        zone = "safe"

    return LiquidityIndicators(
        distance_to_max_pain=round(distance_to_max_pain, 2),
        distance_to_gamma_wall=round(distance_to_gamma_wall, 2),
        distance_to_high_oi_strike=round(distance_to_high_oi, 2),
        max_pain_strike=max_pain_strike,
        gamma_wall_strike=gamma_wall_strike,
        high_oi_strike=max_oi_strike,
        liquidity_zone=zone,
    )


def calculate_greeks_snapshot(
    option_chain: List[Dict],
    atm_strike: int,
) -> GreeksSnapshot:
    """
    Extract Greeks at ATM strike.

    Args:
        option_chain: Option chain data
        atm_strike: ATM strike price

    Returns:
        GreeksSnapshot
    """
    for strike_data in option_chain:
        if strike_data.get("strike") == atm_strike:
            iv_ce = strike_data.get("iv_ce", strike_data.get("iv", 0))
            iv_pe = strike_data.get("iv_pe", strike_data.get("iv", 0))

            return GreeksSnapshot(
                delta_ce=strike_data.get("delta_ce", strike_data.get("delta", 0.5)),
                delta_pe=strike_data.get("delta_pe", -0.5),
                gamma=strike_data.get("gamma", 0),
                theta_ce=strike_data.get("theta_ce", strike_data.get("theta", 0)),
                theta_pe=strike_data.get("theta_pe", strike_data.get("theta", 0)),
                vega=strike_data.get("vega", 0),
                iv_ce=iv_ce,
                iv_pe=iv_pe,
                iv_skew=iv_pe - iv_ce,
            )

    return GreeksSnapshot()


def calculate_market_regime(
    candles: List[Dict],
    current_price: float,
    vix: float = 15.0,
) -> MarketRegime:
    """
    Determine market regime from price action.

    Args:
        candles: Historical candles
        current_price: Current price
        vix: India VIX value

    Returns:
        MarketRegime
    """
    if not candles or len(candles) < 10:
        return MarketRegime()

    closes = [c.get("close", c.get("ltp", 0)) for c in candles]
    highs = [c.get("high", 0) for c in candles]
    lows = [c.get("low", 0) for c in candles]

    # Calculate simple EMAs
    ema_5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else current_price
    ema_10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else current_price

    # Trend detection
    if ema_5 > ema_10 * 1.002 and current_price > ema_5:
        trend = "uptrend"
        regime_score = 0.5
    elif ema_5 < ema_10 * 0.998 and current_price < ema_5:
        trend = "downtrend"
        regime_score = -0.5
    else:
        trend = "ranging"
        regime_score = 0.0

    # Volatility classification
    if vix < 12:
        volatility = "low"
    elif vix < 18:
        volatility = "normal"
    elif vix < 25:
        volatility = "high"
    else:
        volatility = "extreme"

    # Range state
    recent_high = max(highs[-10:]) if highs else current_price
    recent_low = min(lows[-10:]) if lows else current_price
    range_size = recent_high - recent_low

    if current_price > recent_high * 0.998:
        range_state = "breakout"
        regime_score += 0.3
    elif current_price < recent_low * 1.002:
        range_state = "breakdown"
        regime_score -= 0.3
    else:
        range_state = "inside"

    return MarketRegime(
        trend=trend,
        volatility=volatility,
        range_state=range_state,
        regime_score=round(max(-1, min(1, regime_score)), 2),
    )


def calculate_option_flow(
    option_chain: List[Dict],
    prev_ce_oi: int = 0,
    prev_pe_oi: int = 0,
) -> OptionFlowIndicators:
    """
    Calculate option flow indicators.

    Args:
        option_chain: Option chain data
        prev_ce_oi: Previous total CE OI
        prev_pe_oi: Previous total PE OI

    Returns:
        OptionFlowIndicators
    """
    if not option_chain:
        return OptionFlowIndicators()

    ce_oi_total = sum(s.get("ce_oi", 0) for s in option_chain)
    pe_oi_total = sum(s.get("pe_oi", 0) for s in option_chain)

    # PCR
    pcr = pe_oi_total / ce_oi_total if ce_oi_total > 0 else 1.0

    # OI changes
    ce_oi_change = ((ce_oi_total - prev_ce_oi) / prev_ce_oi * 100) if prev_ce_oi > 0 else 0.0
    pe_oi_change = ((pe_oi_total - prev_pe_oi) / prev_pe_oi * 100) if prev_pe_oi > 0 else 0.0

    # OI pattern detection
    if ce_oi_change > 2 and pe_oi_change > 2:
        oi_pattern = "long_buildup"
    elif ce_oi_change < -2 and pe_oi_change < -2:
        oi_pattern = "long_unwinding"
    elif ce_oi_change < -2 and pe_oi_change > 2:
        oi_pattern = "short_covering"
    elif ce_oi_change > 2 and pe_oi_change < -2:
        oi_pattern = "short_buildup"
    else:
        oi_pattern = "neutral"

    # Vote side (simplified)
    if pcr > 1.2:
        vote_side = "PE"
        vote_diff = min((pcr - 1) * 5, 10)
    elif pcr < 0.8:
        vote_side = "CE"
        vote_diff = min((1 - pcr) * 5, 10)
    else:
        vote_side = "neutral"
        vote_diff = 0.0

    return OptionFlowIndicators(
        pcr=round(pcr, 3),
        pcr_change=round(pe_oi_change - ce_oi_change, 2),
        ce_oi_total=ce_oi_total,
        pe_oi_total=pe_oi_total,
        ce_oi_change=round(ce_oi_change, 2),
        pe_oi_change=round(pe_oi_change, 2),
        oi_pattern=oi_pattern,
        vote_side=vote_side,
        vote_diff=round(vote_diff, 1),
    )


# ---------------------------------------------------------------------------
# Main Adapter Class
# ---------------------------------------------------------------------------

class IndicatorDataAdapter:
    """
    Adapter that provides calculated indicators for Hub V2.

    Uses MarketDataAdapter for raw data and computes all indicators
    in a standardized format.
    """

    def __init__(self, market_adapter: Optional[MarketDataAdapter] = None):
        """
        Initialize the indicator adapter.

        Args:
            market_adapter: Optional MarketDataAdapter instance.
                           Creates one if not provided.
        """
        self._market = market_adapter or MarketDataAdapter()
        self._prev_state: Dict[str, Dict] = {}  # Track previous values for deltas
        logger.info("IndicatorDataAdapter initialized")

    def get_indicator_snapshot(
        self,
        index_name: str,
        strike_count: int = 10,
        include_history: bool = True,
    ) -> IndicatorSnapshot:
        """
        Get complete indicator snapshot for an index.

        Args:
            index_name: Index name (SENSEX, NIFTY50, etc.)
            strike_count: Number of strikes to fetch
            include_history: Whether to fetch candle history for momentum

        Returns:
            IndicatorSnapshot with all calculated indicators
        """
        index_name = index_name.upper()
        start_time = time.time()

        try:
            # Get composite market data
            market_data = self._market.get_index_market_data(
                index_name=index_name,
                strike_count=strike_count,
                include_history=include_history,
            )
        except Exception as e:
            logger.error(f"Failed to fetch market data for {index_name}: {e}")
            return IndicatorSnapshot(index=index_name, timestamp=time.time())

        # Extract components
        quote = market_data.get("quote", {})
        option_chain = market_data.get("option_chain", {})
        history = market_data.get("history", {})
        config = market_data.get("config", {})
        vix_quote = market_data.get("vix_quote", {})

        ltp = quote.get("ltp", 0)
        prev_close = quote.get("prev_close", ltp)
        change_pct = ((ltp - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

        # Get previous state for delta calculations
        prev = self._prev_state.get(index_name, {})

        # Get candles for momentum
        candles = history.get("candles", [])

        # Get option chain data
        chain_data = option_chain.get("data", [])
        strike_gap = config.get("strike_gap", 50)

        # Calculate ATM strike
        atm_strike = round(ltp / strike_gap) * strike_gap

        # Calculate all indicators
        momentum = calculate_momentum(candles, ltp)

        microstructure = calculate_microstructure(
            bid_price=quote.get("bid", ltp - 0.5),
            ask_price=quote.get("ask", ltp + 0.5),
            bid_qty=quote.get("bid_qty", 1000),
            ask_qty=quote.get("ask_qty", 1000),
            ltp=ltp,
            prev_volume=prev.get("volume", 0),
            current_volume=quote.get("volume", 0),
        )

        liquidity = calculate_liquidity_distance(ltp, chain_data, strike_gap)

        greeks = calculate_greeks_snapshot(chain_data, atm_strike)

        regime = calculate_market_regime(
            candles,
            ltp,
            vix=vix_quote.get("ltp", 15.0),
        )

        option_flow = calculate_option_flow(
            chain_data,
            prev_ce_oi=prev.get("ce_oi_total", 0),
            prev_pe_oi=prev.get("pe_oi_total", 0),
        )

        # Update previous state
        self._prev_state[index_name] = {
            "ltp": ltp,
            "volume": quote.get("volume", 0),
            "ce_oi_total": option_flow.ce_oi_total,
            "pe_oi_total": option_flow.pe_oi_total,
            "timestamp": time.time(),
        }

        # Build snapshot
        snapshot = IndicatorSnapshot(
            timestamp=time.time(),
            index=index_name,
            ltp=ltp,
            change_pct=round(change_pct, 2),
            momentum=momentum,
            microstructure=microstructure,
            liquidity=liquidity,
            greeks=greeks,
            regime=regime,
            option_flow=option_flow,
            atm_strike=atm_strike,
            strikes_data=chain_data[:strike_count] if chain_data else [],
        )

        elapsed = time.time() - start_time
        logger.debug(f"Indicator snapshot for {index_name} computed in {elapsed:.3f}s")

        return snapshot

    def get_scalping_signals(
        self,
        index_name: str,
        min_momentum: float = 0.05,
        min_volume_accel: float = 1.2,
        max_spread_pct: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Get scalping-ready signals from indicators.

        Args:
            index_name: Index name
            min_momentum: Minimum momentum threshold
            min_volume_accel: Minimum volume acceleration
            max_spread_pct: Maximum acceptable spread

        Returns:
            Dict with signal, confidence, and reasons
        """
        snapshot = self.get_indicator_snapshot(index_name)

        # Build signal
        signal = {
            "index": index_name,
            "timestamp": snapshot.timestamp,
            "ltp": snapshot.ltp,
            "signal": "WAIT",
            "side": None,
            "confidence": 0,
            "reasons": [],
            "warnings": [],
        }

        # Check entry conditions
        mom = snapshot.momentum
        micro = snapshot.microstructure
        regime = snapshot.regime
        flow = snapshot.option_flow

        # Momentum check
        if abs(mom.mom_1m) >= min_momentum:
            signal["reasons"].append(f"Momentum: {mom.mom_1m:.2f}%")
            signal["confidence"] += 20

        # Volume acceleration check
        if mom.volume_acceleration >= min_volume_accel:
            signal["reasons"].append(f"Volume surge: {mom.volume_acceleration:.1f}x")
            signal["confidence"] += 15

        # Spread check
        if micro.bid_ask_spread_pct > max_spread_pct:
            signal["warnings"].append(f"Wide spread: {micro.bid_ask_spread_pct:.2f}%")
            signal["confidence"] -= 10

        # Regime alignment
        if regime.trend != "ranging":
            signal["reasons"].append(f"Trend: {regime.trend}")
            signal["confidence"] += 15

        # Option flow alignment
        if flow.vote_diff >= 3:
            signal["reasons"].append(f"Strong {flow.vote_side} flow: {flow.vote_diff:.1f}")
            signal["confidence"] += 20

        # Determine signal direction
        if signal["confidence"] >= 50:
            if mom.momentum_direction == "bullish" and regime.regime_score > 0:
                signal["signal"] = "LONG"
                signal["side"] = "CE"
            elif mom.momentum_direction == "bearish" and regime.regime_score < 0:
                signal["signal"] = "SHORT"
                signal["side"] = "PE"

        # Add liquidity warnings
        if snapshot.liquidity.liquidity_zone == "danger":
            signal["warnings"].append("Near max pain - caution")
            signal["confidence"] -= 15

        return signal

    def get_stats(self) -> Dict[str, Any]:
        """Get adapter statistics."""
        return {
            "tracked_indices": list(self._prev_state.keys()),
            "last_updates": {
                k: v.get("timestamp", 0) for k, v in self._prev_state.items()
            },
        }

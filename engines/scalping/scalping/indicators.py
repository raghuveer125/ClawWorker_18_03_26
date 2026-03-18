"""
Core Indicators Module - Quant-grade technical indicators for scalping.

Provides:
- ATR (Average True Range) - volatility regime detection
- IV Percentile / IV Rank - option volatility context
- Max Pain Distance - option pinning detection
- OI Change Rate - momentum flow detection
- Volume Acceleration - momentum burst detection
- Liquidity Distance - distance to heavy OI strikes
- Order Flow Imbalance - microstructure signals
- Gamma Exposure Zones - dealer hedging levels

All indicators are optimized for low-latency scalping operations.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import math


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ATRData:
    """ATR calculation result."""
    value: float
    period: int
    expansion: bool  # ATR > 1.5x average
    contraction: bool  # ATR < 0.7x average
    regime: str  # "expanding", "contracting", "normal"
    percentile: float  # 0-100, where current ATR sits vs history


@dataclass
class IVMetrics:
    """Implied Volatility metrics."""
    current_iv: float
    iv_percentile: float  # 0-100, IV rank over period
    iv_rank: float  # 0-100, (current - min) / (max - min)
    avg_iv: float
    min_iv: float
    max_iv: float
    regime: str  # "high", "low", "normal"


@dataclass
class MaxPainData:
    """Max Pain calculation result."""
    strike: int
    distance_points: float
    distance_pct: float
    direction: str  # "above", "below", "at"
    pinning_probability: float  # Higher near expiry


@dataclass
class OIFlowData:
    """Open Interest flow metrics."""
    total_oi: int
    oi_change: int
    oi_change_rate: float  # % change
    ce_oi_change: int
    pe_oi_change: int
    buildup_type: str  # "long_buildup", "short_buildup", "long_unwinding", "short_covering", "neutral"
    strength: float  # 0-1


@dataclass
class VolumeAcceleration:
    """Volume acceleration metrics."""
    current_volume: int
    avg_volume: int
    acceleration: float  # current / avg ratio
    is_spike: bool  # > 2x average
    trend: str  # "accelerating", "decelerating", "stable"
    momentum_score: float  # 0-1


@dataclass
class LiquidityMetrics:
    """Liquidity distance and depth metrics."""
    nearest_heavy_ce_strike: int
    nearest_heavy_pe_strike: int
    ce_distance_points: float
    pe_distance_points: float
    liquidity_zone: Tuple[int, int]  # Range of liquid strikes
    in_liquidity_zone: bool
    liquidity_score: float  # 0-1


@dataclass
class OrderFlowData:
    """Order flow imbalance metrics."""
    bid_volume: int
    ask_volume: int
    imbalance: float  # -1 to 1 (negative = selling pressure)
    aggression: str  # "bid_aggressive", "ask_aggressive", "balanced"
    spread_compression: bool
    spread_bps: float  # Spread in basis points


@dataclass
class GammaExposure:
    """Gamma exposure zone metrics."""
    net_gamma: float
    gamma_flip_strike: int  # Strike where gamma flips sign
    dealer_long_gamma: bool
    pin_strikes: List[int]  # High gamma strikes
    gamma_wall_above: Optional[int]
    gamma_wall_below: Optional[int]


@dataclass
class MarketMicrostructure:
    """Combined microstructure indicators."""
    order_flow: OrderFlowData
    gamma: GammaExposure
    spread_regime: str  # "tight", "normal", "wide"
    execution_quality: float  # 0-1, likelihood of good fill


# =============================================================================
# ATR Calculations
# =============================================================================

class ATRCalculator:
    """Calculate ATR and related volatility metrics."""

    def __init__(self, period: int = 14, history_size: int = 100):
        self.period = period
        self.history_size = history_size
        self._tr_history: Dict[str, List[float]] = {}
        self._atr_history: Dict[str, List[float]] = {}

    def calculate(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
        prev_close: float
    ) -> ATRData:
        """Calculate ATR for a symbol."""
        # True Range = max(H-L, |H-PC|, |L-PC|)
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        # Store TR history
        if symbol not in self._tr_history:
            self._tr_history[symbol] = []
        self._tr_history[symbol].append(tr)
        if len(self._tr_history[symbol]) > self.history_size:
            self._tr_history[symbol] = self._tr_history[symbol][-self.history_size:]

        # Calculate ATR (EMA of TR)
        tr_list = self._tr_history[symbol]
        if len(tr_list) < self.period:
            atr = sum(tr_list) / len(tr_list)
        else:
            # Simple moving average for initial, then EMA
            if symbol not in self._atr_history or not self._atr_history[symbol]:
                atr = sum(tr_list[-self.period:]) / self.period
            else:
                prev_atr = self._atr_history[symbol][-1]
                multiplier = 2 / (self.period + 1)
                atr = (tr - prev_atr) * multiplier + prev_atr

        # Store ATR history
        if symbol not in self._atr_history:
            self._atr_history[symbol] = []
        self._atr_history[symbol].append(atr)
        if len(self._atr_history[symbol]) > self.history_size:
            self._atr_history[symbol] = self._atr_history[symbol][-self.history_size:]

        # Calculate ATR metrics
        atr_list = self._atr_history[symbol]
        avg_atr = sum(atr_list) / len(atr_list) if atr_list else atr

        expansion = atr > avg_atr * 1.5
        contraction = atr < avg_atr * 0.7

        if expansion:
            regime = "expanding"
        elif contraction:
            regime = "contracting"
        else:
            regime = "normal"

        # Calculate percentile
        sorted_atr = sorted(atr_list)
        percentile = (sorted_atr.index(min(sorted_atr, key=lambda x: abs(x - atr))) / len(sorted_atr)) * 100 if sorted_atr else 50

        return ATRData(
            value=atr,
            period=self.period,
            expansion=expansion,
            contraction=contraction,
            regime=regime,
            percentile=percentile,
        )

    def get_atr(self, symbol: str) -> Optional[float]:
        """Get latest ATR for symbol."""
        if symbol in self._atr_history and self._atr_history[symbol]:
            return self._atr_history[symbol][-1]
        return None


# =============================================================================
# IV Calculations
# =============================================================================

class IVCalculator:
    """Calculate IV percentile and rank."""

    def __init__(self, lookback_period: int = 252):  # ~1 year of trading days
        self.lookback_period = lookback_period
        self._iv_history: Dict[str, List[float]] = {}

    def calculate(self, symbol: str, current_iv: float) -> IVMetrics:
        """Calculate IV metrics for a symbol."""
        # Store IV history
        if symbol not in self._iv_history:
            self._iv_history[symbol] = []
        self._iv_history[symbol].append(current_iv)
        if len(self._iv_history[symbol]) > self.lookback_period:
            self._iv_history[symbol] = self._iv_history[symbol][-self.lookback_period:]

        iv_list = self._iv_history[symbol]

        # Calculate metrics
        min_iv = min(iv_list)
        max_iv = max(iv_list)
        avg_iv = sum(iv_list) / len(iv_list)

        # IV Rank: (current - min) / (max - min)
        iv_range = max_iv - min_iv
        iv_rank = ((current_iv - min_iv) / iv_range * 100) if iv_range > 0 else 50

        # IV Percentile: % of days IV was lower
        below_count = sum(1 for iv in iv_list if iv < current_iv)
        iv_percentile = (below_count / len(iv_list)) * 100

        # Determine regime
        if iv_percentile > 80:
            regime = "high"
        elif iv_percentile < 20:
            regime = "low"
        else:
            regime = "normal"

        return IVMetrics(
            current_iv=current_iv,
            iv_percentile=iv_percentile,
            iv_rank=iv_rank,
            avg_iv=avg_iv,
            min_iv=min_iv,
            max_iv=max_iv,
            regime=regime,
        )


# =============================================================================
# Max Pain Calculations
# =============================================================================

def calculate_max_pain(
    strikes: List[int],
    ce_oi: Dict[int, int],
    pe_oi: Dict[int, int],
    spot_price: float,
    days_to_expiry: int = 0,
) -> MaxPainData:
    """
    Calculate max pain strike and distance.

    Max Pain = strike where total option buyer losses are maximized
    (equivalently, where option writers profit most)
    """
    if not strikes:
        return MaxPainData(
            strike=int(spot_price),
            distance_points=0,
            distance_pct=0,
            direction="at",
            pinning_probability=0,
        )

    min_pain = float('inf')
    max_pain_strike = strikes[0]

    for strike in strikes:
        total_pain = 0

        # CE buyer loss if spot < strike
        for s, oi in ce_oi.items():
            if strike < s:
                total_pain += (s - strike) * oi

        # PE buyer loss if spot > strike
        for s, oi in pe_oi.items():
            if strike > s:
                total_pain += (strike - s) * oi

        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = strike

    # Calculate distance
    distance_points = spot_price - max_pain_strike
    distance_pct = (distance_points / spot_price) * 100 if spot_price > 0 else 0

    if distance_points > 0:
        direction = "above"
    elif distance_points < 0:
        direction = "below"
    else:
        direction = "at"

    # Pinning probability increases near expiry
    if days_to_expiry <= 0:
        pinning_prob = 0.8
    elif days_to_expiry == 1:
        pinning_prob = 0.6
    elif days_to_expiry <= 3:
        pinning_prob = 0.4
    else:
        pinning_prob = 0.2

    # Adjust for distance - closer = higher probability
    distance_factor = max(0, 1 - abs(distance_pct) / 2)
    pinning_prob *= distance_factor

    return MaxPainData(
        strike=max_pain_strike,
        distance_points=abs(distance_points),
        distance_pct=abs(distance_pct),
        direction=direction,
        pinning_probability=pinning_prob,
    )


# =============================================================================
# OI Flow Calculations
# =============================================================================

class OIFlowCalculator:
    """Track OI changes and buildup patterns."""

    def __init__(self):
        self._prev_oi: Dict[str, Dict[str, int]] = {}  # symbol -> {ce_oi, pe_oi, total}

    def calculate(
        self,
        symbol: str,
        ce_oi: int,
        pe_oi: int,
        price_change: float,  # Positive = up, negative = down
    ) -> OIFlowData:
        """Calculate OI flow metrics."""
        total_oi = ce_oi + pe_oi

        # Get previous OI
        prev = self._prev_oi.get(symbol, {"ce_oi": ce_oi, "pe_oi": pe_oi, "total": total_oi})

        oi_change = total_oi - prev["total"]
        ce_oi_change = ce_oi - prev["ce_oi"]
        pe_oi_change = pe_oi - prev["pe_oi"]

        oi_change_rate = (oi_change / prev["total"] * 100) if prev["total"] > 0 else 0

        # Determine buildup type based on OI change + price change
        # Long Buildup: Price up + OI up
        # Short Buildup: Price down + OI up
        # Long Unwinding: Price down + OI down
        # Short Covering: Price up + OI down

        if oi_change > 0 and price_change > 0:
            buildup_type = "long_buildup"
            strength = min(1.0, abs(oi_change_rate) / 5)
        elif oi_change > 0 and price_change < 0:
            buildup_type = "short_buildup"
            strength = min(1.0, abs(oi_change_rate) / 5)
        elif oi_change < 0 and price_change < 0:
            buildup_type = "long_unwinding"
            strength = min(1.0, abs(oi_change_rate) / 5)
        elif oi_change < 0 and price_change > 0:
            buildup_type = "short_covering"
            strength = min(1.0, abs(oi_change_rate) / 5)
        else:
            buildup_type = "neutral"
            strength = 0

        # Store current as previous for next calculation
        self._prev_oi[symbol] = {"ce_oi": ce_oi, "pe_oi": pe_oi, "total": total_oi}

        return OIFlowData(
            total_oi=total_oi,
            oi_change=oi_change,
            oi_change_rate=oi_change_rate,
            ce_oi_change=ce_oi_change,
            pe_oi_change=pe_oi_change,
            buildup_type=buildup_type,
            strength=strength,
        )


# =============================================================================
# Volume Acceleration
# =============================================================================

class VolumeAccelerationCalculator:
    """Track volume acceleration and momentum."""

    def __init__(self, lookback: int = 20):
        self.lookback = lookback
        self._volume_history: Dict[str, List[int]] = {}

    def calculate(self, symbol: str, current_volume: int) -> VolumeAcceleration:
        """Calculate volume acceleration metrics."""
        if symbol not in self._volume_history:
            self._volume_history[symbol] = []

        self._volume_history[symbol].append(current_volume)
        if len(self._volume_history[symbol]) > self.lookback:
            self._volume_history[symbol] = self._volume_history[symbol][-self.lookback:]

        vol_list = self._volume_history[symbol]
        avg_volume = sum(vol_list) / len(vol_list) if vol_list else current_volume

        acceleration = current_volume / avg_volume if avg_volume > 0 else 1.0
        is_spike = acceleration > 2.0

        # Determine trend
        if len(vol_list) >= 3:
            recent_avg = sum(vol_list[-3:]) / 3
            older_avg = sum(vol_list[:-3]) / max(1, len(vol_list) - 3) if len(vol_list) > 3 else recent_avg

            if recent_avg > older_avg * 1.2:
                trend = "accelerating"
            elif recent_avg < older_avg * 0.8:
                trend = "decelerating"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # Momentum score
        momentum_score = min(1.0, acceleration / 3) if acceleration > 1 else acceleration / 2

        return VolumeAcceleration(
            current_volume=current_volume,
            avg_volume=int(avg_volume),
            acceleration=acceleration,
            is_spike=is_spike,
            trend=trend,
            momentum_score=momentum_score,
        )


# =============================================================================
# Liquidity Distance
# =============================================================================

def calculate_liquidity_distance(
    spot_price: float,
    strikes: List[int],
    ce_oi: Dict[int, int],
    pe_oi: Dict[int, int],
    oi_threshold_pct: float = 0.1,  # Top 10% OI = heavy
) -> LiquidityMetrics:
    """
    Calculate distance to heavy OI strikes (liquidity zones).

    Heavy OI strikes often act as support/resistance.
    """
    if not strikes or not ce_oi or not pe_oi:
        return LiquidityMetrics(
            nearest_heavy_ce_strike=int(spot_price),
            nearest_heavy_pe_strike=int(spot_price),
            ce_distance_points=0,
            pe_distance_points=0,
            liquidity_zone=(int(spot_price), int(spot_price)),
            in_liquidity_zone=True,
            liquidity_score=0.5,
        )

    # Find heavy OI strikes (top 10%)
    total_ce_oi = sum(ce_oi.values())
    total_pe_oi = sum(pe_oi.values())

    ce_threshold = total_ce_oi * oi_threshold_pct
    pe_threshold = total_pe_oi * oi_threshold_pct

    heavy_ce_strikes = [s for s, oi in ce_oi.items() if oi >= ce_threshold]
    heavy_pe_strikes = [s for s, oi in pe_oi.items() if oi >= pe_threshold]

    # Find nearest heavy strikes
    if heavy_ce_strikes:
        nearest_ce = min(heavy_ce_strikes, key=lambda s: abs(s - spot_price))
    else:
        nearest_ce = max(ce_oi.keys(), key=lambda s: ce_oi[s]) if ce_oi else int(spot_price)

    if heavy_pe_strikes:
        nearest_pe = min(heavy_pe_strikes, key=lambda s: abs(s - spot_price))
    else:
        nearest_pe = max(pe_oi.keys(), key=lambda s: pe_oi[s]) if pe_oi else int(spot_price)

    ce_distance = abs(spot_price - nearest_ce)
    pe_distance = abs(spot_price - nearest_pe)

    # Liquidity zone
    zone_low = min(nearest_ce, nearest_pe)
    zone_high = max(nearest_ce, nearest_pe)
    in_zone = zone_low <= spot_price <= zone_high

    # Liquidity score (closer to heavy OI = higher score)
    avg_distance = (ce_distance + pe_distance) / 2
    step = strikes[1] - strikes[0] if len(strikes) > 1 else 50
    liquidity_score = max(0, 1 - (avg_distance / (step * 5)))

    return LiquidityMetrics(
        nearest_heavy_ce_strike=nearest_ce,
        nearest_heavy_pe_strike=nearest_pe,
        ce_distance_points=ce_distance,
        pe_distance_points=pe_distance,
        liquidity_zone=(zone_low, zone_high),
        in_liquidity_zone=in_zone,
        liquidity_score=liquidity_score,
    )


# =============================================================================
# Order Flow & Microstructure
# =============================================================================

def calculate_order_flow(
    bid_price: float,
    ask_price: float,
    bid_qty: int,
    ask_qty: int,
    last_price: float,
    prev_spread: float = 0,
) -> OrderFlowData:
    """
    Calculate order flow imbalance and microstructure metrics.
    """
    total_qty = bid_qty + ask_qty

    # Imbalance: positive = buying pressure, negative = selling pressure
    if total_qty > 0:
        imbalance = (bid_qty - ask_qty) / total_qty
    else:
        imbalance = 0

    # Aggression detection
    mid_price = (bid_price + ask_price) / 2
    if last_price >= ask_price:
        aggression = "bid_aggressive"  # Buyer lifting offer
    elif last_price <= bid_price:
        aggression = "ask_aggressive"  # Seller hitting bid
    else:
        aggression = "balanced"

    # Spread metrics
    spread = ask_price - bid_price
    spread_bps = (spread / mid_price) * 10000 if mid_price > 0 else 0
    spread_compression = spread < prev_spread if prev_spread > 0 else False

    return OrderFlowData(
        bid_volume=bid_qty,
        ask_volume=ask_qty,
        imbalance=imbalance,
        aggression=aggression,
        spread_compression=spread_compression,
        spread_bps=spread_bps,
    )


# =============================================================================
# Gamma Exposure
# =============================================================================

def calculate_gamma_exposure(
    spot_price: float,
    strikes: List[int],
    ce_oi: Dict[int, int],
    pe_oi: Dict[int, int],
    ce_gamma: Dict[int, float],
    pe_gamma: Dict[int, float],
    lot_size: int = 50,
) -> GammaExposure:
    """
    Calculate gamma exposure zones.

    Dealer gamma hedging creates:
    - Long gamma: Dealers sell on rallies, buy on dips (dampens moves)
    - Short gamma: Dealers buy on rallies, sell on dips (amplifies moves)
    """
    net_gamma = 0
    strike_gamma: Dict[int, float] = {}

    for strike in strikes:
        ce_g = ce_gamma.get(strike, 0) * ce_oi.get(strike, 0) * lot_size
        pe_g = pe_gamma.get(strike, 0) * pe_oi.get(strike, 0) * lot_size

        # Dealers are typically short options, so negate
        strike_gamma[strike] = -(ce_g + pe_g)
        net_gamma += strike_gamma[strike]

    # Find gamma flip strike (where cumulative gamma changes sign)
    gamma_flip = int(spot_price)
    cumulative = 0
    for strike in sorted(strikes):
        prev_cumulative = cumulative
        cumulative += strike_gamma.get(strike, 0)
        if prev_cumulative * cumulative < 0:  # Sign change
            gamma_flip = strike
            break

    # Find high gamma strikes (pin strikes)
    gamma_values = [(s, abs(g)) for s, g in strike_gamma.items()]
    gamma_values.sort(key=lambda x: x[1], reverse=True)
    pin_strikes = [s for s, g in gamma_values[:3]]  # Top 3

    # Find gamma walls
    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot_price))

    gamma_wall_above = None
    for i in range(atm_idx + 1, len(strikes)):
        if abs(strike_gamma.get(strikes[i], 0)) > abs(net_gamma) * 0.2:
            gamma_wall_above = strikes[i]
            break

    gamma_wall_below = None
    for i in range(atm_idx - 1, -1, -1):
        if abs(strike_gamma.get(strikes[i], 0)) > abs(net_gamma) * 0.2:
            gamma_wall_below = strikes[i]
            break

    return GammaExposure(
        net_gamma=net_gamma,
        gamma_flip_strike=gamma_flip,
        dealer_long_gamma=net_gamma > 0,
        pin_strikes=pin_strikes,
        gamma_wall_above=gamma_wall_above,
        gamma_wall_below=gamma_wall_below,
    )


# =============================================================================
# Unified Indicator Engine
# =============================================================================

class IndicatorEngine:
    """
    Unified indicator engine for all scalping metrics.

    Maintains state across calculations and provides a single interface
    for all indicator calculations.
    """

    def __init__(self):
        self.atr = ATRCalculator(period=14)
        self.iv = IVCalculator(lookback_period=252)
        self.oi_flow = OIFlowCalculator()
        self.volume = VolumeAccelerationCalculator(lookback=20)
        self._prev_spreads: Dict[str, float] = {}

    def calculate_all(
        self,
        symbol: str,
        spot_data: Dict[str, Any],
        option_chain: Dict[str, Any],
        lot_size: int = 50,
    ) -> Dict[str, Any]:
        """
        Calculate all indicators for a symbol.

        Returns dict with all indicator results.
        """
        results = {}

        # ATR
        if all(k in spot_data for k in ["high", "low", "close", "prev_close"]):
            results["atr"] = self.atr.calculate(
                symbol,
                spot_data["high"],
                spot_data["low"],
                spot_data["close"],
                spot_data["prev_close"],
            )

        # IV metrics
        avg_iv = option_chain.get("avg_iv", option_chain.get("atm_iv", 15))
        if avg_iv:
            results["iv"] = self.iv.calculate(symbol, avg_iv)

        # Extract chain data
        strikes = option_chain.get("strikes", [])
        ce_oi = option_chain.get("ce_oi", {})
        pe_oi = option_chain.get("pe_oi", {})
        ce_gamma = option_chain.get("ce_gamma", {})
        pe_gamma = option_chain.get("pe_gamma", {})

        spot_price = spot_data.get("ltp", spot_data.get("close", 0))

        # Max Pain
        if strikes and ce_oi and pe_oi:
            days_to_expiry = option_chain.get("days_to_expiry", 7)
            results["max_pain"] = calculate_max_pain(
                strikes, ce_oi, pe_oi, spot_price, days_to_expiry
            )

        # OI Flow
        total_ce_oi = sum(ce_oi.values()) if ce_oi else 0
        total_pe_oi = sum(pe_oi.values()) if pe_oi else 0
        price_change = spot_data.get("change", 0)
        if total_ce_oi or total_pe_oi:
            results["oi_flow"] = self.oi_flow.calculate(
                symbol, total_ce_oi, total_pe_oi, price_change
            )

        # Volume Acceleration
        volume = spot_data.get("volume", 0)
        if volume:
            results["volume"] = self.volume.calculate(symbol, volume)

        # Liquidity Distance
        if strikes and ce_oi and pe_oi:
            results["liquidity"] = calculate_liquidity_distance(
                spot_price, strikes, ce_oi, pe_oi
            )

        # Order Flow (if bid/ask available)
        if all(k in option_chain for k in ["atm_bid", "atm_ask", "atm_bid_qty", "atm_ask_qty"]):
            prev_spread = self._prev_spreads.get(symbol, 0)
            results["order_flow"] = calculate_order_flow(
                option_chain["atm_bid"],
                option_chain["atm_ask"],
                option_chain["atm_bid_qty"],
                option_chain["atm_ask_qty"],
                option_chain.get("atm_ltp", spot_price),
                prev_spread,
            )
            self._prev_spreads[symbol] = option_chain["atm_ask"] - option_chain["atm_bid"]

        # Gamma Exposure
        if strikes and ce_oi and pe_oi and ce_gamma and pe_gamma:
            results["gamma"] = calculate_gamma_exposure(
                spot_price, strikes, ce_oi, pe_oi, ce_gamma, pe_gamma, lot_size
            )

        return results


# Global indicator engine instance
_indicator_engine: Optional[IndicatorEngine] = None


def get_indicator_engine() -> IndicatorEngine:
    """Get or create the global indicator engine."""
    global _indicator_engine
    if _indicator_engine is None:
        _indicator_engine = IndicatorEngine()
    return _indicator_engine

"""
Analysis Layer Agents - Market structure and signal detection.

Agents:
4. StructureAgent - BOS, MSS, Liquidity sweeps, VWAP
5. MomentumAgent - Futures momentum, volume spikes, gamma zones
6. TrapDetectorAgent - OI traps, PCR spikes, bid/ask imbalance
7. StrikeSelectorAgent - Select optimal OTM strikes

All analysis agents use LLM Debate to validate unclear signals and improve accuracy.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
from typing import Any, Dict, List, Optional, Tuple

# Use local base module
from ..base import BaseBot, BotContext, BotResult, BotStatus
from ..config import ScalpingConfig, IndexConfig, get_index_config, IndexType

# Import debate integration for analysis validation
try:
    from ..debate_integration import (
        debate_strike_selection,
        debate_analysis,
        check_debate_available,
    )
    from ..debate_client import get_debate_client, DebateResult
    HAS_DEBATE = True
except ImportError:
    HAS_DEBATE = False


@dataclass
class StructureLevel:
    """Key market structure level."""
    price: float
    level_type: str  # high, low, swing_high, swing_low, liquidity_zone
    strength: float  # 0-1
    timestamp: datetime
    tested_count: int = 0


@dataclass
class StructureBreak:
    """Break of structure event."""
    symbol: str
    break_type: str  # bos_bullish, bos_bearish, mss_bullish, mss_bearish
    break_price: float
    previous_level: float
    strength: float
    timestamp: datetime


@dataclass
class MomentumSignal:
    """Momentum detection signal."""
    symbol: str
    signal_type: str  # futures_surge, volume_spike, gamma_expansion
    strength: float  # 0-1
    price_move: float
    volume_multiple: float
    option_expansion_pct: float
    timestamp: datetime
    direction: str = "neutral"


@dataclass
class TrapSignal:
    """Retail trap detection signal."""
    symbol: str
    trap_type: str  # long_trap, short_trap, liquidity_sweep
    confidence: float
    oi_change: int
    pcr_change: float
    bid_ask_imbalance: float
    timestamp: datetime


@dataclass
class StrikeSelection:
    """Selected option strike for trading."""
    symbol: str
    strike: int
    option_type: str  # CE or PE
    premium: float
    delta: float
    spread: float
    spread_pct: float
    volume: int
    oi: int
    score: float  # Selection score 0-1
    reasons: List[str]
    confidence: float = 0.0
    entry: float = 0.0
    sl: float = 0.0
    t1: float = 0.0
    status: str = ""
    action: str = ""
    source: str = "live"


class StructureAgent(BaseBot):
    """
    Agent 4: Structure Agent

    Detects market structure:
    - Previous high/low
    - Liquidity sweeps
    - Break of Structure (BOS)
    - Market Structure Shift (MSS)
    - VWAP deviation
    """

    BOT_TYPE = "structure"
    REQUIRES_LLM = False  # DISABLED - debate causes latency in execution path

    def __init__(self, timeframes: List[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.timeframes = timeframes or ["1m", "3m", "5m"]
        self._structure_cache: Dict[str, List[StructureLevel]] = {}
        self._last_structure: Dict[str, Dict] = {}

    def get_description(self) -> str:
        return f"Market structure analysis on {self.timeframes}"

    async def execute(self, context: BotContext) -> BotResult:
        """Analyze market structure for all indices."""
        spot_data = context.data.get("spot_data", {})
        config = context.data.get("config", ScalpingConfig())

        structures = {}
        breaks = []
        vwap_signals = []

        for symbol, spot in spot_data.items():
            # Analyze structure
            structure = await self._analyze_structure(symbol, spot, context)
            structures[symbol] = structure

            # Check for breaks
            if structure.get("break"):
                breaks.append(structure["break"])

            # Check VWAP deviation
            vwap_dev = self._check_vwap_deviation(spot, config)
            if vwap_dev:
                vwap_signals.append(vwap_dev)

        context.data["market_structure"] = structures
        context.data["structure_breaks"] = breaks
        context.data["vwap_signals"] = vwap_signals

        # Skip debate in execution path - causes latency
        debate_result = None

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "structures": {s: st.get("summary") for s, st in structures.items()},
                "breaks": [b.__dict__ if hasattr(b, '__dict__') else b for b in breaks],
                "vwap_deviations": vwap_signals,
                "debate_used": debate_result is not None,
            },
            metrics={
                "symbols_analyzed": len(structures),
                "breaks_detected": len(breaks),
                "vwap_signals": len(vwap_signals),
            },
        )

    async def _analyze_structure(
        self, symbol: str, spot: Any, context: BotContext
    ) -> Dict:
        """Analyze structure for a symbol."""
        # Get price history from context or generate
        candles = context.data.get(f"candles_{symbol}", [])
        timeframe_view = self._build_timeframe_view(candles)
        default_trend = "bullish" if spot.ltp > spot.vwap else "bearish"

        if len(candles) < 5:
            return {
                "symbol": symbol,
                "current_price": spot.ltp,
                "swing_highs": [],
                "swing_lows": [],
                "break": None,
                "trend": default_trend,
                "confidence": min(0.45, 0.08 * len(candles)),
                "timeframes": timeframe_view,
                "timeframe_alignment": self._build_timeframe_alignment(timeframe_view),
                "summary": f"{symbol}: structure warming up ({len(candles)}/5 candles)",
            }

        # Find swing highs and lows
        swing_highs = self._find_swing_highs(candles)
        swing_lows = self._find_swing_lows(candles)

        # Check for break of structure
        break_signal = self._detect_bos(
            spot.ltp, swing_highs, swing_lows, symbol
        )

        # Calculate confidence
        confidence = self._calculate_structure_confidence(
            swing_highs, swing_lows, candles
        )

        return {
            "symbol": symbol,
            "current_price": spot.ltp,
            "swing_highs": swing_highs[-3:] if swing_highs else [],
            "swing_lows": swing_lows[-3:] if swing_lows else [],
            "break": break_signal,
            "trend": default_trend,
            "confidence": confidence,
            "timeframes": timeframe_view,
            "timeframe_alignment": self._build_timeframe_alignment(timeframe_view),
            "summary": f"{symbol}: {'Bullish' if spot.ltp > spot.vwap else 'Bearish'} bias, conf={confidence:.2f}",
        }

    def _generate_sample_structure(self, symbol: str, current_price: float) -> Dict:
        """Generate sample structure for testing."""
        import random

        swing_high = current_price + random.uniform(50, 150)
        swing_low = current_price - random.uniform(50, 150)
        trend = "bullish" if random.random() > 0.5 else "bearish"

        # Randomly generate a break signal
        has_break = random.random() > 0.7
        break_signal = None

        if has_break:
            break_signal = StructureBreak(
                symbol=symbol,
                break_type=f"bos_{trend}",
                break_price=current_price,
                previous_level=swing_low if trend == "bullish" else swing_high,
                strength=random.uniform(0.5, 1.0),
                timestamp=datetime.now(),
            )

        return {
            "symbol": symbol,
            "current_price": current_price,
            "swing_highs": [swing_high],
            "swing_lows": [swing_low],
            "break": break_signal,
            "trend": trend,
            "confidence": random.uniform(0.5, 0.95),
            "summary": f"{symbol}: {trend.capitalize()} bias with {'BOS' if has_break else 'no break'}",
        }

    def _find_swing_highs(self, candles: List[Dict]) -> List[float]:
        """Find swing high points."""
        highs = []
        for i in range(2, len(candles) - 2):
            if (candles[i]["high"] > candles[i-1]["high"] and
                candles[i]["high"] > candles[i-2]["high"] and
                candles[i]["high"] > candles[i+1]["high"] and
                candles[i]["high"] > candles[i+2]["high"]):
                highs.append(candles[i]["high"])
        return highs

    def _find_swing_lows(self, candles: List[Dict]) -> List[float]:
        """Find swing low points."""
        lows = []
        for i in range(2, len(candles) - 2):
            if (candles[i]["low"] < candles[i-1]["low"] and
                candles[i]["low"] < candles[i-2]["low"] and
                candles[i]["low"] < candles[i+1]["low"] and
                candles[i]["low"] < candles[i+2]["low"]):
                lows.append(candles[i]["low"])
        return lows

    def _build_timeframe_view(self, candles: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        one_min = self._analyze_one_minute_momentum(candles)
        three_min = self._analyze_three_minute_breakout(candles)
        five_min = self._analyze_five_minute_trend(candles)
        return {
            "1m": one_min,
            "3m": three_min,
            "5m": five_min,
        }

    def _build_timeframe_alignment(self, timeframes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        one_min = dict(timeframes.get("1m", {}) or {})
        three_min = dict(timeframes.get("3m", {}) or {})
        five_min = dict(timeframes.get("5m", {}) or {})
        bullish = (
            one_min.get("trend") == "bullish"
            and three_min.get("breakout") == "bullish"
            and five_min.get("trend") == "bullish"
        )
        bearish = (
            one_min.get("trend") == "bearish"
            and three_min.get("breakout") == "bearish"
            and five_min.get("trend") == "bearish"
        )
        return {
            "bullish": bullish,
            "bearish": bearish,
            "three_tf_aligned": bullish or bearish,
        }

    def _analyze_one_minute_momentum(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(candles) < 2:
            return {"available": False, "trend": "neutral", "momentum_points": 0.0}
        previous_close = float(candles[-2].get("close", 0.0) or 0.0)
        current_close = float(candles[-1].get("close", previous_close) or previous_close)
        delta = current_close - previous_close
        if delta > 0:
            trend = "bullish"
        elif delta < 0:
            trend = "bearish"
        else:
            trend = "neutral"
        return {
            "available": True,
            "trend": trend,
            "momentum_points": round(abs(delta), 4),
            "close_change": round(delta, 4),
        }

    def _analyze_three_minute_breakout(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        groups = self._aggregate_candles(candles, 3, max_groups=2)
        if len(groups) < 2:
            return {"available": False, "trend": "neutral", "breakout": None, "strength": 0.0}
        previous_group, current_group = groups[-2], groups[-1]
        breakout = None
        reference_level = 0.0
        if current_group["close"] > previous_group["high"]:
            breakout = "bullish"
            reference_level = previous_group["high"]
        elif current_group["close"] < previous_group["low"]:
            breakout = "bearish"
            reference_level = previous_group["low"]

        close_delta = float(current_group["close"] - previous_group["close"])
        if close_delta > 0:
            trend = "bullish"
        elif close_delta < 0:
            trend = "bearish"
        else:
            trend = "neutral"
        strength = 0.0
        if breakout is not None and reference_level:
            strength = abs(current_group["close"] - reference_level) / max(abs(reference_level), 1e-9)
        return {
            "available": True,
            "trend": trend,
            "breakout": breakout,
            "strength": round(strength, 6),
            "close_change": round(close_delta, 4),
        }

    def _analyze_five_minute_trend(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        window = candles[-5:]
        if len(window) < 5:
            return {"available": False, "trend": "neutral", "change": 0.0}
        start_open = float(window[0].get("open", window[0].get("close", 0.0)) or 0.0)
        end_close = float(window[-1].get("close", start_open) or start_open)
        delta = end_close - start_open
        if delta > 0:
            trend = "bullish"
        elif delta < 0:
            trend = "bearish"
        else:
            trend = "neutral"
        return {
            "available": True,
            "trend": trend,
            "change": round(delta, 4),
        }

    def _aggregate_candles(
        self,
        candles: List[Dict[str, Any]],
        group_size: int,
        *,
        max_groups: int = 0,
    ) -> List[Dict[str, Any]]:
        if group_size <= 0 or len(candles) < group_size:
            return []
        usable = len(candles) // group_size * group_size
        if usable < group_size:
            return []
        trimmed = candles[-usable:]
        groups: List[Dict[str, Any]] = []
        for start in range(0, len(trimmed), group_size):
            chunk = trimmed[start:start + group_size]
            if len(chunk) < group_size:
                continue
            groups.append(
                {
                    "open": float(chunk[0].get("open", chunk[0].get("close", 0.0)) or 0.0),
                    "high": max(float(candle.get("high", candle.get("close", 0.0)) or 0.0) for candle in chunk),
                    "low": min(float(candle.get("low", candle.get("close", 0.0)) or 0.0) for candle in chunk),
                    "close": float(chunk[-1].get("close", chunk[-1].get("open", 0.0)) or 0.0),
                    "timestamp": chunk[-1].get("timestamp"),
                }
            )
        if max_groups > 0:
            return groups[-max_groups:]
        return groups

    def _detect_bos(
        self,
        current_price: float,
        swing_highs: List[float],
        swing_lows: List[float],
        symbol: str,
    ) -> Optional[StructureBreak]:
        """Detect break of structure."""
        if not swing_highs or not swing_lows:
            return None

        last_high = swing_highs[-1] if swing_highs else current_price
        last_low = swing_lows[-1] if swing_lows else current_price

        # Check for bullish BOS (break above previous high)
        if current_price > last_high:
            return StructureBreak(
                symbol=symbol,
                break_type="bos_bullish",
                break_price=current_price,
                previous_level=last_high,
                strength=min(1.0, (current_price - last_high) / last_high * 100),
                timestamp=datetime.now(),
            )

        # Check for bearish BOS (break below previous low)
        if current_price < last_low:
            return StructureBreak(
                symbol=symbol,
                break_type="bos_bearish",
                break_price=current_price,
                previous_level=last_low,
                strength=min(1.0, (last_low - current_price) / last_low * 100),
                timestamp=datetime.now(),
            )

        return None

    def _calculate_structure_confidence(
        self,
        swing_highs: List[float],
        swing_lows: List[float],
        candles: List[Dict],
    ) -> float:
        """Calculate confidence in structure analysis."""
        confidence = 0.5  # Base confidence

        # More swing points = higher confidence
        confidence += min(0.2, len(swing_highs) * 0.05)
        confidence += min(0.2, len(swing_lows) * 0.05)

        # Recent price action clarity
        if len(candles) >= 10:
            recent_range = max(c["high"] for c in candles[-10:]) - min(c["low"] for c in candles[-10:])
            avg_candle_range = sum(c["high"] - c["low"] for c in candles[-10:]) / 10
            if avg_candle_range > 0:
                range_ratio = recent_range / avg_candle_range
                confidence += min(0.1, range_ratio * 0.02)

        return min(1.0, confidence)

    def _check_vwap_deviation(self, spot: Any, config: ScalpingConfig) -> Optional[Dict]:
        """Check for significant VWAP deviation."""
        if not hasattr(spot, 'vwap') or spot.vwap == 0:
            return None

        deviation_pct = abs(spot.ltp - spot.vwap) / spot.vwap * 100

        if deviation_pct >= config.vwap_deviation_threshold:
            return {
                "symbol": spot.symbol,
                "current_price": spot.ltp,
                "vwap": spot.vwap,
                "deviation_pct": round(deviation_pct, 2),
                "direction": "above" if spot.ltp > spot.vwap else "below",
            }

        return None

    async def _debate_structure(
        self,
        symbols: List[str],
        structures: Dict,
        context: BotContext,
    ) -> Optional[Dict]:
        """Use LLM debate for unclear structure."""
        task = f"""
        Analyze market structure for these indices with unclear signals:

        {[f"{s}: {structures[s].get('summary', 'No data')}" for s in symbols]}

        Based on the swing highs/lows and current price action:
        1. What is the dominant trend?
        2. Are there any liquidity zones being tested?
        3. Is a structure break imminent?
        4. What entry direction is favored?

        Provide specific, actionable analysis.
        """

        try:
            result = await self.request_llm_debate(
                task=task,
                project_path=str(context.data.get("project_path", ".")),
                max_rounds=2,
            )
            return result
        except Exception:
            return None


class MomentumAgent(BaseBot):
    """
    Agent 5: Momentum Agent

    Detects momentum events:
    - Futures move ≥20-30 points quickly
    - Volume spike 3-10× average
    - ATM option expands 15-25% within seconds
    - Gamma expansion zones
    """

    BOT_TYPE = "momentum"
    REQUIRES_LLM = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._volume_history: Dict[str, List[int]] = {}
        self._premium_history: Dict[str, List[float]] = {}

    def get_description(self) -> str:
        return "Momentum detection for scalping entries"

    async def execute(self, context: BotContext) -> BotResult:
        """Detect momentum signals."""
        futures_data = context.data.get("futures_data", {})
        futures_momentum = context.data.get("futures_momentum", {})
        option_chains = context.data.get("option_chains", {})
        config = context.data.get("config", ScalpingConfig())

        signals = []

        for symbol in futures_data.keys():
            # Check futures momentum
            fut_signal = self._check_futures_momentum(
                symbol, futures_momentum.get(symbol, {}), config
            )
            if fut_signal:
                signals.append(fut_signal)

            # Check volume spike
            vol_signal = self._check_volume_spike(
                symbol, option_chains.get(symbol), config
            )
            if vol_signal:
                signals.append(vol_signal)

            # Check option expansion
            exp_signal = self._check_option_expansion(
                symbol, option_chains.get(symbol), config
            )
            if exp_signal:
                signals.append(exp_signal)

            # Check gamma zones
            gamma_signal = self._check_gamma_zone(
                symbol, option_chains.get(symbol), config
            )
            if gamma_signal:
                signals.append(gamma_signal)

        context.data["momentum_signals"] = signals

        # Determine if momentum is strong enough for entry
        strong_signals = [s for s in signals if s.strength >= 0.7]

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "signals": [s.__dict__ for s in signals],
                "strong_signals": len(strong_signals),
                "entry_ready": len(strong_signals) >= 2,
            },
            metrics={
                "total_signals": len(signals),
                "strong_signals": len(strong_signals),
            },
        )

    def _check_futures_momentum(
        self, symbol: str, momentum: Dict, config: ScalpingConfig
    ) -> Optional[MomentumSignal]:
        """Check for futures momentum surge."""
        if not momentum:
            return None

        raw_price_change = float(momentum.get("price_change", 0) or 0)
        price_change = abs(raw_price_change)
        idx_config = self._get_index_config(symbol)
        threshold = idx_config.momentum_threshold if idx_config else 25

        if price_change >= threshold:
            return MomentumSignal(
                symbol=symbol,
                signal_type="futures_surge",
                strength=min(1.0, price_change / threshold),
                price_move=raw_price_change,
                volume_multiple=0,
                option_expansion_pct=0,
                timestamp=datetime.now(),
                direction="bullish" if raw_price_change > 0 else "bearish" if raw_price_change < 0 else "neutral",
            )

        return None

    def _check_volume_spike(
        self, symbol: str, chain: Any, config: ScalpingConfig
    ) -> Optional[MomentumSignal]:
        """Check for volume spike in options."""
        if not chain:
            return None

        # Calculate average volume from history
        if symbol not in self._volume_history:
            self._volume_history[symbol] = []

        total_volume = sum(opt.volume for opt in chain.options)
        self._volume_history[symbol].append(total_volume)

        # Keep last 20 readings
        if len(self._volume_history[symbol]) > 20:
            self._volume_history[symbol] = self._volume_history[symbol][-20:]

        if len(self._volume_history[symbol]) >= 5:
            avg_volume = sum(self._volume_history[symbol][:-1]) / (len(self._volume_history[symbol]) - 1)
            spike_multiple = total_volume / avg_volume if avg_volume > 0 else 1

            idx_config = self._get_index_config(symbol)
            threshold = idx_config.volume_spike_multiplier if idx_config else 5.0

            if spike_multiple >= threshold:
                return MomentumSignal(
                    symbol=symbol,
                    signal_type="volume_spike",
                    strength=min(1.0, spike_multiple / 10),
                    price_move=0,
                    volume_multiple=spike_multiple,
                    option_expansion_pct=0,
                    timestamp=datetime.now(),
                    direction="neutral",
                )

        return None

    def _check_option_expansion(
        self, symbol: str, chain: Any, config: ScalpingConfig
    ) -> Optional[MomentumSignal]:
        """Check for rapid option premium expansion."""
        if not chain:
            return None

        # Track ATM option premiums
        atm_options = [opt for opt in chain.options if opt.strike == chain.atm_strike]
        if not atm_options:
            return None

        atm_premium = sum(opt.ltp for opt in atm_options) / len(atm_options)

        key = f"{symbol}_atm"
        if key not in self._premium_history:
            self._premium_history[key] = []

        self._premium_history[key].append(atm_premium)

        if len(self._premium_history[key]) > 10:
            self._premium_history[key] = self._premium_history[key][-10:]

        if len(self._premium_history[key]) >= 3:
            prev_premium = self._premium_history[key][-3]
            expansion_pct = (atm_premium - prev_premium) / prev_premium * 100 if prev_premium > 0 else 0

            if expansion_pct >= config.option_expansion_threshold * 100:
                return MomentumSignal(
                    symbol=symbol,
                    signal_type="gamma_expansion",
                    strength=min(1.0, expansion_pct / 25),
                    price_move=0,
                    volume_multiple=0,
                    option_expansion_pct=expansion_pct,
                    timestamp=datetime.now(),
                    direction="neutral",
                )

        return None

    def _check_gamma_zone(
        self, symbol: str, chain: Any, config: ScalpingConfig
    ) -> Optional[MomentumSignal]:
        """Check if we're in a gamma expansion zone."""
        if not chain:
            return None

        # Find options with high gamma near ATM
        strike_interval = self._infer_chain_strike_interval(chain)
        high_gamma_options = []
        for opt in chain.options:
            delta = abs(float(getattr(opt, "delta", 0) or 0))
            near_atm = abs(int(getattr(opt, "strike", 0) or 0) - int(getattr(chain, "atm_strike", 0) or 0)) <= max(strike_interval * 2, strike_interval)
            if config.gamma_zone_delta_range[0] <= delta <= config.gamma_zone_delta_range[1]:
                high_gamma_options.append(opt)
            elif delta <= 0.01 and near_atm:
                high_gamma_options.append(opt)

        if len(high_gamma_options) >= 4:  # Multiple strikes in gamma zone
            avg_gamma = sum(max(float(getattr(opt, "gamma", 0) or 0), 0.01) for opt in high_gamma_options) / len(high_gamma_options)

            return MomentumSignal(
                symbol=symbol,
                signal_type="gamma_zone",
                strength=min(1.0, avg_gamma * 100),
                price_move=0,
                volume_multiple=0,
                option_expansion_pct=0,
                timestamp=datetime.now(),
                direction="neutral",
            )

        return None

    def _get_index_config(self, symbol: str) -> Optional[IndexConfig]:
        """Get index configuration for a symbol."""
        for idx_type in IndexType:
            if idx_type.value == symbol:
                return get_index_config(idx_type)
        return None

    def _infer_chain_strike_interval(self, chain: Any) -> int:
        strikes = sorted(
            {
                int(getattr(opt, "strike", 0) or 0)
                for opt in getattr(chain, "options", []) or []
                if int(getattr(opt, "strike", 0) or 0) > 0
            }
        )
        if len(strikes) < 2:
            return 50
        intervals = [b - a for a, b in zip(strikes, strikes[1:]) if b - a > 0]
        return min(intervals) if intervals else 50


class TrapDetectorAgent(BaseBot):
    """
    Agent 6: Trap Detector Agent

    Detects retail traps:
    - Sudden OI buildup
    - OI drop with price increase (short covering)
    - Bid quantity >> Ask quantity
    - PCR spikes
    - Liquidity sweeps near key levels
    """

    BOT_TYPE = "trap_detector"
    REQUIRES_LLM = False  # DISABLED - debate causes latency in execution path

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._oi_history: Dict[str, List[int]] = {}
        self._pcr_history: Dict[str, List[float]] = {}

    def get_description(self) -> str:
        return "Retail trap detection for safer entries"

    async def execute(self, context: BotContext) -> BotResult:
        """Detect trap signals."""
        option_chains = context.data.get("option_chains", {})
        structures = context.data.get("market_structure", {})
        config = context.data.get("config", ScalpingConfig())

        traps = []

        for symbol, chain in option_chains.items():
            # Check OI buildup/unwinding
            oi_trap = self._detect_oi_trap(symbol, chain, config)
            if oi_trap:
                traps.append(oi_trap)

            # Check PCR spike
            pcr_trap = self._detect_pcr_trap(symbol, chain, config)
            if pcr_trap:
                traps.append(pcr_trap)

            # Check bid/ask imbalance
            imbalance_trap = self._detect_bid_ask_trap(symbol, chain, config)
            if imbalance_trap:
                traps.append(imbalance_trap)

            # Check liquidity sweep
            sweep_trap = self._detect_liquidity_sweep(
                symbol, chain, structures.get(symbol, {}), config
            )
            if sweep_trap:
                traps.append(sweep_trap)

        context.data["trap_signals"] = traps

        # Skip debate in execution path - causes latency
        # context.data["trap_debate"] = None

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "traps": [t.__dict__ for t in traps],
                "total_traps": len(traps),
                "high_confidence_traps": len([t for t in traps if t.confidence >= 0.7]),
            },
            metrics={
                "traps_detected": len(traps),
            },
        )

    def _detect_oi_trap(
        self, symbol: str, chain: Any, config: ScalpingConfig
    ) -> Optional[TrapSignal]:
        """Detect OI buildup/unwinding traps."""
        if not chain:
            return None

        total_oi = chain.total_ce_oi + chain.total_pe_oi

        if symbol not in self._oi_history:
            self._oi_history[symbol] = []

        self._oi_history[symbol].append(total_oi)

        if len(self._oi_history[symbol]) > 10:
            self._oi_history[symbol] = self._oi_history[symbol][-10:]

        if len(self._oi_history[symbol]) >= 3:
            prev_oi = self._oi_history[symbol][-3]
            oi_change = (total_oi - prev_oi) / prev_oi if prev_oi > 0 else 0

            if abs(oi_change) >= config.oi_buildup_threshold - 1:
                return TrapSignal(
                    symbol=symbol,
                    trap_type="oi_buildup" if oi_change > 0 else "oi_unwinding",
                    confidence=min(1.0, abs(oi_change)),
                    oi_change=total_oi - prev_oi,
                    pcr_change=0,
                    bid_ask_imbalance=0,
                    timestamp=datetime.now(),
                )

        return None

    def _detect_pcr_trap(
        self, symbol: str, chain: Any, config: ScalpingConfig
    ) -> Optional[TrapSignal]:
        """Detect PCR spike indicating potential trap."""
        if not chain:
            return None

        if symbol not in self._pcr_history:
            self._pcr_history[symbol] = []

        self._pcr_history[symbol].append(chain.pcr)

        if len(self._pcr_history[symbol]) > 10:
            self._pcr_history[symbol] = self._pcr_history[symbol][-10:]

        if len(self._pcr_history[symbol]) >= 3:
            prev_pcr = self._pcr_history[symbol][-3]
            pcr_change = chain.pcr - prev_pcr

            if abs(pcr_change) >= config.pcr_spike_threshold:
                return TrapSignal(
                    symbol=symbol,
                    trap_type="pcr_spike_bearish" if pcr_change > 0 else "pcr_spike_bullish",
                    confidence=min(1.0, abs(pcr_change) / 0.5),
                    oi_change=0,
                    pcr_change=pcr_change,
                    bid_ask_imbalance=0,
                    timestamp=datetime.now(),
                )

        return None

    def _detect_bid_ask_trap(
        self, symbol: str, chain: Any, config: ScalpingConfig
    ) -> Optional[TrapSignal]:
        """Detect bid/ask quantity imbalance."""
        if not chain:
            return None

        # Check ATM options for imbalance
        atm_options = [opt for opt in chain.options if opt.strike == chain.atm_strike]

        total_bid_qty = sum(opt.bid_qty for opt in atm_options)
        total_ask_qty = sum(opt.ask_qty for opt in atm_options)

        if total_ask_qty > 0:
            imbalance = total_bid_qty / total_ask_qty
        else:
            imbalance = 1.0

        if imbalance >= config.bid_ask_imbalance_ratio:
            return TrapSignal(
                symbol=symbol,
                trap_type="bid_heavy_bullish",
                confidence=min(1.0, imbalance / 3),
                oi_change=0,
                pcr_change=0,
                bid_ask_imbalance=imbalance,
                timestamp=datetime.now(),
            )
        elif imbalance <= 1 / config.bid_ask_imbalance_ratio:
            return TrapSignal(
                symbol=symbol,
                trap_type="ask_heavy_bearish",
                confidence=min(1.0, (1/imbalance) / 3),
                oi_change=0,
                pcr_change=0,
                bid_ask_imbalance=imbalance,
                timestamp=datetime.now(),
            )

        return None

    def _detect_liquidity_sweep(
        self, symbol: str, chain: Any, structure: Dict, config: ScalpingConfig
    ) -> Optional[TrapSignal]:
        """Detect liquidity sweep near key levels."""
        if not structure:
            return None

        swing_highs = structure.get("swing_highs", [])
        swing_lows = structure.get("swing_lows", [])
        current_price = structure.get("current_price", 0)

        # Check if price swept a key level
        for high in swing_highs:
            if abs(current_price - high) / high < 0.002:  # Within 0.2%
                return TrapSignal(
                    symbol=symbol,
                    trap_type="liquidity_sweep_high",
                    confidence=0.7,
                    oi_change=0,
                    pcr_change=0,
                    bid_ask_imbalance=0,
                    timestamp=datetime.now(),
                )

        for low in swing_lows:
            if abs(current_price - low) / low < 0.002:
                return TrapSignal(
                    symbol=symbol,
                    trap_type="liquidity_sweep_low",
                    confidence=0.7,
                    oi_change=0,
                    pcr_change=0,
                    bid_ask_imbalance=0,
                    timestamp=datetime.now(),
                )

        return None

    async def _debate_traps(
        self, traps: List[TrapSignal], context: BotContext
    ) -> Optional[Dict]:
        """Use LLM debate to analyze complex trap scenarios."""
        trap_summary = "\n".join([
            f"- {t.symbol}: {t.trap_type} (confidence: {t.confidence:.2f})"
            for t in traps
        ])

        task = f"""
        Multiple trap signals detected in the market:

        {trap_summary}

        Analyze:
        1. Are these independent signals or related?
        2. What is the likely institutional intent?
        3. Which direction (CE or PE) is safer to trade?
        4. Should we wait for confirmation or enter now?

        Provide actionable trading guidance.
        """

        try:
            result = await self.request_llm_debate(
                task=task,
                project_path=str(context.data.get("project_path", ".")),
                max_rounds=2,
            )
            return result
        except Exception:
            return None


class StrikeSelectorAgent(BaseBot):
    """
    Agent 7: Strike Selector Agent

    Selects optimal OTM strikes:
    - 150-300 points OTM (index-specific)
    - Price ₹10-₹25
    - Delta 0.15-0.25
    - Tight bid-ask spread
    - Rising volume and OI
    """

    BOT_TYPE = "strike_selector"
    REQUIRES_LLM = False  # DISABLED - debate causes 15s+ latency in execution path

    def get_description(self) -> str:
        return "Selects optimal far OTM strikes for scalping"

    async def execute(self, context: BotContext) -> BotResult:
        """Select best strikes for each index."""
        option_chains = context.data.get("option_chains", {})
        spot_data = context.data.get("spot_data", {})
        structures = context.data.get("market_structure", {})
        momentum_signals = context.data.get("momentum_signals", [])
        config = context.data.get("config", ScalpingConfig())
        volatility_surface = context.data.get("volatility_surface", {})
        dealer_pressure = context.data.get("dealer_pressure", {})
        replay_mode = bool(context.data.get("replay_mode"))
        replay_payload = context.data.get("replay_payload", {}) if replay_mode else {}

        selections = {}

        for symbol, chain in option_chains.items():
            spot = spot_data.get(symbol)
            structure = structures.get(symbol, {})
            vix = float(context.data.get("vix", 15.0) or 15.0)

            # Determine direction based on structure and momentum
            direction = self._determine_direction(symbol, structure, momentum_signals)

            if replay_mode:
                replay_strikes = self._select_replay_strikes(symbol, chain, replay_payload)
                if replay_strikes:
                    selections[symbol] = replay_strikes
                    continue

            # Select best strikes
            best_strikes = self._select_strikes(
                chain,
                spot.ltp if spot else chain.spot_price,
                direction,
                symbol,
                config,
                vix=vix,
                volatility_surface=volatility_surface.get(symbol, {}),
                dealer_pressure=dealer_pressure.get(symbol, {}),
            )

            if best_strikes:
                selections[symbol] = best_strikes

        context.data["strike_selections"] = selections

        # Skip debate in execution path - causes 15s+ latency
        # Debate only appropriate for learning layers (QuantLearner, StrategyOptimizer)
        debate_result = None

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "selections": {
                    s: [{
                        "strike": sel.strike,
                        "type": sel.option_type,
                        "premium": sel.premium,
                        "score": sel.score,
                        "reasons": sel.reasons,
                    } for sel in sels[:3]]  # Top 3 per symbol
                    for s, sels in selections.items()
                },
                "total_selections": sum(len(s) for s in selections.values()),
            },
            metrics={
                "symbols_with_selection": len(selections),
            },
        )

    def _determine_direction(
        self,
        symbol: str,
        structure: Dict,
        momentum_signals: List[MomentumSignal],
    ) -> str:
        """Determine trade direction based on analysis."""
        # Check structure bias
        trend = structure.get("trend", "neutral")

        # Check momentum signals
        symbol_momentum = [s for s in momentum_signals if s.symbol == symbol]
        momentum_bullish = any(
            getattr(s, "signal_type", "") == "futures_surge"
            and str(getattr(s, "direction", "neutral")) == "bullish"
            for s in symbol_momentum
        )
        momentum_bearish = any(
            getattr(s, "signal_type", "") == "futures_surge"
            and str(getattr(s, "direction", "neutral")) == "bearish"
            for s in symbol_momentum
        )

        if trend == "bullish" or momentum_bullish:
            return "CE"
        elif trend == "bearish" or momentum_bearish:
            return "PE"

        return "CE"  # Default to CE

    @staticmethod
    def _is_expiry_day_for_symbol(symbol: str = "") -> bool:
        """Check if today is expiry day for a specific index using exchange data.

        Reads the expiry schedule cache (populated from Fyers/exchange API)
        rather than assuming fixed weekdays. Falls back to weekday heuristic
        only if cache is unavailable.
        """
        from datetime import date
        from pathlib import Path
        import json as _json

        today = date.today()
        today_str = today.isoformat()

        # Map symbol to index name for cache lookup
        _sym_to_name = {
            "NSE:NIFTY50-INDEX": "NIFTY50",
            "NSE:NIFTYBANK-INDEX": "BANKNIFTY",
            "BSE:SENSEX-INDEX": "SENSEX",
            "NSE:FINNIFTY-INDEX": "FINNIFTY",
            "NSE:MIDCPNIFTY-INDEX": "MIDCPNIFTY",
        }
        index_name = _sym_to_name.get(str(symbol).upper(), "")

        # Priority: exchange-sourced expiry cache
        if index_name:
            for parents_up in (5, 4, 3, 6):
                cache_path = Path(__file__).resolve().parents[parents_up] / "shared_project_engine" / "indices" / ".cache" / "expiry_schedule.json"
                try:
                    if cache_path.exists():
                        data = _json.loads(cache_path.read_text()).get("data", {})
                        info = data.get(index_name, {})
                        if isinstance(info, dict) and info.get("next_expiry"):
                            return info["next_expiry"] == today_str
                except Exception:
                    continue

        # Fallback: weekday heuristic (clearly inferior, log warning)
        import logging
        logging.getLogger("scalping.strike_selector").warning(
            "Expiry cache unavailable for %s — using weekday fallback", symbol
        )
        return today.weekday() in (3, 4)

    @staticmethod
    def _is_expiry_day() -> bool:
        """Legacy global check — DEPRECATED. Use _is_expiry_day_for_symbol()."""
        from datetime import date
        return date.today().weekday() in (3, 4)

    def _select_strikes(
        self,
        chain: Any,
        spot_price: float,
        direction: str,
        symbol: str,
        config: ScalpingConfig,
        vix: float = 15.0,
        volatility_surface: Optional[Dict[str, Any]] = None,
        dealer_pressure: Optional[Dict[str, Any]] = None,
    ) -> List[StrikeSelection]:
        """Select optimal strikes — institutional approach.

        Expiry day:  Allow wider spreads, use strict OTM/premium/delta filters.
        Non-expiry:  Relax premium/delta filters, rank by movement quality
                     (volume×OI momentum, spread tightness, institutional flow).
        """
        idx_config = self._get_index_config(symbol)
        if not idx_config:
            idx_config = get_index_config(IndexType.NIFTY50)
        volatility_surface = volatility_surface or {}
        dealer_pressure = dealer_pressure or {}
        otm_min, otm_max = self._adaptive_otm_range(idx_config, config, vix, volatility_surface)
        is_expiry = self._is_expiry_day_for_symbol(symbol)

        # Non-expiry: relax thresholds to find best-movement strikes
        spread_limit = config.max_bid_ask_spread_pct if is_expiry else config.max_bid_ask_spread_pct * 0.6
        min_vol = config.min_volume_threshold if is_expiry else max(100, config.min_volume_threshold // 5)
        min_oi = config.min_oi_threshold if is_expiry else max(500, config.min_oi_threshold // 5)
        premium_lo = idx_config.premium_min
        premium_hi = idx_config.premium_max if is_expiry else idx_config.premium_max * 4  # wider range on non-expiry

        candidates = []
        adaptive_candidates = []

        for opt in chain.options:
            # Filter by direction
            if opt.option_type != direction:
                continue

            # Calculate OTM distance
            if direction == "CE":
                otm_distance = opt.strike - spot_price
            else:
                otm_distance = spot_price - opt.strike

            # Skip ITM and near ATM
            if otm_distance < idx_config.strike_interval:
                continue

            # Filter by spread — strict on non-expiry (want tight), relaxed on expiry
            if opt.spread_pct > spread_limit:
                continue

            # Filter by liquidity — relaxed on non-expiry to find movement
            if opt.volume < min_vol:
                continue
            if opt.oi < min_oi:
                continue

            # Calculate selection score
            score, reasons = self._calculate_score(opt, idx_config, config, spot_price, volatility_surface, dealer_pressure)

            # Non-expiry: boost score for institutional movement indicators
            if not is_expiry:
                movement_bonus = 0.0
                # High volume relative to OI = institutional entry
                if opt.oi > 0 and opt.volume / opt.oi > 0.1:
                    movement_bonus += 0.15
                    reasons = reasons + ["high_vol_oi_ratio"]
                # Tight spread = institutional interest
                if opt.spread_pct < 2.0:
                    movement_bonus += 0.10
                    reasons = reasons + ["tight_spread"]
                # Good OI buildup
                if opt.oi > config.min_oi_threshold:
                    movement_bonus += 0.05
                    reasons = reasons + ["strong_oi"]
                score += movement_bonus

            entry_price = float(getattr(opt, "ask", 0) or getattr(opt, "ltp", 0) or 0)
            # Scalping SL/target — percentage-based for all premium ranges
            # SL: ~25% of premium (tight scalping risk)
            # Target: ~35% of premium (gives R:R ~1.4)
            sl_price = round(entry_price * 0.75, 2) if entry_price > 0 else 0.0
            first_target_pts = float(getattr(config, "first_target_points", 4.0) or 4.0)
            # Use max of fixed target or 35% of premium
            target_offset = max(first_target_pts, entry_price * 0.35)
            target_price = round(entry_price + target_offset, 2)

            selection = StrikeSelection(
                symbol=opt.symbol,
                strike=opt.strike,
                option_type=opt.option_type,
                premium=opt.ltp,
                delta=opt.delta,
                spread=opt.spread,
                spread_pct=opt.spread_pct,
                volume=opt.volume,
                oi=opt.oi,
                score=score,
                reasons=reasons,
                confidence=score,
                entry=entry_price,
                sl=sl_price,
                t1=target_price,
            )

            delta_reliable = abs(float(opt.delta or 0)) >= 0.01
            delta_ok = (idx_config.delta_min <= abs(opt.delta) <= idx_config.delta_max) if delta_reliable else True
            premium_ok = premium_lo <= opt.ltp <= premium_hi
            otm_ok = otm_min <= otm_distance <= otm_max

            if is_expiry:
                # Expiry: strict filters — must match OTM + premium + delta
                if otm_ok and premium_ok and delta_ok:
                    candidates.append(selection)
                    continue
            else:
                # Non-expiry: institutional approach — prioritize movement quality
                # Accept if premium is in range (wider), skip strict delta/OTM
                if premium_ok:
                    candidates.append(selection)
                    continue

            # Adaptive fallback for both modes
            if premium_ok or (not is_expiry and opt.ltp > 0):
                adaptive_score, adaptive_reasons = self._calculate_adaptive_live_score(
                    opt=opt,
                    idx_config=idx_config,
                    config=config,
                    otm_distance=otm_distance,
                    otm_min=otm_min,
                    otm_max=otm_max,
                    delta_reliable=delta_reliable,
                )
                selection.score = max(selection.score, adaptive_score)
                selection.reasons = selection.reasons + adaptive_reasons
                adaptive_candidates.append(selection)

        # Sort by score descending
        candidates.sort(key=lambda x: x.score, reverse=True)
        if candidates:
            return candidates[:5]

        adaptive_candidates.sort(key=lambda x: x.score, reverse=True)
        return adaptive_candidates[:5]

    def _select_replay_strikes(
        self,
        symbol: str,
        chain: Any,
        replay_payload: Optional[Dict[str, Any]],
    ) -> List[StrikeSelection]:
        """Honor journal-approved replay candidates instead of reapplying live filters."""
        option_rows = replay_payload.get("option_rows", {}) if isinstance(replay_payload, dict) else {}
        rows = option_rows.get(symbol, [])
        if not isinstance(rows, list) or not rows:
            return []

        primary: Dict[Tuple[int, str], StrikeSelection] = {}
        fallback: Dict[Tuple[int, str], StrikeSelection] = {}

        for row in rows:
            if not isinstance(row, dict):
                continue

            option_type = str(row.get("side", row.get("option_type", ""))).upper()
            if option_type not in {"CE", "PE"}:
                continue

            strike = int(float(row.get("strike", row.get("strike_price", 0)) or 0))
            if strike <= 0:
                continue

            status = str(row.get("status", "") or "").strip().upper()
            selected = str(row.get("selected", "") or "").strip().upper() == "Y"
            entry_ready = str(row.get("entry_ready", "") or "").strip().upper() == "Y"
            action = str(row.get("action", "") or "").strip().lower()

            if action in {"skip", "avoid", "reject"} and status != "APPROVED" and not entry_ready:
                continue

            selection = self._build_replay_selection(symbol, chain, row, strike, option_type, status, action)
            if selection is None:
                continue

            key = (selection.strike, selection.option_type)
            if status == "APPROVED" or entry_ready:
                existing = primary.get(key)
                if existing is None or selection.score > existing.score:
                    primary[key] = selection
            elif selected:
                existing = fallback.get(key)
                if existing is None or selection.score > existing.score:
                    fallback[key] = selection

        chosen = list(primary.values()) if primary else list(fallback.values())
        chosen.sort(key=lambda item: item.score, reverse=True)
        return chosen[:5]

    def _build_replay_selection(
        self,
        symbol: str,
        chain: Any,
        row: Dict[str, Any],
        strike: int,
        option_type: str,
        status: str,
        action: str,
    ) -> Optional[StrikeSelection]:
        option = self._find_option(chain, strike, option_type)
        if option is None:
            return None

        confidence = self._normalize_replay_confidence(row.get("confidence", row.get("score", 0.0)))
        premium = float(row.get("entry", getattr(option, "ltp", 0.0)) or getattr(option, "ltp", 0.0) or 0.0)
        score = confidence if confidence > 0 else self._normalize_replay_confidence(getattr(option, "delta", 0.0))

        reasons = ["Replay journal candidate"]
        if status == "APPROVED":
            reasons.append("Journal approved")
        elif str(row.get("entry_ready", "")).strip().upper() == "Y":
            reasons.append("Journal entry ready")
        elif str(row.get("selected", "")).strip().upper() == "Y":
            reasons.append("Journal selected")
        if confidence > 0:
            reasons.append(f"Historical confidence {confidence:.0%}")
        reason_text = str(row.get("reason", "") or "").strip()
        if reason_text:
            reasons.append(reason_text[:120])

        return StrikeSelection(
            symbol=symbol,
            strike=strike,
            option_type=option_type,
            premium=premium,
            delta=float(getattr(option, "delta", 0.0) or 0.0),
            spread=float(getattr(option, "spread", 0.0) or 0.0),
            spread_pct=float(getattr(option, "spread_pct", 0.0) or 0.0),
            volume=int(getattr(option, "volume", 0) or 0),
            oi=int(getattr(option, "oi", 0) or 0),
            score=max(0.5, score),
            reasons=reasons,
            confidence=confidence,
            entry=premium,
            sl=float(row.get("sl", 0.0) or 0.0),
            t1=float(row.get("t1", row.get("target", 0.0)) or 0.0),
            status=status,
            action=action,
            source="replay_journal",
        )

    def _find_option(self, chain: Any, strike: int, option_type: str) -> Optional[Any]:
        for opt in getattr(chain, "options", []) or []:
            if int(getattr(opt, "strike", 0) or 0) == strike and str(getattr(opt, "option_type", "")).upper() == option_type:
                return opt
        return None

    def _normalize_replay_confidence(self, value: Any) -> float:
        try:
            confidence = float(value or 0.0)
        except Exception:
            return 0.0
        if confidence > 1.0:
            confidence /= 100.0
        return max(0.0, min(1.0, confidence))

    def _adaptive_otm_range(
        self,
        idx_config: IndexConfig,
        config: ScalpingConfig,
        vix: float,
        volatility_surface: Dict[str, Any],
    ) -> Tuple[int, int]:
        scale = float(volatility_surface.get("otm_scale", 1.0) or 1.0)
        if scale == 1.0:
            if vix >= config.high_vix_level:
                scale = config.high_vix_otm_scale
            elif vix <= config.low_vix_level:
                scale = config.low_vix_otm_scale

        otm_min = max(idx_config.strike_interval, int(round(idx_config.otm_distance_min * scale / idx_config.strike_interval)) * idx_config.strike_interval)
        otm_max = max(otm_min + idx_config.strike_interval, int(round(idx_config.otm_distance_max * scale / idx_config.strike_interval)) * idx_config.strike_interval)
        return otm_min, otm_max

    def _calculate_score(
        self,
        opt: Any,
        idx_config: IndexConfig,
        config: ScalpingConfig,
        spot_price: float,
        volatility_surface: Dict[str, Any],
        dealer_pressure: Dict[str, Any],
    ) -> Tuple[float, List[str]]:
        """Calculate selection score for an option."""
        score = 0.5  # Base score
        reasons = []

        # Premium sweet spot (prefer ₹12-₹18)
        if 12 <= opt.ltp <= 18:
            score += 0.15
            reasons.append("Optimal premium range")
        elif 10 <= opt.ltp <= 25:
            score += 0.05
            reasons.append("Acceptable premium")

        # Delta sweet spot (prefer 0.18-0.22)
        if 0.18 <= abs(opt.delta) <= 0.22:
            score += 0.15
            reasons.append("Optimal delta")
        elif idx_config.delta_min <= abs(opt.delta) <= idx_config.delta_max:
            score += 0.05
            reasons.append("Acceptable delta")

        # Tight spread bonus
        if opt.spread_pct < 2:
            score += 0.15
            reasons.append("Very tight spread")
        elif opt.spread_pct < 3:
            score += 0.08
            reasons.append("Tight spread")

        # High volume bonus
        if opt.volume > config.min_volume_threshold * 3:
            score += 0.1
            reasons.append("High volume")

        # High OI bonus
        if opt.oi > config.min_oi_threshold * 3:
            score += 0.05
            reasons.append("High OI")

        # OI increasing bonus
        if opt.oi_change > 0:
            score += 0.05
            reasons.append("OI increasing")

        surface_score = float(volatility_surface.get("surface_score", 0.5) or 0.5)
        if surface_score >= 0.7:
            score += 0.05
            reasons.append("Surface favorable")
        elif surface_score <= 0.3:
            score -= 0.05
            reasons.append("Surface cautious")

        gamma_regime = str(dealer_pressure.get("gamma_regime", "neutral"))
        gamma_flip_level = float(dealer_pressure.get("gamma_flip_level", spot_price) or spot_price)
        pinning_score = float(dealer_pressure.get("pinning_score", 0.0) or 0.0)
        pin_distance_pct = abs(spot_price - gamma_flip_level) / max(abs(spot_price), 1.0) * 100
        if gamma_regime == "long" and pinning_score >= float(getattr(config, "dealer_extreme_pinning_score", 0.85) or 0.85):
            score -= 0.08
            reasons.append("Extreme dealer pinning regime")
        elif gamma_regime == "short":
            score += 0.05
            reasons.append("Dealer short gamma acceleration")

        return min(1.0, score), reasons

    def _calculate_adaptive_live_score(
        self,
        *,
        opt: Any,
        idx_config: IndexConfig,
        config: ScalpingConfig,
        otm_distance: float,
        otm_min: int,
        otm_max: int,
        delta_reliable: bool,
    ) -> Tuple[float, List[str]]:
        score = 0.5
        reasons = ["Adaptive premium-band fallback"]

        premium_target = (idx_config.premium_min + idx_config.premium_max) / 2.0
        premium_gap = abs(float(getattr(opt, "ltp", 0) or 0) - premium_target) / max(premium_target, 1.0)
        score += max(0.0, 0.18 - premium_gap * 0.12)

        if otm_distance > otm_max:
            reasons.append(f"Expanded OTM range to {int(round(otm_distance))} pts")
            distance_overflow = (otm_distance - otm_max) / max(otm_max, 1.0)
            score -= min(0.08, distance_overflow * 0.03)
        elif otm_distance < otm_min:
            reasons.append("Closer-to-ATM fallback")
            distance_shortfall = (otm_min - otm_distance) / max(otm_min, 1.0)
            score -= min(0.08, distance_shortfall * 0.04)

        if not delta_reliable:
            reasons.append("Live chain missing Greeks")
            score += 0.04

        if float(getattr(opt, "spread_pct", 0) or 0) <= min(2.5, config.max_bid_ask_spread_pct):
            reasons.append("Execution-grade spread")
            score += 0.08

        if int(getattr(opt, "volume", 0) or 0) >= config.min_volume_threshold * 2:
            reasons.append("Volume confirmation")
            score += 0.08

        if int(getattr(opt, "oi", 0) or 0) >= config.min_oi_threshold * 2:
            reasons.append("High open interest")
            score += 0.05

        return min(1.0, max(0.0, score)), reasons

    def _get_index_config(self, symbol: str) -> Optional[IndexConfig]:
        """Get index configuration for a symbol."""
        for idx_type in IndexType:
            if idx_type.value == symbol:
                return get_index_config(idx_type)
        return None

    async def _debate_selection(
        self, borderline: Dict, context: BotContext
    ) -> Optional[Dict]:
        """Use LLM debate for borderline strike selections."""
        if not HAS_DEBATE:
            return None

        results = {}
        spot_data = context.data.get("spot_data", {})
        structures = context.data.get("market_structure", {})

        for symbol, sels in borderline.items():
            if not sels:
                continue

            best = sels[0]
            spot = spot_data.get(symbol)
            spot_price = spot.ltp if spot else 0
            structure = structures.get(symbol, {})
            regime = structure.get("trend", "neutral")

            # Get VIX from context
            vix = context.data.get("vix", 15.0)

            # Prepare strikes for debate
            strikes_info = [
                {
                    "strike": s.strike,
                    "type": s.option_type,
                    "premium": s.premium,
                    "delta": s.delta,
                    "spread_pct": s.spread_pct,
                }
                for s in sels[:5]
            ]

            try:
                should_use, reason, debate_result = await debate_strike_selection(
                    index=symbol,
                    spot_price=spot_price,
                    direction=best.option_type,
                    strikes=strikes_info,
                    recommended_strike=best.strike,
                    recommended_premium=best.premium,
                    recommended_delta=best.delta,
                    market_regime=regime,
                    vix=vix,
                )

                results[symbol] = {
                    "should_use": should_use,
                    "reason": reason,
                    "confidence": debate_result.confidence if debate_result else 0,
                }

                # Adjust score based on debate result
                if debate_result and should_use:
                    # Boost score if debate approves
                    best.score = min(1.0, best.score + 0.2)
                    best.reasons.append(f"Debate approved: {reason}")

            except Exception as e:
                results[symbol] = {"error": str(e)}

        return results

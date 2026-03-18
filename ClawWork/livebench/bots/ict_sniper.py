"""
ICT Sniper Bot - Institutional-Grade Smart Entry Strategy (Multi-Timeframe)
═══════════════════════════════════════════════════════════════════════════

Converts Pine Script "ICT Sniper Setup [LQ + MSS + FVG]" to Python.
MULTI-TIMEFRAME ANALYSIS: 1m, 5m, 15m

Components:
1. Liquidity Levels (LQ) - Swing highs/lows as buy/sell-side liquidity
2. Liquidity Grab (LQ Grab) - Price sweep + rejection
3. Market Structure Shift (MSS) - Break of swing high/low
4. Fair Value Gap (FVG) - Price gaps (inefficiencies)
5. Displacement Candle - Large range bar
6. Volume Spike - Unusual volume confirmation

Entry: LQ Grab ✓ + MSS ✓ + FVG Tap ✓ + Displacement OK + Volume OK

MUTUAL EXCLUSION: Bull and Bear setups are mutually exclusive.
When a bullish LQ Grab activates, all bearish states are cleared (and vice versa).
This prevents conflicting signals and ensures only one directional bias is active.

Multi-Timeframe: Generates signals for 1m, 5m, and 15m timeframes
- 1m: Quick entries, highest frequency
- 5m: Intermediate entries, better confirmation
- 15m: Structural entries, highest quality

Learning: Adapts vol multiplier, displacement multiplier, swing lookback based on backtest validation.
"""

from dataclasses import dataclass, field
from typing import Any, List, Dict, Optional, Tuple
from datetime import datetime
from collections import deque
import statistics
import logging

from .base import TradingBot, BotSignal, BotPerformance, TradeRecord, SignalType, OptionType, SharedMemory

logger = logging.getLogger(__name__)


@dataclass
class ICTConfig:
    """ICT Sniper configuration - tunable parameters
    
    PHASE 1B OPTIMIZED (2026-03-03) - SYSTEMATICALLY TESTED:
    Comprehensive optimization tested 15 configs across 3 MTF modes
    
    WINNER CONFIG (permissive_Phase1B):
    - vol_multiplier: 1.2 (Phase 1A baseline, validated optimal)
    - displacement_multiplier: 1.3 (Phase 1A baseline, validated optimal)
    - swing_lookback: 10 → 9 (faster swing detection, +2.5% WR)
    - mss_swing_len: 3 → 2 (earlier MSS confirmation, +1.4% WR)
    - MTF Mode: PERMISSIVE (confidence-gated, ≥80% always pass)
    
    Backtest Results (90 days, 577 trades, 3 indices):
    - Win Rate: 53.7% (vs 48.8% baseline, +4.9% improvement) ✓✓
    - P&L: ₹25,647 (vs ₹22,242 baseline, +15.3% improvement) ✓✓
    - Composite Score: 42.5 (BEST of 15 configs)
    
    Beats all alternatives:
    - Conservative (vol=1.3, disp=1.4): 50.3% WR, ₹20K
    - Balanced (vol=1.15, disp=1.25): 50.1% WR, ₹19K
    - Phase1A baseline: 49.4% WR, ₹20.5K
    """
    swing_lookback: int = 9            # ↓ Phase 1B: 10 → 9 (faster swing detection)
    mss_swing_len: int = 2             # ↓ Phase 1B: 3 → 2 (earlier MSS)
    max_bars_after_sweep: int = 10     # Setup expiry
    vol_multiplier: float = 1.2        # ✓ Phase 1A optimized (validated in 1B)
    displacement_multiplier: float = 1.3  # ✓ Phase 1A optimized (validated in 1B)
    atr_sl_buffer: float = 0.5         # SL buffer multiplier
    max_fvg_size: float = 3.0          # Max FVG size (ATR multiples)
    rr_ratio: float = 2.0              # Risk:Reward ratio
    entry_type: str = "Both"           # "FVG", "MSS Break", or "Both"
    require_displacement: bool = True
    require_volume_spike: bool = True
    allow_ifvg_entries: bool = True
    use_order_blocks: bool = True


class TimeframeState:
    """Maintains ICT state for a single timeframe"""
    def __init__(self, timeframe: str):
        self.timeframe = timeframe  # "1m", "5m", "15m"
        
        # Liquidity tracking
        self.swing_highs: deque = deque(maxlen=20)
        self.swing_high_bars: deque = deque(maxlen=20)
        self.swing_lows: deque = deque(maxlen=20)
        self.swing_low_bars: deque = deque(maxlen=20)

        # State tracking
        self.bullish_setup_active = False
        self.bearish_setup_active = False
        self.bullish_mss_confirmed = False
        self.bearish_mss_confirmed = False
        self.bullish_fvg_active = False
        self.bearish_fvg_active = False
        self.bullish_ifvg_active = False
        self.bearish_ifvg_active = False
        self.bullish_order_block_active = False
        self.bearish_order_block_active = False

        # Setup details
        self.bull_setup_bar = None
        self.bear_setup_bar = None
        self.bull_sl = None
        self.bear_sl = None
        self.bull_disp_bar = None
        self.bear_disp_bar = None

        # FVG tracking
        self.fvg_top = None
        self.fvg_bot = None
        self.fvg_bar = None
        self.bullish_ifvg_top = None
        self.bullish_ifvg_bot = None
        self.bearish_ifvg_top = None
        self.bearish_ifvg_bot = None
        self.bullish_ob_top = None
        self.bullish_ob_bot = None
        self.bearish_ob_top = None
        self.bearish_ob_bot = None

        # Liquidity grab tracking
        self.lq_grab_level = None
        self.lq_grab_bar = None
        self.last_bull_grab_bar = None
        self.last_bear_grab_bar = None
        self.latest_swing_high = None
        self.latest_swing_low = None

        # Recent bars (needed for FVG + ATR calc)
        self.recent_closes: deque = deque(maxlen=20)
        self.recent_opens: deque = deque(maxlen=20)
        self.recent_highs: deque = deque(maxlen=20)
        self.recent_lows: deque = deque(maxlen=20)
        self.recent_volumes: deque = deque(maxlen=20)

        # ATR calculation
        self.atr_values: deque = deque(maxlen=14)
        
        # Candle aggregation (for upsampling 1m to 5m, 15m)
        self.open_price_agg = None
        self.high_agg = None
        self.low_agg = None
        self.close_agg = None
        self.volume_agg = 0
        self.candle_count = 0


class ICTSniperBot(TradingBot):
    """
    ICT Sniper Strategy Bot - Smart institutional entry methodology (Multi-Timeframe)
    
    Detects high-probability setups using:
    - Liquidity grab (sweep + rejection)
    - Market structure confirmation
    - Fair value gap entry
    - Displacement + volume confirmation
    
    Analyzes all three timeframes: 1m, 5m, 15m
    """

    def __init__(self, shared_memory: Optional[SharedMemory] = None):
        super().__init__(
            name="ICTSniper",
            description="ICT Sniper: LQ Grab + MSS + FVG Smart Entry (Multi-TF: 1m/5m/15m)",
            shared_memory=shared_memory
        )

        # Configuration
        self.config = ICTConfig()

        # Multi-timeframe state is isolated per index to avoid cross-index contamination.
        self._tf_states_by_index: Dict[str, Dict[str, TimeframeState]] = {}
        self.tf_states = self._create_tf_states()

        # Performance tracking for learning
        self.signal_history: List[Dict] = []

        # Current index being analyzed (set in analyze())
        self._current_index: str = ""

        # 1m candle tracking (for upsampling to 5m, 15m), isolated per index.
        self._bar_count_5m: Dict[str, int] = {}
        self._bar_count_15m: Dict[str, int] = {}
        self._warmup_sessions: Dict[str, Dict[str, Any]] = {}

    def _create_tf_states(self) -> Dict[str, TimeframeState]:
        return {
            "1m": TimeframeState("1m"),
            "5m": TimeframeState("5m"),
            "15m": TimeframeState("15m"),
        }

    def _get_tf_states(self, index: str) -> Dict[str, TimeframeState]:
        canonical_index = str(index or "").upper() or "SENSEX"
        states = self._tf_states_by_index.get(canonical_index)
        if states is None:
            states = self._create_tf_states()
            self._tf_states_by_index[canonical_index] = states
        return states

    def reset_index_state(self, index: str) -> None:
        canonical_index = str(index or "").upper() or "SENSEX"
        self._tf_states_by_index[canonical_index] = self._create_tf_states()
        self._bar_count_5m[canonical_index] = 0
        self._bar_count_15m[canonical_index] = 0
        if self._current_index == canonical_index:
            self.tf_states = self._tf_states_by_index[canonical_index]

    @staticmethod
    def _format_zone(top: Optional[float], bottom: Optional[float]) -> Optional[str]:
        if top is None or bottom is None:
            return None
        hi = max(float(top), float(bottom))
        lo = min(float(top), float(bottom))
        return f"{lo:.2f}-{hi:.2f}"

    def _serialize_timeframe_state(self, state: TimeframeState) -> Dict[str, Any]:
        return {
            "timeframe": state.timeframe,
            "bullish_setup_active": state.bullish_setup_active,
            "bearish_setup_active": state.bearish_setup_active,
            "bullish_mss_confirmed": state.bullish_mss_confirmed,
            "bearish_mss_confirmed": state.bearish_mss_confirmed,
            "bullish_fvg_active": state.bullish_fvg_active,
            "bearish_fvg_active": state.bearish_fvg_active,
            "bullish_ifvg_active": state.bullish_ifvg_active,
            "bearish_ifvg_active": state.bearish_ifvg_active,
            "bullish_order_block_active": state.bullish_order_block_active,
            "bearish_order_block_active": state.bearish_order_block_active,
            "bullish_fvg_range": self._format_zone(state.fvg_top, state.fvg_bot) if state.bullish_fvg_active else None,
            "bearish_fvg_range": self._format_zone(state.fvg_top, state.fvg_bot) if state.bearish_fvg_active else None,
            "bullish_ifvg_range": self._format_zone(state.bullish_ifvg_top, state.bullish_ifvg_bot),
            "bearish_ifvg_range": self._format_zone(state.bearish_ifvg_top, state.bearish_ifvg_bot),
            "bullish_ob_range": self._format_zone(state.bullish_ob_top, state.bullish_ob_bot),
            "bearish_ob_range": self._format_zone(state.bearish_ob_top, state.bearish_ob_bot),
            "latest_swing_high": state.latest_swing_high,
            "latest_swing_low": state.latest_swing_low,
        }

    def get_multi_timeframe_state(self, index: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        target_index = str(index or self._current_index or "SENSEX").upper()
        states = self._get_tf_states(target_index)
        return {timeframe: self._serialize_timeframe_state(state) for timeframe, state in states.items()}

    def warmup_session_matches(self, index: str, session_key: str) -> bool:
        state = self._warmup_sessions.get(str(index or "").upper(), {})
        return str(state.get("session_key", "")) == str(session_key or "")

    def get_warmup_status(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._warmup_sessions)

    def warmup(self, index: str, candles: List[Dict[str, Any]], session_key: Optional[str] = None) -> Dict[str, Any]:
        canonical_index = str(index or "").upper()
        self.reset_index_state(canonical_index)
        processed = 0
        for candle in candles or []:
            if not isinstance(candle, dict):
                continue
            self.analyze(canonical_index, candle, emit_signals=False)
            processed += 1

        status = {
            "session_key": str(session_key or ""),
            "bars_loaded": processed,
            "updated_at": datetime.now().isoformat(),
        }
        self._warmup_sessions[canonical_index] = status
        return status

    def _record_signal(
        self,
        signal: BotSignal,
        signal_1m: Optional[BotSignal],
        signal_5m: Optional[BotSignal],
        signal_15m: Optional[BotSignal],
    ) -> None:
        mtf_analysis = signal.factors.get("mtf_analysis", {})
        metadata = {
            "timeframe": signal.factors.get("timeframe"),
            "signal_1m": bool(signal_1m),
            "signal_5m": bool(signal_5m),
            "signal_15m": bool(signal_15m),
            "confluence": int(mtf_analysis.get("confluence", 0) or 0),
            "primary_timeframe": mtf_analysis.get("primary_timeframe"),
            "displacement": bool(signal.factors.get("displacement")),
            "volume_confirmed": bool(signal.factors.get("volume_confirmed")),
            "ifvg": bool(signal.factors.get("ifvg")),
            "order_block": bool(signal.factors.get("order_block")),
            "entry_sources": list(signal.factors.get("entry_sources", [])),
            "fvg_range": signal.factors.get("fvg_range"),
            "ifvg_range": signal.factors.get("ifvg_range"),
            "ob_range": signal.factors.get("ob_range"),
        }
        self.recent_signals.append(signal)
        self.recent_signals = self.recent_signals[-20:]
        self.signal_history.append({
            "timestamp": signal.timestamp,
            "index": signal.index,
            "signal_type": signal.signal_type.value,
            "option_type": signal.option_type.value,
            "confidence": signal.confidence,
            "metadata": metadata,
        })
        self.signal_history = self.signal_history[-20:]

    def analyze(
        self,
        index: str,
        market_data: Dict,
        option_chain: Optional[List[Dict]] = None,
        emit_signals: bool = True,
    ) -> Optional[BotSignal]:
        """
        Analyze market data for all timeframes and generate ICT Sniper signals

        Process:
        1. Process 1m candle through all timeframes
        2. Aggregate 1m -> 5m candles (every 5 bars)
        3. Aggregate 1m -> 15m candles (every 15 bars)
        4. Generate signals for each timeframe
        5. Return strongest signal (15m > 5m > 1m)

        Args:
            index: Index name (NIFTY50, BANKNIFTY, etc.)
            market_data: OHLCV data with current bar
            option_chain: Option chain data if available (unused by ICT)

        Returns:
            BotSignal with multi-timeframe details or None
        """
        try:
            # Store index for signal creation
            self._current_index = str(index or "").upper() or "SENSEX"
            self.tf_states = self._get_tf_states(self._current_index)
            bar_count_5m = self._bar_count_5m.get(self._current_index, 0)
            bar_count_15m = self._bar_count_15m.get(self._current_index, 0)

            # Extract OHLCV
            open_price = market_data.get("open", 0)
            high = market_data.get("high", 0)
            low = market_data.get("low", 0)
            close = market_data.get("close", 0) or market_data.get("ltp", 0)
            volume = market_data.get("volume", 0)
            bar_index = market_data.get("bar_index", 0)
            atr_input = market_data.get("atr", 0)
            signal_timestamp = str(market_data.get("timestamp") or datetime.now().isoformat())

            if not all([open_price, high, low, close]):
                return None

            # ═══════════════════════════════════════════════════════════════
            # STEP 1: Process 1m timeframe
            # ═══════════════════════════════════════════════════════════════
            signal_1m = self._analyze_timeframe(
                "1m", 
                open_price, high, low, close, volume, atr_input,
                bar_index=bar_index,
                emit_signals=emit_signals,
            )

            # ═══════════════════════════════════════════════════════════════
            # STEP 2: Aggregate 1m -> 5m candles (every 5 bars)
            # ═══════════════════════════════════════════════════════════════
            signal_5m = None
            bar_count_5m += 1
            self._bar_count_5m[self._current_index] = bar_count_5m
            
            state_5m = self.tf_states["5m"]
            if state_5m.open_price_agg is None:
                state_5m.open_price_agg = open_price
            state_5m.high_agg = max(state_5m.high_agg or high, high)
            state_5m.low_agg = min(state_5m.low_agg or low, low)
            state_5m.close_agg = close
            state_5m.volume_agg += volume
            state_5m.candle_count += 1

            if bar_count_5m >= 5:
                # 5m candle complete - analyze it
                signal_5m = self._analyze_timeframe(
                    "5m",
                    state_5m.open_price_agg,
                    state_5m.high_agg,
                    state_5m.low_agg,
                    state_5m.close_agg,
                    state_5m.volume_agg,
                    atr_input * 2.236 if atr_input else 0,  # Scale ATR for 5m
                    bar_index=bar_index,
                    emit_signals=emit_signals,
                )
                # Reset 5m aggregation
                state_5m.open_price_agg = None
                state_5m.high_agg = None
                state_5m.low_agg = None
                state_5m.volume_agg = 0
                state_5m.candle_count = 0
                self._bar_count_5m[self._current_index] = 0

            # ═══════════════════════════════════════════════════════════════
            # STEP 3: Aggregate 1m -> 15m candles (every 15 bars)
            # ═══════════════════════════════════════════════════════════════
            signal_15m = None
            bar_count_15m += 1
            self._bar_count_15m[self._current_index] = bar_count_15m
            
            state_15m = self.tf_states["15m"]
            if state_15m.open_price_agg is None:
                state_15m.open_price_agg = open_price
            state_15m.high_agg = max(state_15m.high_agg or high, high)
            state_15m.low_agg = min(state_15m.low_agg or low, low)
            state_15m.close_agg = close
            state_15m.volume_agg += volume
            state_15m.candle_count += 1

            if bar_count_15m >= 15:
                # 15m candle complete - analyze it
                signal_15m = self._analyze_timeframe(
                    "15m",
                    state_15m.open_price_agg,
                    state_15m.high_agg,
                    state_15m.low_agg,
                    state_15m.close_agg,
                    state_15m.volume_agg,
                    atr_input * 3.873 if atr_input else 0,  # Scale ATR for 15m
                    bar_index=bar_index,
                    emit_signals=emit_signals,
                )
                # Reset 15m aggregation
                state_15m.open_price_agg = None
                state_15m.high_agg = None
                state_15m.low_agg = None
                state_15m.volume_agg = 0
                state_15m.candle_count = 0
                self._bar_count_15m[self._current_index] = 0

            # ═══════════════════════════════════════════════════════════════
            # STEP 4: Combine signals - prefer highest quality (15m > 5m > 1m)
            # ═══════════════════════════════════════════════════════════════
            
            combined_signal = signal_15m or signal_5m or signal_1m

            if combined_signal and emit_signals:
                combined_signal.timestamp = signal_timestamp
                # Enhance signal with multi-timeframe confirmation (use factors dict)
                combined_signal.factors["mtf_analysis"] = {
                    "signal_1m": signal_1m is not None,
                    "signal_5m": signal_5m is not None,
                    "signal_15m": signal_15m is not None,
                    "confluence": sum([
                        signal_1m is not None,
                        signal_5m is not None,
                        signal_15m is not None
                    ]),
                    "primary_timeframe": "15m" if signal_15m else ("5m" if signal_5m else "1m")
                }
                self._record_signal(combined_signal, signal_1m, signal_5m, signal_15m)
                logger.info(f"[ICT-MultiTF] Signal: {combined_signal.signal_type} "
                           f"(1m:{signal_1m is not None} 5m:{signal_5m is not None} 15m:{signal_15m is not None})")
            
            return combined_signal

        except Exception as e:
            logger.error(f"[ICT-MultiTF] Error in analyze: {e}", exc_info=True)
            return None

    def _analyze_timeframe(self, timeframe: str, open_price: float, high: float, low: float,
                          close: float, volume: float, atr: float, bar_index: int,
                          emit_signals: bool = True) -> Optional[BotSignal]:
        """
        Analyze ICT setup for a specific timeframe
        
        Args:
            timeframe: "1m", "5m", or "15m"
            open_price, high, low, close, volume: OHLCV data
            atr: ATR value (scaled for timeframe)
            bar_index: Bar index for setup tracking
            
        Returns:
            BotSignal or None
        """
        state = self.tf_states[timeframe]
        
        # Extract OHLCV
        if not all([open_price, high, low, close]):
            return None

        # Update recent bars
        state.recent_opens.append(open_price)
        state.recent_closes.append(close)
        state.recent_highs.append(high)
        state.recent_lows.append(low)
        state.recent_volumes.append(volume)

        # Update ATR
        if len(state.recent_highs) >= 2:
            tr = max(
                high - low,
                abs(high - state.recent_closes[-2]),
                abs(low - state.recent_closes[-2])
            )
            state.atr_values.append(tr)
        
        atr_calc = statistics.mean(state.atr_values) if state.atr_values else atr
        if atr_calc == 0:
            atr_calc = high - low  # Fallback
        
        if len(state.recent_highs) < 3:
            return None  # Need history

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 1: SWING HIGH/LOW DETECTION
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if len(state.recent_highs) >= self.config.swing_lookback + 1:
            mid = self.config.swing_lookback // 2
            if mid >= 1 and mid < len(state.recent_highs):
                mid_high = state.recent_highs[-mid - 1]
                mid_low = state.recent_lows[-mid - 1]

                is_swing_high = (
                    mid_high == max(list(state.recent_highs)[-self.config.swing_lookback - 1:])
                )
                is_swing_low = (
                    mid_low == min(list(state.recent_lows)[-self.config.swing_lookback - 1:])
                )

                if is_swing_high:
                    state.swing_highs.append(mid_high)
                    state.swing_high_bars.append(bar_index - mid)
                    state.latest_swing_high = mid_high
                    logger.debug(f"[ICT-{timeframe}] Swing high: {mid_high}")

                if is_swing_low:
                    state.swing_lows.append(mid_low)
                    state.swing_low_bars.append(bar_index - mid)
                    state.latest_swing_low = mid_low
                    logger.debug(f"[ICT-{timeframe}] Swing low: {mid_low}")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 2: LIQUIDITY GRAB (Sweep + Rejection)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if state.swing_highs:
            max_swing = max(state.swing_highs)
            break_above = high > max_swing
            reject_below = close < max_swing

            if break_above and reject_below and not state.bullish_setup_active:
                # MUTUAL EXCLUSION: Clear bearish setup when bullish activates
                state.bearish_setup_active = False
                state.bearish_mss_confirmed = False
                state.bearish_fvg_active = False
                state.bearish_ifvg_active = False
                state.bearish_order_block_active = False
                state.bear_setup_bar = None
                state.bear_disp_bar = None

                state.bullish_setup_active = True
                state.bull_setup_bar = bar_index
                state.bull_sl = low
                logger.debug(f"[ICT-{timeframe}] Bullish LQ Grab at {max_swing} (cleared bearish)")
                state.last_bull_grab_bar = bar_index

        if state.swing_lows:
            min_swing = min(state.swing_lows)
            break_below = low < min_swing
            reject_above = close > min_swing

            if break_below and reject_above and not state.bearish_setup_active:
                # MUTUAL EXCLUSION: Clear bullish setup when bearish activates
                state.bullish_setup_active = False
                state.bullish_mss_confirmed = False
                state.bullish_fvg_active = False
                state.bullish_ifvg_active = False
                state.bullish_order_block_active = False
                state.bull_setup_bar = None
                state.bull_disp_bar = None

                state.bearish_setup_active = True
                state.bear_setup_bar = bar_index
                state.bear_sl = high
                logger.debug(f"[ICT-{timeframe}] Bearish LQ Grab at {min_swing} (cleared bullish)")
                state.last_bear_grab_bar = bar_index

        # Expire setups
        if state.bull_setup_bar and bar_index - state.bull_setup_bar > self.config.max_bars_after_sweep:
            state.bullish_setup_active = False
            state.bullish_mss_confirmed = False
            state.bullish_fvg_active = False
            state.bullish_ifvg_active = False
            state.bullish_order_block_active = False
        if state.bear_setup_bar and bar_index - state.bear_setup_bar > self.config.max_bars_after_sweep:
            state.bearish_setup_active = False
            state.bearish_mss_confirmed = False
            state.bearish_fvg_active = False
            state.bearish_ifvg_active = False
            state.bearish_order_block_active = False

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 3: MSS (Market Structure Shift)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if len(state.recent_highs) >= self.config.mss_swing_len + 2:
            if state.swing_highs and state.bullish_setup_active:
                prev_high = state.swing_highs[-1]
                if high > prev_high:
                    state.bullish_mss_confirmed = True
                    state.bearish_mss_confirmed = False  # MUTUAL EXCLUSION
                    logger.debug(f"[ICT-{timeframe}] Bullish MSS at {prev_high}")

            if state.swing_lows and state.bearish_setup_active:
                prev_low = state.swing_lows[-1]
                if low < prev_low:
                    state.bearish_mss_confirmed = True
                    state.bullish_mss_confirmed = False  # MUTUAL EXCLUSION
                    logger.debug(f"[ICT-{timeframe}] Bearish MSS at {prev_low}")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 4: FVG (Fair Value Gap)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if len(state.recent_highs) >= 3:
            bullish_fvg = (low > state.recent_highs[-3] and
                          state.recent_closes[-2] > state.recent_opens[-2] and
                          (low - state.recent_highs[-3]) <= atr_calc * self.config.max_fvg_size)
            if bullish_fvg and state.bullish_setup_active:  # Only if bullish setup is active
                state.fvg_top = low
                state.fvg_bot = state.recent_highs[-3]
                state.bullish_fvg_active = True
                state.bearish_fvg_active = False  # MUTUAL EXCLUSION
                state.bullish_ifvg_active = False
                logger.debug(f"[ICT-{timeframe}] Bullish FVG: {state.fvg_bot:.2f}-{state.fvg_top:.2f}")

            bearish_fvg = (high < state.recent_lows[-3] and
                          state.recent_closes[-2] < state.recent_opens[-2] and
                          (state.recent_lows[-3] - high) <= atr_calc * self.config.max_fvg_size)
            if bearish_fvg and state.bearish_setup_active:  # Only if bearish setup is active
                state.fvg_top = state.recent_lows[-3]
                state.fvg_bot = high
                state.bearish_fvg_active = True
                state.bullish_fvg_active = False  # MUTUAL EXCLUSION
                state.bearish_ifvg_active = False
                logger.debug(f"[ICT-{timeframe}] Bearish FVG: {state.fvg_top:.2f}-{state.fvg_bot:.2f}")

        # FVG inversion -> IFVG
        if self.config.allow_ifvg_entries and state.fvg_top is not None and state.fvg_bot is not None:
            if state.bullish_fvg_active and close < state.fvg_bot:
                state.bullish_fvg_active = False
                state.bearish_ifvg_active = True
                state.bearish_ifvg_top = state.fvg_top
                state.bearish_ifvg_bot = state.fvg_bot
            if state.bearish_fvg_active and close > state.fvg_top:
                state.bearish_fvg_active = False
                state.bullish_ifvg_active = True
                state.bullish_ifvg_top = state.fvg_top
                state.bullish_ifvg_bot = state.fvg_bot

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 5: DISPLACEMENT CANDLE
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if len(state.recent_highs) >= 20:
            avg_range = statistics.mean([
                state.recent_highs[i] - state.recent_lows[i] 
                for i in range(len(state.recent_highs))
            ])
            candle_range = high - low
            is_displacement = candle_range > avg_range * self.config.displacement_multiplier
            is_bull_disp = is_displacement and close > open_price
            is_bear_disp = is_displacement and close < open_price

            if is_bull_disp and state.bullish_setup_active and state.bull_disp_bar is None:
                state.bull_disp_bar = bar_index
                state.bear_disp_bar = None  # MUTUAL EXCLUSION
                if self.config.use_order_blocks and len(state.recent_opens) >= 2:
                    prev_open = state.recent_opens[-2]
                    prev_close = state.recent_closes[-2]
                    if prev_close <= prev_open:
                        state.bullish_order_block_active = True
                        state.bearish_order_block_active = False
                        state.bullish_ob_top = state.recent_highs[-2]
                        state.bullish_ob_bot = state.recent_lows[-2]
                logger.debug(f"[ICT-{timeframe}] Bullish displacement")

            if is_bear_disp and state.bearish_setup_active and state.bear_disp_bar is None:
                state.bear_disp_bar = bar_index
                state.bull_disp_bar = None  # MUTUAL EXCLUSION
                if self.config.use_order_blocks and len(state.recent_opens) >= 2:
                    prev_open = state.recent_opens[-2]
                    prev_close = state.recent_closes[-2]
                    if prev_close >= prev_open:
                        state.bearish_order_block_active = True
                        state.bullish_order_block_active = False
                        state.bearish_ob_top = state.recent_highs[-2]
                        state.bearish_ob_bot = state.recent_lows[-2]
                logger.debug(f"[ICT-{timeframe}] Bearish displacement")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 6: VOLUME SPIKE
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if len(state.recent_volumes) >= 20:
            avg_vol = statistics.mean(list(state.recent_volumes))
            is_vol_spike = volume > avg_vol * self.config.vol_multiplier
        else:
            is_vol_spike = True

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 7: SIGNAL GENERATION
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        bullish_entry_sources: List[str] = []
        if state.bullish_fvg_active and state.fvg_top is not None and low <= state.fvg_top:
            bullish_entry_sources.append("FVG")
        if (
            self.config.allow_ifvg_entries
            and state.bullish_ifvg_active
            and state.bullish_ifvg_top is not None
            and state.bullish_ifvg_bot is not None
            and low <= state.bullish_ifvg_top
            and high >= state.bullish_ifvg_bot
        ):
            bullish_entry_sources.append("IFVG")
        if (
            self.config.use_order_blocks
            and state.bullish_order_block_active
            and state.bullish_ob_top is not None
            and state.bullish_ob_bot is not None
            and low <= state.bullish_ob_top
            and high >= state.bullish_ob_bot
        ):
            bullish_entry_sources.append("OB")

        bearish_entry_sources: List[str] = []
        if state.bearish_fvg_active and state.fvg_top is not None and state.fvg_bot is not None and high >= state.fvg_bot and low <= state.fvg_top:
            bearish_entry_sources.append("FVG")
        if (
            self.config.allow_ifvg_entries
            and state.bearish_ifvg_active
            and state.bearish_ifvg_top is not None
            and state.bearish_ifvg_bot is not None
            and high >= state.bearish_ifvg_bot
            and low <= state.bearish_ifvg_top
        ):
            bearish_entry_sources.append("IFVG")
        if (
            self.config.use_order_blocks
            and state.bearish_order_block_active
            and state.bearish_ob_top is not None
            and state.bearish_ob_bot is not None
            and high >= state.bearish_ob_bot
            and low <= state.bearish_ob_top
        ):
            bearish_entry_sources.append("OB")

        # Bullish Signal
        if (state.bullish_setup_active and state.bullish_mss_confirmed and
            bullish_entry_sources and close > open_price):

            disp_ok = not self.config.require_displacement or state.bull_disp_bar is not None
            vol_ok = not self.config.require_volume_spike or is_vol_spike

            if disp_ok and vol_ok:
                signal = None
                if emit_signals:
                    self.performance.total_signals += 1

                    signal = BotSignal(
                        bot_name="ICTSniper",
                        index=self._current_index,
                        signal_type=SignalType.BUY,
                        option_type=OptionType.CE,
                        confidence=85.0,
                        entry=close,
                        stop_loss=state.bull_sl if state.bull_sl else low - atr_calc * self.config.atr_sl_buffer,
                        target=close + atr_calc * self.config.rr_ratio,
                        reasoning=f"Bullish LQ Grab + MSS + {'/'.join(bullish_entry_sources)} on {timeframe}",
                        factors={
                            "setup_type": "Bullish LQ Grab + MSS",
                            "timeframe": timeframe,
                            "fvg_range": self._format_zone(state.fvg_top, state.fvg_bot),
                            "ifvg_range": self._format_zone(state.bullish_ifvg_top, state.bullish_ifvg_bot),
                            "ob_range": self._format_zone(state.bullish_ob_top, state.bullish_ob_bot),
                            "entry_sources": bullish_entry_sources,
                            "displacement": state.bull_disp_bar is not None,
                            "volume_confirmed": is_vol_spike,
                            "ifvg": state.bullish_ifvg_active,
                            "order_block": state.bullish_order_block_active,
                        }
                    )

                # Reset setup
                state.bullish_setup_active = False
                state.bullish_mss_confirmed = False
                state.bullish_fvg_active = False
                state.bullish_ifvg_active = False
                state.bullish_order_block_active = False
                state.bull_disp_bar = None

                if signal is not None:
                    logger.info(f"[ICT-{timeframe}] ✓ BULLISH: {close:.2f} SL:{signal.stop_loss:.2f} TP:{signal.target:.2f}")
                return signal

        # Bearish Signal
        if (state.bearish_setup_active and state.bearish_mss_confirmed and
            bearish_entry_sources and close < open_price):

            disp_ok = not self.config.require_displacement or state.bear_disp_bar is not None
            vol_ok = not self.config.require_volume_spike or is_vol_spike

            if disp_ok and vol_ok:
                signal = None
                if emit_signals:
                    self.performance.total_signals += 1

                    signal = BotSignal(
                        bot_name="ICTSniper",
                        index=self._current_index,
                        signal_type=SignalType.SELL,
                        option_type=OptionType.PE,
                        confidence=85.0,
                        entry=close,
                        stop_loss=state.bear_sl if state.bear_sl else high + atr_calc * self.config.atr_sl_buffer,
                        target=close - atr_calc * self.config.rr_ratio,
                        reasoning=f"Bearish LQ Grab + MSS + {'/'.join(bearish_entry_sources)} on {timeframe}",
                        factors={
                            "setup_type": "Bearish LQ Grab + MSS",
                            "timeframe": timeframe,
                            "fvg_range": self._format_zone(state.fvg_top, state.fvg_bot),
                            "ifvg_range": self._format_zone(state.bearish_ifvg_top, state.bearish_ifvg_bot),
                            "ob_range": self._format_zone(state.bearish_ob_top, state.bearish_ob_bot),
                            "entry_sources": bearish_entry_sources,
                            "displacement": state.bear_disp_bar is not None,
                            "volume_confirmed": is_vol_spike,
                            "ifvg": state.bearish_ifvg_active,
                            "order_block": state.bearish_order_block_active,
                        }
                    )

                # Reset setup
                state.bearish_setup_active = False
                state.bearish_mss_confirmed = False
                state.bearish_fvg_active = False
                state.bearish_ifvg_active = False
                state.bearish_order_block_active = False
                state.bear_disp_bar = None

                if signal is not None:
                    logger.info(f"[ICT-{timeframe}] ✓ BEARISH: {close:.2f} SL:{signal.stop_loss:.2f} TP:{signal.target:.2f}")
                return signal

        return None

    def learn(self, trade):
        """
        Learn from completed trade and adapt parameters

        Adapts:
        - vol_multiplier: if volume was key, lower threshold
        - displacement_multiplier: if displacement was key, adjust
        - swing_lookback: optimize for market volatility

        Args:
            trade: TradeRecord from base class
        """
        # Let parent class handle performance tracking (wins/losses/total_trades/win_rate)
        self.update_performance(trade)

        # ICT-specific learning logic for parameter adaptation
        try:
            outcome = trade.outcome if hasattr(trade, 'outcome') else trade.get("outcome", "")
            pnl_pct = trade.pnl_pct if hasattr(trade, 'pnl_pct') else trade.get("pnl_pct", 0)

            # Learning logic - adjust parameters based on outcomes
            if outcome == "WIN" and pnl_pct > 2:
                self.config.vol_multiplier = max(1.0, self.config.vol_multiplier - 0.05)
                logger.info(f"[ICT] WIN: vol_mult→{self.config.vol_multiplier:.2f}")

            elif outcome == "LOSS" and pnl_pct < -2:
                self.config.vol_multiplier = min(2.0, self.config.vol_multiplier + 0.1)
                self.config.displacement_multiplier = min(2.5, self.config.displacement_multiplier + 0.1)
                logger.info(f"[ICT] LOSS: vol_mult→{self.config.vol_multiplier:.2f} disp_mult→{self.config.displacement_multiplier:.2f}")

            logger.info(f"[ICT] Trade learned: W/L = {self.performance.wins}/{self.performance.losses}, WR = {self.performance.win_rate:.1f}%")

        except Exception as e:
            logger.error(f"[ICT] Error in learn: {e}")

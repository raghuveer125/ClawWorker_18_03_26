"""
FyersN7 Signal Adapter - Uses FyersN7 engine indicators for Hub V2.

This adapter integrates the sophisticated FyersN7 scoring system instead
of basic EMA/RSI indicators. It uses:
- spread_pct, delta, gamma, iv
- vote_diff, net_pcr, fut_basis_pct
- max_pain_dist, strike_pcr, vix
- vol_dom, flow_match, stable

This is Option B: Use FyersN7 as indicator engine, Hub V2 for decisions.
"""

import os
import sys
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# Add FyersN7 scripts to path
SCRIPT_DIR = Path(__file__).resolve().parents[3]
FYERS_SCRIPTS = SCRIPT_DIR / "fyersN7" / "fyers-2026-03-05" / "scripts"
if str(FYERS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FYERS_SCRIPTS))

from .base_adapter import BaseAdapter, AdapterStatus

logger = logging.getLogger(__name__)


@dataclass
class FyersN7Signal:
    """Signal from FyersN7 engine with all indicators."""
    # Core identifiers
    index: str = ""
    side: str = ""  # CE or PE
    strike: int = 0
    timestamp: float = field(default_factory=time.time)

    # Price data
    entry: float = 0.0
    sl: float = 0.0
    t1: float = 0.0
    t2: float = 0.0

    # FyersN7 indicators
    confidence: int = 0
    vote_diff: float = 0.0
    vote_ce: int = 0
    vote_pe: int = 0
    vol_dom: str = "NEUTRAL"

    # Option Greeks
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    iv: float = 0.0

    # Microstructure
    spread_pct: float = 0.0
    bid: float = 0.0
    ask: float = 0.0

    # Market context
    net_pcr: float = 0.0
    strike_pcr: float = 0.0
    fut_basis_pct: float = 0.0
    max_pain_dist: float = 0.0
    vix: float = 0.0

    # Signal quality
    stable: bool = False
    flow_match: bool = False
    entry_ready: bool = False
    status: str = ""
    action: str = ""

    # Time context (for time window filter)
    hour: int = 0
    minute: int = 0

    # Computed score (0-100)
    score: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "side": self.side,
            "strike": self.strike,
            "timestamp": self.timestamp,
            "entry": self.entry,
            "sl": self.sl,
            "t1": self.t1,
            "t2": self.t2,
            "confidence": self.confidence,
            "vote_diff": self.vote_diff,
            "vote_ce": self.vote_ce,
            "vote_pe": self.vote_pe,
            "vol_dom": self.vol_dom,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "iv": self.iv,
            "spread_pct": self.spread_pct,
            "net_pcr": self.net_pcr,
            "strike_pcr": self.strike_pcr,
            "fut_basis_pct": self.fut_basis_pct,
            "max_pain_dist": self.max_pain_dist,
            "vix": self.vix,
            "stable": self.stable,
            "flow_match": self.flow_match,
            "entry_ready": self.entry_ready,
            "score": self.score,
        }


def to_float(v: Any, default: float = 0.0) -> float:
    """Safe float conversion."""
    try:
        return float(v)
    except Exception:
        return default


def to_int(v: Any, default: int = 0) -> int:
    """Safe int conversion."""
    try:
        return int(float(v))
    except Exception:
        return default


class FyersN7SignalScorer:
    """
    Scores signals using FyersN7's multi-factor scoring system.

    This replicates the score_row() logic from opportunity_engine.py.
    """

    def score_signal(self, signal: FyersN7Signal) -> int:
        """
        Score a signal using FyersN7 indicators.

        Returns score 0-100.
        """
        score = 0
        side = signal.side.upper()

        # Vote diff scoring (0-24 points)
        if signal.vote_diff >= 8:
            score += 24
        elif signal.vote_diff >= 6:
            score += 20
        elif signal.vote_diff >= 4:
            score += 12

        # Confidence scoring (0-14 points)
        if signal.confidence >= 92:
            score += 14
        elif signal.confidence >= 88:
            score += 12
        elif signal.confidence >= 84:
            score += 8

        # Stability and flow match (0-20 points)
        if signal.stable:
            score += 10
        if signal.flow_match:
            score += 10

        # Volume dominance alignment (0-10 points)
        if signal.vol_dom == side:
            score += 10
        elif signal.vol_dom == "NEUTRAL":
            score += 5

        # Spread scoring (0-10 points)
        if signal.spread_pct <= 1.2:
            score += 10
        elif signal.spread_pct <= 2.0:
            score += 7
        elif signal.spread_pct <= 2.8:
            score += 3

        # Delta scoring (0-12 points)
        delta = abs(signal.delta)
        if 0.06 <= delta <= 0.35:
            score += 12
        elif delta > 0.35:
            score += 8
        elif delta >= 0.03:
            score += 4

        # Gamma scoring (0-12 points)
        if signal.gamma >= 0.0010:
            score += 12
        elif signal.gamma >= 0.0006:
            score += 9
        elif signal.gamma >= 0.00035:
            score += 6

        # IV scoring (0-8 points)
        if 22.0 <= signal.iv <= 55.0:
            score += 8

        # VIX scoring (-1 to +2 points)
        if signal.vix > 0:
            if 12.0 <= signal.vix <= 25.0:
                score += 2
            elif signal.vix < 9.0 or signal.vix > 35.0:
                score -= 1

        # Cross-market context scoring
        if side == "CE":
            # PCR for CE
            if signal.net_pcr >= 1.10:
                score += 4
            elif 0 < signal.net_pcr <= 0.90:
                score -= 4

            # Futures basis for CE
            if signal.fut_basis_pct >= 0.15:
                score += 3
            elif signal.fut_basis_pct < -0.05:
                score -= 3

            # Max pain distance for CE
            if signal.max_pain_dist >= 120.0:
                score += 2
            elif 0 < signal.max_pain_dist <= 40.0:
                score -= 2

            # Strike PCR for CE
            if signal.strike_pcr >= 1.05:
                score += 2
            elif 0 < signal.strike_pcr <= 0.80:
                score -= 2

        elif side == "PE":
            # PCR for PE
            if 0 < signal.net_pcr <= 0.95:
                score += 4
            elif signal.net_pcr >= 1.20:
                score -= 4

            # Futures basis for PE
            if signal.fut_basis_pct <= -0.05:
                score += 3
            elif signal.fut_basis_pct >= 0.20:
                score -= 3

            # Max pain distance for PE
            if signal.max_pain_dist >= 120.0:
                score += 2
            elif 0 < signal.max_pain_dist <= 40.0:
                score -= 2

            # Strike PCR for PE
            if 0 < signal.strike_pcr <= 0.95:
                score += 2
            elif signal.strike_pcr >= 1.20:
                score -= 2

        # Entry ready bonus
        if signal.status == "APPROVED" and signal.action == "TAKE" and signal.entry_ready:
            score += 8

        return max(0, min(100, score))


@dataclass
class SignalCooldown:
    """Track signal cooldown to prevent overtrading."""
    min_signal_gap_seconds: int = 45
    max_trades_per_hour: int = 10

    _last_signal_time: float = field(default=0.0, repr=False)
    _hourly_signals: List[float] = field(default_factory=list, repr=False)

    def can_signal(self) -> bool:
        now = time.time()
        if now - self._last_signal_time < self.min_signal_gap_seconds:
            return False
        hour_ago = now - 3600
        self._hourly_signals = [t for t in self._hourly_signals if t > hour_ago]
        return len(self._hourly_signals) < self.max_trades_per_hour

    def record_signal(self):
        now = time.time()
        self._last_signal_time = now
        self._hourly_signals.append(now)

    def get_hourly_remaining(self) -> int:
        hour_ago = time.time() - 3600
        self._hourly_signals = [t for t in self._hourly_signals if t > hour_ago]
        return max(0, self.max_trades_per_hour - len(self._hourly_signals))


class FyersN7SignalAdapter(BaseAdapter):
    """
    Adapter that provides FyersN7 signals to Hub V2.

    Uses FyersN7's sophisticated multi-factor scoring instead of basic indicators.

    Optimal settings (backtested 2026-03-15):
    - target_points: 30, sl_points: 20, reverse_exit: True
    - Win rate: 66.7% on SENSEX + NIFTY50
    """

    SUPPORTED_INDICES = ["SENSEX", "NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

    # Optimal thresholds (backtested 2026-03-15, 66.7% WR)
    MIN_SCORE = 50
    MIN_CONFIDENCE = 70
    MIN_VOTE_DIFF = 3.0
    MAX_SPREAD_PCT = 2.5
    MIN_DELTA = 0.03
    MIN_GAMMA = 0.0003

    # Exit settings
    TARGET_POINTS = 25
    SL_POINTS = 15
    REVERSE_EXIT = True

    # Trend alignment filter - CRITICAL for avoiding wrong-side entries
    # When vol_dom = CE (bullish flow), only allow CE entries
    # When vol_dom = PE (bearish flow), only allow PE entries
    REQUIRE_TREND_ALIGNMENT = True

    # Layer 1: Volatility Filter (VIX-based since ATR not available)
    # Avoid dead markets (VIX too low) and chaotic spikes (VIX too high)
    # VIX 15-30 is ideal for scalping
    MIN_VIX = 12.0   # Below this = dead market, targets won't hit
    MAX_VIX = 35.0   # Above this = too chaotic, SLs get hit
    ENABLE_VIX_FILTER = False  # Disabled - testing showed it reduces performance

    # Layer 5: Time Window Filter
    # Avoid mid-day theta decay zone (11:45-13:30)
    # Best scalping windows: 9:20-11:30 and 14:00-15:15
    AVOID_DECAY_ZONE = False  # Disabled - testing showed it reduces performance
    DECAY_ZONE_START = (11, 45)  # 11:45
    DECAY_ZONE_END = (13, 30)    # 13:30

    def __init__(
        self,
        min_score: int = 50,  # Optimal (backtested)
        min_confidence: int = 70,  # Optimal (backtested)
        min_signal_gap: int = 30,
        max_trades_per_hour: int = 10,
    ):
        self._scorer = FyersN7SignalScorer()
        self._cooldowns: Dict[str, SignalCooldown] = {}
        self._last_signals: Dict[str, FyersN7Signal] = {}

        # Configurable thresholds
        self.MIN_SCORE = min_score
        self.MIN_CONFIDENCE = min_confidence

        self._min_signal_gap = min_signal_gap
        self._max_trades_per_hour = max_trades_per_hour

        logger.info(
            f"FyersN7SignalAdapter initialized: "
            f"min_score={min_score}, min_conf={min_confidence}"
        )

    @property
    def name(self) -> str:
        return "fyersn7_signals"

    @property
    def supported_indices(self) -> List[str]:
        return self.SUPPORTED_INDICES

    def _get_cooldown(self, index: str) -> SignalCooldown:
        if index not in self._cooldowns:
            self._cooldowns[index] = SignalCooldown(
                min_signal_gap_seconds=self._min_signal_gap,
                max_trades_per_hour=self._max_trades_per_hour,
            )
        return self._cooldowns[index]

    def get_status(self) -> AdapterStatus:
        return AdapterStatus(
            connected=True,
            last_update=time.time(),
            latency_ms=0,
            error=None,
        )

    def get_quote(self, symbol: str, **kwargs) -> Dict[str, Any]:
        return {"symbol": symbol, "adapter": "fyersn7_signals"}

    def get_quotes(self, symbols: List[str], **kwargs) -> Dict[str, Any]:
        return {"symbols": symbols}

    def get_history(self, symbol: str, **kwargs) -> Dict[str, Any]:
        return {"candles": []}

    def get_option_chain(self, symbol: str, **kwargs) -> Dict[str, Any]:
        return {"strikes": []}

    def get_index_data(self, index_name: str, **kwargs) -> Dict[str, Any]:
        return self.get_signal_status(index_name)

    def create_signal_from_dict(self, row: Dict[str, Any], index: str) -> FyersN7Signal:
        """Create FyersN7Signal from signal row dictionary."""
        # Parse time for time window filter
        hour, minute = 9, 0
        time_str = row.get("time", "")
        if time_str:
            try:
                parts = time_str.split(":")
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                pass

        signal = FyersN7Signal(
            index=index,
            side=str(row.get("side", "")).upper(),
            strike=to_int(row.get("strike", 0)),
            timestamp=time.time(),
            entry=to_float(row.get("entry", 0)),
            sl=to_float(row.get("sl", 0)),
            t1=to_float(row.get("t1", 0)),
            t2=to_float(row.get("t2", 0)),
            confidence=to_int(row.get("confidence", 0)),
            vote_diff=to_float(row.get("vote_diff", 0)),
            vote_ce=to_int(row.get("vote_ce", 0)),
            vote_pe=to_int(row.get("vote_pe", 0)),
            vol_dom=str(row.get("vol_dom", "NEUTRAL")).upper(),
            delta=to_float(row.get("delta", 0)),
            gamma=to_float(row.get("gamma", 0)),
            theta=to_float(row.get("theta", 0)),
            iv=to_float(row.get("iv", 0)),
            spread_pct=to_float(row.get("spread_pct", 0)),
            net_pcr=to_float(row.get("net_pcr", 0)),
            strike_pcr=to_float(row.get("strike_pcr", 0)),
            fut_basis_pct=to_float(row.get("fut_basis_pct", 0)),
            max_pain_dist=to_float(row.get("max_pain_dist", 0)),
            vix=to_float(row.get("vix", 0)),
            stable=str(row.get("stable", "N")).upper() == "Y",
            flow_match=str(row.get("flow_match", "N")).upper() == "Y",
            entry_ready=str(row.get("entry_ready", "N")).upper() == "Y",
            status=str(row.get("status", "")).upper(),
            action=str(row.get("action", "")).upper(),
            hour=hour,
            minute=minute,
        )

        # Score the signal
        signal.score = self._scorer.score_signal(signal)

        return signal

    def evaluate_signal(self, signal: FyersN7Signal) -> Tuple[bool, List[str], List[str]]:
        """
        Evaluate if signal meets entry criteria.

        Returns:
            (should_trade, reasons, warnings)
        """
        reasons = []
        warnings = []

        # Check cooldown
        cooldown = self._get_cooldown(signal.index)
        if not cooldown.can_signal():
            return False, [], [f"Cooldown active: {cooldown.get_hourly_remaining()} remaining"]

        # ============== LAYER 1: VIX/VOLATILITY FILTER ==============
        vix_ok = True
        if self.ENABLE_VIX_FILTER and signal.vix > 0:
            if signal.vix < self.MIN_VIX:
                warnings.append(f"VIX too low: {signal.vix:.1f} < {self.MIN_VIX} (dead market)")
                vix_ok = False
            elif signal.vix > self.MAX_VIX:
                warnings.append(f"VIX too high: {signal.vix:.1f} > {self.MAX_VIX} (chaotic)")
                vix_ok = False
            else:
                reasons.append(f"VIX OK: {signal.vix:.1f}")

        # ============== LAYER 5: TIME WINDOW FILTER ==============
        time_ok = True
        if self.AVOID_DECAY_ZONE and signal.hour > 0:
            signal_minutes = signal.hour * 60 + signal.minute
            decay_start = self.DECAY_ZONE_START[0] * 60 + self.DECAY_ZONE_START[1]
            decay_end = self.DECAY_ZONE_END[0] * 60 + self.DECAY_ZONE_END[1]

            if decay_start <= signal_minutes <= decay_end:
                warnings.append(f"DECAY ZONE: {signal.hour}:{signal.minute:02d} in 11:45-13:30")
                time_ok = False
            else:
                reasons.append(f"Time OK: {signal.hour}:{signal.minute:02d}")

        # Score check
        if signal.score < self.MIN_SCORE:
            warnings.append(f"Score too low: {signal.score} < {self.MIN_SCORE}")
        else:
            reasons.append(f"Score: {signal.score}")

        # Confidence check
        if signal.confidence < self.MIN_CONFIDENCE:
            warnings.append(f"Low confidence: {signal.confidence}")
        else:
            reasons.append(f"Confidence: {signal.confidence}")

        # Vote diff check
        if signal.vote_diff >= self.MIN_VOTE_DIFF:
            reasons.append(f"Strong {signal.side} vote: {signal.vote_diff:.1f}")

        # Spread check
        if signal.spread_pct > self.MAX_SPREAD_PCT:
            warnings.append(f"Wide spread: {signal.spread_pct:.2f}%")
        elif signal.spread_pct <= 1.5:
            reasons.append(f"Tight spread: {signal.spread_pct:.2f}%")

        # Delta check
        if abs(signal.delta) < self.MIN_DELTA:
            warnings.append(f"Low delta: {signal.delta:.3f}")
        else:
            reasons.append(f"Good delta: {signal.delta:.3f}")

        # Gamma check
        if signal.gamma >= self.MIN_GAMMA:
            reasons.append(f"Good gamma: {signal.gamma:.5f}")

        # Volume dominance / Trend alignment check - CRITICAL
        # vol_dom = CE means bullish flow → only CE entries allowed
        # vol_dom = PE means bearish flow → only PE entries allowed
        # vol_dom = NEUTRAL → both sides allowed
        trend_aligned = True
        if self.REQUIRE_TREND_ALIGNMENT and signal.vol_dom in ("CE", "PE"):
            if signal.vol_dom != signal.side:
                warnings.append(f"TREND MISMATCH: {signal.side} entry vs {signal.vol_dom} market")
                trend_aligned = False
            else:
                reasons.append(f"Trend aligned: {signal.side} with {signal.vol_dom} market")
        elif signal.vol_dom == "NEUTRAL":
            reasons.append("Neutral market - both sides OK")

        # Entry ready bonus
        if signal.entry_ready:
            reasons.append("Entry ready confirmed")

        # Decision - INCLUDES ALL LAYER FILTERS
        should_trade = (
            signal.score >= self.MIN_SCORE
            and signal.confidence >= self.MIN_CONFIDENCE
            and signal.spread_pct <= self.MAX_SPREAD_PCT
            and abs(signal.delta) >= self.MIN_DELTA
            and trend_aligned  # Layer 2: Must be aligned with market trend
            and vix_ok         # Layer 1: VIX in acceptable range
            and time_ok        # Layer 5: Not in decay zone
        )

        if should_trade:
            cooldown.record_signal()

        return should_trade, reasons, warnings

    def get_signal_status(self, index: str) -> Dict[str, Any]:
        """Get current signal status for an index."""
        index = index.upper()
        signal = self._last_signals.get(index)
        cooldown = self._get_cooldown(index)

        return {
            "index": index,
            "has_signal": signal is not None,
            "last_signal": signal.to_dict() if signal else None,
            "cooldown": {
                "can_signal": cooldown.can_signal(),
                "hourly_remaining": cooldown.get_hourly_remaining(),
            },
        }

    def process_signal_row(
        self,
        row: Dict[str, Any],
        index: str
    ) -> Tuple[Optional[FyersN7Signal], bool, List[str], List[str]]:
        """
        Process a signal row from FyersN7.

        Returns:
            (signal, should_trade, reasons, warnings)
        """
        signal = self.create_signal_from_dict(row, index)
        should_trade, reasons, warnings = self.evaluate_signal(signal)

        # Store for reference
        self._last_signals[index] = signal

        return signal, should_trade, reasons, warnings

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "min_score": self.MIN_SCORE,
            "min_confidence": self.MIN_CONFIDENCE,
            "indices_tracked": list(self._last_signals.keys()),
            "cooldowns": {
                idx: {
                    "can_signal": cd.can_signal(),
                    "hourly_remaining": cd.get_hourly_remaining(),
                }
                for idx, cd in self._cooldowns.items()
            },
        }

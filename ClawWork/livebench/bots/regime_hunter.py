"""
Regime Hunter Bot

Strategy: Detect regime transitions (not predict direction)
- Identifies breakout initiation, liquidity sweep reversals,
  trend exhaustion, and sideways compression breakouts
- Derived from forensic analysis of SENSEX 5-Mar-2026 intraday data

Key Insight:
  14 consecutive PE losses (14:32-14:36) followed by 11 CE wins (14:37-15:20)
  on the same day. The difference was regime timing, not signal quality.
  The vote_diff transition at 14:24:53 was the inflection point.

Learning Focus:
- Regime transition detection timing
- SL cascade → flip pattern recognition
- Sideways compression breakout timing
- Trap avoidance via flip-rate monitoring
"""

from collections import deque
from typing import Any, Dict, List, Optional
from .base import (
    TradingBot, BotSignal, TradeRecord, SharedMemory,
    SignalType, OptionType, get_strike_gap,
)


class RegimeHunterBot(TradingBot):
    """
    Regime Hunter Bot

    Philosophy: "Don't predict direction — detect regime shifts"

    The bot monitors five regime states and only enters
    when a high-conviction transition occurs:

    1. BREAKOUT_INITIATION  — OTM cascade + volume spike
    2. LIQUIDITY_SWEEP_REVERSAL — SL cascade on one side → flip
    3. TREND_EXHAUSTION  — decaying momentum in established trend
    4. SIDEWAYS_COMPRESSION_BREAKOUT — tight range → expansion
    5. TRAP — rapid CE/PE oscillation → stay out

    Data consumed (via market_data dict):
    - Standard: ltp, change_pct, high, low, volume
    - FyersN7 enriched: vote_ce, vote_pe, vote_diff, vote_side,
      vol_dom, signal_side, signal_confidence, sl_exits_recent,
      flip_count_5m, spread_pct, delta, gamma, iv
    """

    def __init__(self, shared_memory: Optional[SharedMemory] = None):
        super().__init__(
            name="RegimeHunter",
            description="Detects regime shifts (breakouts, liquidity sweeps, "
                        "exhaustion, compression breakouts) instead of "
                        "predicting direction. Avoids traps via flip-rate monitoring.",
            shared_memory=shared_memory,
        )

        # ----- tunable parameters -----
        self.parameters = {
            # Regime detection thresholds
            "vote_diff_strong": 5,       # vote_diff ≥ this → strong conviction
            "vote_diff_weak": 2,         # vote_diff ≤ this → uncertain
            "stability_window_sec": 180, # 3 min of same-side before entry
            "flip_trap_count": 3,        # ≥ flips in 5 min → TRAP regime
            "sl_cascade_threshold": 3,   # ≥ SL exits in 10 min → exhaustion
            # Sideways detection
            "sideways_range_pct": 0.10,  # range < 0.10% of spot → sideways
            "sideways_duration_min": 40, # minutes of compression before signal
            # Entry quality filters
            "min_confidence": 80,        # minimum fyersN7 signal confidence
            "max_otm_depth": 2,          # ATM + OTM1 only (skip OTM3+)
            "min_spread_ok_pct": 0.50,   # max bid-ask spread %
            # Risk
            "stop_loss_pct": 9.0,        # default SL as % of premium
            "target1_pct": 11.0,         # T1 as % of premium
            "target2_pct": 22.0,         # T2 as % of premium
            # Capital protection
            "max_entries_per_regime": 3,  # max entries in one regime cycle
            "cooldown_after_trap_sec": 180,  # 3 min cooldown after trap
        }

        # ----- rolling state -----
        self._vote_history: deque = deque(maxlen=300)   # (ts, side, diff)
        self._sl_exits: deque = deque(maxlen=50)        # timestamps of SL exits
        self._flip_times: deque = deque(maxlen=50)      # timestamps of side flips
        self._price_window: deque = deque(maxlen=600)   # (ts, price) for range calc
        self._current_regime: str = "UNKNOWN"
        self._regime_since: float = 0.0
        self._last_side: str = ""
        self._entries_this_regime: int = 0
        self._last_trap_time: float = 0.0

        # Learning history
        self._regime_outcomes: List[Dict] = []

    # ------------------------------------------------------------------
    # Core analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        option_chain: Optional[List[Dict]] = None,
    ) -> Optional[BotSignal]:
        """Analyze market data for regime-transition entry opportunities."""

        ltp = market_data.get("ltp", 0)
        if not ltp:
            return None

        now_ts = market_data.get("timestamp_epoch", 0)
        change_pct = market_data.get("change_pct", 0)

        # ── Ingest fyersN7-enriched fields ──
        vote_ce = _int(market_data.get("vote_ce", 0))
        vote_pe = _int(market_data.get("vote_pe", 0))
        vote_diff = _int(market_data.get("vote_diff", 0))
        vote_side = str(market_data.get("vote_side", "")).upper()
        vol_dom = str(market_data.get("vol_dom", "NEUTRAL")).upper()
        signal_side = str(market_data.get("signal_side", "")).upper()
        signal_conf = _float(market_data.get("signal_confidence", 0))
        sl_exits_recent = _int(market_data.get("sl_exits_recent", 0))
        flip_count_5m = _int(market_data.get("flip_count_5m", 0))
        spread_pct = _float(market_data.get("spread_pct", 1.0))
        stable = market_data.get("stable", "")
        flow_match = market_data.get("flow_match", "")
        learn_gate = market_data.get("learn_gate", "")

        # ── Update rolling buffers ──
        if now_ts:
            self._price_window.append((now_ts, ltp))
            self._vote_history.append((now_ts, vote_side, vote_diff))

            # Track flips
            if vote_side and vote_side != self._last_side and self._last_side:
                self._flip_times.append(now_ts)
            if vote_side:
                self._last_side = vote_side

            # Track SL exits
            if sl_exits_recent > 0:
                for _ in range(sl_exits_recent):
                    self._sl_exits.append(now_ts)

        # ── Compute derived indicators ──
        flips_5m = self._count_recent(self._flip_times, now_ts, 300)
        sls_10m = self._count_recent(self._sl_exits, now_ts, 600)
        price_range_pct = self._price_range_pct(now_ts, self.parameters["sideways_duration_min"] * 60, ltp)
        side_stable_sec = self._side_stable_seconds(now_ts)

        # Also accept pre-computed values from the engine if present
        if flip_count_5m > 0:
            flips_5m = max(flips_5m, flip_count_5m)

        # ── REGIME DETECTION ──
        new_regime = self._detect_regime(
            vote_diff=vote_diff,
            vote_side=vote_side,
            vol_dom=vol_dom,
            flips_5m=flips_5m,
            sls_10m=sls_10m,
            price_range_pct=price_range_pct,
            side_stable_sec=side_stable_sec,
            change_pct=change_pct,
        )

        if new_regime != self._current_regime:
            self._current_regime = new_regime
            self._regime_since = now_ts
            self._entries_this_regime = 0

        # ── ENTRY DECISION ──
        # Only enter on actionable regimes
        if self._current_regime in ("TRAP", "SIDEWAYS", "UNKNOWN"):
            return None

        # Cooldown after trap
        if now_ts and self._last_trap_time and (now_ts - self._last_trap_time) < self.parameters["cooldown_after_trap_sec"]:
            return None

        # Must have stable side for minimum window
        if side_stable_sec < self.parameters["stability_window_sec"]:
            return None

        # Max entries per regime
        if self._entries_this_regime >= self.parameters["max_entries_per_regime"]:
            return None

        # Confidence filter
        if signal_conf and signal_conf < self.parameters["min_confidence"]:
            return None

        # Spread filter
        if spread_pct > self.parameters["min_spread_ok_pct"]:
            return None

        # Triple quality filter (from forensic analysis)
        if stable and stable not in ("Y", "y", "1", "True", "true"):
            return None

        # ── Determine direction from regime + vote ──
        option_type, signal_type, direction = self._direction_from_regime(
            vote_side, vote_diff, self._current_regime, change_pct
        )
        if option_type is None:
            return None

        # ── Confidence scoring ──
        confidence = self._score_confidence(
            regime=self._current_regime,
            vote_diff=vote_diff,
            sls_10m=sls_10m,
            flips_5m=flips_5m,
            side_stable_sec=side_stable_sec,
            vol_dom=vol_dom,
            vote_side=vote_side,
            flow_match=flow_match,
            learn_gate=learn_gate,
        )

        # ── Strike selection ──
        strike = self._select_strike(ltp, option_type, option_chain, index)

        # ── Price levels ──
        entry, target, sl = self._compute_levels(ltp, option_type, market_data)

        reasoning = self._build_reasoning(
            self._current_regime, direction, vote_diff, vote_side,
            sls_10m, flips_5m, confidence, side_stable_sec,
        )

        signal = BotSignal(
            bot_name=self.name,
            index=index,
            signal_type=signal_type,
            option_type=option_type,
            confidence=confidence,
            strike=strike,
            entry=entry,
            target=target,
            stop_loss=sl,
            reasoning=reasoning,
            factors={
                "regime": self._current_regime,
                "direction": direction,
                "vote_diff": vote_diff,
                "vote_side": vote_side,
                "vol_dom": vol_dom,
                "sl_cascade_10m": sls_10m,
                "flip_count_5m": flips_5m,
                "side_stable_sec": side_stable_sec,
                "price_range_pct": round(price_range_pct, 4),
            },
        )

        self.recent_signals.append(signal)
        self.performance.total_signals += 1
        self._entries_this_regime += 1

        return signal

    # ------------------------------------------------------------------
    # Regime detection logic
    # ------------------------------------------------------------------

    def _detect_regime(
        self,
        vote_diff: int,
        vote_side: str,
        vol_dom: str,
        flips_5m: int,
        sls_10m: int,
        price_range_pct: float,
        side_stable_sec: float,
        change_pct: float,
    ) -> str:
        """
        Classify current market regime.

        Priority order (highest to lowest):
        1. TRAP — rapid oscillation, stay out
        2. LIQUIDITY_SWEEP_REVERSAL — SL cascade + vote flip
        3. BREAKOUT_INITIATION — strong vote + momentum
        4. TREND_EXHAUSTION — fading momentum in trend
        5. SIDEWAYS — tight range compression
        6. UNKNOWN
        """
        # ── TRAP: rapid flipping ──
        if flips_5m >= self.parameters["flip_trap_count"]:
            self._last_trap_time = max(
                (t for t in self._flip_times), default=0
            )
            return "TRAP"

        # ── LIQUIDITY_SWEEP_REVERSAL: SL cascade just happened + side now stable ──
        if (
            sls_10m >= self.parameters["sl_cascade_threshold"]
            and vote_diff >= self.parameters["vote_diff_strong"]
            and side_stable_sec >= 60  # at least 1 min of new direction
        ):
            return "LIQUIDITY_SWEEP_REVERSAL"

        # ── BREAKOUT_INITIATION: strong unidirectional conviction ──
        if (
            vote_diff >= self.parameters["vote_diff_strong"]
            and side_stable_sec >= self.parameters["stability_window_sec"]
            and abs(change_pct) >= 0.15
        ):
            return "BREAKOUT_INITIATION"

        # ── TREND_EXHAUSTION: was trending but momentum fading ──
        if (
            vote_diff <= self.parameters["vote_diff_weak"]
            and abs(change_pct) >= 0.3
            and vol_dom == "NEUTRAL"
        ):
            return "TREND_EXHAUSTION"

        # ── SIDEWAYS: tight range ──
        if price_range_pct < self.parameters["sideways_range_pct"]:
            return "SIDEWAYS"

        # Default
        if vote_diff >= self.parameters["vote_diff_strong"]:
            return "BREAKOUT_INITIATION"

        return "UNKNOWN"

    # ------------------------------------------------------------------
    # Direction, confidence, levels
    # ------------------------------------------------------------------

    def _direction_from_regime(
        self, vote_side: str, vote_diff: int, regime: str, change_pct: float
    ):
        """Determine option type and signal strength from regime + vote state."""
        if not vote_side or vote_side not in ("CE", "PE"):
            return None, None, None

        if vote_side == "CE":
            option_type = OptionType.CE
            direction = "BULLISH"
            if regime == "LIQUIDITY_SWEEP_REVERSAL":
                signal_type = SignalType.STRONG_BUY
            elif regime == "BREAKOUT_INITIATION" and vote_diff >= 7:
                signal_type = SignalType.STRONG_BUY
            else:
                signal_type = SignalType.BUY
        else:
            option_type = OptionType.PE
            direction = "BEARISH"
            if regime == "LIQUIDITY_SWEEP_REVERSAL":
                signal_type = SignalType.STRONG_SELL
            elif regime == "BREAKOUT_INITIATION" and vote_diff >= 7:
                signal_type = SignalType.STRONG_SELL
            else:
                signal_type = SignalType.SELL

        return option_type, signal_type, direction

    def _score_confidence(
        self,
        regime: str,
        vote_diff: int,
        sls_10m: int,
        flips_5m: int,
        side_stable_sec: float,
        vol_dom: str,
        vote_side: str,
        flow_match: str,
        learn_gate: str,
    ) -> float:
        """
        Score confidence 0-100 based on regime quality indicators.
        """
        base = 45

        # Regime type bonus
        regime_bonus = {
            "LIQUIDITY_SWEEP_REVERSAL": 25,
            "BREAKOUT_INITIATION": 20,
            "TREND_EXHAUSTION": 5,
        }
        base += regime_bonus.get(regime, 0)

        # Vote diff strength
        if vote_diff >= 7:
            base += 12
        elif vote_diff >= 5:
            base += 8
        elif vote_diff >= 3:
            base += 3

        # Side stability
        if side_stable_sec >= 300:   # 5 min
            base += 8
        elif side_stable_sec >= 180:  # 3 min
            base += 4

        # Vol dominance alignment
        if vol_dom == vote_side:
            base += 5

        # Quality triple filter
        if str(flow_match).upper() in ("Y", "TRUE", "1"):
            base += 3
        if str(learn_gate).upper() in ("Y", "TRUE", "1"):
            base += 2

        # SL cascade reversal signal (only for reversal regime)
        if regime == "LIQUIDITY_SWEEP_REVERSAL" and sls_10m >= 5:
            base += 5

        # Penalty for residual flip noise
        if flips_5m >= 2:
            base -= 5 * flips_5m

        # Historical performance adjustment
        if self.performance.total_trades >= 10:
            if self.performance.win_rate > 60:
                base += 3
            elif self.performance.win_rate < 40:
                base -= 5

        return float(max(20, min(95, base)))

    def _select_strike(
        self,
        ltp: float,
        option_type: OptionType,
        option_chain: Optional[List[Dict]],
        index: str,
    ) -> int:
        """Select ATM or OTM1 strike."""
        step = get_strike_gap(index)
        atm = round(ltp / step) * step

        # For regime-based trading, prefer ATM (highest delta / liquidity)
        return atm

    def _compute_levels(
        self, ltp: float, option_type: OptionType, market_data: Dict
    ) -> tuple:
        """Compute entry, target, stop-loss from available data or estimate."""
        # Prefer pre-computed values from fyersN7 signal engine
        entry = _float(market_data.get("entry"))
        sl = _float(market_data.get("sl"))
        t1 = _float(market_data.get("t1"))

        if entry and sl and t1:
            return entry, t1, sl

        # Fallback: estimate from ltp
        est_premium = ltp * 0.015
        entry = round(est_premium, 2)
        sl = round(entry * (1 - self.parameters["stop_loss_pct"] / 100), 2)
        target = round(entry * (1 + self.parameters["target1_pct"] / 100), 2)
        return entry, target, sl

    def _build_reasoning(
        self, regime, direction, vote_diff, vote_side,
        sls_10m, flips_5m, confidence, side_stable_sec,
    ) -> str:
        """Build human-readable reasoning string."""
        parts = [
            f"Regime: {regime}.",
            f"Direction: {direction} (vote {vote_side} diff={vote_diff}).",
        ]
        if regime == "LIQUIDITY_SWEEP_REVERSAL":
            parts.append(
                f"SL cascade detected ({sls_10m} stops hit in 10 min) "
                f"followed by stable flip to {vote_side}."
            )
        elif regime == "BREAKOUT_INITIATION":
            parts.append(
                f"Strong unidirectional conviction "
                f"(side stable for {side_stable_sec:.0f}s)."
            )

        if flips_5m > 0:
            parts.append(f"Recent flip noise: {flips_5m} flips in 5 min.")

        parts.append(f"Confidence: {confidence:.0f}%.")
        return " ".join(parts)

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def learn(self, trade: TradeRecord):
        """Learn from completed trade — adjust regime detection parameters."""
        self.update_performance(trade)
        self.memory.record_trade(trade)

        conditions = trade.market_conditions
        regime = conditions.get("regime", "UNKNOWN")
        vote_diff = conditions.get("vote_diff", 0)
        sls_10m = conditions.get("sl_cascade_10m", 0)

        if trade.outcome == "WIN":
            self.save_learning(
                topic=f"Regime {regime} WIN in {trade.index}",
                insight=(
                    f"Regime {regime} with vote_diff={vote_diff} "
                    f"yielded {trade.pnl_pct:.1f}% profit"
                ),
                conditions={
                    "regime": regime,
                    "vote_diff": vote_diff,
                    "outcome": "WIN",
                    "index": trade.index,
                    "option_type": trade.option_type,
                },
            )

            # Reinforce parameters that led to the win
            if regime == "LIQUIDITY_SWEEP_REVERSAL" and sls_10m >= 5:
                # Strong SL cascade reversal worked — tighten threshold
                self.parameters["sl_cascade_threshold"] = max(
                    2, self.parameters["sl_cascade_threshold"] - 0.2
                )

        elif trade.outcome == "LOSS":
            self.save_learning(
                topic=f"Regime {regime} LOSS in {trade.index}",
                insight=(
                    f"Regime {regime} with vote_diff={vote_diff} "
                    f"resulted in {trade.pnl_pct:.1f}% loss"
                ),
                conditions={
                    "regime": regime,
                    "vote_diff": vote_diff,
                    "outcome": "LOSS",
                    "index": trade.index,
                    "option_type": trade.option_type,
                    "type": "avoid_condition",
                },
            )

            lessons = []

            # If lost during what we classified as breakout, maybe it was a trap
            if regime == "BREAKOUT_INITIATION":
                lessons.append("Breakout entry was premature — increase stability window")
                self.parameters["stability_window_sec"] = min(
                    300, self.parameters["stability_window_sec"] + 15
                )

            # If lost on low vote_diff, raise threshold
            if vote_diff < 5:
                lessons.append("Low vote conviction led to loss — raise threshold")
                self.parameters["vote_diff_strong"] = min(
                    7, self.parameters["vote_diff_strong"] + 0.5
                )

            trade.lessons_learned = lessons

        self._regime_outcomes.append({
            "index": trade.index,
            "regime": regime,
            "outcome": trade.outcome,
            "pnl": trade.pnl,
            "vote_diff": vote_diff,
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _count_recent(buf: deque, now_ts: float, window_sec: int) -> int:
        """Count items in buffer within the last window_sec seconds."""
        if not now_ts:
            return 0
        cutoff = now_ts - window_sec
        return sum(1 for t in buf if t >= cutoff)

    def _side_stable_seconds(self, now_ts: float) -> float:
        """How many seconds the current vote_side has been held."""
        if not self._vote_history or not now_ts:
            return 0.0
        current = self._vote_history[-1][1]
        for ts, side, _ in reversed(self._vote_history):
            if side != current:
                return now_ts - ts
        # All history is same side
        return now_ts - self._vote_history[0][0] if self._vote_history else 0.0

    def _price_range_pct(self, now_ts: float, window_sec: int, ltp: float) -> float:
        """Calculate price range as % of LTP over the lookback window."""
        if not self._price_window or not ltp or not now_ts:
            return 999.0  # large → not sideways
        cutoff = now_ts - window_sec
        prices = [p for ts, p in self._price_window if ts >= cutoff]
        if not prices:
            return 999.0
        hi, lo = max(prices), min(prices)
        return ((hi - lo) / ltp) * 100

    def to_dict(self) -> Dict[str, Any]:
        """Extended dict with regime state."""
        d = super().to_dict()
        d["regime_state"] = {
            "current_regime": self._current_regime,
            "entries_this_regime": self._entries_this_regime,
            "recent_outcomes": self._regime_outcomes[-20:],
        }
        return d


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "", "None") else default
    except (ValueError, TypeError):
        return default


def _int(v, default: int = 0) -> int:
    try:
        return int(float(v)) if v not in (None, "", "None") else default
    except (ValueError, TypeError):
        return default

"""
Volatility Trader Bot

Strategy: Trade volatility expansion/contraction cycles
- Identify volatility breakouts
- Trade straddles/strangles during high IV
- Buy options during low IV periods

Learning Focus:
- Optimal IV percentile thresholds
- Best times for volatility trades
- Index-specific volatility patterns
"""

from typing import Any, Dict, List, Optional
from .base import (
    TradingBot, BotSignal, TradeRecord, SharedMemory,
    SignalType, OptionType, get_strike_gap
)


class VolatilityTraderBot(TradingBot):
    """
    Volatility Trader Bot

    Philosophy: "Volatility is the only certainty in markets"

    Entry Criteria:
    - IV percentile extremes (buy low, sell high)
    - Volatility breakouts after consolidation
    - VIX-based market regime signals

    Exit Criteria:
    - IV mean reversion
    - Volatility crush after events
    - Time decay management
    """

    def __init__(self, shared_memory: Optional[SharedMemory] = None):
        super().__init__(
            name="VolatilityTrader",
            description="Trades volatility cycles using IV analysis and regime detection.",
            shared_memory=shared_memory
        )

        self.parameters = {
            "low_iv_percentile": 45,      # IV below 45th percentile = cheap options (optimized)
            "high_iv_percentile": 60,     # IV above 60th percentile = expensive options (optimized)
            "vol_breakout_threshold": 0.5, # 0.5x normal range = breakout (more sensitive)
            "consolidation_periods": 3,    # Fewer periods needed before breakout
            "iv_crush_threshold": 0.2,     # 20% IV drop expected after events
            "stop_loss_pct": 2.0,
            "target_pct": 3.5,
        }

        self.volatility_history: List[Dict] = []
        self.regime: str = "NORMAL"  # LOW_VOL, NORMAL, HIGH_VOL, CRISIS

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        option_chain: Optional[List[Dict]] = None
    ) -> Optional[BotSignal]:
        """Analyze volatility for trading opportunities"""
        ltp = market_data.get("ltp", 0)
        change_pct = market_data.get("change_pct", 0)

        # Volatility data
        iv = market_data.get("iv", 0)
        iv_percentile = market_data.get("iv_percentile", 50)
        high = market_data.get("high", ltp)
        low = market_data.get("low", ltp)
        vix = market_data.get("vix", market_data.get("india_vix", 15))

        if not ltp:
            return None

        # Calculate intraday range volatility
        range_pct = ((high - low) / ltp * 100) if ltp > 0 else 0

        # Determine volatility regime
        self.regime = self._determine_regime(iv_percentile, vix, range_pct)

        # Get learnings
        conditions = {
            "regime": self.regime,
            "iv_percentile_bucket": self._bucket_iv(iv_percentile),
            "index": index
        }
        learnings = self.get_relevant_learnings(index, conditions)

        signal = None

        # === VOLATILITY STRATEGIES ===

        # Strategy 1: Low IV - Buy options (volatility expansion expected)
        if iv_percentile <= self.parameters["low_iv_percentile"]:
            # Check for consolidation pattern
            if self._is_consolidating(range_pct):
                confidence = self._calculate_confidence(
                    iv_percentile, "LOW_IV_BUY", learnings
                )

                # Direction bias from price action
                if change_pct > 0.2:
                    signal = self._create_signal(
                        index, ltp, OptionType.CE, SignalType.BUY,
                        confidence, "LOW_IV_BULLISH", iv_percentile, option_chain
                    )
                elif change_pct < -0.2:
                    signal = self._create_signal(
                        index, ltp, OptionType.PE, SignalType.BUY,
                        confidence, "LOW_IV_BEARISH", iv_percentile, option_chain
                    )
                else:
                    # Neutral - could suggest straddle (for now, skip)
                    pass

        # Strategy 2: High IV - Sell options (volatility contraction expected)
        elif iv_percentile >= self.parameters["high_iv_percentile"]:
            confidence = self._calculate_confidence(
                iv_percentile, "HIGH_IV_SELL", learnings
            ) * 0.9  # Slightly lower confidence for selling

            # Counter-trend during high IV
            if change_pct > 1.0:  # Overextended up
                signal = self._create_signal(
                    index, ltp, OptionType.PE, SignalType.SELL,
                    confidence, "HIGH_IV_REVERSAL", iv_percentile, option_chain
                )
            elif change_pct < -1.0:  # Overextended down
                signal = self._create_signal(
                    index, ltp, OptionType.CE, SignalType.SELL,
                    confidence, "HIGH_IV_REVERSAL", iv_percentile, option_chain
                )

        # Strategy 3: Volatility Breakout
        elif self._is_volatility_breakout(range_pct, change_pct):
            confidence = self._calculate_confidence(
                iv_percentile, "VOL_BREAKOUT", learnings
            )

            if change_pct > 0:
                signal = self._create_signal(
                    index, ltp, OptionType.CE, SignalType.STRONG_BUY,
                    confidence, "VOLATILITY_BREAKOUT_UP", iv_percentile, option_chain
                )
            else:
                signal = self._create_signal(
                    index, ltp, OptionType.PE, SignalType.STRONG_SELL,
                    confidence, "VOLATILITY_BREAKOUT_DOWN", iv_percentile, option_chain
                )

        # Strategy 4: VIX-based regime trading
        if not signal and vix:
            vix_signal = self._analyze_vix_regime(vix, change_pct)
            if vix_signal:
                confidence = self._calculate_confidence(
                    iv_percentile, vix_signal[0], learnings
                ) * 0.85

                signal = self._create_signal(
                    index, ltp, vix_signal[1], vix_signal[2],
                    confidence, vix_signal[0], iv_percentile, option_chain
                )

        if signal:
            signal.factors["regime"] = self.regime
            signal.factors["range_pct"] = round(range_pct, 2)
            signal.factors["vix"] = vix
            self.recent_signals.append(signal)
            self.performance.total_signals += 1

        # Store volatility data for pattern recognition
        self._update_volatility_history(index, iv_percentile, range_pct)

        return signal

    def _determine_regime(
        self,
        iv_percentile: float,
        vix: float,
        range_pct: float
    ) -> str:
        """Determine current volatility regime"""
        if vix >= 25 or iv_percentile >= 90:
            return "CRISIS"
        elif vix >= 18 or iv_percentile >= 70:
            return "HIGH_VOL"
        elif vix <= 12 or iv_percentile <= 25:
            return "LOW_VOL"
        return "NORMAL"

    def _bucket_iv(self, iv_percentile: float) -> str:
        """Bucket IV percentile for pattern matching"""
        if iv_percentile <= 20:
            return "VERY_LOW"
        elif iv_percentile <= 40:
            return "LOW"
        elif iv_percentile <= 60:
            return "NORMAL"
        elif iv_percentile <= 80:
            return "HIGH"
        return "VERY_HIGH"

    def _is_consolidating(self, range_pct: float) -> bool:
        """Check if market is consolidating (low range)"""
        # Low range indicates consolidation (relaxed threshold)
        return range_pct < 0.8

    def _is_volatility_breakout(self, range_pct: float, change_pct: float) -> bool:
        """Check for volatility breakout"""
        # High range with directional move (lowered thresholds)
        return range_pct > 1.0 and abs(change_pct) > 0.7

    def _analyze_vix_regime(
        self,
        vix: float,
        change_pct: float
    ) -> Optional[tuple]:
        """
        Analyze VIX for regime-based signals

        Returns: (signal_name, option_type, signal_type) or None
        """
        if vix >= 25:
            # Crisis mode - expect mean reversion
            if change_pct < -2.0:
                return ("VIX_CRISIS_REVERSAL", OptionType.CE, SignalType.BUY)
        elif vix <= 12:
            # Complacency - expect vol expansion
            # Direction from recent price action
            if change_pct > 0.5:
                return ("VIX_LOW_MOMENTUM", OptionType.CE, SignalType.BUY)
            elif change_pct < -0.5:
                return ("VIX_LOW_MOMENTUM", OptionType.PE, SignalType.SELL)

        return None

    def _calculate_confidence(
        self,
        iv_percentile: float,
        vol_signal: str,
        learnings: List[Dict]
    ) -> float:
        """Calculate confidence based on volatility analysis"""
        base_confidence = 50

        # Extreme IV adds confidence
        if iv_percentile <= 15 or iv_percentile >= 85:
            base_confidence += 15
        elif iv_percentile <= 25 or iv_percentile >= 75:
            base_confidence += 10

        # Breakouts have higher confidence
        if "BREAKOUT" in vol_signal:
            base_confidence += 10

        # Historical accuracy
        similar = [l for l in learnings if l.get("vol_signal") == vol_signal]
        wins = [l for l in similar if l.get("outcome") == "WIN"]

        if len(similar) >= 3:
            win_rate = len(wins) / len(similar)
            base_confidence += (win_rate - 0.5) * 20

        return min(85, max(30, base_confidence))

    def _create_signal(
        self,
        index: str,
        ltp: float,
        option_type: OptionType,
        signal_type: SignalType,
        confidence: float,
        vol_signal: str,
        iv_percentile: float,
        option_chain: Optional[List[Dict]]
    ) -> BotSignal:
        """Create volatility-based signal"""
        strike = self._select_strike(ltp, option_type, index)
        entry, target, sl = self._calculate_levels(ltp, option_type)

        return BotSignal(
            bot_name=self.name,
            index=index,
            signal_type=signal_type,
            option_type=option_type,
            confidence=confidence,
            strike=strike,
            entry=entry,
            target=target,
            stop_loss=sl,
            reasoning=self._build_reasoning(vol_signal, iv_percentile),
            factors={
                "vol_signal": vol_signal,
                "iv_percentile": round(iv_percentile, 1),
            }
        )

    def _select_strike(self, ltp: float, option_type: OptionType, index: str) -> int:
        """Select strike based on volatility regime"""
        step = get_strike_gap(index)
        atm = round(ltp / step) * step

        # In low vol, go ATM for gamma
        # In high vol, go slightly OTM for theta decay protection
        if self.regime == "LOW_VOL":
            return atm
        elif self.regime in ["HIGH_VOL", "CRISIS"]:
            offset = step if option_type == OptionType.CE else -step
            return atm + offset
        return atm

    def _calculate_levels(self, ltp: float, option_type: OptionType) -> tuple:
        """Calculate trade levels with volatility-adjusted targets"""
        premium = ltp * 0.012  # Slightly higher premium assumption
        entry = round(premium, 2)

        # Wider targets for volatility trades
        target = round(entry * (1 + self.parameters["target_pct"] / 100), 2)
        sl = round(entry * (1 - self.parameters["stop_loss_pct"] / 100), 2)

        return entry, target, sl

    def _build_reasoning(self, vol_signal: str, iv_percentile: float) -> str:
        """Build volatility-based reasoning"""
        signal_descriptions = {
            "LOW_IV_BULLISH": "Low IV environment - buying calls for volatility expansion",
            "LOW_IV_BEARISH": "Low IV environment - buying puts for volatility expansion",
            "HIGH_IV_REVERSAL": "High IV with overextension - expecting mean reversion",
            "VOLATILITY_BREAKOUT_UP": "Volatility breakout to upside - riding momentum",
            "VOLATILITY_BREAKOUT_DOWN": "Volatility breakout to downside - riding momentum",
            "VIX_CRISIS_REVERSAL": "VIX spike with panic selling - contrarian buy",
            "VIX_LOW_MOMENTUM": "Low VIX complacency - expecting vol expansion",
        }

        desc = signal_descriptions.get(vol_signal, vol_signal)
        return f"{desc}. IV Percentile: {iv_percentile:.0f}%. Regime: {self.regime}."

    def _update_volatility_history(
        self,
        index: str,
        iv_percentile: float,
        range_pct: float
    ):
        """Track volatility for pattern recognition"""
        self.volatility_history.append({
            "index": index,
            "iv_percentile": iv_percentile,
            "range_pct": range_pct,
            "regime": self.regime,
        })

        # Keep last 50 entries
        if len(self.volatility_history) > 50:
            self.volatility_history = self.volatility_history[-50:]

    def learn(self, trade: TradeRecord):
        """Learn from volatility-based trades"""
        self.update_performance(trade)
        self.memory.record_trade(trade)

        vol_signal = trade.market_conditions.get("vol_signal", "UNKNOWN")
        iv_percentile = trade.market_conditions.get("iv_percentile", 50)
        regime = trade.market_conditions.get("regime", "NORMAL")

        self.save_learning(
            topic=f"Volatility trade: {vol_signal}",
            insight=f"IV {iv_percentile:.0f}% in {regime} regime = {trade.outcome}",
            conditions={
                "vol_signal": vol_signal,
                "iv_percentile_bucket": self._bucket_iv(iv_percentile),
                "regime": regime,
                "outcome": trade.outcome,
            }
        )

        # Adjust parameters based on outcomes
        if trade.outcome == "WIN":
            # Track successful regimes
            pass
        elif trade.outcome == "LOSS":
            # Adjust IV thresholds
            if "LOW_IV" in vol_signal:
                # Be more conservative with low IV buys
                self.parameters["low_iv_percentile"] = max(
                    10, self.parameters["low_iv_percentile"] - 2
                )
            elif "HIGH_IV" in vol_signal:
                # Be more conservative with high IV sells
                self.parameters["high_iv_percentile"] = min(
                    90, self.parameters["high_iv_percentile"] + 2
                )

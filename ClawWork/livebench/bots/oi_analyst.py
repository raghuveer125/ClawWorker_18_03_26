"""
Open Interest Analyst Bot

Strategy: Follow institutional money through OI/PCR analysis
- Track OI buildup/unwinding
- Analyze Put-Call Ratio
- Identify Max Pain levels

Learning Focus:
- Correlation between OI changes and price moves
- Best PCR levels for entries
- Max Pain accuracy
"""

from typing import Any, Dict, List, Optional
from .base import (
    TradingBot, BotSignal, TradeRecord, SharedMemory,
    SignalType, OptionType, get_strike_gap
)


class OIAnalystBot(TradingBot):
    """
    OI Analyst Bot

    Philosophy: "Follow the institutional money"

    Entry Criteria:
    - Bullish OI buildup (Price Up + OI Up)
    - Bearish OI buildup (Price Down + OI Up)
    - Extreme PCR levels

    Exit Criteria:
    - Long/Short unwinding signals
    - PCR reversal
    - Max Pain approach
    """

    def __init__(self, shared_memory: Optional[SharedMemory] = None):
        super().__init__(
            name="OIAnalyst",
            description="Analyzes Open Interest and PCR for institutional flow signals.",
            shared_memory=shared_memory
        )

        self.parameters = {
            "bullish_pcr_threshold": 1.2,    # PCR > 1.2 = bullish
            "bearish_pcr_threshold": 0.8,    # PCR < 0.8 = bearish
            "extreme_pcr_high": 1.5,         # Very bullish
            "extreme_pcr_low": 0.5,          # Very bearish
            "oi_change_threshold": 5.0,      # % OI change to consider significant
            "max_pain_weight": 0.3,          # Weight for max pain in decisions
            "stop_loss_pct": 1.5,
            "target_pct": 3.0,
        }

        self.oi_history: List[Dict] = []

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        option_chain: Optional[List[Dict]] = None
    ) -> Optional[BotSignal]:
        """Analyze OI data for institutional signals"""
        change_pct = market_data.get("change_pct", 0)
        ltp = market_data.get("ltp", 0)

        # OI data (required for this bot)
        ce_oi = market_data.get("ce_oi", 0)
        pe_oi = market_data.get("pe_oi", 0)
        ce_oi_change = market_data.get("ce_oi_change", 0)
        pe_oi_change = market_data.get("pe_oi_change", 0)
        pcr = market_data.get("pcr", 0)
        max_pain = market_data.get("max_pain", 0)

        if not ltp or (ce_oi == 0 and pe_oi == 0):
            return None

        # Calculate PCR if not provided
        if pcr == 0 and ce_oi > 0:
            pcr = pe_oi / ce_oi

        # Calculate total OI change
        total_oi_change = ce_oi_change + pe_oi_change
        oi_increasing = total_oi_change > 0

        # Get learnings
        conditions = {
            "pcr_level": "HIGH" if pcr > 1.2 else "LOW" if pcr < 0.8 else "NEUTRAL",
            "oi_trend": "INCREASING" if oi_increasing else "DECREASING",
            "index": index
        }
        learnings = self.get_relevant_learnings(index, conditions)

        # === OI ANALYSIS ===

        signal = None
        oi_signal = self._analyze_oi_pattern(change_pct, oi_increasing)

        # Bullish signals
        if oi_signal == "BULLISH_BUILDUP":
            # Fresh longs - strong bullish
            if pcr >= self.parameters["bullish_pcr_threshold"]:
                confidence = self._calculate_confidence(pcr, oi_signal, learnings)
                signal = self._create_signal(
                    index, ltp, OptionType.CE, SignalType.STRONG_BUY,
                    confidence, oi_signal, pcr, option_chain
                )

        elif oi_signal == "SHORT_COVERING":
            # Shorts exiting - moderately bullish
            if pcr >= 1.0:
                confidence = self._calculate_confidence(pcr, oi_signal, learnings) * 0.8
                signal = self._create_signal(
                    index, ltp, OptionType.CE, SignalType.BUY,
                    confidence, oi_signal, pcr, option_chain
                )

        # Bearish signals
        elif oi_signal == "BEARISH_BUILDUP":
            # Fresh shorts - strong bearish
            if pcr <= self.parameters["bearish_pcr_threshold"]:
                confidence = self._calculate_confidence(pcr, oi_signal, learnings)
                signal = self._create_signal(
                    index, ltp, OptionType.PE, SignalType.STRONG_SELL,
                    confidence, oi_signal, pcr, option_chain
                )

        elif oi_signal == "LONG_UNWINDING":
            # Longs exiting - moderately bearish
            if pcr <= 1.0:
                confidence = self._calculate_confidence(pcr, oi_signal, learnings) * 0.8
                signal = self._create_signal(
                    index, ltp, OptionType.PE, SignalType.SELL,
                    confidence, oi_signal, pcr, option_chain
                )

        # PCR extreme signals (override)
        if pcr >= self.parameters["extreme_pcr_high"] and not signal:
            # Extreme bullish PCR
            confidence = 70
            signal = self._create_signal(
                index, ltp, OptionType.CE, SignalType.BUY,
                confidence, "EXTREME_PCR_BULLISH", pcr, option_chain
            )
        elif pcr <= self.parameters["extreme_pcr_low"] and not signal:
            # Extreme bearish PCR
            confidence = 70
            signal = self._create_signal(
                index, ltp, OptionType.PE, SignalType.SELL,
                confidence, "EXTREME_PCR_BEARISH", pcr, option_chain
            )

        if signal:
            signal.factors["max_pain"] = max_pain
            signal.factors["max_pain_distance"] = ((ltp - max_pain) / ltp * 100) if max_pain else 0
            self.recent_signals.append(signal)
            self.performance.total_signals += 1

        return signal

    def _analyze_oi_pattern(self, change_pct: float, oi_increasing: bool) -> str:
        """
        Analyze OI pattern

        Price Up + OI Up = BULLISH_BUILDUP (Fresh longs)
        Price Up + OI Down = SHORT_COVERING
        Price Down + OI Up = BEARISH_BUILDUP (Fresh shorts)
        Price Down + OI Down = LONG_UNWINDING
        """
        price_up = change_pct > 0.1

        if price_up and oi_increasing:
            return "BULLISH_BUILDUP"
        elif price_up and not oi_increasing:
            return "SHORT_COVERING"
        elif not price_up and oi_increasing:
            return "BEARISH_BUILDUP"
        elif not price_up and not oi_increasing:
            return "LONG_UNWINDING"
        return "NEUTRAL"

    def _calculate_confidence(
        self,
        pcr: float,
        oi_signal: str,
        learnings: List[Dict]
    ) -> float:
        """Calculate confidence based on OI analysis"""
        base_confidence = 50

        # PCR extremes add confidence
        if pcr >= self.parameters["extreme_pcr_high"] or pcr <= self.parameters["extreme_pcr_low"]:
            base_confidence += 15

        # Fresh positions (buildup) more reliable than unwinding
        if "BUILDUP" in oi_signal:
            base_confidence += 10

        # Historical accuracy
        similar = [l for l in learnings if l.get("oi_signal") == oi_signal]
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
        oi_signal: str,
        pcr: float,
        option_chain: Optional[List[Dict]]
    ) -> BotSignal:
        """Create OI-based signal"""
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
            reasoning=self._build_reasoning(oi_signal, pcr),
            factors={
                "oi_signal": oi_signal,
                "pcr": round(pcr, 2),
            }
        )

    def _select_strike(self, ltp: float, option_type: OptionType, index: str) -> int:
        """Select strike based on OI analysis"""
        step = get_strike_gap(index)
        return round(ltp / step) * step

    def _calculate_levels(self, ltp: float, option_type: OptionType) -> tuple:
        """Calculate trade levels"""
        premium = ltp * 0.01
        entry = round(premium, 2)
        target = round(entry * (1 + self.parameters["target_pct"] / 100), 2)
        sl = round(entry * (1 - self.parameters["stop_loss_pct"] / 100), 2)
        return entry, target, sl

    def _build_reasoning(self, oi_signal: str, pcr: float) -> str:
        """Build OI-based reasoning"""
        signal_descriptions = {
            "BULLISH_BUILDUP": "Fresh long positions being added (Price Up + OI Up)",
            "SHORT_COVERING": "Short positions being covered (Price Up + OI Down)",
            "BEARISH_BUILDUP": "Fresh short positions being added (Price Down + OI Up)",
            "LONG_UNWINDING": "Long positions being unwound (Price Down + OI Down)",
            "EXTREME_PCR_BULLISH": "Extremely high PCR indicating strong support",
            "EXTREME_PCR_BEARISH": "Extremely low PCR indicating strong resistance",
        }

        desc = signal_descriptions.get(oi_signal, oi_signal)
        return f"{desc}. PCR: {pcr:.2f}. Following institutional flow."

    def learn(self, trade: TradeRecord):
        """Learn from OI-based trades"""
        self.update_performance(trade)
        self.memory.record_trade(trade)

        oi_signal = trade.market_conditions.get("oi_signal", "UNKNOWN")
        pcr = trade.market_conditions.get("pcr", 1.0)

        self.save_learning(
            topic=f"OI trade: {oi_signal}",
            insight=f"PCR {pcr:.2f} with {oi_signal} = {trade.outcome}",
            conditions={
                "oi_signal": oi_signal,
                "pcr_range": (pcr - 0.1, pcr + 0.1),
                "outcome": trade.outcome,
            }
        )

        # Adjust parameters based on outcomes
        if trade.outcome == "WIN":
            # Reinforce current thresholds
            pass
        elif trade.outcome == "LOSS":
            # Tighten thresholds
            if "BULLISH" in oi_signal:
                self.parameters["bullish_pcr_threshold"] = min(1.5, self.parameters["bullish_pcr_threshold"] + 0.05)
            elif "BEARISH" in oi_signal:
                self.parameters["bearish_pcr_threshold"] = max(0.5, self.parameters["bearish_pcr_threshold"] - 0.05)

        self.oi_history.append({
            "oi_signal": oi_signal,
            "pcr": pcr,
            "outcome": trade.outcome,
        })

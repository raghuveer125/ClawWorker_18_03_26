"""
Sentiment Module for Regime Hunter Pipeline

Analyzes institutional sentiment using:
- Put-Call Ratio (PCR)
- Open Interest changes (CE/PE)
- IV Skew (Call IV vs Put IV)
- OI concentration at strikes

Answers: "What are institutions/smart money doing?"
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .base_module import BaseModule, ModuleOutput


class SentimentBias(Enum):
    """Market sentiment classification"""
    STRONG_BULLISH = "STRONG_BULLISH"  # PCR >= 1.3, heavy PE writing
    BULLISH = "BULLISH"                 # PCR >= 1.1, PE OI > CE OI
    NEUTRAL = "NEUTRAL"                 # PCR 0.9-1.1, balanced OI
    BEARISH = "BEARISH"                 # PCR <= 0.9, CE OI > PE OI
    STRONG_BEARISH = "STRONG_BEARISH"   # PCR <= 0.7, heavy CE writing


class OIPattern(Enum):
    """Open Interest patterns"""
    LONG_BUILDUP = "LONG_BUILDUP"       # Price up + OI up (bullish)
    SHORT_BUILDUP = "SHORT_BUILDUP"     # Price down + OI up (bearish)
    LONG_UNWINDING = "LONG_UNWINDING"   # Price down + OI down (bearish)
    SHORT_COVERING = "SHORT_COVERING"   # Price up + OI down (bullish)
    NO_CLEAR_PATTERN = "NO_CLEAR_PATTERN"


@dataclass
class SentimentOutput(ModuleOutput):
    """Output specific to Sentiment Module"""
    bias: SentimentBias
    pcr: float
    ce_oi_change: float
    pe_oi_change: float
    oi_pattern: OIPattern
    institutional_signal: str  # RES+, SUP+, SC, LU, etc.
    position_bias: str  # LONG, SHORT, NEUTRAL

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["bias"] = self.bias.value
        d["oi_pattern"] = self.oi_pattern.value
        return d


class SentimentModule(BaseModule):
    """
    Sentiment Analysis Module

    Analyzes PCR, OI patterns to determine institutional bias.

    Thresholds (can be overridden per index):
    - PCR_STRONG_BULLISH: 1.3
    - PCR_BULLISH: 1.1
    - PCR_BEARISH: 0.9
    - PCR_STRONG_BEARISH: 0.7
    """

    # Default thresholds
    DEFAULT_THRESHOLDS = {
        "pcr_strong_bullish": 1.3,
        "pcr_bullish": 1.1,
        "pcr_neutral_low": 0.9,
        "pcr_bearish": 0.9,
        "pcr_strong_bearish": 0.7,
        "oi_significant_ratio": 1.5,  # OI diff considered significant
        "oi_extreme_ratio": 2.0,      # OI diff considered extreme
    }

    # Index-specific adjustments
    INDEX_ADJUSTMENTS = {
        "BANKNIFTY": {
            "pcr_strong_bullish": 1.4,   # BANKNIFTY PCR tends higher
            "pcr_bullish": 1.15,
        },
        "NIFTY50": {
            "pcr_strong_bullish": 1.25,
            "pcr_bullish": 1.05,
        },
        "SENSEX": {
            "pcr_strong_bullish": 1.2,
            "pcr_strong_bearish": 0.8,
        },
    }

    def __init__(self, data_dir: str = None):
        super().__init__(
            name="sentiment_module",
            description="Analyzes PCR and OI patterns for institutional bias",
            data_dir=data_dir
        )

        # Apply default index adjustments if not configured
        for index, adjustments in self.INDEX_ADJUSTMENTS.items():
            if index not in self.config.index_overrides:
                self.config.index_overrides[index] = adjustments
        self.save_config()

    def _get_threshold(self, param: str, index: str) -> float:
        """Get threshold for parameter, checking index overrides"""
        return self.config.get_threshold(
            param,
            index,
            self.DEFAULT_THRESHOLDS.get(param, 0)
        )

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        historical_data: Optional[List[Dict]] = None
    ) -> SentimentOutput:
        """
        Analyze market sentiment from options data

        Args:
            index: Index name
            market_data: Current market data with PCR, OI, price change
            historical_data: Recent data for pattern detection

        Returns:
            SentimentOutput with bias and institutional signals
        """
        # Extract data
        pcr = market_data.get("pcr", market_data.get("net_pcr", 1.0))
        ce_oi = market_data.get("ce_oi", market_data.get("total_ce_oi", 0))
        pe_oi = market_data.get("pe_oi", market_data.get("total_pe_oi", 0))
        ce_oi_change = market_data.get("ce_oi_change", 0)
        pe_oi_change = market_data.get("pe_oi_change", 0)
        change_pct = market_data.get("change_pct", 0)
        ltp = market_data.get("ltp", 0)

        # ATM strike OI for concentration analysis
        atm_ce_oi = market_data.get("atm_ce_oi", 0)
        atm_pe_oi = market_data.get("atm_pe_oi", 0)

        # Get index-specific thresholds
        pcr_strong_bullish = self._get_threshold("pcr_strong_bullish", index)
        pcr_bullish = self._get_threshold("pcr_bullish", index)
        pcr_bearish = self._get_threshold("pcr_bearish", index)
        pcr_strong_bearish = self._get_threshold("pcr_strong_bearish", index)
        oi_significant = self._get_threshold("oi_significant_ratio", index)

        # Classify sentiment bias
        bias = self._classify_bias(
            pcr, ce_oi_change, pe_oi_change,
            pcr_strong_bullish, pcr_bullish, pcr_bearish, pcr_strong_bearish,
            oi_significant
        )

        # Detect OI pattern
        oi_pattern = self._detect_oi_pattern(change_pct, ce_oi_change, pe_oi_change)

        # Generate institutional signal
        institutional_signal = self._generate_institutional_signal(
            bias, oi_pattern, pcr, change_pct
        )

        # Determine position bias
        position_bias = self._determine_position_bias(bias, oi_pattern)

        # Generate recommendation
        recommendation = self._generate_recommendation(
            bias, oi_pattern, pcr, institutional_signal
        )

        # Calculate confidence
        confidence = self._calculate_confidence(
            pcr, ce_oi_change, pe_oi_change, bias, oi_pattern
        )

        output = SentimentOutput(
            module_name=self.name,
            timestamp=datetime.now().isoformat(),
            index=index,
            confidence=confidence,
            factors={
                "pcr": round(pcr, 3),
                "ce_oi": ce_oi,
                "pe_oi": pe_oi,
                "ce_oi_change": ce_oi_change,
                "pe_oi_change": pe_oi_change,
                "change_pct": round(change_pct, 2),
                "atm_ce_oi": atm_ce_oi,
                "atm_pe_oi": atm_pe_oi,
                "thresholds": {
                    "pcr_strong_bullish": pcr_strong_bullish,
                    "pcr_bullish": pcr_bullish,
                    "pcr_bearish": pcr_bearish,
                },
            },
            recommendation=recommendation,
            bias=bias,
            pcr=round(pcr, 3),
            ce_oi_change=ce_oi_change,
            pe_oi_change=pe_oi_change,
            oi_pattern=oi_pattern,
            institutional_signal=institutional_signal,
            position_bias=position_bias,
        )

        # Store in history
        self.signal_history.append(output)
        if len(self.signal_history) > 100:
            self.signal_history.pop(0)

        return output

    def _classify_bias(
        self,
        pcr: float,
        ce_oi_change: float,
        pe_oi_change: float,
        pcr_strong_bullish: float,
        pcr_bullish: float,
        pcr_bearish: float,
        pcr_strong_bearish: float,
        oi_significant: float
    ) -> SentimentBias:
        """Classify market sentiment bias"""
        # PCR-based classification
        pcr_score = 0
        if pcr >= pcr_strong_bullish:
            pcr_score = 2
        elif pcr >= pcr_bullish:
            pcr_score = 1
        elif pcr <= pcr_strong_bearish:
            pcr_score = -2
        elif pcr <= pcr_bearish:
            pcr_score = -1

        # OI-based classification
        oi_score = 0
        if pe_oi_change > 0 and ce_oi_change > 0:
            # Both writing - check which is dominant
            if pe_oi_change > ce_oi_change * oi_significant:
                oi_score = 1  # More PE writing = bullish
            elif ce_oi_change > pe_oi_change * oi_significant:
                oi_score = -1  # More CE writing = bearish
        elif pe_oi_change > ce_oi_change * oi_significant:
            oi_score = 1
        elif ce_oi_change > pe_oi_change * oi_significant:
            oi_score = -1

        # Combine scores
        total_score = pcr_score + oi_score

        if total_score >= 3:
            return SentimentBias.STRONG_BULLISH
        elif total_score >= 1:
            return SentimentBias.BULLISH
        elif total_score <= -3:
            return SentimentBias.STRONG_BEARISH
        elif total_score <= -1:
            return SentimentBias.BEARISH
        else:
            return SentimentBias.NEUTRAL

    def _detect_oi_pattern(
        self,
        change_pct: float,
        ce_oi_change: float,
        pe_oi_change: float
    ) -> OIPattern:
        """Detect OI pattern based on price and OI changes"""
        total_oi_change = ce_oi_change + pe_oi_change

        # Price up
        if change_pct > 0.2:
            if total_oi_change > 0:
                return OIPattern.LONG_BUILDUP  # Bullish
            elif total_oi_change < 0:
                return OIPattern.SHORT_COVERING  # Bullish (shorts exiting)

        # Price down
        elif change_pct < -0.2:
            if total_oi_change > 0:
                return OIPattern.SHORT_BUILDUP  # Bearish
            elif total_oi_change < 0:
                return OIPattern.LONG_UNWINDING  # Bearish (longs exiting)

        return OIPattern.NO_CLEAR_PATTERN

    def _generate_institutional_signal(
        self,
        bias: SentimentBias,
        oi_pattern: OIPattern,
        pcr: float,
        change_pct: float
    ) -> str:
        """Generate institutional signal code"""
        signals = []

        # Resistance signals (bearish institutional activity)
        if bias in [SentimentBias.BEARISH, SentimentBias.STRONG_BEARISH]:
            if oi_pattern == OIPattern.SHORT_BUILDUP:
                signals.append("RES+")  # Strong resistance building
            elif oi_pattern in [OIPattern.LONG_UNWINDING]:
                signals.append("LU")  # Long unwinding

        # Support signals (bullish institutional activity)
        if bias in [SentimentBias.BULLISH, SentimentBias.STRONG_BULLISH]:
            if oi_pattern == OIPattern.LONG_BUILDUP:
                signals.append("SUP+")  # Strong support building
            elif oi_pattern == OIPattern.SHORT_COVERING:
                signals.append("SC")  # Short covering

        # PCR extreme signals
        if pcr >= 1.5:
            signals.append("PCR_EXTREME_BULL")
        elif pcr <= 0.6:
            signals.append("PCR_EXTREME_BEAR")

        return ", ".join(signals) if signals else "NEUTRAL"

    def _determine_position_bias(
        self,
        bias: SentimentBias,
        oi_pattern: OIPattern
    ) -> str:
        """Determine suggested position direction"""
        bullish_patterns = [OIPattern.LONG_BUILDUP, OIPattern.SHORT_COVERING]
        bearish_patterns = [OIPattern.SHORT_BUILDUP, OIPattern.LONG_UNWINDING]

        # Strong bias overrides pattern
        if bias == SentimentBias.STRONG_BULLISH:
            return "LONG"
        elif bias == SentimentBias.STRONG_BEARISH:
            return "SHORT"

        # Pattern confirmation
        if bias == SentimentBias.BULLISH and oi_pattern in bullish_patterns:
            return "LONG"
        elif bias == SentimentBias.BEARISH and oi_pattern in bearish_patterns:
            return "SHORT"

        # Conflicting signals
        if bias == SentimentBias.BULLISH and oi_pattern in bearish_patterns:
            return "NEUTRAL"  # Conflict
        elif bias == SentimentBias.BEARISH and oi_pattern in bullish_patterns:
            return "NEUTRAL"  # Conflict

        return "NEUTRAL"

    def _calculate_confidence(
        self,
        pcr: float,
        ce_oi_change: float,
        pe_oi_change: float,
        bias: SentimentBias,
        oi_pattern: OIPattern
    ) -> float:
        """Calculate confidence in sentiment assessment"""
        confidence = 50  # Base confidence

        # PCR extremes increase confidence
        if pcr >= 1.5 or pcr <= 0.6:
            confidence += 20
        elif pcr >= 1.3 or pcr <= 0.7:
            confidence += 10

        # Clear OI pattern increases confidence
        if oi_pattern != OIPattern.NO_CLEAR_PATTERN:
            confidence += 15

        # Strong bias increases confidence
        if bias in [SentimentBias.STRONG_BULLISH, SentimentBias.STRONG_BEARISH]:
            confidence += 10

        # Large OI changes increase confidence
        total_oi_change = abs(ce_oi_change) + abs(pe_oi_change)
        if total_oi_change > 1000000:
            confidence += 10

        return min(95, confidence)

    def _generate_recommendation(
        self,
        bias: SentimentBias,
        oi_pattern: OIPattern,
        pcr: float,
        institutional_signal: str
    ) -> str:
        """Generate actionable recommendation"""
        base = f"Sentiment: {bias.value} | PCR: {pcr:.2f} | Pattern: {oi_pattern.value}"

        if bias == SentimentBias.STRONG_BULLISH:
            return f"{base}. FAVOR CE positions. Institutions heavily writing puts."
        elif bias == SentimentBias.BULLISH:
            return f"{base}. Lean bullish. Look for CE entries on dips."
        elif bias == SentimentBias.STRONG_BEARISH:
            return f"{base}. FAVOR PE positions. Institutions heavily writing calls."
        elif bias == SentimentBias.BEARISH:
            return f"{base}. Lean bearish. Look for PE entries on rallies."
        else:
            return f"{base}. No clear institutional bias. Wait for clarity."

    def validate(
        self,
        output: ModuleOutput,
        actual_outcome: Dict[str, Any]
    ) -> bool:
        """
        Validate if sentiment assessment was correct

        Args:
            output: Previous sentiment output
            actual_outcome: What happened (price direction)

        Returns:
            True if position bias was correct
        """
        if not isinstance(output, SentimentOutput):
            return False

        actual_change = actual_outcome.get("change_pct", 0)
        predicted_bias = output.position_bias

        # Check if predicted direction was correct
        if predicted_bias == "LONG" and actual_change > 0.3:
            return True
        elif predicted_bias == "SHORT" and actual_change < -0.3:
            return True
        elif predicted_bias == "NEUTRAL" and abs(actual_change) < 0.5:
            return True

        return False

    def get_sentiment_for_trade(self, index: str, market_data: Dict) -> Dict[str, Any]:
        """
        Quick method to get sentiment assessment for a trade

        Returns dict with bias and signals
        """
        output = self.analyze(index, market_data)
        return {
            "bias": output.bias.value,
            "position_bias": output.position_bias,
            "pcr": output.pcr,
            "oi_pattern": output.oi_pattern.value,
            "institutional_signal": output.institutional_signal,
            "confidence": output.confidence,
            "recommendation": output.recommendation,
        }

"""
LLM Veto Layer - Reviews and Filters Bot Signals

This layer acts as a "senior trader" that reviews all signals from
rule-based bots before they reach execution. It can:
1. APPROVE - Signal is good, proceed
2. REJECT - Signal is bad, don't trade
3. MODIFY - Adjust confidence or parameters

This minimizes losses by catching bad signals that pass rule-based filters.
"""

import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

from .base import BotSignal, SignalType, OptionType


@dataclass
class VetoDecision:
    """Decision from LLM Veto Layer"""
    signal: BotSignal
    approved: bool
    modified_confidence: float
    reasoning: str
    risk_factors: List[str]
    recommendation: str  # EXECUTE, SKIP, REDUCE_SIZE


class LLMVetoLayer:
    """
    LLM-Powered Veto Layer

    Reviews all bot signals before execution to:
    1. Catch false signals that rule-based bots miss
    2. Identify hidden risks in market conditions
    3. Provide reasoning for each decision
    4. Reduce losses by rejecting bad trades

    Philosophy: "It's better to miss a good trade than take a bad one"
    """

    SYSTEM_PROMPT = """You are a senior risk manager at a hedge fund reviewing trading signals.

Your job is to PROTECT CAPITAL by reviewing signals from junior traders (bots).

For each signal, you must decide:
1. APPROVE - The signal logic is sound, execute the trade
2. REJECT - The signal has flaws, DO NOT trade
3. REDUCE - The signal is okay but risky, reduce position size

IMPORTANT RULES:
- Default to REJECT if uncertain. Missing a good trade is better than taking a bad one.
- Look for contradictions (e.g., buying calls in downtrend)
- Check if multiple signals conflict with each other
- Consider time of day, day of week risks
- Watch for overconfidence from bots
- Be extra cautious near market open (9:15-9:45) and close (3:00-3:30)

You will receive:
1. Market data (price, change%, momentum, volatility)
2. Multiple bot signals with their reasoning
3. Recent trade history

Respond in JSON format:
{
    "decisions": [
        {
            "bot_name": "BotName",
            "action": "APPROVE" | "REJECT" | "REDUCE",
            "modified_confidence": 0-100,
            "reasoning": "Why you made this decision",
            "risk_factors": ["risk1", "risk2"],
            "recommendation": "EXECUTE" | "SKIP" | "REDUCE_SIZE"
        }
    ],
    "overall_market_assessment": "Your view of current market conditions",
    "max_trades_recommended": 1-3,
    "caution_level": "LOW" | "MEDIUM" | "HIGH" | "EXTREME"
}
"""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model

        if HAS_OPENAI and self.api_key:
            self.client = OpenAI(api_key=self.api_key)
            self.enabled = True
        else:
            self.client = None
            self.enabled = False
            print("[LLMVeto] Warning: OpenAI not available, veto layer disabled")

        # Track veto statistics
        self.stats = {
            "total_reviewed": 0,
            "approved": 0,
            "rejected": 0,
            "reduced": 0,
            "saved_from_loss": 0,  # Track trades that would have been losses
        }

        # Recent decisions for context
        self.recent_decisions: List[VetoDecision] = []

    def review_signals(
        self,
        signals: List[BotSignal],
        market_data: Dict[str, Any],
        recent_trades: List[Dict] = None
    ) -> Tuple[List[BotSignal], List[VetoDecision]]:
        """
        Review all signals and return only approved ones

        Args:
            signals: List of signals from rule-based bots
            market_data: Current market conditions
            recent_trades: Recent trade history for context

        Returns:
            Tuple of (approved_signals, all_decisions)
        """
        if not self.enabled or not signals:
            # If veto layer disabled, pass all signals through
            return signals, []

        try:
            # Build review prompt
            prompt = self._build_review_prompt(signals, market_data, recent_trades)

            # Call LLM for review
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,  # Low temperature for consistent decisions
                response_format={"type": "json_object"}
            )

            # Parse response
            review = json.loads(response.choices[0].message.content)

            # Process decisions
            approved_signals = []
            all_decisions = []

            for signal in signals:
                decision = self._find_decision(signal, review.get("decisions", []))

                if decision:
                    veto_decision = VetoDecision(
                        signal=signal,
                        approved=decision.get("action") != "REJECT",
                        modified_confidence=decision.get("modified_confidence", signal.confidence),
                        reasoning=decision.get("reasoning", ""),
                        risk_factors=decision.get("risk_factors", []),
                        recommendation=decision.get("recommendation", "SKIP")
                    )

                    all_decisions.append(veto_decision)
                    self.recent_decisions.append(veto_decision)

                    # Update stats
                    self.stats["total_reviewed"] += 1

                    if decision.get("action") == "APPROVE":
                        self.stats["approved"] += 1
                        # Modify confidence if needed
                        signal.confidence = decision.get("modified_confidence", signal.confidence)
                        signal.factors["veto_approved"] = True
                        signal.factors["veto_reasoning"] = decision.get("reasoning", "")
                        approved_signals.append(signal)

                    elif decision.get("action") == "REDUCE":
                        self.stats["reduced"] += 1
                        # Reduce confidence significantly
                        signal.confidence = min(signal.confidence * 0.7, decision.get("modified_confidence", 50))
                        signal.factors["veto_reduced"] = True
                        signal.factors["veto_reasoning"] = decision.get("reasoning", "")
                        signal.factors["reduce_position"] = True
                        approved_signals.append(signal)

                    else:  # REJECT
                        self.stats["rejected"] += 1
                        signal.factors["veto_rejected"] = True
                        signal.factors["veto_reasoning"] = decision.get("reasoning", "")
                        print(f"[LLMVeto] REJECTED {signal.bot_name} signal: {decision.get('reasoning', 'No reason')}")

                else:
                    # No decision found, default to reject for safety
                    self.stats["rejected"] += 1
                    print(f"[LLMVeto] REJECTED {signal.bot_name} signal: No LLM decision received")

            # Log overall assessment
            caution = review.get("caution_level", "MEDIUM")
            max_trades = review.get("max_trades_recommended", 1)
            print(f"[LLMVeto] Caution: {caution}, Max trades: {max_trades}, "
                  f"Approved: {len(approved_signals)}/{len(signals)}")

            # Limit number of trades based on LLM recommendation
            if len(approved_signals) > max_trades:
                # Keep only highest confidence signals
                approved_signals.sort(key=lambda s: s.confidence, reverse=True)
                approved_signals = approved_signals[:max_trades]
                print(f"[LLMVeto] Limited to {max_trades} trades due to caution level")

            # Keep recent decisions bounded
            if len(self.recent_decisions) > 100:
                self.recent_decisions = self.recent_decisions[-100:]

            return approved_signals, all_decisions

        except Exception as e:
            print(f"[LLMVeto] Error in review: {e}")
            # On error, be conservative - reject all signals
            return [], []

    def _build_review_prompt(
        self,
        signals: List[BotSignal],
        market_data: Dict[str, Any],
        recent_trades: List[Dict] = None
    ) -> str:
        """Build prompt for LLM review"""

        # Get market info for the index
        index = signals[0].index if signals else "UNKNOWN"
        data = market_data.get(index, {})

        prompt = f"""
## SIGNAL REVIEW REQUEST

**Time**: {datetime.now().strftime("%H:%M:%S")}
**Day**: {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][datetime.now().weekday()]}

## Market Conditions for {index}
- Price: {data.get('ltp', 'N/A')}
- Change: {data.get('change_pct', 0):.2f}%
- Momentum: {data.get('momentum', 0):.3f}
- PCR: {data.get('pcr', 1.0):.2f}
- IV Percentile: {data.get('iv_percentile', 50):.0f}
- VIX: {data.get('vix', 15):.1f}
- Market Bias: {data.get('market_bias', 'NEUTRAL')}

## Signals to Review ({len(signals)} signals)
"""

        for i, signal in enumerate(signals, 1):
            prompt += f"""
### Signal {i}: {signal.bot_name}
- Action: {signal.signal_type.value} {signal.option_type.value}
- Strike: {signal.strike}
- Confidence: {signal.confidence:.0f}%
- Entry: {signal.entry}, Target: {signal.target}, SL: {signal.stop_loss}
- Reasoning: {signal.reasoning}
- Factors: {json.dumps(signal.factors, default=str)}
"""

        # Add recent trade context
        if recent_trades:
            prompt += f"""
## Recent Trade History (last 5)
{json.dumps(recent_trades[-5:], indent=2, default=str)}
"""
        else:
            prompt += "\n## Recent Trade History\nNo recent trades.\n"

        prompt += """
## Your Task
Review each signal and decide: APPROVE, REJECT, or REDUCE.
Consider:
1. Is the signal logic sound given market conditions?
2. Are there conflicting signals?
3. What risks might the bot have missed?
4. Is this a good time to trade?

Remember: Protecting capital is priority #1. When in doubt, REJECT.
"""

        return prompt

    def _find_decision(self, signal: BotSignal, decisions: List[Dict]) -> Optional[Dict]:
        """Find decision for a specific signal"""
        for decision in decisions:
            if decision.get("bot_name") == signal.bot_name:
                return decision
        return None

    def record_outcome(self, signal: BotSignal, outcome: str, pnl_pct: float):
        """
        Record trade outcome to track veto effectiveness

        This helps us know if rejected signals would have been losses
        """
        # Find the decision for this signal
        for decision in self.recent_decisions:
            if (decision.signal.bot_name == signal.bot_name and
                decision.signal.index == signal.index and
                decision.signal.strike == signal.strike):

                if not decision.approved and outcome == "LOSS":
                    self.stats["saved_from_loss"] += 1
                    print(f"[LLMVeto] Saved from loss! Rejected signal would have lost {pnl_pct:.1f}%")

                break

    def get_stats(self) -> Dict[str, Any]:
        """Get veto layer statistics"""
        total = self.stats["total_reviewed"]
        if total > 0:
            approval_rate = self.stats["approved"] / total * 100
            rejection_rate = self.stats["rejected"] / total * 100
        else:
            approval_rate = 0
            rejection_rate = 0

        return {
            **self.stats,
            "approval_rate": f"{approval_rate:.1f}%",
            "rejection_rate": f"{rejection_rate:.1f}%",
            "enabled": self.enabled,
        }

    def quick_review(self, signal: BotSignal, market_data: Dict) -> bool:
        """
        Quick single-signal review for urgent decisions

        Returns True if approved, False if rejected
        """
        approved, _ = self.review_signals([signal], market_data)
        return len(approved) > 0

"""
LLM-Powered Trading Bot - Works like ClawWorker

This bot uses an LLM (like GPT-4 or Claude) to make trading decisions,
similar to how LiveAgent works for task execution.

Key differences from rule-based bots:
1. REASONS about market conditions
2. UNDERSTANDS context and nuance
3. LEARNS from experience meaningfully
4. Can explain WHY it made a decision
"""

import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict

# Try to import LLM client
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

from .base import (
    TradingBot, BotSignal, TradeRecord, SharedMemory,
    SignalType, OptionType, BotPerformance, get_strike_gap
)


@dataclass
class TradingContext:
    """Full context for LLM decision making"""
    index: str
    ltp: float
    change_pct: float
    momentum: float
    pcr: float
    vix: float
    iv_percentile: float
    market_bias: str
    recent_trades: List[Dict]
    learnings: List[Dict]
    current_time: str
    day_of_week: int


class LLMTradingBot(TradingBot):
    """
    LLM-Powered Trading Bot

    Uses an LLM to:
    1. Analyze market conditions with REASONING
    2. Consider historical learnings
    3. Make contextual decisions
    4. Generate explainable signals

    This is how ClawWorker/LiveAgent works - TRUE AI decision making.
    """

    SYSTEM_PROMPT = """You are an expert options trader with 30+ years of experience.
You analyze market data and make trading decisions based on:

1. **Current Market Conditions**: Price, momentum, volatility, PCR
2. **Historical Learnings**: What worked/failed in similar conditions
3. **Risk Management**: Position sizing, stop losses, targets
4. **Market Context**: Time of day, day of week, overall bias

IMPORTANT RULES:
- Only trade when you have HIGH CONFIDENCE (70%+)
- Prefer NO_TRADE over uncertain trades
- Always explain your reasoning
- Learn from past mistakes

Respond in JSON format:
{
    "action": "BUY_CE" | "BUY_PE" | "NO_TRADE",
    "confidence": 0-100,
    "reasoning": "Detailed explanation of why",
    "strike": ATM strike price,
    "entry": estimated entry price,
    "target": target price,
    "stop_loss": stop loss price,
    "key_factors": ["factor1", "factor2"],
    "risks": ["risk1", "risk2"]
}
"""

    def __init__(
        self,
        shared_memory: Optional[SharedMemory] = None,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None
    ):
        super().__init__(
            name="LLMTrader",
            description="LLM-powered bot that REASONS about trades like a human expert",
            shared_memory=shared_memory
        )

        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        if HAS_OPENAI and self.api_key:
            self.client = OpenAI(api_key=self.api_key)
            self.enabled = True
        else:
            self.client = None
            self.enabled = False
            print("[LLMTrader] Warning: OpenAI not available, bot disabled")

        # Store conversation history for context
        self.conversation_history: List[Dict] = []

        # Learning memory - stores insights from trades
        self.insights: List[Dict] = []

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        option_chain: Optional[List[Dict]] = None
    ) -> Optional[BotSignal]:
        """
        Analyze market using LLM reasoning

        Unlike rule-based bots, this:
        1. Sends full context to LLM
        2. LLM REASONS about the situation
        3. Returns a thoughtful decision
        """
        if not self.enabled:
            return None

        # Build full context
        context = self._build_context(index, market_data)

        # Get learnings relevant to this situation
        learnings = self._get_relevant_learnings(context)

        # Build prompt
        prompt = self._build_prompt(context, learnings)

        try:
            # Call LLM for decision
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,  # Lower temperature for more consistent decisions
                response_format={"type": "json_object"}
            )

            # Parse response
            decision = json.loads(response.choices[0].message.content)

            # Convert to BotSignal
            return self._decision_to_signal(index, market_data, decision)

        except Exception as e:
            print(f"[LLMTrader] Error in analysis: {e}")
            return None

    def _build_context(self, index: str, market_data: Dict) -> TradingContext:
        """Build full context for LLM"""
        return TradingContext(
            index=index,
            ltp=market_data.get("ltp", 0),
            change_pct=market_data.get("change_pct", 0),
            momentum=market_data.get("momentum", 0),
            pcr=market_data.get("pcr", 1.0),
            vix=market_data.get("vix", 15),
            iv_percentile=market_data.get("iv_percentile", 50),
            market_bias=market_data.get("market_bias", "NEUTRAL"),
            recent_trades=self._get_recent_trades(index),
            learnings=self.insights[-10:],
            current_time=datetime.now().strftime("%H:%M"),
            day_of_week=datetime.now().weekday()
        )

    def _get_relevant_learnings(self, context: TradingContext) -> List[Dict]:
        """Get learnings relevant to current context"""
        relevant = []

        for insight in self.insights:
            # Check if conditions are similar
            if insight.get("index") == context.index:
                relevant.append(insight)
            elif insight.get("universal", False):
                relevant.append(insight)

        return relevant[-5:]

    def _get_recent_trades(self, index: str) -> List[Dict]:
        """Get recent trades for this index"""
        trades = self.memory.get_trades(self.name, limit=20)
        return [
            {
                "index": t.index,
                "option_type": t.option_type,
                "outcome": t.outcome,
                "pnl_pct": t.pnl_pct,
                "conditions": t.market_conditions
            }
            for t in trades if t.index == index
        ][-5:]

    def _build_prompt(self, context: TradingContext, learnings: List[Dict]) -> str:
        """Build prompt for LLM"""
        prompt = f"""
## Current Market Analysis Request

**Index**: {context.index}
**Current Price**: {context.ltp}
**Change Today**: {context.change_pct:.2f}%
**Momentum**: {context.momentum:.3f}
**Put-Call Ratio**: {context.pcr:.2f}
**VIX**: {context.vix:.1f}
**IV Percentile**: {context.iv_percentile:.0f}
**Market Bias**: {context.market_bias}
**Time**: {context.current_time}
**Day**: {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][context.day_of_week]}

## Recent Trades in {context.index}
{json.dumps(context.recent_trades, indent=2) if context.recent_trades else "No recent trades"}

## Learnings from Past Experience
{json.dumps(learnings, indent=2) if learnings else "No relevant learnings yet"}

## Your Task
Analyze the market conditions and decide:
1. Should I trade? (BUY_CE, BUY_PE, or NO_TRADE)
2. If trading, what's the confidence level?
3. What are the key factors supporting this decision?
4. What are the risks?

Remember: It's better to NOT trade than to make a low-confidence trade.
"""
        return prompt

    def _decision_to_signal(
        self,
        index: str,
        market_data: Dict,
        decision: Dict
    ) -> Optional[BotSignal]:
        """Convert LLM decision to BotSignal"""
        action = decision.get("action", "NO_TRADE")

        if action == "NO_TRADE":
            return None

        confidence = decision.get("confidence", 50)

        # Map action to signal/option type
        if action == "BUY_CE":
            signal_type = SignalType.STRONG_BUY if confidence >= 80 else SignalType.BUY
            option_type = OptionType.CE
        elif action == "BUY_PE":
            signal_type = SignalType.STRONG_SELL if confidence >= 80 else SignalType.SELL
            option_type = OptionType.PE
        else:
            return None

        signal = BotSignal(
            bot_name=self.name,
            index=index,
            signal_type=signal_type,
            option_type=option_type,
            confidence=confidence,
            strike=decision.get("strike", self._get_atm_strike(market_data.get("ltp", 0), index)),
            entry=decision.get("entry"),
            target=decision.get("target"),
            stop_loss=decision.get("stop_loss"),
            reasoning=decision.get("reasoning", "LLM decision"),
            factors={
                "key_factors": decision.get("key_factors", []),
                "risks": decision.get("risks", []),
                "llm_decision": True
            }
        )

        self.recent_signals.append(signal)
        self.performance.total_signals += 1

        return signal

    def _get_atm_strike(self, ltp: float, index: str) -> int:
        """Get ATM strike"""
        step = get_strike_gap(index)
        return round(ltp / step) * step

    def learn(self, trade: TradeRecord):
        """
        Learn from trade outcome - REAL learning with reasoning

        Unlike rule-based bots that just adjust thresholds,
        this actually REASONS about what happened and stores
        meaningful insights.
        """
        self.update_performance(trade)
        self.memory.record_trade(trade)

        if not self.enabled:
            return

        # Generate learning insight using LLM
        try:
            prompt = f"""
A trade was completed. Learn from it.

## Trade Details
- Index: {trade.index}
- Type: {trade.option_type}
- Outcome: {trade.outcome}
- P&L: {trade.pnl_pct:.1f}%
- Entry conditions: {json.dumps(trade.market_conditions)}

## Your Task
Analyze this trade and extract a learning insight:
1. WHY did this trade {trade.outcome}?
2. What should I do differently next time?
3. What pattern should I remember?

Respond in JSON:
{{
    "insight": "One sentence insight",
    "pattern": "Pattern to remember",
    "adjustment": "What to do differently",
    "confidence": 0-100 (how confident are you in this learning),
    "universal": true/false (applies to all indices?)
}}
"""
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are analyzing trading outcomes to extract learnings."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                response_format={"type": "json_object"}
            )

            learning = json.loads(response.choices[0].message.content)
            learning["index"] = trade.index
            learning["outcome"] = trade.outcome
            learning["timestamp"] = datetime.now().isoformat()

            self.insights.append(learning)

            # Save to shared memory
            self.save_learning(
                topic=f"Trade insight: {trade.outcome}",
                insight=learning.get("insight", ""),
                conditions={
                    "index": trade.index,
                    "pattern": learning.get("pattern", ""),
                    "universal": learning.get("universal", False)
                }
            )

            print(f"[LLMTrader] Learned: {learning.get('insight', '')}")

        except Exception as e:
            print(f"[LLMTrader] Error in learning: {e}")

            # Fallback to simple learning
            self.save_learning(
                topic=f"Trade {trade.outcome} in {trade.index}",
                insight=f"Trade resulted in {trade.pnl_pct:.1f}% P&L",
                conditions=trade.market_conditions
            )

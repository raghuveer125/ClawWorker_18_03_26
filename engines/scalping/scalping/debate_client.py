"""
LLM Debate Client for Scalping System.

Provides async interface to the LLM Debate API for trade decision validation.
Uses Claude vs GPT-4 debates to validate high-stakes trading decisions.
"""

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp


@dataclass
class DebateResult:
    """Result of an LLM debate session."""
    session_id: str
    consensus: bool
    confidence: float  # 0-100
    decision: str  # "APPROVE", "REJECT", "UNCERTAIN"
    reasoning: str
    rounds: int
    proposer_model: str
    critic_model: str
    concerns: List[str]
    duration_ms: int


class DebateClient:
    """
    Async client for LLM Debate API.

    Usage:
        client = DebateClient()
        result = await client.validate_trade_decision(
            decision_type="entry",
            context={...}
        )
        if result.consensus and result.confidence > 70:
            execute_trade()
    """

    def __init__(
        self,
        api_url: str = None,
        timeout: float = 120.0,
        max_rounds: int = 5,
    ):
        self.api_url = api_url or os.getenv("LLM_DEBATE_URL", "http://localhost:8080")
        self.timeout = timeout
        self.max_rounds = max_rounds
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session

    async def close(self):
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def check_status(self) -> Dict[str, Any]:
        """Check if debate API is running and configured."""
        try:
            session = await self._get_session()
            async with session.get(f"{self.api_url}/api/status") as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"configured": False, "error": f"HTTP {resp.status}"}
        except Exception as e:
            return {"configured": False, "error": str(e)}

    async def validate_trade_decision(
        self,
        decision_type: str,
        context: Dict[str, Any],
        proposer: str = "anthropic",
        critic: str = "openai",
    ) -> DebateResult:
        """
        Validate a trading decision through LLM debate.

        Args:
            decision_type: "entry", "exit", "strike_selection", "risk_check"
            context: Trading context with market data, signals, etc.
            proposer: LLM provider for proposer (anthropic/openai)
            critic: LLM provider for critic (anthropic/openai)

        Returns:
            DebateResult with consensus status and decision
        """
        start_time = datetime.now()

        # Format task for debate
        task = self._format_trade_task(decision_type, context)

        # Create session and run debate
        session_id = str(uuid.uuid4())[:8]

        try:
            result = await self._run_debate(
                session_id=session_id,
                task=task,
                proposer=proposer,
                critic=critic,
            )

            duration = int((datetime.now() - start_time).total_seconds() * 1000)
            return DebateResult(
                session_id=session_id,
                consensus=result.get("has_consensus", False),
                confidence=result.get("confidence", 50.0),
                decision=result.get("decision", "UNCERTAIN"),
                reasoning=result.get("reasoning", ""),
                rounds=result.get("rounds", 0),
                proposer_model=result.get("proposer_model", proposer),
                critic_model=result.get("critic_model", critic),
                concerns=result.get("concerns", []),
                duration_ms=duration,
            )
        except Exception as e:
            duration = int((datetime.now() - start_time).total_seconds() * 1000)
            return DebateResult(
                session_id=session_id,
                consensus=False,
                confidence=0.0,
                decision="ERROR",
                reasoning=f"Debate failed: {str(e)}",
                rounds=0,
                proposer_model=proposer,
                critic_model=critic,
                concerns=[str(e)],
                duration_ms=duration,
            )

    async def validate_single_decision(
        self,
        decision_type: str,
        context: Dict[str, Any],
        provider: str = "openai",
    ) -> DebateResult:
        """Get a single-model suggestion (no debate)."""
        single_client = DebateClient(
            api_url=self.api_url,
            timeout=self.timeout,
            max_rounds=1,
        )
        try:
            return await single_client.validate_trade_decision(
                decision_type=decision_type,
                context=context,
                proposer=provider,
                critic=provider,
            )
        finally:
            await single_client.close()

    def _format_trade_task(self, decision_type: str, context: Dict[str, Any]) -> str:
        """Format trading context into a debate task."""

        if decision_type == "entry":
            return self._format_entry_task(context)
        elif decision_type == "exit":
            return self._format_exit_task(context)
        elif decision_type == "strike_selection":
            return self._format_strike_task(context)
        elif decision_type == "risk_check":
            return self._format_risk_task(context)
        else:
            return self._format_generic_task(decision_type, context)

    def _format_entry_task(self, ctx: Dict[str, Any]) -> str:
        """Format entry decision task."""
        return f"""
ROLE: 65+ year institutional scalper. Prioritize capital preservation, liquidity, and clean structure. Avoid low-quality, rushed entries.

## Trading Decision Validation: ENTRY

### Market Context
- **Index**: {ctx.get('index', 'UNKNOWN')}
- **Spot Price**: {ctx.get('spot_price', 0):.2f}
- **Direction**: {ctx.get('direction', 'UNKNOWN')}
- **Timestamp**: {ctx.get('timestamp', datetime.now().isoformat())}

### Signal Inputs
- **Signal Strength**: {ctx.get('signal_strength', 0)}/100
- **Momentum**: {ctx.get('momentum', 'neutral')}
- **Structure Break**: {ctx.get('structure_break', False)}
- **PCR**: {ctx.get('pcr', 1.0):.3f}
- **VIX**: {ctx.get('vix', 15):.2f}

### Proposed Trade
- **Strike**: {ctx.get('strike', 0)}
- **Option Type**: {ctx.get('option_type', 'CE/PE')}
- **Premium**: Rs.{ctx.get('premium', 0):.2f}
- **Stop Loss**: Rs.{ctx.get('stop_loss', 0):.2f} ({ctx.get('sl_pct', 30):.1f}%)
- **Target**: Rs.{ctx.get('target', 0):.2f} ({ctx.get('target_pct', 50):.1f}%)

### Risk Context
- **Risk Amount**: Rs.{ctx.get('risk_amount', 0):.2f}
- **Capital Used**: {ctx.get('capital_used_pct', 0):.1f}%
- **Open Positions**: {ctx.get('open_positions', 0)}

### Decision Rules
- REJECT if: premium <= 0, stop_loss >= premium, or target <= premium.
- REJECT if signal_strength is very weak (<40) or direction is UNKNOWN.
- WAIT if data looks inconsistent or missing; otherwise decide.
- Assume liquidity & risk guards run downstream unless explicitly flagged.

### Output (single line)
DECISION: APPROVE|REJECT|WAIT | CONFIDENCE: 0-100 | REASONS: 1-3 short bullets
"""

    def _format_exit_task(self, ctx: Dict[str, Any]) -> str:
        """Format exit decision task."""
        return f"""
ROLE: 65+ year institutional scalper. Prioritize capital preservation, liquidity, and clean structure. Avoid low-quality, rushed decisions.

## Trading Decision Validation: EXIT

### Position Details
- **Index**: {ctx.get('index', 'UNKNOWN')}
- **Entry Price**: Rs.{ctx.get('entry_price', 0):.2f}
- **Current Price**: Rs.{ctx.get('current_price', 0):.2f}
- **Unrealized P&L**: Rs.{ctx.get('unrealized_pnl', 0):.2f} ({ctx.get('pnl_pct', 0):.1f}%)
- **Time in Trade**: {ctx.get('time_in_trade', '0m')}

### Market Context
- **Spot Price**: {ctx.get('spot_price', 0):.2f}
- **Momentum**: {ctx.get('momentum', 'neutral')}
- **Volume Spike**: {ctx.get('volume_spike', False)}

### Exit Reason
{ctx.get('exit_reason', 'Manual review')}

### Decision Rules
- EXIT_NOW if momentum flips sharply against trade or time in trade is high with no progress.
- PARTIAL_EXIT if in profit but momentum weakens.
- TRAIL_SL if trend remains favorable and volatility supports extension.
- HOLD if momentum supports and no risk trigger.

### Output (single line)
DECISION: EXIT_NOW|PARTIAL_EXIT|TRAIL_SL|HOLD | CONFIDENCE: 0-100 | REASONS: 1-3 short bullets
"""

    def _format_strike_task(self, ctx: Dict[str, Any]) -> str:
        """Format strike selection task."""
        strikes_info = ctx.get('strikes', [])
        strikes_text = "\n".join([
            f"  - {s.get('strike')} {s.get('type')}: Premium Rs.{s.get('premium', 0):.2f}, "
            f"OI={s.get('oi', 0):,}, IV={s.get('iv', 0):.1f}%"
            for s in strikes_info[:5]
        ])

        return f"""
ROLE: 65+ year institutional scalper. Prioritize capital preservation, liquidity, and clean structure. Avoid low-quality, rushed decisions.

## Trading Decision Validation: STRIKE SELECTION

### Market Context
- **Index**: {ctx.get('index', 'UNKNOWN')}
- **Spot Price**: {ctx.get('spot_price', 0):.2f}
- **ATM Strike**: {ctx.get('atm_strike', 0)}
- **Direction**: {ctx.get('direction', 'UNKNOWN')}

### Available Strikes (top candidates)
{strikes_text}

### Recommended Strike
- **Strike**: {ctx.get('recommended_strike', 0)}
- **Type**: {ctx.get('recommended_type', 'CE/PE')}
- **Premium**: Rs.{ctx.get('recommended_premium', 0):.2f}
- **Delta**: {ctx.get('recommended_delta', 0):.3f}

### Decision Rules
- Prefer higher OI and tighter spreads for liquidity.
- Avoid far OTM with tiny premium unless momentum is strong.
- Reject if recommended premium is near zero or delta is unrealistic for direction.

### Output (single line)
DECISION: APPROVE|SUGGEST_ALTERNATIVE|SKIP | CONFIDENCE: 0-100 | REASONS: 1-3 short bullets
"""

    def _format_risk_task(self, ctx: Dict[str, Any]) -> str:
        """Format risk check task."""
        return f"""
ROLE: 65+ year institutional scalper. Prioritize capital preservation, liquidity, and clean structure. Avoid low-quality, rushed decisions.

## Trading Decision Validation: RISK CHECK

### Portfolio State
- **Capital**: Rs.{ctx.get('capital', 0):,.2f}
- **Used Capital**: Rs.{ctx.get('used_capital', 0):,.2f} ({ctx.get('used_pct', 0):.1f}%)
- **Daily P&L**: Rs.{ctx.get('daily_pnl', 0):,.2f}
- **Daily Loss Limit**: Rs.{ctx.get('daily_loss_limit', 0):,.2f}
- **Open Positions**: {ctx.get('open_positions', 0)}

### Proposed Action
{ctx.get('proposed_action', 'Unknown action')}

### Risk Metrics
- **Risk Amount**: Rs.{ctx.get('risk_amount', 0):.2f}
- **Correlation Risk**: {ctx.get('correlation_risk', 'low')}
- **Concentration Risk**: {ctx.get('concentration_risk', 'low')}

### Decision Rules
- REJECT if used_pct is very high or daily P&L near loss limit.
- REDUCE_SIZE if risk_amount is high but not catastrophic.
- APPROVE if within limits and correlation/concentration are low.

### Output (single line)
DECISION: APPROVE|REJECT|REDUCE_SIZE | CONFIDENCE: 0-100 | REASONS: 1-3 short bullets
"""

    def _format_generic_task(self, decision_type: str, ctx: Dict[str, Any]) -> str:
        """Format generic decision task."""
        context_text = json.dumps(ctx, indent=2, default=str)
        return f"""
ROLE: 65+ year institutional scalper. Prioritize capital preservation, liquidity, and clean structure. Avoid low-quality, rushed decisions.

## Trading Decision Validation: {decision_type.upper()}

### Context
```json
{context_text}
```

### Question
Should we proceed with this {decision_type} decision?

### Output (single line)
DECISION: APPROVE|REJECT|UNCERTAIN | CONFIDENCE: 0-100 | REASONS: 1-3 short bullets
"""

    async def _run_debate(
        self,
        session_id: str,
        task: str,
        proposer: str,
        critic: str,
    ) -> Dict[str, Any]:
        """Run debate via WebSocket connection."""
        import websockets

        ws_url = self.api_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws/debate/{session_id}"

        project_path = str(Path(__file__).parent.parent.parent)

        try:
            async with websockets.connect(ws_url) as websocket:
                # Send start message
                await websocket.send(json.dumps({
                    "action": "start",
                    "task": task,
                    "project_path": project_path,
                    "proposer_provider": proposer,
                    "critic_provider": critic,
                    "max_rounds": self.max_rounds,
                }))

                # Collect messages
                messages = []
                final_result = {}

                async for message in websocket:
                    data = json.loads(message)
                    msg_type = data.get("type", "")

                    if msg_type == "message":
                        messages.append(data)
                    elif msg_type == "debate_complete":
                        final_result = data
                        break
                    elif msg_type == "error":
                        raise Exception(data.get("message", "Unknown error"))

                # Parse final decision from last messages
                decision, confidence, reasoning, concerns = self._parse_decision(messages)

                return {
                    "has_consensus": final_result.get("has_consensus", False),
                    "rounds": final_result.get("rounds", len(messages) // 2),
                    "decision": decision,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "concerns": concerns,
                    "proposer_model": proposer,
                    "critic_model": critic,
                }

        except ImportError:
            # Fall back to REST API if websockets not available
            return await self._run_debate_rest(session_id, task, proposer, critic)
        except Exception as e:
            raise Exception(f"Debate connection failed: {e}")

    async def _run_debate_rest(
        self,
        session_id: str,
        task: str,
        proposer: str,
        critic: str,
    ) -> Dict[str, Any]:
        """Fall back to REST API for debate."""
        session = await self._get_session()
        project_path = str(Path(__file__).parent.parent.parent)

        # Start debate
        async with session.post(
            f"{self.api_url}/api/debate/start",
            json={
                "task": task,
                "project_path": project_path,
                "proposer_provider": proposer,
                "critic_provider": critic,
                "max_rounds": self.max_rounds,
            }
        ) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to start debate: {resp.status}")
            start_data = await resp.json()
            session_id = start_data.get("session_id", session_id)

        # Poll for completion (simplified - real impl would use WebSocket)
        await asyncio.sleep(5)  # Give debate time to run

        # Get result
        async with session.get(f"{self.api_url}/api/debate/{session_id}") as resp:
            if resp.status == 200:
                result = await resp.json()
                return {
                    "has_consensus": result.get("status") == "consensus",
                    "rounds": result.get("current_round", 0),
                    "decision": "APPROVE" if result.get("status") == "consensus" else "UNCERTAIN",
                    "confidence": 70 if result.get("status") == "consensus" else 30,
                    "reasoning": result.get("summary", ""),
                    "concerns": [],
                    "proposer_model": proposer,
                    "critic_model": critic,
                }
            raise Exception(f"Failed to get debate result: {resp.status}")

    def _parse_decision(
        self,
        messages: List[Dict[str, Any]]
    ) -> tuple[str, float, str, List[str]]:
        """Parse decision from debate messages."""
        if not messages:
            return "UNCERTAIN", 0.0, "", []

        # Get last critic message (contains final verdict)
        last_message = messages[-1] if messages else {}
        content = last_message.get("content", "")

        # Parse decision keywords
        decision = "UNCERTAIN"
        confidence = 50.0

        content_upper = content.upper()
        if "APPROVE" in content_upper or "CONSENSUS" in content_upper:
            decision = "APPROVE"
            confidence = 80.0
        elif "REJECT" in content_upper:
            decision = "REJECT"
            confidence = 75.0
        elif "WAIT" in content_upper or "HOLD" in content_upper:
            decision = "WAIT"
            confidence = 60.0

        # Extract confidence if mentioned
        import re
        confidence_match = re.search(r"confidence[:\s]*(\d+)", content, re.IGNORECASE)
        if confidence_match:
            confidence = float(confidence_match.group(1))

        # Get concerns from messages
        concerns = []
        for msg in messages:
            msg_concerns = msg.get("concerns", [])
            concerns.extend(msg_concerns)

        return decision, confidence, content[:500], list(set(concerns))


# Singleton instance
_debate_client: Optional[DebateClient] = None


def get_debate_client() -> DebateClient:
    """Get or create singleton debate client."""
    global _debate_client
    if _debate_client is None:
        _debate_client = DebateClient()
    return _debate_client


async def validate_entry(context: Dict[str, Any]) -> DebateResult:
    """Convenience function to validate entry decision."""
    client = get_debate_client()
    return await client.validate_trade_decision("entry", context)


async def validate_exit(context: Dict[str, Any]) -> DebateResult:
    """Convenience function to validate exit decision."""
    client = get_debate_client()
    return await client.validate_trade_decision("exit", context)


async def validate_strike(context: Dict[str, Any]) -> DebateResult:
    """Convenience function to validate strike selection."""
    client = get_debate_client()
    return await client.validate_trade_decision("strike_selection", context)


async def validate_risk(context: Dict[str, Any]) -> DebateResult:
    """Convenience function to validate risk check."""
    client = get_debate_client()
    return await client.validate_trade_decision("risk_check", context)

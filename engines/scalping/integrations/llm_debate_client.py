"""
LLM Debate Client - Interface for bot army to use multi-LLM consensus.

Provides:
- Async debate requests
- Streaming message capture
- Result parsing for bot decisions
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import aiohttp
    import websockets
    HAS_ASYNC = True
except ImportError:
    HAS_ASYNC = False


@dataclass
class DebateResult:
    """Result from an LLM debate session."""
    session_id: str
    status: str  # consensus, deadlock, error
    consensus_reached: bool
    final_proposal: Optional[str] = None
    rounds: int = 0
    messages: List[Dict] = field(default_factory=list)
    proposer: str = ""
    critic: str = ""
    duration_ms: float = 0
    error: Optional[str] = None


class LLMDebateClient:
    """
    Client for the LLM Debate backend service.

    Used by bots like ExperimentBot and SelfHealingBot
    to request multi-LLM consensus on complex decisions.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        timeout_seconds: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    async def check_status(self) -> Dict[str, Any]:
        """Check if the debate service is available."""
        if not HAS_ASYNC:
            return {"error": "aiohttp not installed", "available": False}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/status",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"available": True, **data}
                    return {"available": False, "status_code": resp.status}
        except Exception as e:
            return {"available": False, "error": str(e)}

    async def start_debate(
        self,
        task: str,
        project_path: str,
        proposer: str = "anthropic",
        critic: str = "openai",
        max_rounds: int = 5,
        context: Optional[Dict] = None,
    ) -> DebateResult:
        """
        Start and run a complete debate session.

        Args:
            task: The task/question for the LLMs to debate
            project_path: Path to the project context
            proposer: Provider for proposer role (anthropic, openai)
            critic: Provider for critic role
            max_rounds: Maximum debate rounds
            context: Additional context data

        Returns:
            DebateResult with consensus or deadlock info
        """
        if not HAS_ASYNC:
            return DebateResult(
                session_id="",
                status="error",
                consensus_reached=False,
                error="aiohttp/websockets not installed"
            )

        start_time = datetime.now()

        try:
            async with aiohttp.ClientSession() as session:
                # Start the debate session
                payload = {
                    "task": task,
                    "project_path": project_path,
                    "proposer_provider": proposer,
                    "critic_provider": critic,
                    "max_rounds": max_rounds,
                }
                if context:
                    payload["context"] = context

                async with session.post(
                    f"{self.base_url}/api/debate/start",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        return DebateResult(
                            session_id="",
                            status="error",
                            consensus_reached=False,
                            error=f"Failed to start debate: {text}"
                        )
                    start_data = await resp.json()
                    session_id = start_data["session_id"]

                # Connect via WebSocket to run debate
                ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
                ws_url = f"{ws_url}/ws/debate/{session_id}"

                messages = []
                final_status = "unknown"
                final_proposal = None
                rounds = 0

                async with websockets.connect(
                    ws_url,
                    close_timeout=5,
                ) as ws:
                    # Send start command
                    await ws.send(json.dumps({"action": "start"}))

                    # Collect messages until completion
                    try:
                        async for msg in ws:
                            data = json.loads(msg)
                            msg_type = data.get("type", "")

                            if msg_type == "message":
                                messages.append(data)
                                if data.get("role") == "proposer":
                                    rounds += 1
                                    final_proposal = data.get("content", "")

                            elif msg_type == "debate_complete":
                                final_status = data.get("status", "unknown")
                                break

                            elif msg_type == "error":
                                return DebateResult(
                                    session_id=session_id,
                                    status="error",
                                    consensus_reached=False,
                                    error=data.get("message", "Unknown error"),
                                    messages=messages,
                                )
                    except asyncio.TimeoutError:
                        final_status = "timeout"

                duration = (datetime.now() - start_time).total_seconds() * 1000

                return DebateResult(
                    session_id=session_id,
                    status=final_status,
                    consensus_reached=final_status == "consensus",
                    final_proposal=final_proposal,
                    rounds=rounds,
                    messages=messages,
                    proposer=proposer,
                    critic=critic,
                    duration_ms=duration,
                )

        except Exception as e:
            return DebateResult(
                session_id="",
                status="error",
                consensus_reached=False,
                error=str(e),
            )

    async def apply_patch(self, session_id: str) -> Dict[str, Any]:
        """Apply the consensus patch from a debate session."""
        if not HAS_ASYNC:
            return {"success": False, "error": "aiohttp not installed"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/debate/{session_id}/apply",
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    data = await resp.json()
                    return {"success": resp.status == 200, **data}
        except Exception as e:
            return {"success": False, "error": str(e)}


# Singleton instance
_client: Optional[LLMDebateClient] = None


def get_debate_client(base_url: str = "http://localhost:8080") -> LLMDebateClient:
    """Get or create the LLM debate client singleton."""
    global _client
    if _client is None or _client.base_url != base_url:
        _client = LLMDebateClient(base_url=base_url)
    return _client


async def debate_request(
    task: str,
    project_path: str,
    proposer: str = "anthropic",
    critic: str = "openai",
    max_rounds: int = 5,
    base_url: str = "http://localhost:8080",
) -> DebateResult:
    """
    Convenience function for one-off debate requests.

    Example:
        result = await debate_request(
            task="Generate a new mean reversion strategy variation",
            project_path="/path/to/project",
        )
        if result.consensus_reached:
            print(result.final_proposal)
    """
    client = get_debate_client(base_url)
    return await client.start_debate(
        task=task,
        project_path=project_path,
        proposer=proposer,
        critic=critic,
        max_rounds=max_rounds,
    )

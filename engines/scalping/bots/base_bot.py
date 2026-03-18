"""
Base Bot - Abstract interface for all bots in the army.
Each bot has one clear job and communicates via events.
"""

import asyncio
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import sys

# Add paths for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "llm_debate" / "backend"))


class BotStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"  # Waiting for LLM debate consensus
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"  # Blocked by risk/validation


@dataclass
class BotResult:
    """Result from a bot execution."""
    bot_id: str
    bot_type: str
    status: BotStatus
    output: Any = None
    artifacts: Dict[str, Any] = field(default_factory=dict)  # Files, data, etc.
    metrics: Dict[str, float] = field(default_factory=dict)  # Performance metrics
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    duration_ms: float = 0
    next_bot: Optional[str] = None  # Suggested next bot in pipeline


@dataclass
class BotContext:
    """Context passed between bots in a pipeline."""
    pipeline_id: str
    trigger: str  # What triggered this pipeline (commit, schedule, manual, event)
    data: Dict[str, Any] = field(default_factory=dict)  # Shared data
    history: List[BotResult] = field(default_factory=list)  # Previous bot results
    config: Dict[str, Any] = field(default_factory=dict)  # Pipeline config

    def add_result(self, result: BotResult):
        self.history.append(result)

    def get_last_result(self) -> Optional[BotResult]:
        return self.history[-1] if self.history else None

    def has_failures(self) -> bool:
        return any(r.status == BotStatus.FAILED for r in self.history)


class BaseBot(ABC):
    """
    Abstract base class for all bots.

    Each bot:
    - Has one clear responsibility
    - Can use LLM debate for complex decisions
    - Emits events for other bots
    - Can be chained in pipelines
    """

    BOT_TYPE: str = "base"
    REQUIRES_LLM: bool = False  # Whether this bot needs LLM for decisions

    def __init__(
        self,
        bot_id: Optional[str] = None,
        event_bus: Optional[Any] = None,
        llm_debate_url: str = "http://localhost:8080",
    ):
        self.bot_id = bot_id or f"{self.BOT_TYPE}_{uuid.uuid4().hex[:8]}"
        self.event_bus = event_bus
        self.llm_debate_url = llm_debate_url
        self.status = BotStatus.IDLE
        self._callbacks: Dict[str, List[Callable]] = {}

    @abstractmethod
    async def execute(self, context: BotContext) -> BotResult:
        """
        Execute the bot's main task.
        Must be implemented by each bot.
        """
        pass

    @abstractmethod
    def get_description(self) -> str:
        """Return a description of what this bot does."""
        pass

    async def run(self, context: BotContext) -> BotResult:
        """
        Run the bot with timing and event emission.
        Wraps execute() with common functionality.
        """
        start_time = datetime.now()
        self.status = BotStatus.RUNNING
        token = None

        try:
            try:
                from scalping.debate_integration import set_active_bot_type
                token = set_active_bot_type(self.BOT_TYPE)
            except Exception:
                token = None

            # Emit start event
            await self._emit_event("bot_started", {
                "bot_id": self.bot_id,
                "bot_type": self.BOT_TYPE,
                "context": context.pipeline_id,
            })

            # Execute the bot's task
            result = await self.execute(context)

            # Calculate duration
            result.duration_ms = (datetime.now() - start_time).total_seconds() * 1000

            # Update status
            self.status = result.status

            # Add to context history
            context.add_result(result)

            # Emit completion event
            await self._emit_event("bot_completed", {
                "bot_id": self.bot_id,
                "bot_type": self.BOT_TYPE,
                "status": result.status.value,
                "duration_ms": result.duration_ms,
            })

            return result

        except Exception as e:
            self.status = BotStatus.FAILED
            result = BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.FAILED,
                errors=[str(e)],
                duration_ms=(datetime.now() - start_time).total_seconds() * 1000,
            )
            context.add_result(result)

            await self._emit_event("bot_failed", {
                "bot_id": self.bot_id,
                "error": str(e),
            })

            return result
        finally:
            if token is not None:
                try:
                    from scalping.debate_integration import reset_active_bot_type
                    reset_active_bot_type(token)
                except Exception:
                    pass

    async def request_llm_debate(
        self,
        task: str,
        project_path: str,
        proposer: str = "anthropic",
        critic: str = "openai",
        max_rounds: int = 5,
    ) -> Dict[str, Any]:
        """
        Request LLM debate for complex decisions.
        Returns the consensus result or deadlock info.
        """
        import aiohttp

        self.status = BotStatus.WAITING

        async with aiohttp.ClientSession() as session:
            # Start debate
            async with session.post(
                f"{self.llm_debate_url}/api/debate/start",
                json={
                    "task": task,
                    "project_path": project_path,
                    "proposer_provider": proposer,
                    "critic_provider": critic,
                    "max_rounds": max_rounds,
                }
            ) as resp:
                if resp.status != 200:
                    return {"error": "Failed to start debate", "status": "error"}
                start_data = await resp.json()
                session_id = start_data["session_id"]

            # Connect to WebSocket and run debate
            import websockets
            ws_url = f"ws://localhost:8080/ws/debate/{session_id}"

            async with websockets.connect(ws_url) as ws:
                # Send start command
                await ws.send('{"action": "start"}')

                # Wait for completion
                final_status = None
                messages = []

                async for msg in ws:
                    import json
                    data = json.loads(msg)

                    if data.get("type") == "message":
                        messages.append(data)
                    elif data.get("type") == "debate_complete":
                        final_status = data.get("status")
                        break
                    elif data.get("type") == "error":
                        return {"error": data.get("message"), "status": "error"}

                return {
                    "status": final_status,
                    "session_id": session_id,
                    "messages": messages,
                    "consensus": final_status == "consensus",
                }

    async def _emit_event(self, event_type: str, data: Dict[str, Any]):
        """Emit an event to the event bus."""
        if self.event_bus:
            await self.event_bus.emit(event_type, {
                **data,
                "timestamp": datetime.now().isoformat(),
            })

    def on(self, event_type: str, callback: Callable):
        """Register a callback for an event type."""
        if event_type not in self._callbacks:
            self._callbacks[event_type] = []
        self._callbacks[event_type].append(callback)


class CompositeBot(BaseBot):
    """
    A bot that orchestrates multiple sub-bots.
    Useful for creating bot pipelines within a single logical unit.
    """

    BOT_TYPE = "composite"

    def __init__(self, bots: List[BaseBot], **kwargs):
        super().__init__(**kwargs)
        self.bots = bots

    async def execute(self, context: BotContext) -> BotResult:
        """Execute all sub-bots in sequence."""
        results = []

        for bot in self.bots:
            result = await bot.run(context)
            results.append(result)

            # Stop on failure unless configured otherwise
            if result.status == BotStatus.FAILED:
                return BotResult(
                    bot_id=self.bot_id,
                    bot_type=self.BOT_TYPE,
                    status=BotStatus.FAILED,
                    output=results,
                    errors=[f"Sub-bot {bot.bot_id} failed"],
                )

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output=results,
        )

    def get_description(self) -> str:
        bot_names = [b.BOT_TYPE for b in self.bots]
        return f"Composite bot running: {' -> '.join(bot_names)}"

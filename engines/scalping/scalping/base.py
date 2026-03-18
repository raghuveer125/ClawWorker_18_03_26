"""
Base classes for scalping agents.

Provides fallback implementations when bots.base_bot is not available.
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

# Try to import from bot_army
PROJECT_ROOT = Path(__file__).parent.parent.parent
BOT_ARMY_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BOT_ARMY_ROOT))

try:
    from bots.base_bot import BaseBot, BotContext, BotResult, BotStatus
    from bots.orchestrator.event_bus import create_event_bus, EventBus
    HAS_BASE_BOT = True
except ImportError:
    HAS_BASE_BOT = False


if not HAS_BASE_BOT:
    class BotStatus(Enum):
        SUCCESS = "success"
        FAILED = "failed"
        BLOCKED = "blocked"
        ERROR = "error"
        SKIPPED = "skipped"

    @dataclass
    class BotContext:
        pipeline_id: str = ""
        trigger: str = ""
        data: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class BotResult:
        bot_id: str = ""
        bot_type: str = ""
        status: BotStatus = BotStatus.SUCCESS
        output: Any = field(default_factory=dict)
        metrics: Dict[str, Any] = field(default_factory=dict)
        errors: list = field(default_factory=list)
        warnings: list = field(default_factory=list)
        artifacts: Dict[str, Any] = field(default_factory=dict)
        message: str = ""

    class BaseBot:
        """Base class for all scalping agents."""

        BOT_TYPE: str = "base"
        REQUIRES_LLM: bool = False

        def __init__(self, name: str = "", event_bus=None, bot_id: str = "", **kwargs):
            self.name = name or self.__class__.__name__
            self.bot_id = bot_id or f"{self.__class__.__name__}_{id(self)}"
            self.event_bus = event_bus
            self.last_run: Optional[datetime] = None
            self.run_count = 0

        def get_description(self) -> str:
            """Override to provide agent description."""
            return self.name

        async def run(self, context: BotContext) -> BotResult:
            """Run the agent."""
            self.last_run = datetime.now()
            self.run_count += 1
            token = None
            try:
                try:
                    from .debate_integration import set_active_bot_type
                    token = set_active_bot_type(getattr(self, "BOT_TYPE", None))
                except Exception:
                    token = None
                return await self.execute(context)
            finally:
                if token is not None:
                    try:
                        from .debate_integration import reset_active_bot_type
                        reset_active_bot_type(token)
                    except Exception:
                        pass

        async def execute(self, context: BotContext) -> BotResult:
            """Override in subclass to implement agent logic."""
            return BotResult(
                bot_id=self.bot_id,
                bot_type=getattr(self, "BOT_TYPE", "base"),
                status=BotStatus.SUCCESS,
                output={},
                metrics={"run_count": self.run_count},
            )

        async def publish(self, topic: str, message: Dict):
            """Publish message to event bus."""
            if self.event_bus:
                await self.event_bus.publish(topic, message)

        async def _emit_event(self, event_type: str, data: Dict):
            """Emit an event to the event bus."""
            await self.publish(event_type, {
                "bot_id": self.bot_id,
                "bot_type": getattr(self, "BOT_TYPE", "base"),
                "event_type": event_type,
                "data": data,
                "timestamp": datetime.now().isoformat(),
            })

        async def request_llm_debate(
            self,
            task: str,
            project_path: str = ".",
            max_rounds: int = 3,
            proposer: str = "anthropic",
            critic: str = "openai",
        ) -> Optional[Dict]:
            """
            Request LLM debate for complex decisions.

            Uses Claude vs GPT-4 debate to validate decisions.
            Returns debate result with consensus and reasoning.
            """
            try:
                from .debate_client import get_debate_client

                client = get_debate_client()
                result = await client.validate_trade_decision(
                    decision_type="general",
                    context={"task": task, "project_path": project_path},
                    proposer=proposer,
                    critic=critic,
                )

                return {
                    "session_id": result.session_id,
                    "consensus": result.consensus,
                    "confidence": result.confidence,
                    "decision": result.decision,
                    "reasoning": result.reasoning,
                    "concerns": result.concerns,
                    "duration_ms": result.duration_ms,
                }
            except ImportError:
                return None
            except Exception as e:
                return {"error": str(e), "consensus": False, "decision": "ERROR"}

    class EventBus:
        """Simple in-memory event bus."""

        def __init__(self):
            self.handlers: Dict[str, list] = {}

        async def start(self):
            pass

        async def stop(self):
            pass

        async def publish(self, topic: str, message: Dict):
            if topic in self.handlers:
                for handler in self.handlers[topic]:
                    try:
                        await handler(message)
                    except Exception:
                        pass

        async def subscribe(self, topic: str, handler):
            if topic not in self.handlers:
                self.handlers[topic] = []
            self.handlers[topic].append(handler)

    def create_event_bus(bus_type: str = "memory") -> EventBus:
        return EventBus()


# Export everything
__all__ = [
    "BaseBot",
    "BotContext",
    "BotResult",
    "BotStatus",
    "EventBus",
    "create_event_bus",
    "HAS_BASE_BOT",
]

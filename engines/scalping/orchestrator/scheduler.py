"""
Bot Orchestrator - Manages pipelines and coordinates bot execution.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type
import logging

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    yaml = None

from .event_bus import EventBus, EventTypes, create_event_bus
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from bots.base_bot import BaseBot, BotContext, BotResult, BotStatus

logger = logging.getLogger(__name__)


@dataclass
class PipelineStep:
    """A step in a pipeline."""
    bot_type: str
    config: Dict[str, Any] = field(default_factory=dict)
    on_failure: str = "stop"  # stop, skip, retry
    max_retries: int = 3
    timeout_seconds: int = 300
    condition: Optional[str] = None  # Python expression to evaluate


@dataclass
class Pipeline:
    """A sequence of bots to execute."""
    pipeline_id: str
    name: str
    description: str
    steps: List[PipelineStep]
    trigger: str = "manual"  # manual, schedule, event
    trigger_config: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class PipelineRun:
    """Record of a pipeline execution."""
    run_id: str
    pipeline_id: str
    status: str  # running, completed, failed
    started_at: str
    completed_at: Optional[str] = None
    context: Optional[BotContext] = None
    error: Optional[str] = None


class BotOrchestrator:
    """
    Orchestrates bot execution through pipelines.

    Features:
    - Define pipelines in YAML or programmatically
    - Event-driven triggers
    - Retry logic and failure handling
    - Parallel and sequential execution
    - LLM debate integration for complex decisions
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        pipelines_dir: Optional[Path] = None,
    ):
        self.event_bus = event_bus or create_event_bus()
        self.pipelines_dir = pipelines_dir or Path(__file__).parent.parent / "pipelines"

        self._bot_registry: Dict[str, Type[BaseBot]] = {}
        self._pipelines: Dict[str, Pipeline] = {}
        self._runs: Dict[str, PipelineRun] = {}
        self._running = False

        # Register event handlers
        self._setup_event_handlers()

    def register_bot(self, bot_class: Type[BaseBot]):
        """Register a bot type for use in pipelines."""
        self._bot_registry[bot_class.BOT_TYPE] = bot_class
        logger.info(f"Registered bot: {bot_class.BOT_TYPE}")

    def register_pipeline(self, pipeline: Pipeline):
        """Register a pipeline."""
        self._pipelines[pipeline.pipeline_id] = pipeline
        logger.info(f"Registered pipeline: {pipeline.name}")

    def load_pipelines_from_yaml(self):
        """Load all pipeline definitions from YAML files."""
        if not HAS_YAML:
            logger.warning("PyYAML not installed. Loading default pipelines.")
            self._load_default_pipelines()
            return

        if not self.pipelines_dir.exists():
            logger.warning(f"Pipelines directory not found: {self.pipelines_dir}")
            return

        for yaml_file in self.pipelines_dir.glob("*.yaml"):
            try:
                pipeline = self._parse_pipeline_yaml(yaml_file)
                self.register_pipeline(pipeline)
            except Exception as e:
                logger.error(f"Failed to load pipeline {yaml_file}: {e}")

    def _load_default_pipelines(self):
        """Load hardcoded default pipelines when YAML is not available."""
        # Learning Loop Pipeline
        self.register_pipeline(Pipeline(
            pipeline_id="learning_loop",
            name="Learning Control Loop",
            description="Complete strategy lifecycle",
            steps=[
                PipelineStep(bot_type="regime", config={"symbol": "NSE:NIFTY50-INDEX"}),
                PipelineStep(bot_type="backtest", config={"days": 30}),
                PipelineStep(bot_type="meta_strategy", config={}),
                PipelineStep(bot_type="correlation", config={}),
                PipelineStep(bot_type="alpha_decay", config={}),
                PipelineStep(bot_type="risk_sentinel", config={}, on_failure="stop"),
                PipelineStep(bot_type="execution", config={"dry_run": True}),
            ],
            trigger="manual",
        ))

        # Code Review Pipeline
        self.register_pipeline(Pipeline(
            pipeline_id="code_review",
            name="Code Review Pipeline",
            description="Validates code quality",
            steps=[
                PipelineStep(bot_type="guardian", config={}),
                PipelineStep(bot_type="self_healing", config={}, on_failure="skip"),
            ],
            trigger="manual",
        ))

    def _parse_pipeline_yaml(self, yaml_file: Path) -> Pipeline:
        """Parse a pipeline YAML file."""
        with open(yaml_file) as f:
            data = yaml.safe_load(f)

        steps = []
        for step_data in data.get("steps", []):
            steps.append(PipelineStep(
                bot_type=step_data["bot"],
                config=step_data.get("config", {}),
                on_failure=step_data.get("on_failure", "stop"),
                max_retries=step_data.get("max_retries", 3),
                timeout_seconds=step_data.get("timeout", 300),
                condition=step_data.get("condition"),
            ))

        return Pipeline(
            pipeline_id=data.get("id", yaml_file.stem),
            name=data.get("name", yaml_file.stem),
            description=data.get("description", ""),
            steps=steps,
            trigger=data.get("trigger", "manual"),
            trigger_config=data.get("trigger_config", {}),
            enabled=data.get("enabled", True),
        )

    async def start(self):
        """Start the orchestrator."""
        self._running = True
        await self.event_bus.start()
        self.load_pipelines_from_yaml()
        logger.info("Bot orchestrator started")

    async def stop(self):
        """Stop the orchestrator."""
        self._running = False
        await self.event_bus.stop()
        logger.info("Bot orchestrator stopped")

    async def run_pipeline(
        self,
        pipeline_id: str,
        trigger: str = "manual",
        initial_data: Optional[Dict[str, Any]] = None,
    ) -> PipelineRun:
        """
        Execute a pipeline.

        Args:
            pipeline_id: ID of the pipeline to run
            trigger: What triggered this run
            initial_data: Initial data for the context

        Returns:
            PipelineRun record
        """
        pipeline = self._pipelines.get(pipeline_id)
        if not pipeline:
            raise ValueError(f"Pipeline not found: {pipeline_id}")

        if not pipeline.enabled:
            raise ValueError(f"Pipeline is disabled: {pipeline_id}")

        # Create run record
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        run = PipelineRun(
            run_id=run_id,
            pipeline_id=pipeline_id,
            status="running",
            started_at=datetime.now().isoformat(),
        )
        self._runs[run_id] = run

        # Create context
        context = BotContext(
            pipeline_id=pipeline_id,
            trigger=trigger,
            data=initial_data or {},
            config=pipeline.trigger_config,
        )
        run.context = context

        # Emit pipeline started event
        await self.event_bus.emit(
            EventTypes.PIPELINE_STARTED,
            {"pipeline_id": pipeline_id, "run_id": run_id},
            source="orchestrator",
        )

        try:
            # Execute each step
            for i, step in enumerate(pipeline.steps):
                logger.info(f"Executing step {i+1}/{len(pipeline.steps)}: {step.bot_type}")

                # Check condition
                if step.condition:
                    if not self._evaluate_condition(step.condition, context):
                        logger.info(f"Skipping step {step.bot_type}: condition not met")
                        continue

                # Get bot class
                bot_class = self._bot_registry.get(step.bot_type)
                if not bot_class:
                    raise ValueError(f"Unknown bot type: {step.bot_type}")

                # Create and run bot
                bot = bot_class(event_bus=self.event_bus, **step.config)
                result = await self._run_with_retry(bot, context, step)

                # Handle failure
                if result.status == BotStatus.FAILED:
                    if step.on_failure == "stop":
                        raise RuntimeError(f"Step {step.bot_type} failed: {result.errors}")
                    elif step.on_failure == "skip":
                        logger.warning(f"Step {step.bot_type} failed, skipping")
                        continue

                # Handle blocked (risk/validation)
                if result.status == BotStatus.BLOCKED:
                    logger.warning(f"Step {step.bot_type} blocked, stopping pipeline")
                    run.status = "blocked"
                    break

            # Pipeline completed
            if run.status == "running":
                run.status = "completed"

            run.completed_at = datetime.now().isoformat()

            await self.event_bus.emit(
                EventTypes.PIPELINE_COMPLETED,
                {
                    "pipeline_id": pipeline_id,
                    "run_id": run_id,
                    "status": run.status,
                },
                source="orchestrator",
            )

        except Exception as e:
            run.status = "failed"
            run.error = str(e)
            run.completed_at = datetime.now().isoformat()

            await self.event_bus.emit(
                EventTypes.PIPELINE_FAILED,
                {
                    "pipeline_id": pipeline_id,
                    "run_id": run_id,
                    "error": str(e),
                },
                source="orchestrator",
            )

            logger.error(f"Pipeline {pipeline_id} failed: {e}")

        return run

    async def _run_with_retry(
        self,
        bot: BaseBot,
        context: BotContext,
        step: PipelineStep,
    ) -> BotResult:
        """Run a bot with retry logic."""
        for attempt in range(step.max_retries):
            try:
                result = await asyncio.wait_for(
                    bot.run(context),
                    timeout=step.timeout_seconds,
                )

                if result.status != BotStatus.FAILED:
                    return result

                if attempt < step.max_retries - 1:
                    logger.warning(f"Retry {attempt + 1}/{step.max_retries} for {bot.BOT_TYPE}")
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff

            except asyncio.TimeoutError:
                logger.error(f"Bot {bot.BOT_TYPE} timed out")
                if attempt < step.max_retries - 1:
                    continue

                return BotResult(
                    bot_id=bot.bot_id,
                    bot_type=bot.BOT_TYPE,
                    status=BotStatus.FAILED,
                    errors=["Timeout"],
                )

        return result

    def _evaluate_condition(self, condition: str, context: BotContext) -> bool:
        """Evaluate a condition expression."""
        try:
            # Safe eval with limited context
            return eval(condition, {"__builtins__": {}}, {
                "context": context,
                "has_failures": context.has_failures(),
                "last_result": context.get_last_result(),
            })
        except Exception as e:
            logger.error(f"Condition evaluation failed: {e}")
            return False

    def _setup_event_handlers(self):
        """Setup handlers for event-triggered pipelines."""
        async def handle_event(event):
            for pipeline in self._pipelines.values():
                if pipeline.trigger == "event":
                    trigger_event = pipeline.trigger_config.get("event_type")
                    if trigger_event == event.event_type:
                        logger.info(f"Event {event.event_type} triggered pipeline {pipeline.name}")
                        await self.run_pipeline(
                            pipeline.pipeline_id,
                            trigger=f"event:{event.event_type}",
                            initial_data=event.data,
                        )

        # Will be registered when event bus starts
        asyncio.create_task(self.event_bus.subscribe("*", handle_event))

    def get_pipeline(self, pipeline_id: str) -> Optional[Pipeline]:
        """Get a pipeline by ID."""
        return self._pipelines.get(pipeline_id)

    def list_pipelines(self) -> List[Pipeline]:
        """List all registered pipelines."""
        return list(self._pipelines.values())

    def get_run(self, run_id: str) -> Optional[PipelineRun]:
        """Get a pipeline run by ID."""
        return self._runs.get(run_id)

    def list_runs(self, pipeline_id: Optional[str] = None) -> List[PipelineRun]:
        """List pipeline runs."""
        runs = list(self._runs.values())
        if pipeline_id:
            runs = [r for r in runs if r.pipeline_id == pipeline_id]
        return sorted(runs, key=lambda r: r.started_at, reverse=True)

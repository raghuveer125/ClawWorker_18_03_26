"""
Chain-of-Thought Pipeline - Orchestrates multi-step reasoning.

Coordinates the full reasoning process:
Observe → Analyze → Hypothesize → Validate → Decide
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field

from ..context.context_agent import ContextAgent, ContextSnapshot
from ..memory.memory_agent import MemoryAgent
from .reason_agent import ReasonAgent, ReasoningResult

logger = logging.getLogger(__name__)


class ReasoningPhase(Enum):
    """Phases of chain-of-thought reasoning."""
    OBSERVE = "observe"
    ANALYZE = "analyze"
    HYPOTHESIZE = "hypothesize"
    VALIDATE = "validate"
    DECIDE = "decide"
    COMPLETE = "complete"


@dataclass
class ReasoningStep:
    """A single step in the reasoning process."""
    phase: ReasoningPhase
    description: str
    inputs: Dict[str, Any]
    outputs: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0
    success: bool = True
    error: Optional[str] = None


@dataclass
class PipelineResult:
    """Complete pipeline execution result."""
    goal_id: str
    goal_type: str
    targets: List[str]
    steps: List[ReasoningStep]
    reasoning_result: Optional[ReasoningResult]
    decision: Optional[str]
    should_proceed: bool
    total_duration_ms: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "goal_type": self.goal_type,
            "targets": self.targets,
            "steps": [
                {
                    "phase": s.phase.value,
                    "description": s.description,
                    "duration_ms": s.duration_ms,
                    "success": s.success,
                }
                for s in self.steps
            ],
            "decision": self.decision,
            "should_proceed": self.should_proceed,
            "total_duration_ms": self.total_duration_ms,
        }


class CoTPipeline:
    """
    Chain-of-Thought Pipeline for goal reasoning.

    Orchestrates the complete reasoning process with:
    - Phase-by-phase execution
    - Progress callbacks
    - Error handling and recovery
    - LLM debate integration for uncertain cases
    """

    def __init__(
        self,
        context_agent: Optional[ContextAgent] = None,
        memory_agent: Optional[MemoryAgent] = None,
        reason_agent: Optional[ReasonAgent] = None,
        use_debate: bool = True,
    ):
        self.context_agent = context_agent or ContextAgent()
        self.memory_agent = memory_agent or MemoryAgent()
        self.reason_agent = reason_agent or ReasonAgent(
            self.context_agent, self.memory_agent
        )
        self.use_debate = use_debate

        self._progress_callbacks: List[Callable] = []
        self._debate_fn: Optional[Callable] = None

    def on_progress(self, callback: Callable[[ReasoningStep], None]):
        """Register progress callback."""
        self._progress_callbacks.append(callback)

    def set_debate_function(self, debate_fn: Callable):
        """Set LLM debate function for uncertain cases."""
        self._debate_fn = debate_fn

    async def run(
        self,
        goal_id: str,
        goal_type: str,
        targets: List[str],
        parameters: Dict[str, Any],
        context: Optional[ContextSnapshot] = None,
    ) -> PipelineResult:
        """
        Run the complete reasoning pipeline.

        Args:
            goal_id: Unique goal identifier
            goal_type: Type of goal
            targets: Target symbols
            parameters: Goal parameters
            context: Pre-fetched context (optional)

        Returns:
            PipelineResult with decision
        """
        start_time = time.time()
        steps: List[ReasoningStep] = []

        logger.info(f"Starting CoT pipeline for goal {goal_id}")

        # Phase 1: Observe
        step = await self._run_phase(
            ReasoningPhase.OBSERVE,
            "Gathering context and observations",
            {"targets": targets, "goal_type": goal_type},
            lambda inputs: self._observe_phase(inputs, context),
        )
        steps.append(step)
        if not step.success:
            return self._create_failed_result(goal_id, goal_type, targets, steps, start_time)

        context = step.outputs.get("context")

        # Phase 2: Analyze
        step = await self._run_phase(
            ReasoningPhase.ANALYZE,
            "Analyzing patterns and history",
            {"context": context, "goal_type": goal_type},
            lambda inputs: self._analyze_phase(inputs),
        )
        steps.append(step)

        # Phase 3: Hypothesize
        step = await self._run_phase(
            ReasoningPhase.HYPOTHESIZE,
            "Generating action hypotheses",
            {"analyses": step.outputs.get("analyses", []), "parameters": parameters},
            lambda inputs: self._hypothesize_phase(inputs, goal_type),
        )
        steps.append(step)

        # Phase 4: Validate
        step = await self._run_phase(
            ReasoningPhase.VALIDATE,
            "Validating hypotheses",
            {"hypotheses": step.outputs.get("hypotheses", []), "context": context},
            lambda inputs: self._validate_phase(inputs),
        )
        steps.append(step)

        # Phase 5: Decide
        reasoning_result = await self.reason_agent.reason(
            goal_type, targets, parameters, context
        )

        decision, should_proceed = await self._decide(reasoning_result, goal_type)

        step = ReasoningStep(
            phase=ReasoningPhase.DECIDE,
            description="Making final decision",
            inputs={"reasoning_result": reasoning_result.confidence.value},
            outputs={"decision": decision, "should_proceed": should_proceed},
            success=True,
        )
        steps.append(step)
        self._notify_progress(step)

        # Use debate for uncertain cases
        if reasoning_result.confidence.value in ["low", "uncertain"] and self.use_debate:
            debate_result = await self._run_debate(
                goal_type, targets, reasoning_result
            )
            if debate_result:
                should_proceed = debate_result.get("proceed", should_proceed)
                decision = debate_result.get("decision", decision)

        total_duration = (time.time() - start_time) * 1000

        logger.info(
            f"CoT pipeline complete for {goal_id}: "
            f"decision={decision}, proceed={should_proceed}, "
            f"duration={total_duration:.0f}ms"
        )

        return PipelineResult(
            goal_id=goal_id,
            goal_type=goal_type,
            targets=targets,
            steps=steps,
            reasoning_result=reasoning_result,
            decision=decision,
            should_proceed=should_proceed,
            total_duration_ms=total_duration,
            metadata={
                "confidence": reasoning_result.confidence.value,
                "warnings": reasoning_result.warnings,
            }
        )

    async def _run_phase(
        self,
        phase: ReasoningPhase,
        description: str,
        inputs: Dict[str, Any],
        phase_fn: Callable,
    ) -> ReasoningStep:
        """Run a single reasoning phase."""
        start = time.time()

        step = ReasoningStep(
            phase=phase,
            description=description,
            inputs={k: str(v)[:100] for k, v in inputs.items()},  # Truncate for logging
        )

        try:
            outputs = await phase_fn(inputs)
            step.outputs = outputs
            step.success = True
        except Exception as e:
            logger.error(f"Phase {phase.value} failed: {e}")
            step.success = False
            step.error = str(e)

        step.duration_ms = (time.time() - start) * 1000
        self._notify_progress(step)

        return step

    async def _observe_phase(
        self,
        inputs: Dict[str, Any],
        context: Optional[ContextSnapshot],
    ) -> Dict[str, Any]:
        """Execute observe phase."""
        if context is None:
            context = await self.context_agent.get_context(
                inputs["targets"],
                inputs["goal_type"]
            )

        return {
            "context": context,
            "regime": context.regime,
            "volatility": context.volatility_state,
            "market_summary": self.context_agent.get_context_summary(context),
        }

    async def _analyze_phase(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute analyze phase."""
        context = inputs["context"]
        goal_type = inputs["goal_type"]

        # Get relevant memories
        memories = self.memory_agent.recall_for_context(
            goal_type,
            context.regime,
            list(context.market_data.keys())
        )

        # Detect patterns
        patterns = self.memory_agent.detect_patterns()

        return {
            "memories": {k: len(v) for k, v in memories.items()},
            "patterns": patterns,
            "analyses": [
                {"source": "memory", "count": sum(len(v) for v in memories.values())},
                {"source": "patterns", "count": len(patterns)},
            ],
        }

    async def _hypothesize_phase(
        self,
        inputs: Dict[str, Any],
        goal_type: str
    ) -> Dict[str, Any]:
        """Execute hypothesize phase."""
        # Hypotheses are generated in reason_agent.reason()
        # This phase prepares the input structure
        return {
            "hypotheses": ["proceed", "reduce_risk", "defer"],
            "parameters": inputs.get("parameters", {}),
        }

    async def _validate_phase(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute validate phase."""
        # Validation happens in reason_agent.reason()
        context = inputs.get("context")

        constraints = {
            "max_positions": 3,
            "volatility_limit": "extreme",
            "min_confidence": 0.3,
        }

        violations = []
        if context and context.positions and len(context.positions) >= constraints["max_positions"]:
            violations.append("max_positions_reached")
        if context and context.volatility_state == constraints["volatility_limit"]:
            violations.append("extreme_volatility")

        return {
            "constraints": constraints,
            "violations": violations,
            "valid": len(violations) == 0,
        }

    async def _decide(
        self,
        reasoning_result: ReasoningResult,
        goal_type: str
    ) -> tuple:
        """Make final decision based on reasoning."""
        if reasoning_result.recommendation is None:
            return "no_action", False

        rec = reasoning_result.recommendation

        # Decision thresholds by goal type
        thresholds = {
            "trade": 0.6,
            "optimize": 0.4,
            "analyze": 0.3,
            "monitor": 0.3,
            "learn": 0.3,
            "improve": 0.4,
        }

        threshold = thresholds.get(goal_type, 0.5)
        should_proceed = rec.confidence >= threshold

        if "Defer" in rec.action:
            should_proceed = False
            decision = "defer"
        elif should_proceed:
            decision = rec.action
        else:
            decision = f"low_confidence_{rec.action}"

        return decision, should_proceed

    async def _run_debate(
        self,
        goal_type: str,
        targets: List[str],
        reasoning_result: ReasoningResult,
    ) -> Optional[Dict[str, Any]]:
        """Run LLM debate for uncertain cases."""
        if not self._debate_fn:
            logger.debug("No debate function configured")
            return None

        try:
            context = {
                "goal_type": goal_type,
                "targets": targets,
                "recommendation": reasoning_result.recommendation.action if reasoning_result.recommendation else None,
                "confidence": reasoning_result.confidence.value,
                "warnings": reasoning_result.warnings,
            }

            result = await self._debate_fn(
                analysis_type="goal_execution",
                context=context
            )

            return result

        except Exception as e:
            logger.error(f"Debate failed: {e}")
            return None

    def _notify_progress(self, step: ReasoningStep):
        """Notify progress callbacks."""
        for callback in self._progress_callbacks:
            try:
                callback(step)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

    def _create_failed_result(
        self,
        goal_id: str,
        goal_type: str,
        targets: List[str],
        steps: List[ReasoningStep],
        start_time: float,
    ) -> PipelineResult:
        """Create result for failed pipeline."""
        return PipelineResult(
            goal_id=goal_id,
            goal_type=goal_type,
            targets=targets,
            steps=steps,
            reasoning_result=None,
            decision="failed",
            should_proceed=False,
            total_duration_ms=(time.time() - start_time) * 1000,
            metadata={"error": steps[-1].error if steps else "Unknown error"},
        )


# Convenience function
async def run_reasoning(
    goal_id: str,
    goal_type: str,
    targets: List[str],
    parameters: Dict[str, Any],
) -> PipelineResult:
    """Run reasoning pipeline with default agents."""
    pipeline = CoTPipeline()
    return await pipeline.run(goal_id, goal_type, targets, parameters)

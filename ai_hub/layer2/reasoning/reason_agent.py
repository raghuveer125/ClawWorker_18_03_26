"""
Reason Agent - Chain-of-thought reasoning for decisions.

Implements structured reasoning:
1. Observe - Gather facts from context
2. Analyze - Identify patterns and relationships
3. Hypothesize - Form potential actions
4. Validate - Check against constraints and memory
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from ..context.context_agent import ContextAgent, ContextSnapshot
from ..memory.memory_agent import MemoryAgent, MemoryType

logger = logging.getLogger(__name__)


class ReasoningConfidence(Enum):
    """Confidence levels for reasoning outputs."""
    HIGH = "high"      # > 0.8
    MEDIUM = "medium"  # 0.5 - 0.8
    LOW = "low"        # < 0.5
    UNCERTAIN = "uncertain"


@dataclass
class Observation:
    """Observed fact from context."""
    fact: str
    source: str
    value: Any
    importance: float = 1.0


@dataclass
class Analysis:
    """Analysis of observations."""
    pattern: str
    observations: List[str]
    implication: str
    confidence: float


@dataclass
class Hypothesis:
    """Potential action or conclusion."""
    action: str
    rationale: str
    supporting_evidence: List[str]
    risks: List[str]
    confidence: float
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReasoningResult:
    """Complete reasoning output."""
    observations: List[Observation]
    analyses: List[Analysis]
    hypotheses: List[Hypothesis]
    recommendation: Optional[Hypothesis]
    confidence: ReasoningConfidence
    reasoning_trace: List[str]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observations": [
                {"fact": o.fact, "source": o.source, "value": o.value}
                for o in self.observations
            ],
            "analyses": [
                {"pattern": a.pattern, "implication": a.implication, "confidence": a.confidence}
                for a in self.analyses
            ],
            "hypotheses": [
                {"action": h.action, "rationale": h.rationale, "confidence": h.confidence}
                for h in self.hypotheses
            ],
            "recommendation": {
                "action": self.recommendation.action,
                "parameters": self.recommendation.parameters,
                "confidence": self.recommendation.confidence,
            } if self.recommendation else None,
            "confidence": self.confidence.value,
            "warnings": self.warnings,
        }


class ReasonAgent:
    """
    Performs chain-of-thought reasoning for goal execution decisions.

    Uses context from Layer 0 and memory from past executions to
    reason about the best approach to achieve goals.
    """

    AGENT_TYPE = "reason"

    # Reasoning rules by goal type
    GOAL_RULES = {
        "trade": [
            "Check regime alignment with trade direction",
            "Verify risk parameters within limits",
            "Confirm volatility conditions acceptable",
            "Validate entry timing with market structure",
        ],
        "optimize": [
            "Ensure sufficient historical data",
            "Check for recent regime changes",
            "Verify parameter ranges are valid",
        ],
        "analyze": [
            "Gather multi-timeframe context",
            "Check indicator confluence",
            "Review historical patterns",
        ],
        "monitor": [
            "Verify alert conditions are specific",
            "Check for existing monitors",
        ],
    }

    def __init__(
        self,
        context_agent: Optional[ContextAgent] = None,
        memory_agent: Optional[MemoryAgent] = None,
    ):
        self.context_agent = context_agent or ContextAgent()
        self.memory_agent = memory_agent or MemoryAgent()

    async def reason(
        self,
        goal_type: str,
        targets: List[str],
        parameters: Dict[str, Any],
        context: Optional[ContextSnapshot] = None,
    ) -> ReasoningResult:
        """
        Perform chain-of-thought reasoning.

        Args:
            goal_type: Type of goal (trade, analyze, etc.)
            targets: Target symbols
            parameters: Goal parameters
            context: Pre-fetched context (optional)

        Returns:
            ReasoningResult with recommendation
        """
        trace = []
        warnings = []

        # Phase 1: Observe
        trace.append("PHASE 1: OBSERVE")

        if context is None:
            context = await self.context_agent.get_context(targets, goal_type)
            trace.append(f"  Fetched context for {targets}")

        observations = self._observe(context, parameters)
        trace.append(f"  Made {len(observations)} observations")

        # Phase 2: Analyze
        trace.append("PHASE 2: ANALYZE")

        # Get relevant memories
        memories = self.memory_agent.recall_for_context(
            goal_type, context.regime, targets
        )
        trace.append(f"  Retrieved {sum(len(v) for v in memories.values())} memories")

        analyses = self._analyze(observations, memories, goal_type)
        trace.append(f"  Produced {len(analyses)} analyses")

        # Check for failure patterns
        failure_rate, sample_size = self.memory_agent.get_failure_rate(
            goal_type, context.regime
        )
        if failure_rate > 0.5 and sample_size >= 3:
            warnings.append(
                f"High failure rate ({failure_rate:.0%}) for {goal_type} in {context.regime} regime"
            )
            trace.append(f"  WARNING: High historical failure rate")

        # Phase 3: Hypothesize
        trace.append("PHASE 3: HYPOTHESIZE")

        hypotheses = self._hypothesize(analyses, goal_type, parameters)
        trace.append(f"  Generated {len(hypotheses)} hypotheses")

        # Phase 4: Validate
        trace.append("PHASE 4: VALIDATE")

        validated = self._validate(hypotheses, context, memories)
        trace.append(f"  Validated {len(validated)} hypotheses")

        # Select best hypothesis
        recommendation = None
        if validated:
            recommendation = max(validated, key=lambda h: h.confidence)
            trace.append(f"  Recommended: {recommendation.action} (conf={recommendation.confidence:.2f})")
        else:
            trace.append("  No valid hypothesis found")
            warnings.append("No confident recommendation available")

        # Determine overall confidence
        if recommendation and recommendation.confidence > 0.8:
            confidence = ReasoningConfidence.HIGH
        elif recommendation and recommendation.confidence > 0.5:
            confidence = ReasoningConfidence.MEDIUM
        elif recommendation:
            confidence = ReasoningConfidence.LOW
        else:
            confidence = ReasoningConfidence.UNCERTAIN

        return ReasoningResult(
            observations=observations,
            analyses=analyses,
            hypotheses=hypotheses,
            recommendation=recommendation,
            confidence=confidence,
            reasoning_trace=trace,
            warnings=warnings,
        )

    def _observe(
        self,
        context: ContextSnapshot,
        parameters: Dict[str, Any]
    ) -> List[Observation]:
        """Extract observations from context."""
        observations = []

        # Market data observations
        for symbol, data in context.market_data.items():
            observations.append(Observation(
                fact=f"{symbol} trading at {data.get('ltp', 0):.2f}",
                source="market_data",
                value=data.get("ltp"),
                importance=1.0,
            ))

            change = data.get("change_pct", 0)
            observations.append(Observation(
                fact=f"{symbol} changed {change:+.2f}% today",
                source="market_data",
                value=change,
                importance=0.8 if abs(change) > 1 else 0.5,
            ))

        # Regime observation
        if context.regime:
            observations.append(Observation(
                fact=f"Market regime is {context.regime}",
                source="regime_detection",
                value=context.regime,
                importance=1.2,
            ))

        # Volatility observation
        if context.volatility_state:
            observations.append(Observation(
                fact=f"Volatility is {context.volatility_state}",
                source="volatility_detection",
                value=context.volatility_state,
                importance=1.1,
            ))

        # Indicator observations
        for symbol, indicators in context.indicators.items():
            for name, value in indicators.items():
                observations.append(Observation(
                    fact=f"{symbol} {name}: {value}",
                    source="indicator",
                    value=value,
                    importance=0.7,
                ))

        # Position observations
        if context.positions:
            observations.append(Observation(
                fact=f"Currently holding {len(context.positions)} positions",
                source="positions",
                value=len(context.positions),
                importance=1.5,
            ))

        return observations

    def _analyze(
        self,
        observations: List[Observation],
        memories: Dict[str, List],
        goal_type: str,
    ) -> List[Analysis]:
        """Analyze observations for patterns."""
        analyses = []

        # Regime analysis
        regime_obs = [o for o in observations if "regime" in o.source]
        if regime_obs:
            regime = regime_obs[0].value

            # Check historical performance in this regime
            past_outcomes = memories.get("past_outcomes", [])
            regime_outcomes = [m for m in past_outcomes if m.content.get("regime") == regime]

            if regime_outcomes:
                success_rate = sum(
                    1 for m in regime_outcomes if m.content.get("success")
                ) / len(regime_outcomes)

                analyses.append(Analysis(
                    pattern=f"Historical {regime} regime performance",
                    observations=[f"Regime is {regime}", f"{len(regime_outcomes)} past outcomes"],
                    implication=f"Success rate in this regime: {success_rate:.0%}",
                    confidence=min(0.5 + len(regime_outcomes) * 0.1, 0.9),
                ))

        # Volatility-regime interaction
        vol_obs = [o for o in observations if "volatility" in o.source]
        if vol_obs and regime_obs:
            vol = vol_obs[0].value
            regime = regime_obs[0].value

            if vol in ["high", "extreme"] and goal_type == "trade":
                analyses.append(Analysis(
                    pattern="High volatility trading conditions",
                    observations=[f"Volatility: {vol}", f"Regime: {regime}"],
                    implication="Consider wider stops and smaller position size",
                    confidence=0.8,
                ))

        # Check failure patterns
        failure_patterns = memories.get("failure_patterns", [])
        for pattern in failure_patterns:
            conditions = pattern.content.get("conditions", {})

            # Check if current conditions match failure pattern
            matches = 0
            total = len(conditions)
            for key, expected in conditions.items():
                for obs in observations:
                    if key in obs.fact.lower() and expected in str(obs.value).lower():
                        matches += 1

            if total > 0 and matches / total > 0.5:
                analyses.append(Analysis(
                    pattern=f"Failure pattern match: {pattern.content.get('pattern_name')}",
                    observations=[f"Matched {matches}/{total} conditions"],
                    implication=f"Risk of {pattern.content.get('failure_type')}",
                    confidence=0.7 + (matches / total) * 0.2,
                ))

        # Success strategy match
        success_strategies = memories.get("success_strategies", [])
        for strategy in success_strategies:
            if strategy.content.get("regime") == (regime_obs[0].value if regime_obs else None):
                analyses.append(Analysis(
                    pattern=f"Successful strategy available: {strategy.content.get('strategy_name')}",
                    observations=[f"Win rate: {strategy.content.get('win_rate', 0):.0%}"],
                    implication="Consider using proven parameters",
                    confidence=strategy.content.get("win_rate", 0.5),
                ))

        return analyses

    def _hypothesize(
        self,
        analyses: List[Analysis],
        goal_type: str,
        parameters: Dict[str, Any],
    ) -> List[Hypothesis]:
        """Generate action hypotheses from analyses."""
        hypotheses = []

        # Base hypothesis: Proceed with goal
        base_confidence = 0.6
        supporting = []
        risks = []

        for analysis in analyses:
            if "success" in analysis.pattern.lower() or "successful" in analysis.implication.lower():
                base_confidence += 0.1
                supporting.append(analysis.pattern)
            elif "failure" in analysis.pattern.lower() or "risk" in analysis.implication.lower():
                base_confidence -= 0.15
                risks.append(analysis.implication)
            else:
                supporting.append(analysis.pattern)

        hypotheses.append(Hypothesis(
            action=f"Execute {goal_type} with current parameters",
            rationale="Proceed with standard execution",
            supporting_evidence=supporting,
            risks=risks,
            confidence=max(0.1, min(base_confidence, 0.95)),
            parameters=parameters,
        ))

        # Modified hypothesis if risks detected
        if risks:
            modified_params = dict(parameters)

            # Reduce position size for high risk
            if "lots" in modified_params:
                modified_params["lots"] = max(1, modified_params["lots"] // 2)
            if "percentage" in modified_params:
                modified_params["percentage"] = modified_params["percentage"] * 0.5

            hypotheses.append(Hypothesis(
                action=f"Execute {goal_type} with reduced risk",
                rationale="Mitigate identified risks with smaller size",
                supporting_evidence=supporting,
                risks=risks,
                confidence=base_confidence + 0.1,  # Higher confidence for safer approach
                parameters=modified_params,
            ))

        # Wait hypothesis if too many risks
        if len(risks) >= 2:
            hypotheses.append(Hypothesis(
                action="Defer execution",
                rationale="Too many risk factors present",
                supporting_evidence=[],
                risks=risks,
                confidence=0.5 + len(risks) * 0.1,
                parameters={},
            ))

        return hypotheses

    def _validate(
        self,
        hypotheses: List[Hypothesis],
        context: ContextSnapshot,
        memories: Dict[str, List],
    ) -> List[Hypothesis]:
        """Validate hypotheses against constraints."""
        validated = []

        for hyp in hypotheses:
            valid = True

            # Check position limits
            if context.positions and "Execute" in hyp.action:
                if len(context.positions) >= 3:  # Max positions
                    hyp.confidence *= 0.5
                    hyp.risks.append("At maximum position count")

            # Check volatility constraints for trades
            if "trade" in hyp.action.lower() and context.volatility_state == "extreme":
                hyp.confidence *= 0.7
                hyp.risks.append("Extreme volatility conditions")

            # Don't completely invalidate, just adjust confidence
            if hyp.confidence > 0.1:
                validated.append(hyp)

        return validated

    def explain(self, result: ReasoningResult) -> str:
        """Create human-readable explanation of reasoning."""
        lines = ["Reasoning Trace:", "=" * 40]

        for step in result.reasoning_trace:
            lines.append(step)

        lines.append("")
        lines.append("Key Observations:")
        for obs in result.observations[:5]:
            lines.append(f"  - {obs.fact}")

        lines.append("")
        lines.append("Analyses:")
        for analysis in result.analyses:
            lines.append(f"  - {analysis.pattern}: {analysis.implication}")

        if result.recommendation:
            lines.append("")
            lines.append(f"Recommendation: {result.recommendation.action}")
            lines.append(f"Confidence: {result.confidence.value}")
            if result.recommendation.risks:
                lines.append(f"Risks: {', '.join(result.recommendation.risks)}")

        if result.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in result.warnings:
                lines.append(f"  ! {w}")

        return "\n".join(lines)

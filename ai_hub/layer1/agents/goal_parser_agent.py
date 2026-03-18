"""
Goal Parser Agent - Parses natural language goals.

Takes raw goal input and extracts:
- Goal type (trade, optimize, analyze, learn, monitor, improve)
- Target indices/symbols
- Timeframe
- Specific parameters
- Success criteria
"""

import re
import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from ..goal.goal_manager import Goal, GoalType, GoalStatus, GoalManager, get_goal_manager

logger = logging.getLogger(__name__)

# Try to import debate for complex parsing
try:
    from bot_army.scalping.debate_integration import debate_analysis
    HAS_DEBATE = True
except ImportError:
    HAS_DEBATE = False


@dataclass
class ParsedGoal:
    """Structured representation of a parsed goal."""
    goal_type: GoalType
    action: str
    targets: List[str]
    timeframe: Optional[str]
    parameters: Dict[str, Any]
    success_criteria: List[str]
    confidence: float


class GoalParserAgent:
    """
    Parses natural language goals into structured form.

    Uses pattern matching for common goals and LLM debate for complex ones.
    """

    AGENT_TYPE = "goal_parser"

    # Keywords for goal type detection
    GOAL_PATTERNS = {
        GoalType.TRADE: [
            r"trade", r"buy", r"sell", r"entry", r"exit", r"scalp",
            r"execute", r"position", r"order"
        ],
        GoalType.OPTIMIZE: [
            r"optimize", r"tune", r"adjust", r"improve parameters",
            r"backtest", r"find best"
        ],
        GoalType.ANALYZE: [
            r"analyze", r"check", r"look at", r"examine", r"review",
            r"what is", r"show me", r"tell me about"
        ],
        GoalType.LEARN: [
            r"learn", r"pattern", r"discover", r"find correlations",
            r"train", r"identify"
        ],
        GoalType.MONITOR: [
            r"monitor", r"watch", r"alert", r"notify", r"track",
            r"when.*reaches"
        ],
        GoalType.IMPROVE: [
            r"improve", r"fix", r"upgrade", r"enhance", r"self"
        ],
    }

    # Index name mappings
    INDEX_PATTERNS = {
        "NIFTY50": [r"nifty\s*50", r"nifty", r"^nifty$"],
        "BANKNIFTY": [r"bank\s*nifty", r"banknifty", r"bnf"],
        "SENSEX": [r"sensex"],
        "FINNIFTY": [r"fin\s*nifty", r"finnifty"],
    }

    # Timeframe patterns
    TIMEFRAME_PATTERNS = {
        "intraday": [r"intraday", r"today", r"this session"],
        "short_term": [r"this week", r"few days", r"short term"],
        "medium_term": [r"this month", r"medium term"],
        "long_term": [r"long term", r"over time"],
    }

    def __init__(
        self,
        goal_manager: Optional[GoalManager] = None,
        use_debate: bool = True,
    ):
        self.goal_manager = goal_manager or get_goal_manager()
        self.use_debate = use_debate and HAS_DEBATE

    async def parse(self, goal: Goal) -> ParsedGoal:
        """
        Parse a goal into structured form.

        Args:
            goal: Goal object with raw_input

        Returns:
            ParsedGoal with structured data
        """
        raw = goal.raw_input.lower().strip()

        # Update goal status
        self.goal_manager.set_status(goal.goal_id, GoalStatus.PARSING)

        # Detect goal type
        goal_type = self._detect_goal_type(raw)

        # Extract targets (indices/symbols)
        targets = self._extract_targets(raw)

        # Extract timeframe
        timeframe = self._extract_timeframe(raw)

        # Extract parameters
        parameters = self._extract_parameters(raw, goal_type)

        # Extract success criteria
        success_criteria = self._extract_success_criteria(raw)

        # Calculate confidence
        confidence = self._calculate_confidence(
            goal_type, targets, timeframe, parameters
        )

        # For complex goals with low confidence, use debate
        if confidence < 0.6 and self.use_debate:
            parsed = await self._debate_parse(goal.raw_input)
            if parsed:
                goal_type = parsed.goal_type
                targets = parsed.targets or targets
                parameters = {**parameters, **parsed.parameters}
                confidence = max(confidence, parsed.confidence)

        # Create action description
        action = self._create_action_description(goal_type, targets, parameters)

        parsed_goal = ParsedGoal(
            goal_type=goal_type,
            action=action,
            targets=targets,
            timeframe=timeframe,
            parameters=parameters,
            success_criteria=success_criteria,
            confidence=confidence,
        )

        # Update goal with parsed data
        self.goal_manager.update_goal(
            goal.goal_id,
            goal_type=goal_type,
            parsed={
                "action": action,
                "targets": targets,
                "timeframe": timeframe,
                "parameters": parameters,
                "success_criteria": success_criteria,
                "confidence": confidence,
            }
        )
        self.goal_manager.set_status(goal.goal_id, GoalStatus.PARSED)

        logger.info(f"Parsed goal {goal.goal_id}: type={goal_type.value}, conf={confidence:.2f}")
        return parsed_goal

    def _detect_goal_type(self, text: str) -> GoalType:
        """Detect goal type from text."""
        scores = {gt: 0 for gt in GoalType}

        for goal_type, patterns in self.GOAL_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    scores[goal_type] += 1

        # Return highest scoring type, default to ANALYZE
        best_type = max(scores, key=scores.get)
        return best_type if scores[best_type] > 0 else GoalType.ANALYZE

    def _extract_targets(self, text: str) -> List[str]:
        """Extract target indices/symbols from text."""
        targets = []

        for index_name, patterns in self.INDEX_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    if index_name not in targets:
                        targets.append(index_name)

        # If no specific targets, default to main indices
        if not targets:
            targets = ["NIFTY50", "BANKNIFTY"]

        return targets

    def _extract_timeframe(self, text: str) -> Optional[str]:
        """Extract timeframe from text."""
        for timeframe, patterns in self.TIMEFRAME_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    return timeframe
        return "intraday"  # Default

    def _extract_parameters(self, text: str, goal_type: GoalType) -> Dict[str, Any]:
        """Extract goal-specific parameters."""
        params = {}

        # Extract numbers
        numbers = re.findall(r"(\d+(?:\.\d+)?)\s*(%|points|lots)", text)
        for num, unit in numbers:
            if unit == "%":
                params["percentage"] = float(num)
            elif unit == "points":
                params["points"] = float(num)
            elif unit == "lots":
                params["lots"] = int(float(num))

        # Trade-specific
        if goal_type == GoalType.TRADE:
            if "ce" in text or "call" in text:
                params["option_type"] = "CE"
            elif "pe" in text or "put" in text:
                params["option_type"] = "PE"

            if "breakout" in text:
                params["strategy"] = "breakout"
            elif "reversal" in text:
                params["strategy"] = "reversal"
            elif "momentum" in text:
                params["strategy"] = "momentum"

        # Optimize-specific
        if goal_type == GoalType.OPTIMIZE:
            if "strike" in text:
                params["optimize_target"] = "strike_distance"
            elif "entry" in text:
                params["optimize_target"] = "entry_threshold"
            elif "exit" in text:
                params["optimize_target"] = "exit_rules"

        # Monitor-specific
        if goal_type == GoalType.MONITOR:
            price_match = re.search(r"(?:reaches?|crosses?|above|below)\s*(\d+)", text)
            if price_match:
                params["price_level"] = float(price_match.group(1))

        return params

    def _extract_success_criteria(self, text: str) -> List[str]:
        """Extract success criteria from text."""
        criteria = []

        # Common success patterns
        if re.search(r"profit|gain|make", text):
            criteria.append("positive_pnl")
        if re.search(r"win\s*rate", text):
            criteria.append("win_rate_target")
        if re.search(r"risk.{0,10}reward|r:r", text):
            criteria.append("risk_reward_ratio")
        if re.search(r"complete|finish|done", text):
            criteria.append("task_completion")

        if not criteria:
            criteria = ["task_completion"]

        return criteria

    def _calculate_confidence(
        self,
        goal_type: GoalType,
        targets: List[str],
        timeframe: Optional[str],
        parameters: Dict,
    ) -> float:
        """Calculate confidence in parsing."""
        confidence = 0.5  # Base

        # Has specific goal type
        if goal_type != GoalType.ANALYZE:
            confidence += 0.1

        # Has targets
        if targets:
            confidence += 0.15

        # Has timeframe
        if timeframe:
            confidence += 0.1

        # Has parameters
        if parameters:
            confidence += 0.05 * min(len(parameters), 3)

        return min(confidence, 1.0)

    def _create_action_description(
        self,
        goal_type: GoalType,
        targets: List[str],
        parameters: Dict,
    ) -> str:
        """Create human-readable action description."""
        target_str = ", ".join(targets) if targets else "market"

        if goal_type == GoalType.TRADE:
            strategy = parameters.get("strategy", "scalping")
            return f"Execute {strategy} strategy on {target_str}"

        elif goal_type == GoalType.OPTIMIZE:
            target = parameters.get("optimize_target", "parameters")
            return f"Optimize {target} for {target_str}"

        elif goal_type == GoalType.ANALYZE:
            return f"Analyze market conditions for {target_str}"

        elif goal_type == GoalType.LEARN:
            return f"Learn patterns from {target_str} data"

        elif goal_type == GoalType.MONITOR:
            level = parameters.get("price_level", "")
            return f"Monitor {target_str}" + (f" for level {level}" if level else "")

        elif goal_type == GoalType.IMPROVE:
            return f"Self-improvement analysis"

        return f"Process goal for {target_str}"

    async def _debate_parse(self, raw_input: str) -> Optional[ParsedGoal]:
        """Use LLM debate for complex goal parsing."""
        if not HAS_DEBATE:
            return None

        try:
            is_valid, reason, result = await debate_analysis(
                analysis_type="goal_parsing",
                context={
                    "raw_input": raw_input,
                    "questions": [
                        "What type of goal is this? (trade/optimize/analyze/learn/monitor/improve)",
                        "What indices or symbols are targeted?",
                        "What specific parameters were mentioned?",
                        "What are the success criteria?",
                    ],
                }
            )

            if is_valid and result:
                # Parse debate result
                goal_type = GoalType.ANALYZE  # Default
                for gt in GoalType:
                    if gt.value in result.reasoning.lower():
                        goal_type = gt
                        break

                return ParsedGoal(
                    goal_type=goal_type,
                    action=reason,
                    targets=[],  # Will be merged
                    timeframe=None,
                    parameters={},
                    success_criteria=[],
                    confidence=result.confidence / 100,
                )

        except Exception as e:
            logger.error(f"Debate parsing failed: {e}")

        return None

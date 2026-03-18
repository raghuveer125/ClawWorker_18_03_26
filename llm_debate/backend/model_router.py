"""
Model Router - Auto-switches between models based on task complexity.
Uses cheaper/faster models for simple tasks, powerful models for complex ones.
"""

from enum import Enum
from typing import Dict, Optional
import tiktoken


class TaskComplexity(Enum):
    SIMPLE = "simple"       # Quick questions, simple edits
    MEDIUM = "medium"       # Code review, moderate changes
    COMPLEX = "complex"     # Architecture decisions, multi-file changes


class ModelTier(Enum):
    FAST = "fast"           # Cheap, quick responses
    BALANCED = "balanced"   # Good balance of cost/quality
    POWERFUL = "powerful"   # Best quality, higher cost


# Model configurations for each provider
MODEL_CONFIGS = {
    "anthropic": {
        ModelTier.FAST: "claude-haiku-4-5-20251001",
        ModelTier.BALANCED: "claude-sonnet-4-20250514",
        ModelTier.POWERFUL: "claude-opus-4-20250514",
    },
    "openai": {
        ModelTier.FAST: "gpt-4o-mini",
        ModelTier.BALANCED: "gpt-4o",
        ModelTier.POWERFUL: "gpt-4o",  # or "o1-preview" if available
    },
}

# Complexity indicators
COMPLEX_KEYWORDS = [
    "architecture", "refactor", "redesign", "optimize", "security",
    "performance", "scalability", "multi-file", "database schema",
    "api design", "system design", "migration", "breaking change",
]

SIMPLE_KEYWORDS = [
    "typo", "rename", "format", "comment", "log", "print",
    "simple fix", "quick", "minor", "small change",
]


class ModelRouter:
    """Routes tasks to appropriate model tiers based on complexity."""

    def __init__(self):
        self.encoding = tiktoken.get_encoding("cl100k_base")

    def estimate_complexity(self, task: str, code_context: str = "") -> TaskComplexity:
        """Estimate task complexity based on content analysis."""

        combined = f"{task} {code_context}".lower()

        # Check for complexity indicators
        complex_score = sum(1 for kw in COMPLEX_KEYWORDS if kw in combined)
        simple_score = sum(1 for kw in SIMPLE_KEYWORDS if kw in combined)

        # Token count as complexity factor
        token_count = len(self.encoding.encode(combined))

        # File count indicator
        file_indicators = combined.count("file") + combined.count(".py") + combined.count(".js")

        # Scoring
        if simple_score > complex_score and token_count < 500:
            return TaskComplexity.SIMPLE
        elif complex_score >= 3 or token_count > 3000 or file_indicators > 3:
            return TaskComplexity.COMPLEX
        else:
            return TaskComplexity.MEDIUM

    def get_model_tier(self, complexity: TaskComplexity, debate_round: int = 0) -> ModelTier:
        """
        Get appropriate model tier based on complexity and debate round.
        Later rounds use more powerful models if consensus isn't reached.
        """

        if complexity == TaskComplexity.SIMPLE:
            return ModelTier.FAST
        elif complexity == TaskComplexity.MEDIUM:
            # Escalate to powerful if debate goes beyond round 3
            return ModelTier.POWERFUL if debate_round > 3 else ModelTier.BALANCED
        else:
            return ModelTier.POWERFUL

    def get_model(
        self,
        provider: str,
        task: str,
        code_context: str = "",
        debate_round: int = 0,
        force_tier: Optional[ModelTier] = None
    ) -> Dict[str, str]:
        """
        Get the appropriate model for a task.

        Returns:
            Dict with 'model' name and 'tier' used
        """

        if force_tier:
            tier = force_tier
        else:
            complexity = self.estimate_complexity(task, code_context)
            tier = self.get_model_tier(complexity, debate_round)

        provider_key = provider.lower()
        if provider_key not in MODEL_CONFIGS:
            raise ValueError(f"Unknown provider: {provider}")

        model = MODEL_CONFIGS[provider_key].get(tier)
        if not model:
            # Fallback to balanced
            model = MODEL_CONFIGS[provider_key][ModelTier.BALANCED]
            tier = ModelTier.BALANCED

        return {
            "model": model,
            "tier": tier.value,
            "provider": provider,
        }

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for a text."""
        return len(self.encoding.encode(text))

    def should_escalate(self, rounds: int, has_consensus: bool) -> bool:
        """Determine if we should escalate to more powerful models."""
        return rounds >= 3 and not has_consensus

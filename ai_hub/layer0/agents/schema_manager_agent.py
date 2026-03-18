"""
Schema Manager Agent - Handles dynamic schema changes via debate.

Processes field requests from Learning Army and uses LLM Debate
to decide whether to approve new indicators.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from ..schema.adaptive_schema_manager import AdaptiveSchemaManager
from ..enrichment.indicator_registry import get_indicator_registry
from ..pipe.data_pipe import DataPipe, get_data_pipe, DataEventType

logger = logging.getLogger(__name__)

# Try to import debate system
try:
    from bot_army.scalping.debate_integration import debate_analysis
    HAS_DEBATE = True
except ImportError:
    HAS_DEBATE = False
    logger.warning("Debate system not available for SchemaManagerAgent")


@dataclass
class FieldRequestDecision:
    """Decision on a field request."""
    request_id: str
    field_name: str
    approved: bool
    reason: str
    confidence: float
    debate_result: Optional[Dict] = None


class SchemaManagerAgent:
    """
    Schema Manager Agent for Layer 0.

    Responsibilities:
    - Process field requests from Learning Army (Layer 6)
    - Use LLM Debate to validate new indicators
    - Manage schema versioning
    - Hot-reload indicators without restart
    """

    AGENT_TYPE = "schema_manager"

    def __init__(
        self,
        schema_manager: Optional[AdaptiveSchemaManager] = None,
        data_pipe: Optional[DataPipe] = None,
        auto_approve_threshold: float = 0.85,
        require_debate: bool = True,
    ):
        """
        Initialize Schema Manager Agent.

        Args:
            schema_manager: AdaptiveSchemaManager instance
            data_pipe: DataPipe instance
            auto_approve_threshold: Confidence threshold for auto-approval
            require_debate: Whether to require LLM debate for all requests
        """
        self.schema_manager = schema_manager or AdaptiveSchemaManager()
        self.indicator_registry = get_indicator_registry()
        self.data_pipe = data_pipe or get_data_pipe()

        self.auto_approve_threshold = auto_approve_threshold
        self.require_debate = require_debate

        # Track decisions
        self._decisions: List[FieldRequestDecision] = []

        # Subscribe to field request events
        self.data_pipe.subscribe(
            self._on_field_request,
            [DataEventType.FIELD_REQUEST]
        )

        logger.info("SchemaManagerAgent initialized")

    def _on_field_request(self, event):
        """Handle field request events from pipe."""
        asyncio.create_task(self.process_request(
            event.data.get("request_id", ""),
        ))

    async def process_request(self, request_id: str) -> FieldRequestDecision:
        """
        Process a field request - main entry point.

        Args:
            request_id: ID of the request to process

        Returns:
            FieldRequestDecision with approval status
        """
        # Get pending requests
        pending = self.data_pipe.get_pending_field_requests()
        request = next((r for r in pending if r["request_id"] == request_id), None)

        if not request:
            logger.warning(f"Request {request_id} not found")
            return FieldRequestDecision(
                request_id=request_id,
                field_name="",
                approved=False,
                reason="Request not found",
                confidence=0.0,
            )

        field_name = request["field_name"]
        logger.info(f"Processing field request: {field_name}")

        # Check if indicator already exists
        if self.indicator_registry.has(f"compute_{field_name}"):
            # Indicator exists, just need to add to schema
            decision = FieldRequestDecision(
                request_id=request_id,
                field_name=field_name,
                approved=True,
                reason="Indicator already registered",
                confidence=1.0,
            )
        elif self.require_debate and HAS_DEBATE:
            # Use debate to decide
            decision = await self._debate_request(request)
        else:
            # Auto-approve based on confidence
            confidence = request.get("confidence", 0.5)
            if confidence >= self.auto_approve_threshold:
                decision = FieldRequestDecision(
                    request_id=request_id,
                    field_name=field_name,
                    approved=True,
                    reason=f"Auto-approved (confidence: {confidence:.2f})",
                    confidence=confidence,
                )
            else:
                decision = FieldRequestDecision(
                    request_id=request_id,
                    field_name=field_name,
                    approved=False,
                    reason=f"Confidence too low ({confidence:.2f} < {self.auto_approve_threshold})",
                    confidence=confidence,
                )

        # Apply decision
        if decision.approved:
            self.data_pipe.approve_field_request(request_id)
            logger.info(f"Field request APPROVED: {field_name}")
        else:
            self.data_pipe.reject_field_request(request_id, decision.reason)
            logger.info(f"Field request REJECTED: {field_name} - {decision.reason}")

        self._decisions.append(decision)
        return decision

    async def _debate_request(self, request: Dict) -> FieldRequestDecision:
        """
        Use LLM Debate to evaluate a field request.

        The debate considers:
        - Statistical significance of the improvement
        - Compute cost vs benefit
        - Dependencies availability
        - Potential side effects
        """
        field_name = request["field_name"]
        request_id = request["request_id"]

        try:
            is_valid, reason, result = await debate_analysis(
                analysis_type="indicator_request",
                context={
                    "field_name": field_name,
                    "description": request.get("description", ""),
                    "dependencies": request.get("dependencies", []),
                    "reason": request.get("reason", ""),
                    "confidence": request.get("confidence", 0.5),
                    "requester": request.get("requester", "unknown"),
                    "questions": [
                        f"Should we add '{field_name}' to the data schema?",
                        "Is the claimed improvement statistically significant?",
                        "What is the compute cost vs benefit?",
                        "Are there any risks or side effects?",
                        "Are all dependencies available?",
                    ],
                }
            )

            debate_confidence = result.confidence / 100 if result else 0.5

            return FieldRequestDecision(
                request_id=request_id,
                field_name=field_name,
                approved=is_valid,
                reason=reason,
                confidence=debate_confidence,
                debate_result={
                    "valid": is_valid,
                    "reason": reason,
                    "confidence": debate_confidence,
                    "reasoning": result.reasoning if result else "",
                    "concerns": result.concerns if result else [],
                }
            )

        except Exception as e:
            logger.error(f"Debate error for {field_name}: {e}")
            # Fall back to confidence-based decision
            confidence = request.get("confidence", 0.5)
            return FieldRequestDecision(
                request_id=request_id,
                field_name=field_name,
                approved=confidence >= self.auto_approve_threshold,
                reason=f"Debate failed ({e}), using confidence threshold",
                confidence=confidence,
            )

    async def add_indicator_manually(
        self,
        name: str,
        description: str,
        dependencies: List[str],
        compute_fn: str,
        params: Optional[Dict] = None,
        reason: str = "Manual addition",
    ) -> bool:
        """
        Manually add an indicator without going through debate.

        Use this for system-level additions or testing.
        """
        success = self.schema_manager.add_field(
            name=name,
            description=description,
            dependencies=dependencies,
            compute_fn=compute_fn,
            params=params,
            reason=reason,
            added_by="manual",
        )

        if success:
            logger.info(f"Manually added indicator: {name}")

        return success

    def get_schema(self) -> Dict:
        """Get current schema."""
        return self.schema_manager.export_schema()

    def get_pending_requests(self) -> List[Dict]:
        """Get pending field requests."""
        return self.data_pipe.get_pending_field_requests()

    def get_decisions(self, limit: int = 20) -> List[Dict]:
        """Get recent decisions."""
        decisions = self._decisions[-limit:]
        return [
            {
                "request_id": d.request_id,
                "field_name": d.field_name,
                "approved": d.approved,
                "reason": d.reason,
                "confidence": d.confidence,
            }
            for d in decisions
        ]

    def get_available_indicators(self) -> List[Dict]:
        """Get list of available compute functions."""
        return self.indicator_registry.list_indicators()

"""
Data Checker Agent - Verifies Layer 0 data availability.

Checks if all required data fields are available in Layer 0.
Requests missing fields from Layer 0 if needed.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..goal.goal_manager import Goal, GoalStatus, GoalManager, get_goal_manager

logger = logging.getLogger(__name__)

# Import Layer 0 components
try:
    from ai_hub.layer0.pipe.data_pipe import DataPipe, get_data_pipe
    from ai_hub.layer0.schema.adaptive_schema_manager import AdaptiveSchemaManager
    HAS_LAYER0 = True
except ImportError:
    HAS_LAYER0 = False
    logger.warning("Layer 0 not available for DataCheckerAgent")


class DataCheckerAgent:
    """
    Verifies data availability for goal execution.

    Queries Layer 0 schema to check if required fields exist.
    Can request new fields from Layer 0 if missing.
    """

    AGENT_TYPE = "data_checker"

    # Map common field names to Layer 0 field names
    FIELD_ALIASES = {
        "price": "ltp",
        "last_price": "ltp",
        "open_interest": "oi",
        "bid_price": "bid",
        "ask_price": "ask",
        "candles": "history",
        "ohlc": "history",
    }

    # Fields that can be requested from Layer 0
    REQUESTABLE_FIELDS = {
        "vwap": {
            "compute_fn": "compute_vwap",
            "dependencies": ["close", "volume"],
            "description": "Volume Weighted Average Price",
        },
        "fvg_zones": {
            "compute_fn": "compute_fvg",
            "dependencies": ["open", "high", "low", "close"],
            "description": "Fair Value Gap zones",
        },
        "atr": {
            "compute_fn": "compute_atr",
            "dependencies": ["high", "low", "close"],
            "description": "Average True Range",
        },
        "spread": {
            "compute_fn": "compute_spread",
            "dependencies": ["bid", "ask"],
            "description": "Bid-Ask spread",
        },
        "volume_profile": {
            "compute_fn": "compute_volume_profile",
            "dependencies": ["high", "low", "volume"],
            "description": "Volume profile with POC",
        },
        "order_imbalance": {
            "compute_fn": "compute_order_imbalance",
            "dependencies": ["bid_volume", "ask_volume"],
            "description": "Order flow imbalance",
        },
    }

    def __init__(
        self,
        goal_manager: Optional[GoalManager] = None,
        data_pipe: Optional[Any] = None,
        schema_manager: Optional[Any] = None,
        auto_request: bool = True,
    ):
        self.goal_manager = goal_manager or get_goal_manager()
        self.auto_request = auto_request

        if HAS_LAYER0:
            self.data_pipe = data_pipe or get_data_pipe()
            # Get schema manager from registered adapter
            adapter = self.data_pipe.get_adapter("fyers")
            self.schema_manager = schema_manager or (
                adapter.schema_manager if adapter else None
            )
        else:
            self.data_pipe = None
            self.schema_manager = None

    async def check(self, goal: Goal) -> Tuple[bool, List[str]]:
        """
        Check if all required data is available for a goal.

        Args:
            goal: Goal to check

        Returns:
            (all_available, missing_fields)
        """
        self.goal_manager.set_status(goal.goal_id, GoalStatus.CHECKING)

        # Collect all data requirements
        required_fields = set(goal.data_requirements)
        for task in goal.tasks:
            required_fields.update(task.data_requirements)

        # Normalize field names
        normalized = set()
        for field in required_fields:
            normalized.add(self._normalize_field(field))

        # Check availability
        available, missing = self._check_availability(list(normalized))

        # Try to request missing fields
        requested = []
        if missing and self.auto_request:
            for field in missing:
                if self._request_field(field, goal):
                    requested.append(field)

        # Update missing list after requests
        still_missing = [f for f in missing if f not in requested]

        # Update goal
        self.goal_manager.update_goal(
            goal.goal_id,
            missing_data=still_missing
        )

        if still_missing:
            self.goal_manager.set_status(goal.goal_id, GoalStatus.BLOCKED)
            logger.warning(f"Goal {goal.goal_id} blocked: missing {still_missing}")
        else:
            self.goal_manager.set_status(goal.goal_id, GoalStatus.READY)
            logger.info(f"Goal {goal.goal_id} ready: all data available")

        return len(still_missing) == 0, still_missing

    def _normalize_field(self, field: str) -> str:
        """Normalize field name to Layer 0 convention."""
        field = field.lower().strip()

        # Remove index prefixes
        for prefix in ["nifty50_", "banknifty_", "sensex_"]:
            if field.startswith(prefix):
                field = field[len(prefix):]

        # Apply aliases
        return self.FIELD_ALIASES.get(field, field)

    def _check_availability(self, fields: List[str]) -> Tuple[List[str], List[str]]:
        """Check which fields are available in Layer 0."""
        available = []
        missing = []

        if not self.schema_manager:
            # Without schema manager, assume base fields exist
            base_fields = ["ltp", "open", "high", "low", "close", "volume",
                          "oi", "bid", "ask", "prev_close", "symbol"]
            for field in fields:
                if field in base_fields:
                    available.append(field)
                else:
                    missing.append(field)
        else:
            for field in fields:
                if self.schema_manager.has_field(field):
                    available.append(field)
                else:
                    missing.append(field)

        return available, missing

    def _request_field(self, field: str, goal: Goal) -> bool:
        """Request a missing field from Layer 0."""
        if field not in self.REQUESTABLE_FIELDS:
            logger.debug(f"Field '{field}' is not requestable")
            return False

        if not self.data_pipe:
            logger.warning("Cannot request field: DataPipe not available")
            return False

        field_spec = self.REQUESTABLE_FIELDS[field]

        request = {
            "name": field,
            "description": field_spec["description"],
            "dependencies": field_spec["dependencies"],
            "compute_fn": field_spec["compute_fn"],
            "reason": f"Required by goal {goal.goal_id}",
            "confidence": 0.9,
            "requester": f"DataCheckerAgent (goal: {goal.goal_id})",
        }

        try:
            request_id = self.data_pipe.request_field(request)
            logger.info(f"Requested field '{field}' from Layer 0: {request_id}")

            # Auto-approve for known fields
            self.data_pipe.approve_field_request(request_id)
            return True

        except Exception as e:
            logger.error(f"Failed to request field '{field}': {e}")
            return False

    def get_data_summary(self, goal: Goal) -> Dict[str, Any]:
        """Get summary of data requirements for a goal."""
        required = set(goal.data_requirements)
        for task in goal.tasks:
            required.update(task.data_requirements)

        available, missing = self._check_availability(list(required))

        return {
            "total_required": len(required),
            "available": available,
            "missing": missing,
            "requestable": [f for f in missing if f in self.REQUESTABLE_FIELDS],
            "not_requestable": [f for f in missing if f not in self.REQUESTABLE_FIELDS],
        }

    def validate_task_data(self, goal: Goal) -> Dict[str, bool]:
        """Validate data availability per task."""
        results = {}

        for task in goal.tasks:
            normalized = [self._normalize_field(f) for f in task.data_requirements]
            available, missing = self._check_availability(normalized)
            results[task.task_id] = len(missing) == 0

        return results

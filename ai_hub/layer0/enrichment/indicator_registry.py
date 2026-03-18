"""
Indicator Registry - Manages compute functions for dynamic indicators.

Learning Army can register new indicators here.
Works with AdaptiveSchemaManager for field definitions.
"""

from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass
import logging
import importlib
import inspect

from .indicators import INDICATOR_FUNCTIONS, IndicatorResult

logger = logging.getLogger(__name__)


@dataclass
class RegisteredIndicator:
    """Registered indicator with its compute function."""
    name: str
    compute_fn: Callable
    description: str
    dependencies: List[str]
    params_schema: Dict[str, Any]
    added_by: str = "system"
    version: int = 1


class IndicatorRegistry:
    """
    Registry for indicator compute functions.

    Features:
    - Pre-registered core indicators (VWAP, FVG, ATR, etc.)
    - Dynamic registration of new indicators
    - Hot-reload capability for indicator updates
    - Validation of compute function signatures
    """

    def __init__(self):
        self._indicators: Dict[str, RegisteredIndicator] = {}
        self._compute_cache: Dict[str, Any] = {}

        # Register built-in indicators
        self._register_builtins()

    def _register_builtins(self):
        """Register built-in indicator functions."""
        builtins = {
            "compute_vwap": {
                "description": "Volume Weighted Average Price",
                "dependencies": ["close", "volume"],
                "params_schema": {},
            },
            "compute_fvg": {
                "description": "Fair Value Gap zones detection",
                "dependencies": ["open", "high", "low", "close"],
                "params_schema": {
                    "min_gap_pct": {"type": "float", "default": 0.1},
                    "lookback": {"type": "int", "default": 50},
                },
            },
            "compute_spread": {
                "description": "Bid-Ask spread calculation",
                "dependencies": ["bid", "ask"],
                "params_schema": {},
            },
            "compute_volume_profile": {
                "description": "Volume profile with POC",
                "dependencies": ["high", "low", "volume"],
                "params_schema": {
                    "bins": {"type": "int", "default": 20},
                },
            },
            "compute_order_imbalance": {
                "description": "Order flow imbalance ratio",
                "dependencies": ["bid_volume", "ask_volume"],
                "params_schema": {},
            },
            "compute_atr": {
                "description": "Average True Range",
                "dependencies": ["high", "low", "close"],
                "params_schema": {
                    "period": {"type": "int", "default": 14},
                },
            },
        }

        for fn_name, meta in builtins.items():
            if fn_name in INDICATOR_FUNCTIONS:
                self._indicators[fn_name] = RegisteredIndicator(
                    name=fn_name,
                    compute_fn=INDICATOR_FUNCTIONS[fn_name],
                    description=meta["description"],
                    dependencies=meta["dependencies"],
                    params_schema=meta["params_schema"],
                    added_by="system",
                )
                logger.debug(f"Registered builtin indicator: {fn_name}")

    def register(
        self,
        name: str,
        compute_fn: Callable,
        description: str,
        dependencies: List[str],
        params_schema: Optional[Dict] = None,
        added_by: str = "manual",
    ) -> bool:
        """
        Register a new indicator compute function.

        Args:
            name: Function name (e.g., "compute_my_indicator")
            compute_fn: The actual compute function
            description: Human-readable description
            dependencies: List of required input fields
            params_schema: Schema for optional parameters
            added_by: Who registered this (system, learning_army, manual)

        Returns:
            True if registration successful
        """
        # Validate function signature
        sig = inspect.signature(compute_fn)
        params = list(sig.parameters.keys())

        if len(params) < 1:
            logger.error(f"Invalid indicator function {name}: needs at least 1 parameter")
            return False

        # Check if already exists
        if name in self._indicators:
            existing = self._indicators[name]
            existing.compute_fn = compute_fn
            existing.description = description
            existing.dependencies = dependencies
            existing.params_schema = params_schema or {}
            existing.version += 1
            logger.info(f"Updated indicator: {name} (v{existing.version})")
        else:
            self._indicators[name] = RegisteredIndicator(
                name=name,
                compute_fn=compute_fn,
                description=description,
                dependencies=dependencies,
                params_schema=params_schema or {},
                added_by=added_by,
            )
            logger.info(f"Registered new indicator: {name}")

        return True

    def unregister(self, name: str) -> bool:
        """Unregister an indicator."""
        if name in self._indicators:
            del self._indicators[name]
            logger.info(f"Unregistered indicator: {name}")
            return True
        return False

    def get(self, name: str) -> Optional[RegisteredIndicator]:
        """Get a registered indicator by name."""
        return self._indicators.get(name)

    def has(self, name: str) -> bool:
        """Check if indicator is registered."""
        return name in self._indicators

    def list_indicators(self) -> List[Dict]:
        """List all registered indicators."""
        return [
            {
                "name": ind.name,
                "description": ind.description,
                "dependencies": ind.dependencies,
                "params_schema": ind.params_schema,
                "added_by": ind.added_by,
                "version": ind.version,
            }
            for ind in self._indicators.values()
        ]

    def compute(
        self,
        name: str,
        data: Any,
        params: Optional[Dict] = None,
    ) -> IndicatorResult:
        """
        Compute an indicator value.

        Args:
            name: Indicator function name
            data: Input data (candles, quote, etc.)
            params: Optional parameters for the compute function

        Returns:
            IndicatorResult with computed value
        """
        indicator = self._indicators.get(name)
        if not indicator:
            logger.error(f"Indicator not found: {name}")
            return IndicatorResult(name=name, value=None, metadata={"error": "not_found"})

        try:
            result = indicator.compute_fn(data, params)
            if isinstance(result, IndicatorResult):
                return result
            # Wrap raw result
            return IndicatorResult(name=name, value=result)
        except Exception as e:
            logger.error(f"Error computing {name}: {e}")
            return IndicatorResult(name=name, value=None, metadata={"error": str(e)})

    def compute_all(
        self,
        data: Any,
        indicators: List[str],
        params_map: Optional[Dict[str, Dict]] = None,
    ) -> Dict[str, IndicatorResult]:
        """
        Compute multiple indicators at once.

        Args:
            data: Input data
            indicators: List of indicator names to compute
            params_map: Map of indicator name to params

        Returns:
            Dict of indicator name to IndicatorResult
        """
        params_map = params_map or {}
        results = {}

        for name in indicators:
            params = params_map.get(name, {})
            results[name] = self.compute(name, data, params)

        return results

    def get_dependencies(self, name: str) -> List[str]:
        """Get dependencies for an indicator."""
        indicator = self._indicators.get(name)
        return indicator.dependencies if indicator else []

    def validate_dependencies(
        self,
        name: str,
        available_fields: List[str]
    ) -> tuple[bool, List[str]]:
        """
        Check if all dependencies are available.

        Returns:
            (is_valid, missing_fields)
        """
        deps = self.get_dependencies(name)
        missing = [d for d in deps if d not in available_fields]
        return len(missing) == 0, missing


# Global singleton instance
_registry_instance: Optional[IndicatorRegistry] = None


def get_indicator_registry() -> IndicatorRegistry:
    """Get the global IndicatorRegistry instance."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = IndicatorRegistry()
    return _registry_instance

"""
Base Module for Regime Hunter Pipeline

Provides common interface and functionality for all regime modules.
Each module analyzes specific market parameters and outputs a standardized result.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_BOT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "bots"


@dataclass
class ModuleConfig:
    """Configuration for a module"""
    enabled: bool = True
    weight: float = 1.0  # Weight in pipeline aggregation
    index_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Custom thresholds per index: {"BANKNIFTY": {"vix_extreme": 28}}

    def get_threshold(self, param: str, index: str, default: Any) -> Any:
        """Get threshold value, checking index overrides first"""
        if index in self.index_overrides:
            if param in self.index_overrides[index]:
                return self.index_overrides[index][param]
        return default


@dataclass
class ModulePerformance:
    """Performance tracking for a module"""
    module_name: str
    total_signals: int = 0
    correct_predictions: int = 0
    accuracy: float = 0.0
    avg_confidence: float = 0.0
    last_10_results: List[bool] = field(default_factory=list)
    index_accuracy: Dict[str, float] = field(default_factory=dict)

    def record_result(self, correct: bool, confidence: float, index: str):
        """Record a prediction result"""
        self.total_signals += 1
        if correct:
            self.correct_predictions += 1

        # Update accuracy
        self.accuracy = (self.correct_predictions / self.total_signals) * 100

        # Rolling average confidence
        self.avg_confidence = (
            (self.avg_confidence * (self.total_signals - 1) + confidence)
            / self.total_signals
        )

        # Last 10 results for recent performance
        self.last_10_results.append(correct)
        if len(self.last_10_results) > 10:
            self.last_10_results.pop(0)

        # Per-index accuracy
        if index not in self.index_accuracy:
            self.index_accuracy[index] = 0.0
        # Simplified per-index tracking (would need more data in production)
        recent_correct = sum(1 for r in self.last_10_results if r)
        self.index_accuracy[index] = (recent_correct / len(self.last_10_results)) * 100

    def get_recent_accuracy(self) -> float:
        """Get accuracy from last 10 predictions"""
        if not self.last_10_results:
            return 0.0
        return (sum(1 for r in self.last_10_results if r) / len(self.last_10_results)) * 100


@dataclass
class ModuleOutput:
    """Standard output from any module"""
    module_name: str
    timestamp: str
    index: str
    confidence: float  # 0-100
    factors: Dict[str, Any]  # Raw factors analyzed
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BaseModule(ABC):
    """
    Abstract base class for all regime modules

    Each module must implement:
    - analyze(): Analyze market data and return module-specific output
    - validate(): Validate if module output was correct after outcome known
    """

    def __init__(
        self,
        name: str,
        description: str,
        data_dir: str = None
    ):
        self.name = name
        self.description = description

        # Data directory for persistence
        self.data_dir = Path(data_dir or os.getenv(
            "BOT_DATA_DIR",
            str(DEFAULT_BOT_DATA_DIR)
        )) / "regime_modules"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Module configuration
        self.config = self._load_config()

        # Performance tracking
        self.performance = self._load_performance()

        # Signal history for analysis
        self.signal_history: List[ModuleOutput] = []

    def _load_config(self) -> ModuleConfig:
        """Load module configuration from disk"""
        config_file = self.data_dir / f"{self.name}_config.json"
        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    data = json.load(f)
                    return ModuleConfig(**data)
            except (json.JSONDecodeError, TypeError):
                pass
        return ModuleConfig()

    def save_config(self):
        """Save module configuration to disk"""
        config_file = self.data_dir / f"{self.name}_config.json"
        with open(config_file, "w") as f:
            json.dump(asdict(self.config), f, indent=2)

    def _load_performance(self) -> ModulePerformance:
        """Load performance data from disk"""
        perf_file = self.data_dir / f"{self.name}_performance.json"
        if perf_file.exists():
            try:
                with open(perf_file, "r") as f:
                    data = json.load(f)
                    return ModulePerformance(**data)
            except (json.JSONDecodeError, TypeError):
                pass
        return ModulePerformance(module_name=self.name)

    def save_performance(self):
        """Save performance data to disk"""
        perf_file = self.data_dir / f"{self.name}_performance.json"
        with open(perf_file, "w") as f:
            json.dump(asdict(self.performance), f, indent=2)

    @abstractmethod
    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        historical_data: Optional[List[Dict]] = None
    ) -> ModuleOutput:
        """
        Analyze market data and return module output

        Args:
            index: Index name (NIFTY50, BANKNIFTY, etc.)
            market_data: Current market data
            historical_data: Recent historical data points

        Returns:
            ModuleOutput with analysis results
        """
        pass

    @abstractmethod
    def validate(
        self,
        output: ModuleOutput,
        actual_outcome: Dict[str, Any]
    ) -> bool:
        """
        Validate if module's prediction was correct

        Args:
            output: The module's previous output
            actual_outcome: What actually happened

        Returns:
            True if prediction was correct
        """
        pass

    def is_enabled(self) -> bool:
        """Check if module is enabled"""
        return self.config.enabled

    def get_weight(self) -> float:
        """Get module's weight in pipeline aggregation"""
        # Adjust weight based on recent performance
        base_weight = self.config.weight
        recent_accuracy = self.performance.get_recent_accuracy()

        if self.performance.total_signals < 10:
            # Not enough data, use base weight
            return base_weight

        # Boost weight for high accuracy, reduce for low
        if recent_accuracy >= 70:
            return base_weight * 1.2
        elif recent_accuracy >= 50:
            return base_weight
        else:
            return base_weight * 0.8

    def record_outcome(self, output: ModuleOutput, actual_outcome: Dict[str, Any]):
        """Record actual outcome and update performance"""
        correct = self.validate(output, actual_outcome)
        self.performance.record_result(correct, output.confidence, output.index)
        self.save_performance()

    def set_index_override(self, index: str, param: str, value: Any):
        """Set index-specific threshold override"""
        if index not in self.config.index_overrides:
            self.config.index_overrides[index] = {}
        self.config.index_overrides[index][param] = value
        self.save_config()

    def enable(self):
        """Enable this module"""
        self.config.enabled = True
        self.save_config()

    def disable(self):
        """Disable this module"""
        self.config.enabled = False
        self.save_config()

    def set_weight(self, weight: float):
        """Set module weight"""
        self.config.weight = max(0.1, min(3.0, weight))
        self.save_config()

    def to_dict(self) -> Dict[str, Any]:
        """Convert module state to dictionary"""
        return {
            "name": self.name,
            "description": self.description,
            "enabled": self.config.enabled,
            "weight": self.get_weight(),
            "config": asdict(self.config),
            "performance": asdict(self.performance),
        }

"""Strategy module — state machine, trigger logic, signal engine, entry/exit rules."""

from .state_machine import StateMachine, StateContext, TriggerZone, resolve_triggers
from .signal_engine import SignalEngine
from .risk_guard import RiskGuard, RiskCheckResult
from .confirmation import BreakoutConfirmation, ConfirmationConfig, ConfirmationMode, ConfirmationResult
from .profiles import (
    StrategyMode, StrategyProfile, get_profile, get_profile_for_dte, get_all_profiles,
    PRE_EXPIRY_MOMENTUM, DTE1_HYBRID, EXPIRY_DAY_TRUE_LOTTERY,
)
from .dte_detector import DTEDetector
from .hysteresis import TriggerHysteresis

__all__ = [
    "StateMachine", "StateContext", "TriggerZone", "resolve_triggers",
    "SignalEngine",
    "RiskGuard", "RiskCheckResult",
    "BreakoutConfirmation", "ConfirmationConfig", "ConfirmationMode", "ConfirmationResult",
    "StrategyMode", "StrategyProfile", "get_profile", "get_profile_for_dte", "get_all_profiles",
    "PRE_EXPIRY_MOMENTUM", "DTE1_HYBRID", "EXPIRY_DAY_TRUE_LOTTERY",
    "DTEDetector",
]

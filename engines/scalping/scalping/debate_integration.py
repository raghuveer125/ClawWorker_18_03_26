"""
LLM Debate Integration for Scalping Agents.

Provides decorators and helpers for agents to use LLM debate validation.
Only triggers debate for high-value/high-risk decisions.
"""

import asyncio
import os
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, Optional

from .debate_client import DebateClient, DebateResult, get_debate_client

_active_bot_type: ContextVar[Optional[str]] = ContextVar("active_bot_type", default=None)
_debate_mode_overrides: Dict[str, str] = {}
_global_debate_mode: Optional[str] = None
_VALID_DEBATE_MODES = {"debate", "single", "off"}


@dataclass
class DebateConfig:
    """Configuration for debate integration."""
    enabled: bool = True
    min_risk_amount: float = 2000.0  # Only debate if risk > Rs.2000
    min_confidence_to_proceed: float = 60.0  # Require 60%+ confidence
    timeout_seconds: float = 60.0  # Max wait time for debate
    fallback_on_error: str = "proceed"  # "proceed" or "block" on debate error


# Global configuration
_debate_config = DebateConfig(
    enabled=os.getenv("SCALPING_DEBATE_ENABLED", "1") == "1",
    min_risk_amount=float(os.getenv("SCALPING_DEBATE_MIN_RISK", "2000")),
    min_confidence_to_proceed=float(os.getenv("SCALPING_DEBATE_MIN_CONFIDENCE", "60")),
)


def get_debate_config() -> DebateConfig:
    """Get current debate configuration."""
    return _debate_config


def set_active_bot_type(bot_type: Optional[str]) -> Optional[object]:
    """Track the currently running bot (for per-bot debate overrides)."""
    return _active_bot_type.set(bot_type)


def reset_active_bot_type(token: object) -> None:
    """Reset active bot context."""
    _active_bot_type.reset(token)


def set_agent_debate_mode(bot_type: str, mode: str) -> None:
    """Set debate mode override for a bot_type."""
    if mode not in _VALID_DEBATE_MODES:
        raise ValueError(f"Invalid debate mode: {mode}")
    _debate_mode_overrides[bot_type] = mode


def get_agent_debate_mode(bot_type: Optional[str]) -> Optional[str]:
    """Get debate mode override for a bot_type (None if not set)."""
    if not bot_type:
        return None
    return _debate_mode_overrides.get(bot_type)


def set_global_debate_mode(mode: str) -> None:
    """Set global debate mode override."""
    global _global_debate_mode, _debate_config
    if mode not in _VALID_DEBATE_MODES:
        raise ValueError(f"Invalid debate mode: {mode}")
    _global_debate_mode = mode
    _debate_config.enabled = mode != "off"


def get_global_debate_mode() -> Optional[str]:
    """Get global debate mode override."""
    return _global_debate_mode


def _resolve_debate_mode() -> str:
    """Resolve debate mode for current bot (override > global)."""
    if _global_debate_mode:
        return _global_debate_mode
    bot_type = _active_bot_type.get()
    override = get_agent_debate_mode(bot_type)
    if override:
        return override
    return "debate" if _debate_config.enabled else "off"


async def _single_llm_suggestion(
    decision_type: str,
    context: Dict[str, Any],
    provider: str = "openai",
) -> Optional[DebateResult]:
    """Get a single-model suggestion via debate backend."""
    try:
        client = get_debate_client()
        return await client.validate_single_decision(
            decision_type=decision_type,
            context=context,
            provider=provider,
        )
    except Exception:
        return None


def configure_debate(
    enabled: bool = None,
    min_risk_amount: float = None,
    min_confidence: float = None,
    timeout: float = None,
    fallback: str = None,
):
    """Update debate configuration."""
    global _debate_config
    if enabled is not None:
        _debate_config.enabled = enabled
    if min_risk_amount is not None:
        _debate_config.min_risk_amount = min_risk_amount
    if min_confidence is not None:
        _debate_config.min_confidence_to_proceed = min_confidence
    if timeout is not None:
        _debate_config.timeout_seconds = timeout
    if fallback is not None:
        _debate_config.fallback_on_error = fallback


async def check_debate_available() -> bool:
    """Check if LLM debate service is available."""
    if not _debate_config.enabled:
        return False

    try:
        client = get_debate_client()
        status = await client.check_status()
        return status.get("configured", False)
    except Exception:
        return False


async def debate_entry_decision(
    index: str,
    spot_price: float,
    direction: str,
    strike: int,
    option_type: str,
    premium: float,
    stop_loss: float,
    target: float,
    risk_amount: float,
    signal_strength: int = 50,
    momentum: str = "neutral",
    structure_break: bool = False,
    pcr: float = 1.0,
    vix: float = 15.0,
    capital_used_pct: float = 0.0,
    open_positions: int = 0,
) -> tuple[bool, str, DebateResult]:
    """
    Debate an entry decision.

    Returns:
        (should_proceed, reason, debate_result)
    """
    config = get_debate_config()

    decision_context = {
        "index": index,
        "spot_price": spot_price,
        "direction": direction,
        "strike": strike,
        "option_type": option_type,
        "premium": premium,
        "stop_loss": stop_loss,
        "target": target,
        "sl_pct": (premium - stop_loss) / premium * 100 if premium > 0 else 30,
        "target_pct": (target - premium) / premium * 100 if premium > 0 else 50,
        "risk_amount": risk_amount,
        "signal_strength": signal_strength,
        "momentum": momentum,
        "structure_break": structure_break,
        "pcr": pcr,
        "vix": vix,
        "capital_used_pct": capital_used_pct,
        "open_positions": open_positions,
        "timestamp": datetime.now().isoformat(),
    }

    mode = _resolve_debate_mode()
    if mode == "off":
        return True, "Debate disabled", None
    if mode == "single":
        result = await _single_llm_suggestion("entry", decision_context)
        return True, "Single GPT suggestion", result

    if risk_amount < config.min_risk_amount:
        return True, f"Risk Rs.{risk_amount:.0f} below threshold", None

    # Check if service available
    if not await check_debate_available():
        if config.fallback_on_error == "proceed":
            return True, "Debate service unavailable, proceeding", None
        else:
            return False, "Debate service unavailable, blocking", None

    # Run debate
    try:
        client = get_debate_client()
        result = await asyncio.wait_for(
            client.validate_trade_decision(
                decision_type="entry",
                context=decision_context,
            ),
            timeout=config.timeout_seconds
        )

        # Check consensus and confidence
        if result.decision == "APPROVE" and result.confidence >= config.min_confidence_to_proceed:
            return True, f"Debate approved ({result.confidence:.0f}% confidence)", result
        elif result.decision == "REJECT":
            return False, f"Debate rejected: {result.reasoning[:100]}", result
        else:
            # Uncertain - use fallback
            if config.fallback_on_error == "proceed":
                return True, f"Debate uncertain ({result.confidence:.0f}%), proceeding", result
            else:
                return False, f"Debate uncertain ({result.confidence:.0f}%), blocking", result

    except asyncio.TimeoutError:
        if config.fallback_on_error == "proceed":
            return True, "Debate timeout, proceeding", None
        else:
            return False, "Debate timeout, blocking", None
    except Exception as e:
        if config.fallback_on_error == "proceed":
            return True, f"Debate error: {e}, proceeding", None
        else:
            return False, f"Debate error: {e}, blocking", None


async def debate_exit_decision(
    index: str,
    entry_price: float,
    current_price: float,
    spot_price: float,
    unrealized_pnl: float,
    time_in_trade: str,
    exit_reason: str,
    momentum: str = "neutral",
    volume_spike: bool = False,
) -> tuple[bool, str, DebateResult]:
    """
    Debate an exit decision.

    Returns:
        (should_exit, reason, debate_result)
    """
    config = get_debate_config()
    decision_context = {
        "index": index,
        "entry_price": entry_price,
        "current_price": current_price,
        "spot_price": spot_price,
        "unrealized_pnl": unrealized_pnl,
        "pnl_pct": (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0,
        "time_in_trade": time_in_trade,
        "exit_reason": exit_reason,
        "momentum": momentum,
        "volume_spike": volume_spike,
    }

    mode = _resolve_debate_mode()
    if mode == "off":
        return True, "Debate disabled", None
    if mode == "single":
        result = await _single_llm_suggestion("exit", decision_context)
        return True, "Single GPT suggestion", result

    if not await check_debate_available():
        return True, "Debate unavailable, proceeding with exit", None

    try:
        client = get_debate_client()
        result = await asyncio.wait_for(
            client.validate_trade_decision(
                decision_type="exit",
                context=decision_context,
            ),
            timeout=config.timeout_seconds
        )

        if result.decision in ("EXIT_NOW", "APPROVE"):
            return True, f"Exit approved: {result.reasoning[:100]}", result
        elif result.decision == "PARTIAL_EXIT":
            return True, "Partial exit recommended", result
        else:
            return False, f"Hold recommended: {result.reasoning[:100]}", result

    except Exception as e:
        return True, f"Debate error, proceeding with exit: {e}", None


async def debate_risk_check(
    capital: float,
    used_capital: float,
    daily_pnl: float,
    daily_loss_limit: float,
    proposed_action: str,
    risk_amount: float,
    open_positions: int,
    correlation_risk: str = "low",
    concentration_risk: str = "low",
) -> tuple[bool, str, DebateResult]:
    """
    Debate a risk management decision.

    Returns:
        (is_allowed, reason, debate_result)
    """
    config = get_debate_config()

    decision_context = {
        "capital": capital,
        "used_capital": used_capital,
        "used_pct": used_capital / capital * 100 if capital > 0 else 0,
        "daily_pnl": daily_pnl,
        "daily_loss_limit": daily_loss_limit,
        "proposed_action": proposed_action,
        "risk_amount": risk_amount,
        "open_positions": open_positions,
        "correlation_risk": correlation_risk,
        "concentration_risk": concentration_risk,
    }

    mode = _resolve_debate_mode()
    if mode == "off":
        return True, "Debate disabled", None
    if mode == "single":
        result = await _single_llm_suggestion("risk_check", decision_context)
        return True, "Single GPT suggestion", result

    # Quick check - if daily loss limit hit, block without debate
    if abs(daily_pnl) >= daily_loss_limit * 0.9:
        return False, "Daily loss limit reached", None

    if not await check_debate_available():
        # Apply basic rules without debate
        if used_capital / capital > 0.5:
            return False, "Capital utilization too high", None
        return True, "Debate unavailable, basic checks passed", None

    try:
        client = get_debate_client()
        result = await asyncio.wait_for(
            client.validate_trade_decision(
                decision_type="risk_check",
                context=decision_context,
            ),
            timeout=config.timeout_seconds
        )

        if result.decision == "APPROVE":
            return True, "Risk check approved", result
        elif result.decision == "REDUCE_SIZE":
            return True, "Proceed with reduced size", result
        else:
            return False, f"Risk check failed: {result.reasoning[:100]}", result

    except Exception as e:
        # On error, apply conservative rules
        if risk_amount > capital * 0.1:
            return False, f"Risk amount too high (error: {e})", None
        return True, f"Debate error, basic checks passed: {e}", None


async def debate_strike_selection(
    index: str,
    spot_price: float,
    direction: str,
    strikes: list,
    recommended_strike: int,
    recommended_premium: float,
    recommended_delta: float,
    market_regime: str = "neutral",
    vix: float = 15.0,
) -> tuple[bool, str, DebateResult]:
    """
    Debate a strike selection decision.

    Returns:
        (should_use_strike, reason, debate_result)
    """
    config = get_debate_config()

    decision_context = {
        "index": index,
        "spot_price": spot_price,
        "direction": direction,
        "atm_strike": round(spot_price / 100) * 100,  # Approximate ATM
        "strikes": strikes[:5],  # Top 5 candidates
        "recommended_strike": recommended_strike,
        "recommended_type": direction,
        "recommended_premium": recommended_premium,
        "recommended_delta": recommended_delta,
        "market_regime": market_regime,
        "vix": vix,
    }

    mode = _resolve_debate_mode()
    if mode == "off":
        return True, "Debate disabled", None
    if mode == "single":
        result = await _single_llm_suggestion("strike_selection", decision_context)
        return True, "Single GPT suggestion", result

    if not await check_debate_available():
        return True, "Debate unavailable, proceeding", None

    try:
        client = get_debate_client()
        result = await asyncio.wait_for(
            client.validate_trade_decision(
                decision_type="strike_selection",
                context=decision_context,
            ),
            timeout=config.timeout_seconds
        )

        if result.decision in ("APPROVE", "CONSENSUS"):
            return True, f"Strike approved: {result.reasoning[:80]}", result
        elif result.decision == "SUGGEST_ALTERNATIVE":
            return True, f"Alternative suggested: {result.reasoning[:80]}", result
        else:
            return False, f"Strike rejected: {result.reasoning[:80]}", result

    except Exception as e:
        return True, f"Debate error: {e}, proceeding", None


async def debate_analysis(
    analysis_type: str,
    context: dict,
) -> tuple[bool, str, DebateResult]:
    """
    General debate function for analysis validation.

    Args:
        analysis_type: "structure", "momentum", "trap", "regime"
        context: Analysis context dict

    Returns:
        (is_valid, reason, debate_result)
    """
    config = get_debate_config()

    mode = _resolve_debate_mode()
    if mode == "off":
        return True, "Debate disabled", None
    if mode == "single":
        result = await _single_llm_suggestion(analysis_type, context)
        return True, "Single GPT suggestion", result

    if not await check_debate_available():
        return True, "Debate unavailable", None

    try:
        client = get_debate_client()
        result = await asyncio.wait_for(
            client.validate_trade_decision(
                decision_type=analysis_type,
                context=context,
            ),
            timeout=config.timeout_seconds
        )

        if result.confidence >= config.min_confidence_to_proceed:
            return True, f"Analysis validated ({result.confidence:.0f}%)", result
        else:
            return False, f"Low confidence ({result.confidence:.0f}%): {result.reasoning[:80]}", result

    except Exception as e:
        return True, f"Debate error: {e}", None


def with_debate_validation(decision_type: str = "entry"):
    """
    Decorator to add debate validation to agent methods.

    Usage:
        @with_debate_validation("entry")
        async def generate_signal(self, context):
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            # Call original function
            result = await func(self, *args, **kwargs)

            # If result suggests action, validate with debate
            if hasattr(result, "output") and result.output:
                output = result.output
                if isinstance(output, dict) and output.get("action"):
                    # Add debate validation to output
                    output["debate_validated"] = False
                    output["debate_reason"] = "Not yet validated"

            return result

        return wrapper
    return decorator


# Export key functions
__all__ = [
    "DebateConfig",
    "DebateResult",
    "get_debate_config",
    "configure_debate",
    "set_active_bot_type",
    "reset_active_bot_type",
    "set_agent_debate_mode",
    "get_agent_debate_mode",
    "set_global_debate_mode",
    "get_global_debate_mode",
    "check_debate_available",
    "debate_entry_decision",
    "debate_exit_decision",
    "debate_risk_check",
    "debate_strike_selection",
    "debate_analysis",
    "with_debate_validation",
]

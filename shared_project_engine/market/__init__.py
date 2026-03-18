"""Shared market-hours helpers plus the shared market-data adapter."""

import os
from datetime import datetime, time
from typing import TYPE_CHECKING, Dict, Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# =============================================================================
# TIMEZONE
# =============================================================================

IST = ZoneInfo("Asia/Kolkata")

# =============================================================================
# MARKET HOURS CONFIGURATION (IST)
# =============================================================================

MARKET_HOURS: Dict[str, Any] = {
    # Core trading hours
    "open_hour": 9,
    "open_minute": 15,
    "close_hour": 15,
    "close_minute": 30,

    # Pre/post market buffers (for data collection)
    "pre_open_hour": 9,
    "pre_open_minute": 0,
    "post_close_hour": 15,
    "post_close_minute": 45,

    # Session definitions
    "sessions": {
        "pre_open": {"start": (9, 0), "end": (9, 15), "status": "PRE_OPEN"},
        "opening": {"start": (9, 15), "end": (9, 30), "status": "OPENING_VOLATILE"},
        "morning": {"start": (9, 30), "end": (12, 0), "status": "OPEN"},
        "midday": {"start": (12, 0), "end": (14, 0), "status": "OPEN"},
        "afternoon": {"start": (14, 0), "end": (15, 0), "status": "OPEN"},
        "closing": {"start": (15, 0), "end": (15, 30), "status": "CLOSING_VOLATILE"},
        "post_close": {"start": (15, 30), "end": (15, 45), "status": "POST_CLOSE"},
    },

    # Trading days (0=Monday, 6=Sunday)
    "trading_days": [0, 1, 2, 3, 4],  # Monday to Friday

    # Timezone
    "timezone": "Asia/Kolkata",
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _now_ist() -> datetime:
    """Get current time in IST."""
    return datetime.now(IST)


def _to_minutes(hour: int, minute: int) -> int:
    """Convert hour:minute to minutes since midnight."""
    return hour * 60 + minute


def is_trading_day(dt: Optional[datetime] = None) -> bool:
    """Check if given date is a trading day (Mon-Fri)."""
    if dt is None:
        dt = _now_ist()
    return dt.weekday() in MARKET_HOURS["trading_days"]


def is_market_open(dt: Optional[datetime] = None, include_buffer: bool = False) -> bool:
    """
    Check if the market is currently open.

    Args:
        dt: Datetime to check (defaults to now in IST)
        include_buffer: If True, includes pre/post market buffer times

    Returns:
        True if market is open
    """
    if dt is None:
        dt = _now_ist()

    # Weekend check
    if not is_trading_day(dt):
        return False

    now_mins = _to_minutes(dt.hour, dt.minute)

    if include_buffer:
        open_mins = _to_minutes(MARKET_HOURS["pre_open_hour"], MARKET_HOURS["pre_open_minute"])
        close_mins = _to_minutes(MARKET_HOURS["post_close_hour"], MARKET_HOURS["post_close_minute"])
    else:
        open_mins = _to_minutes(MARKET_HOURS["open_hour"], MARKET_HOURS["open_minute"])
        close_mins = _to_minutes(MARKET_HOURS["close_hour"], MARKET_HOURS["close_minute"])

    return open_mins <= now_mins <= close_mins


def is_within_buffer_hours(dt: Optional[datetime] = None) -> bool:
    """
    Check if within extended market hours (includes pre/post buffer).
    Use this for data collection that should run slightly before/after market.
    """
    return is_market_open(dt, include_buffer=True)


def get_session_info(dt: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Get detailed session information.

    Returns:
        Dict with keys: status, session_name, is_volatile, recommended_action
    """
    if dt is None:
        dt = _now_ist()

    # Weekend
    if not is_trading_day(dt):
        return {
            "status": "CLOSED",
            "session_name": "weekend",
            "is_volatile": False,
            "recommended_action": "WAIT",
            "message": "Market closed (weekend)",
        }

    now_mins = _to_minutes(dt.hour, dt.minute)

    # Check each session
    for session_name, session_info in MARKET_HOURS["sessions"].items():
        start_mins = _to_minutes(*session_info["start"])
        end_mins = _to_minutes(*session_info["end"])

        if start_mins <= now_mins < end_mins:
            is_volatile = "VOLATILE" in session_info["status"]

            if session_info["status"] == "PRE_OPEN":
                action = "WAIT for 9:15 AM"
            elif is_volatile:
                action = "CAUTION - High volatility"
            else:
                action = "TRADE"

            return {
                "status": session_info["status"],
                "session_name": session_name,
                "is_volatile": is_volatile,
                "recommended_action": action,
                "message": f"Session: {session_name}",
            }

    # Before market
    pre_open_mins = _to_minutes(MARKET_HOURS["pre_open_hour"], MARKET_HOURS["pre_open_minute"])
    if now_mins < pre_open_mins:
        return {
            "status": "CLOSED",
            "session_name": "before_market",
            "is_volatile": False,
            "recommended_action": "WAIT",
            "message": f"Market opens at {MARKET_HOURS['open_hour']}:{MARKET_HOURS['open_minute']:02d}",
        }

    # After market
    return {
        "status": "CLOSED",
        "session_name": "after_market",
        "is_volatile": False,
        "recommended_action": "WAIT",
        "message": "Market closed for the day",
    }


def get_market_open_time() -> time:
    """Get market opening time."""
    return time(MARKET_HOURS["open_hour"], MARKET_HOURS["open_minute"])


def get_market_close_time() -> time:
    """Get market closing time."""
    return time(MARKET_HOURS["close_hour"], MARKET_HOURS["close_minute"])


def get_buffer_open_time() -> time:
    """Get pre-market buffer start time (for data collection)."""
    return time(MARKET_HOURS["pre_open_hour"], MARKET_HOURS["pre_open_minute"])


def get_buffer_close_time() -> time:
    """Get post-market buffer end time (for data collection)."""
    return time(MARKET_HOURS["post_close_hour"], MARKET_HOURS["post_close_minute"])


# Export for shell scripts (called via python -c)
def print_market_hours_for_shell():
    """Print market hours as shell variables."""
    print(f"MARKET_OPEN_HOUR={MARKET_HOURS['open_hour']}")
    print(f"MARKET_OPEN_MIN={MARKET_HOURS['open_minute']}")
    print(f"MARKET_CLOSE_HOUR={MARKET_HOURS['close_hour']}")
    print(f"MARKET_CLOSE_MIN={MARKET_HOURS['close_minute']}")
    print(f"PRE_OPEN_HOUR={MARKET_HOURS['pre_open_hour']}")
    print(f"PRE_OPEN_MIN={MARKET_HOURS['pre_open_minute']}")
    print(f"POST_CLOSE_HOUR={MARKET_HOURS['post_close_hour']}")
    print(f"POST_CLOSE_MIN={MARKET_HOURS['post_close_minute']}")

if TYPE_CHECKING:
    from .adapter import MarketDataAdapter
    from .client import MarketDataClient
    from .service import MarketDataService
    from .indicator_adapter import (
        IndicatorDataAdapter,
        IndicatorSnapshot,
        MomentumIndicators,
        MicrostructureIndicators,
        LiquidityIndicators,
        GreeksSnapshot,
        MarketRegime,
        OptionFlowIndicators,
    )


def build_runtime_report(metrics, top=10):
    from .report import build_report

    return build_report(metrics, top)


def __getattr__(name):
    if name == "MarketDataAdapter":
        from .adapter import MarketDataAdapter

        return MarketDataAdapter
    if name == "MarketDataClient":
        from .client import MarketDataClient

        return MarketDataClient
    if name == "MarketDataService":
        from .service import MarketDataService

        return MarketDataService
    if name == "IndicatorDataAdapter":
        from .indicator_adapter import IndicatorDataAdapter

        return IndicatorDataAdapter
    if name == "IndicatorSnapshot":
        from .indicator_adapter import IndicatorSnapshot

        return IndicatorSnapshot
    if name in (
        "MomentumIndicators",
        "MicrostructureIndicators",
        "LiquidityIndicators",
        "GreeksSnapshot",
        "MarketRegime",
        "OptionFlowIndicators",
    ):
        from . import indicator_adapter

        return getattr(indicator_adapter, name)
    raise AttributeError(name)


__all__ = [
    # Timezone and hours
    "IST",
    "MARKET_HOURS",
    # Adapters
    "MarketDataAdapter",
    "MarketDataClient",
    "MarketDataService",
    "IndicatorDataAdapter",
    # Indicator data classes
    "IndicatorSnapshot",
    "MomentumIndicators",
    "MicrostructureIndicators",
    "LiquidityIndicators",
    "GreeksSnapshot",
    "MarketRegime",
    "OptionFlowIndicators",
    # Helpers
    "build_runtime_report",
    "is_market_open",
    "is_within_buffer_hours",
    "is_trading_day",
    "get_session_info",
    "get_market_open_time",
    "get_market_close_time",
    "get_buffer_open_time",
    "get_buffer_close_time",
    "print_market_hours_for_shell",
]

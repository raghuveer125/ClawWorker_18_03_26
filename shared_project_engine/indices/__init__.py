"""
Centralized Index Configuration
Shared across ClawWork, fyersN7, and Frontend
"""

from .config import (
    VIX_SYMBOL,
    INDEX_ALIASES,
    INDEX_CONFIG,
    ACTIVE_INDICES,
    canonicalize_index_name,
    get_index_config,
    get_market_index_config,
    get_watchlist,
    get_all_watchlists,
    get_expiry_info,
    is_expiry_today,
    get_expiry_schedule,
    get_todays_expiring_indices,
    export_for_frontend,
)

__all__ = [
    "VIX_SYMBOL",
    "INDEX_ALIASES",
    "INDEX_CONFIG",
    "ACTIVE_INDICES",
    "canonicalize_index_name",
    "get_index_config",
    "get_market_index_config",
    "get_watchlist",
    "get_all_watchlists",
    "get_expiry_info",
    "is_expiry_today",
    "get_expiry_schedule",
    "get_todays_expiring_indices",
    "export_for_frontend",
]

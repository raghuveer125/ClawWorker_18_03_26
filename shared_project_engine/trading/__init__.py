"""
Shared Trading Configuration
=============================
Centralized exchange-defined trading parameters.

This module fetches lot sizes DYNAMICALLY from the exchange via Fyers API,
with fallback to cached/default values when API is unavailable.

Usage:
    from shared_project_engine.trading import get_lot_size, get_strike_gap

    lot = get_lot_size("NIFTY50")  # Fetches from exchange or cache
    gap = get_strike_gap("BANKNIFTY")  # 100
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional, Any

# =============================================================================
# CACHE CONFIGURATION
# =============================================================================

# Cache lot sizes for 24 hours (they rarely change)
CACHE_TTL_SECONDS = 86400

# Cache file location
_CACHE_DIR = Path(__file__).parent / ".cache"
_LOT_SIZE_CACHE_FILE = _CACHE_DIR / "lot_sizes.json"

# =============================================================================
# FALLBACK VALUES (used when API unavailable)
# Updated these only when exchange announces changes
# =============================================================================

_FALLBACK_LOT_SIZES: Dict[str, int] = {
    "NIFTY50": 65,       # Verified Mar 2026
    "BANKNIFTY": 30,     # Verified Mar 2026
    "FINNIFTY": 60,      # Verified Mar 2026
    "MIDCPNIFTY": 120,   # Verified Mar 2026
    "SENSEX": 20,        # Verified Mar 2026
}

STRIKE_GAPS: Dict[str, int] = {
    "NIFTY50": 50,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
    "MIDCPNIFTY": 25,
    "SENSEX": 100,
}

# In-memory cache
_lot_size_cache: Dict[str, Any] = {
    "data": {},
    "timestamp": 0,
}

# =============================================================================
# DYNAMIC LOT SIZE FETCHING
# =============================================================================

def _load_cache() -> Dict[str, int]:
    """Load lot sizes from disk cache."""
    try:
        if _LOT_SIZE_CACHE_FILE.exists():
            with open(_LOT_SIZE_CACHE_FILE, "r") as f:
                cached = json.load(f)
                if time.time() - cached.get("timestamp", 0) < CACHE_TTL_SECONDS:
                    return cached.get("data", {})
    except Exception:
        pass
    return {}


def _save_cache(data: Dict[str, int]) -> None:
    """Save lot sizes to disk cache."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_LOT_SIZE_CACHE_FILE, "w") as f:
            json.dump({"data": data, "timestamp": time.time()}, f)
    except Exception:
        pass


def _fetch_all_lot_sizes_from_api() -> Dict[str, int]:
    """
    Fetch all lot sizes from Fyers API.

    Uses the FyersClient.get_lot_sizes() method which queries option chain
    for each index.
    """
    try:
        from shared_project_engine.auth import get_client
        client = get_client()
        return client.get_lot_sizes()
    except Exception:
        return {}


def refresh_lot_sizes(force: bool = False) -> Dict[str, int]:
    """
    Refresh lot sizes from exchange API.

    Args:
        force: If True, ignores cache and fetches fresh data

    Returns:
        Dict of index -> lot size
    """
    global _lot_size_cache

    # Check in-memory cache first
    if not force and time.time() - _lot_size_cache["timestamp"] < CACHE_TTL_SECONDS:
        if _lot_size_cache["data"]:
            return _lot_size_cache["data"]

    # Check disk cache
    if not force:
        cached = _load_cache()
        if cached:
            _lot_size_cache = {"data": cached, "timestamp": time.time()}
            return cached

    # Fetch from API
    result = _fetch_all_lot_sizes_from_api()

    # If we got any data, cache it
    if result:
        _lot_size_cache = {"data": result, "timestamp": time.time()}
        _save_cache(result)
        return result

    # Return fallback if API failed
    return _FALLBACK_LOT_SIZES.copy()


def get_lot_size(index: str, default: Optional[int] = None, use_api: bool = True) -> int:
    """
    Get lot size for an index.

    Tries to fetch from exchange API first, falls back to cached/default values.

    Args:
        index: Index name (NIFTY50, BANKNIFTY, etc.)
        default: Default lot size if not found (if None, uses fallback)
        use_api: If True, tries to fetch from API (default True)

    Returns:
        Lot size (number of shares per lot)
    """
    idx = index.upper()

    # Check in-memory cache first (fast path)
    if _lot_size_cache["data"] and idx in _lot_size_cache["data"]:
        if time.time() - _lot_size_cache["timestamp"] < CACHE_TTL_SECONDS:
            return _lot_size_cache["data"][idx]

    # Try to refresh from API/disk cache
    if use_api:
        try:
            lots = refresh_lot_sizes(force=False)
            if idx in lots:
                return lots[idx]
        except Exception:
            pass

    # Use fallback
    if default is not None:
        return _FALLBACK_LOT_SIZES.get(idx, default)
    return _FALLBACK_LOT_SIZES.get(idx, 50)


def get_strike_gap(index: str, default: int = 50) -> int:
    """
    Get strike price gap for an index.

    Strike gaps are more stable than lot sizes, so we use static values.

    Args:
        index: Index name (NIFTY50, BANKNIFTY, etc.)
        default: Default strike gap if index not found

    Returns:
        Strike gap in points
    """
    return STRIKE_GAPS.get(index.upper(), default)


def get_all_indices() -> list:
    """Get list of all supported indices."""
    return list(_FALLBACK_LOT_SIZES.keys())


# For backwards compatibility, expose LOT_SIZES dict
# Note: This is a snapshot and may be stale. Prefer get_lot_size() function.
LOT_SIZES = _FALLBACK_LOT_SIZES.copy()


__all__ = [
    "LOT_SIZES",
    "STRIKE_GAPS",
    "get_lot_size",
    "get_strike_gap",
    "get_all_indices",
    "refresh_lot_sizes",
]

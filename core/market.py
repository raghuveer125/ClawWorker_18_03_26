"""
Shared market constants and utilities for ClawWorker.

Single source of truth for IST timezone, market hours, and index defaults.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional
from zoneinfo import ZoneInfo


# ── Timezone ─────────────────────────────────────────────────────────────────

IST = ZoneInfo("Asia/Kolkata")


# ── Market hours ─────────────────────────────────────────────────────────────

MARKET_OPEN_HOUR, MARKET_OPEN_MIN = 9, 0
MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN = 15, 45


def is_market_open(now: Optional[dt.datetime] = None) -> bool:
    """Check if Indian equity market is open (9:00-15:45 IST, Mon-Fri)."""
    now = now or dt.datetime.now(IST)
    if now.weekday() >= 5:
        return False
    now_mins = now.hour * 60 + now.minute
    return (MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MIN) <= now_mins <= (MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MIN)


# ── Index configuration fallbacks ────────────────────────────────────────────
# These are FALLBACKS only — canonical values come from
# shared_project_engine.indices.INDEX_CONFIG when available.

INDEX_STRIKE_GAPS = {
    "NIFTY50": 50,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
    "SENSEX": 100,
    "MIDCPNIFTY": 25,
}

INDEX_LOT_SIZES = {
    "NIFTY50": 25,
    "BANKNIFTY": 15,
    "FINNIFTY": 25,
    "SENSEX": 10,
    "MIDCPNIFTY": 50,
}


def get_strike_gap(index: str) -> int:
    """Get strike gap for an index (fallback values)."""
    return INDEX_STRIKE_GAPS.get(index, 50)


def get_lot_size(index: str) -> int:
    """Get lot size for an index (fallback values)."""
    return INDEX_LOT_SIZES.get(index, 50)

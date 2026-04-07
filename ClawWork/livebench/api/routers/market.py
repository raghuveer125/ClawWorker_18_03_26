"""Live market data + expiry endpoints (~3 routes)."""

import logging
from datetime import datetime

from fastapi import APIRouter

from ..deps import (
    _build_live_market_response,
    _build_market_client,
    _load_market_index_symbols,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["market"])


@router.get("/indices/config")
async def get_indices_config():
    """Get centralized indices configuration."""
    from ..routers.pipelines import _get_cached_indices_config
    return _get_cached_indices_config()


@router.get("/indices/expiry-schedule")
async def get_index_expiry_schedule():
    """Get exchange-backed expiry schedule for all indices."""
    try:
        from shared_project_engine.indices import (
            get_expiry_snapshot,
            INDEX_CONFIG,
            ACTIVE_INDICES,
        )

        expiry_snapshot = get_expiry_snapshot()

        return {
            "expirySchedule": expiry_snapshot["expirySchedule"],
            "todaysExpiry": expiry_snapshot["todaysExpiry"],
            "sourceStatus": expiry_snapshot["sourceStatus"],
            "fetchedAt": expiry_snapshot["fetchedAt"],
            "activeIndices": ACTIVE_INDICES,
            "indices": list(INDEX_CONFIG.keys()),
            "timestamp": datetime.now().isoformat(),
        }
    except ImportError as e:
        logger.warning(f"Could not import shared_project_engine: {e}")
        fallback_indices = ["SENSEX", "NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
        fallback_exchanges = {
            "SENSEX": "BSE",
            "NIFTY50": "NSE",
            "BANKNIFTY": "NSE",
            "FINNIFTY": "NSE",
            "MIDCPNIFTY": "NSE",
        }
        fallback_schedule = {
            name: {
                "exchange": fallback_exchanges.get(name),
                "source": "unavailable",
                "next_expiry": None,
                "weekday": None,
                "weekday_name": None,
                "weekday_short": None,
                "is_expiry_today": False,
            }
            for name in fallback_indices
        }
        return {
            "expirySchedule": fallback_schedule,
            "todaysExpiry": [],
            "sourceStatus": "unavailable",
            "fetchedAt": None,
            "activeIndices": ["SENSEX", "NIFTY50", "BANKNIFTY", "FINNIFTY"],
            "indices": list(fallback_schedule.keys()),
            "timestamp": datetime.now().isoformat(),
        }


@router.get("/market/live")
async def get_live_market_data():
    """
    Fetch live market data for all indices through the shared market adapter.
    Falls back to fyersN7 decision journals if upstream data is unavailable.
    """
    try:
        client = _build_market_client()
        symbols, index_symbol_map, expected_indices = _load_market_index_symbols()

        if not symbols:
            return {"error": "No index symbols configured", "indices": {}}

        result = client.quotes(",".join(symbols))
        return _build_live_market_response(result, index_symbol_map, expected_indices)

    except ImportError as e:
        logger.warning(f"Could not import shared_project_engine: {e}")
        return {
            "error": f"Missing dependency: {e}",
            "indices": {},
            "market_open": False,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Error fetching live market data: {e}")
        return {
            "error": str(e),
            "indices": {},
            "market_open": False,
            "timestamp": datetime.now().isoformat(),
        }

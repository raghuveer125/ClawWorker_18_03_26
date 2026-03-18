"""Compatibility wrapper around the shared FYERS client."""

from __future__ import annotations

import sys
from pathlib import Path

_SHARED_ROOT = Path(__file__).resolve().parents[3]
if str(_SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(_SHARED_ROOT))

from shared_project_engine.auth import FyersClient as SharedFyersClient
from shared_project_engine.market import MarketDataClient as SharedMarketDataClient


class FyersClient(SharedFyersClient):
    """Backwards-compatible alias for the shared FYERS client."""


class MarketDataClient(SharedMarketDataClient):
    """Backwards-compatible alias for the shared market-data client."""

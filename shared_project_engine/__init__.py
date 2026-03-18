"""
Shared Project Engine for ClawWork + fyersN7 Integration
=========================================================

This module provides shared components for both ClawWork and fyersN7 projects.

Modules:
    auth     - FYERS authentication and API client
    indices  - Centralized index configuration (symbols, expiry, watchlists)
    market   - Shared market adapter and market-hours utilities
    services - Port and URL configuration for services

Usage:
    from shared_project_engine.auth import quick_login, get_client
    from shared_project_engine.indices import INDEX_CONFIG, ACTIVE_INDICES, get_watchlist
    from shared_project_engine.services import API_PORT, FRONTEND_PORT, get_api_url
"""

from . import auth
from . import indices
from . import market
from . import services

__all__ = ["auth", "indices", "market", "services"]
__version__ = "1.0.0"

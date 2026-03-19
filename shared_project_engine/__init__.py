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

__all__ = ["auth", "indices", "market", "services"]
__version__ = "1.0.0"


def __getattr__(name):
    """Import subpackages lazily so optional dependencies stay optional."""
    if name in __all__:
        import importlib

        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

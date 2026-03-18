"""
Shared Services Configuration
==============================
Centralized configuration for ports, URLs, and service settings.

Usage:
    from shared_project_engine.services import API_PORT, FRONTEND_PORT, get_api_url

    # Get ports
    print(API_PORT)  # 8001
    print(FRONTEND_PORT)  # 3001

    # Get URLs
    api_url = get_api_url()  # http://localhost:8001/api
"""

import os
from typing import Optional

# =============================================================================
# PORT CONFIGURATION
# =============================================================================

# Backend API server
API_PORT: int = int(os.environ.get("API_PORT", "8001"))

# Frontend dashboard
FRONTEND_PORT: int = int(os.environ.get("FRONTEND_PORT", "3001"))

# Auth callback server (used during OAuth)
AUTH_CALLBACK_PORT: int = int(os.environ.get("AUTH_CALLBACK_PORT", "8080"))

# Localhost market-data adapter
MARKET_ADAPTER_PORT: int = int(os.environ.get("MARKET_ADAPTER_PORT", "8765"))

# =============================================================================
# HOST CONFIGURATION
# =============================================================================

API_HOST: str = os.environ.get("API_HOST", "0.0.0.0")
FRONTEND_HOST: str = os.environ.get("FRONTEND_HOST", "0.0.0.0")
AUTH_CALLBACK_HOST: str = os.environ.get("AUTH_CALLBACK_HOST", "127.0.0.1")
MARKET_ADAPTER_HOST: str = os.environ.get("MARKET_ADAPTER_HOST", "127.0.0.1")

# =============================================================================
# URL HELPERS
# =============================================================================

def get_api_url(host: str = "localhost", port: Optional[int] = None) -> str:
    """Get the API base URL."""
    p = port or API_PORT
    return f"http://{host}:{p}/api"


def get_frontend_url(host: str = "localhost", port: Optional[int] = None) -> str:
    """Get the frontend URL."""
    p = port or FRONTEND_PORT
    return f"http://{host}:{p}"


def get_auth_redirect_uri(host: Optional[str] = None, port: Optional[int] = None) -> str:
    """Get the OAuth redirect URI."""
    h = host or AUTH_CALLBACK_HOST
    p = port or AUTH_CALLBACK_PORT
    return f"http://{h}:{p}/"


def get_market_adapter_url(host: Optional[str] = None, port: Optional[int] = None) -> str:
    """Get the localhost market adapter base URL."""
    h = host or MARKET_ADAPTER_HOST
    p = port or MARKET_ADAPTER_PORT
    return f"http://{h}:{p}"


# Default redirect URI (for backwards compatibility)
DEFAULT_REDIRECT_URI = get_auth_redirect_uri()

# =============================================================================
# SERVICE DEFAULTS
# =============================================================================

SERVICE_CONFIG = {
    "api": {
        "port": API_PORT,
        "host": API_HOST,
        "name": "Backend API",
    },
    "frontend": {
        "port": FRONTEND_PORT,
        "host": FRONTEND_HOST,
        "name": "Frontend Dashboard",
    },
    "auth_callback": {
        "port": AUTH_CALLBACK_PORT,
        "host": AUTH_CALLBACK_HOST,
        "name": "Auth Callback Server",
    },
    "market_adapter": {
        "port": MARKET_ADAPTER_PORT,
        "host": MARKET_ADAPTER_HOST,
        "name": "Market Adapter",
        "url": get_market_adapter_url(),
    },
}

__all__ = [
    "API_PORT",
    "FRONTEND_PORT",
    "AUTH_CALLBACK_PORT",
    "MARKET_ADAPTER_PORT",
    "API_HOST",
    "FRONTEND_HOST",
    "AUTH_CALLBACK_HOST",
    "MARKET_ADAPTER_HOST",
    "get_api_url",
    "get_frontend_url",
    "get_auth_redirect_uri",
    "get_market_adapter_url",
    "DEFAULT_REDIRECT_URI",
    "SERVICE_CONFIG",
]

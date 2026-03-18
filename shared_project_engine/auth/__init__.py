"""
Shared Authentication Module
============================

Unified FYERS authentication for ClawWork and fyersN7.

Quick Start:
    # Login (opens browser)
    from shared_project_engine.auth import quick_login
    token = quick_login()

    # Use API client
    from shared_project_engine.auth import get_client
    client = get_client()
    quotes = client.quotes("NSE:NIFTY50-INDEX")

    # Or use classes directly
    from shared_project_engine.auth import FyersAuth, FyersClient, EnvManager
    auth = FyersAuth(client_id="XXX", secret_key="YYY")
    token = auth.login()

Environment Variables:
    FYERS_CLIENT_ID     - App ID from FYERS
    FYERS_SECRET_KEY    - Secret key from FYERS
    FYERS_REDIRECT_URI  - OAuth redirect URI (default: http://127.0.0.1:8080/)
    FYERS_ACCESS_TOKEN  - Access token (auto-saved after login)
"""

from .config import (
    FYERS_API_BASE_URL,
    FYERS_AUTH_URL,
    FYERS_TOKEN_URL,
    DEFAULT_REDIRECT_URI,
    ENV_VARS,
)

from .env_manager import (
    EnvManager,
    find_env_file,
)

from .fyers_auth import (
    FyersAuth,
    quick_login,
)

from .fyers_client import (
    FyersClient,
    get_client,
)

__all__ = [
    # Config
    "FYERS_API_BASE_URL",
    "FYERS_AUTH_URL",
    "FYERS_TOKEN_URL",
    "DEFAULT_REDIRECT_URI",
    "ENV_VARS",
    # Env Manager
    "EnvManager",
    "find_env_file",
    # Auth
    "FyersAuth",
    "quick_login",
    # Client
    "FyersClient",
    "get_client",
]

__version__ = "1.0.0"

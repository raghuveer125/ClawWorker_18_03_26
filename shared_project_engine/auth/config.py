"""
FYERS Authentication Configuration Constants
Shared across ClawWork and fyersN7
"""

# API Endpoints
FYERS_API_BASE_URL = "https://api-t1.fyers.in/api/v3"
FYERS_AUTH_URL = "https://api-t1.fyers.in/api/v3/generate-authcode"
FYERS_TOKEN_URL = "https://api-t1.fyers.in/api/v3/validate-authcode"

# Default callback settings
DEFAULT_REDIRECT_HOST = "127.0.0.1"
DEFAULT_REDIRECT_PORT = 8080
DEFAULT_REDIRECT_URI = f"http://{DEFAULT_REDIRECT_HOST}:{DEFAULT_REDIRECT_PORT}/"

# Timeout settings
DEFAULT_AUTH_TIMEOUT_SEC = 180
DEFAULT_REQUEST_TIMEOUT_SEC = 30

# Environment variable names
ENV_VARS = {
    "client_id": "FYERS_CLIENT_ID",
    "secret_key": "FYERS_SECRET_KEY",
    "redirect_uri": "FYERS_REDIRECT_URI",
    "access_token": "FYERS_ACCESS_TOKEN",
    "insecure": "FYERS_INSECURE",
    "ca_bundle": "FYERS_CA_BUNDLE",
    "app_id": "FYERS_APP_ID",  # Alias for client_id (ClawWork uses this)
    "secret_id": "FYERS_SECRET_ID",  # Alias for secret_key (ClawWork uses this)
}

# Default env file locations (relative to project root)
DEFAULT_ENV_FILES = [
    ".env",
    ".fyers.env",
]

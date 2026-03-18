"""
FYERS REST API Client
Thin wrapper for FYERS v3 API calls

Combines best features from:
- ClawWork: Endpoint fallbacks, error extraction
- fyersN7: Direct fyersModel usage where needed
"""

import json
import os
from typing import Any, Dict, List, Optional

import requests

from .config import FYERS_API_BASE_URL, DEFAULT_REQUEST_TIMEOUT_SEC, ENV_VARS
from .env_manager import EnvManager

# Try to import fyers SDK (optional, for better quote support)
try:
    from fyers_apiv3 import fyersModel
    HAS_FYERS_SDK = True
except ImportError:
    HAS_FYERS_SDK = False


class FyersClient:
    """
    FYERS v3 REST API Client.

    Handles authentication headers, endpoint fallbacks, and error handling.

    Usage:
        client = FyersClient()
        quotes = client.quotes("NSE:NIFTY50-INDEX")
        profile = client.profile()
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        client_id: Optional[str] = None,
        api_base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        env_file: Optional[str] = None,
    ):
        """
        Initialize FyersClient.

        Args:
            access_token: FYERS access token. Falls back to env var.
            client_id: FYERS App ID. Falls back to env var.
            api_base_url: API base URL. Defaults to v3 API.
            timeout_seconds: Request timeout.
            env_file: Path to .env file for credentials.
        """
        # Always try to load from env file (searches cwd if not specified)
        env_manager = EnvManager(env_file=env_file)
        creds = env_manager.load()
        access_token = access_token or creds.get("access_token")
        client_id = client_id or creds.get("client_id")

        self.api_base_url = (
            api_base_url or
            os.getenv("FYERS_API_BASE_URL") or
            FYERS_API_BASE_URL
        ).rstrip("/")

        self.api_root_url = self._derive_api_root(self.api_base_url)

        self.access_token = (
            access_token or
            os.getenv(ENV_VARS["access_token"])
        )

        self.client_id = (
            client_id or
            os.getenv(ENV_VARS["client_id"]) or
            os.getenv(ENV_VARS["app_id"])
        )

        self.timeout_seconds = timeout_seconds or float(
            os.getenv("FYERS_TIMEOUT_SECONDS", str(DEFAULT_REQUEST_TIMEOUT_SEC))
        )

    @staticmethod
    def _derive_api_root(base_url: str) -> str:
        """Derive host root from API URL."""
        for marker in ["/api/v3", "/api/v2", "/api"]:
            if marker in base_url:
                return base_url.split(marker, 1)[0].rstrip("/")
        return base_url.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        """Build request headers with authentication."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if self.access_token:
            token = self.access_token.strip()
            if self.client_id and self.client_id.strip():
                # FYERS native format: APP_ID:ACCESS_TOKEN
                headers["Authorization"] = f"{self.client_id.strip()}:{token}"
            else:
                # Fallback to Bearer
                if not token.lower().startswith("bearer "):
                    token = f"Bearer {token}"
                headers["Authorization"] = token

        return headers

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request to FYERS API."""
        if not self.access_token:
            return {
                "success": False,
                "error": "FYERS_ACCESS_TOKEN not set",
                "message": "Run authentication first or set FYERS_ACCESS_TOKEN",
            }

        # Resolve URL
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{self.api_base_url}/{path.lstrip('/')}"

        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=self._headers(),
                json=payload,
                params=params,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {e}",
                "url": url,
            }

        # Parse response
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}

        success = 200 <= response.status_code < 300
        result = {
            "success": success,
            "status_code": response.status_code,
            "url": url,
            "data": body,
        }

        if not success:
            result["error"] = self._extract_error(body)

        return result

    def _data_url(self, path: str) -> str:
        """Build a FYERS market-data URL outside the API v3 root."""
        return f"{self.api_root_url}/data/{path.lstrip('/')}"

    @staticmethod
    def _extract_error(body: Any) -> str:
        """Extract error message from response body."""
        if isinstance(body, dict):
            for key in ("message", "error", "reason", "s"):
                value = body.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return json.dumps(body)
        return str(body)

    def is_authenticated(self) -> bool:
        """Check if client has valid authentication."""
        if not self.access_token:
            return False
        result = self.profile()
        return result.get("success", False)

    # ========== Account APIs ==========

    def profile(self) -> Dict[str, Any]:
        """Get user profile."""
        return self._request("GET", "/profile")

    def funds(self) -> Dict[str, Any]:
        """Get fund balance."""
        return self._request("GET", "/funds")

    def holdings(self) -> Dict[str, Any]:
        """Get holdings."""
        return self._request("GET", "/holdings")

    def positions(self) -> Dict[str, Any]:
        """Get positions."""
        return self._request("GET", "/positions")

    # ========== Market Data APIs ==========

    def quotes(self, symbols: str) -> Dict[str, Any]:
        """
        Get quotes for symbols.

        Args:
            symbols: Comma-separated symbol string (e.g., "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX")

        Returns:
            Quote data or error.
        """
        # Try SDK first (most reliable)
        if HAS_FYERS_SDK and self.access_token and self.client_id:
            try:
                fyers = fyersModel.FyersModel(
                    client_id=self.client_id,
                    is_async=False,
                    token=self.access_token,
                    log_path="",
                )
                resp = fyers.quotes(data={"symbols": symbols})
                if isinstance(resp, dict):
                    if resp.get("s") == "ok" or resp.get("code") == 200:
                        return {"success": True, "data": resp, "method": "sdk"}
            except Exception as e:
                pass  # Fall through to REST attempts

        # Fallback: Try multiple REST endpoints
        attempts = [
            ("POST", "/quotes", {"symbols": symbols}, None),
            ("GET", "/quotes", None, {"symbols": symbols}),
            ("GET", "/data/quotes", None, {"symbols": symbols}),
            ("POST", "/data/quotes", {"symbols": symbols}, None),
            ("GET", f"{self.api_root_url}/data/quotes", None, {"symbols": symbols}),
        ]

        errors: List[Dict[str, Any]] = []
        for method, path, payload, params in attempts:
            result = self._request(method, path, payload=payload, params=params)
            if result.get("success"):
                result["endpoint_used"] = f"{method} {path}"
                return result
            errors.append({
                "attempt": f"{method} {path}",
                "status_code": result.get("status_code"),
                "error": result.get("error"),
            })

        return {
            "success": False,
            "error": "All quote endpoints failed",
            "attempts": errors,
        }

    def history(
        self,
        symbol: str,
        resolution: str = "D",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        range_from: Optional[int] = None,
        range_to: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Get historical candle data.

        Args:
            symbol: FYERS symbol (e.g., "NSE:NIFTY50-INDEX")
            resolution: Candle resolution (1, 5, 15, 30, 60, D, W, M)
            from_date: Start date (YYYY-MM-DD) - alternative to range_from
            to_date: End date (YYYY-MM-DD) - alternative to range_to
            range_from: Unix timestamp start
            range_to: Unix timestamp end

        Returns:
            Candle data or error.
        """
        payload = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "0",
            "cont_flag": "1",
        }

        if range_from and range_to:
            payload["range_from"] = str(range_from)
            payload["range_to"] = str(range_to)
        elif from_date and to_date:
            payload["date_format"] = "1"
            payload["range_from"] = from_date
            payload["range_to"] = to_date

        attempts = [
            ("GET", self._data_url("/history"), None, payload),
            ("POST", "/history", payload, None),
        ]

        errors: List[Dict[str, Any]] = []
        for method, path, request_payload, params in attempts:
            result = self._request(method, path, payload=request_payload, params=params)
            body = result.get("data")
            if result.get("success") and isinstance(body, dict) and body.get("candles") is not None:
                result["endpoint_used"] = f"{method} {path}"
                return result
            errors.append({
                "attempt": f"{method} {path}",
                "status_code": result.get("status_code"),
                "error": result.get("error"),
            })

        return {
            "success": False,
            "error": "All history endpoints failed",
            "attempts": errors,
        }

    def option_chain(self, symbol: str, strike_count: int = 10) -> Dict[str, Any]:
        """
        Get option chain data.

        Args:
            symbol: Underlying symbol (e.g., "NSE:NIFTY50-INDEX")
            strike_count: Number of strikes to fetch

        Returns:
            Option chain data or error.
        """
        payload = {
            "symbol": symbol,
            "strikecount": strike_count,
            "timestamp": "",
        }
        attempts = [
            ("GET", self._data_url("/options-chain-v3"), None, payload),
            ("POST", "/options-chain-v3", payload, None),
            ("POST", "/optionchain", payload, None),
        ]

        errors: List[Dict[str, Any]] = []
        for method, path, request_payload, params in attempts:
            result = self._request(method, path, payload=request_payload, params=params)
            body = result.get("data")
            if result.get("success") and isinstance(body, dict):
                status = str(body.get("s", "")).lower()
                code = body.get("code")
                if body.get("data") is not None or body.get("optionsChain") is not None or status == "ok" or code == 200:
                    result["endpoint_used"] = f"{method} {path}"
                    return result
            errors.append({
                "attempt": f"{method} {path}",
                "status_code": result.get("status_code"),
                "error": result.get("error"),
            })

        return {
            "success": False,
            "error": "All option-chain endpoints failed",
            "attempts": errors,
        }

    def get_lot_sizes(self) -> Dict[str, int]:
        """
        Fetch lot sizes from Fyers public symbol master CSV.

        This method works even when market is closed as it fetches from
        publicly available symbol master files.

        Returns:
            Dict of index name -> lot size (e.g., {"NIFTY50": 65, "BANKNIFTY": 30})
        """
        import csv
        import io

        NSE_FO_URL = "https://public.fyers.in/sym_details/NSE_FO.csv"
        BSE_FO_URL = "https://public.fyers.in/sym_details/BSE_FO.csv"

        result = {}

        # Fetch NSE F&O symbols
        try:
            resp = requests.get(NSE_FO_URL, timeout=30)
            if resp.status_code == 200:
                reader = csv.reader(io.StringIO(resp.text))
                for row in reader:
                    if len(row) < 10:
                        continue
                    desc = row[1] if len(row) > 1 else ""
                    lot_size = row[3] if len(row) > 3 else ""

                    # Look for index options (CE/PE in description)
                    if ("CE" in desc or "PE" in desc) and lot_size.isdigit():
                        lot = int(lot_size)
                        if "BANKNIFTY" in desc and "BANKNIFTY" not in result:
                            result["BANKNIFTY"] = lot
                        elif "FINNIFTY" in desc and "FINNIFTY" not in result:
                            result["FINNIFTY"] = lot
                        elif "MIDCPNIFTY" in desc and "MIDCPNIFTY" not in result:
                            result["MIDCPNIFTY"] = lot
                        elif "NIFTY" in desc and all(x not in desc for x in ["BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]):
                            if "NIFTY50" not in result:
                                result["NIFTY50"] = lot
        except Exception:
            pass

        # Fetch BSE F&O for SENSEX
        try:
            resp = requests.get(BSE_FO_URL, timeout=30)
            if resp.status_code == 200:
                reader = csv.reader(io.StringIO(resp.text))
                for row in reader:
                    if len(row) < 10:
                        continue
                    desc = row[1] if len(row) > 1 else ""
                    lot_size = row[3] if len(row) > 3 else ""

                    if "SENSEX" in desc and ("CE" in desc or "PE" in desc) and lot_size.isdigit():
                        if "SENSEX" not in result:
                            result["SENSEX"] = int(lot_size)
                            break
        except Exception:
            pass

        return result

    # ========== Order APIs ==========

    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """
        Place an order.

        Args:
            order: Order payload with symbol, qty, side, type, etc.

        Returns:
            Order response or error.
        """
        attempts = [
            ("POST", "/orders/sync", order),
            ("POST", "/orders", order),
            ("POST", f"{self.api_root_url}/api/v3/orders/sync", order),
        ]

        errors: List[Dict[str, Any]] = []
        for method, path, payload in attempts:
            result = self._request(method, path, payload=payload)
            if result.get("success"):
                result["endpoint_used"] = f"{method} {path}"
                return result
            errors.append({
                "attempt": f"{method} {path}",
                "error": result.get("error"),
            })

        return {
            "success": False,
            "error": "All order endpoints failed",
            "attempts": errors,
        }

    def modify_order(self, order_id: str, modifications: Dict[str, Any]) -> Dict[str, Any]:
        """Modify an existing order."""
        modifications["id"] = order_id
        return self._request("PATCH", "/orders", payload=modifications)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order."""
        return self._request("DELETE", "/orders", payload={"id": order_id})

    def orders(self) -> Dict[str, Any]:
        """Get all orders for the day."""
        return self._request("GET", "/orders")

    def trades(self) -> Dict[str, Any]:
        """Get all trades for the day."""
        return self._request("GET", "/tradebook")


def get_client(env_file: Optional[str] = None) -> FyersClient:
    """
    Factory function to get configured FyersClient.

    Args:
        env_file: Path to .env file.

    Returns:
        Configured FyersClient instance.

    Example:
        from shared.auth import get_client
        client = get_client()
        print(client.quotes("NSE:NIFTY50-INDEX"))
    """
    return FyersClient(env_file=env_file)

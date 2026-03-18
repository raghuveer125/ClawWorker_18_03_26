"""
FYERS Authentication Module
Handles OAuth login flow and token generation

Combines best features from:
- fyersN7: CLI options, SSL handling, auto-callback
- ClawWork: Clean UI, simple flow
"""

import hashlib
import http.server
import json
import os
import secrets
import threading
import webbrowser
from typing import Dict, Optional, Tuple, Union
from urllib.parse import parse_qs, urlencode, urlparse

import requests
import urllib3

from .config import (
    FYERS_AUTH_URL,
    FYERS_TOKEN_URL,
    DEFAULT_REDIRECT_HOST,
    DEFAULT_REDIRECT_PORT,
    DEFAULT_AUTH_TIMEOUT_SEC,
    DEFAULT_REQUEST_TIMEOUT_SEC,
)
from .env_manager import EnvManager


class FyersAuth:
    """
    FYERS OAuth Authentication Handler.

    Usage:
        auth = FyersAuth(client_id="XXX", secret_key="YYY")
        token = auth.login()  # Opens browser, waits for callback
        # Token is auto-saved to .env
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        env_file: Optional[str] = None,
        insecure: bool = False,
        ca_bundle: Optional[str] = None,
    ):
        """
        Initialize FyersAuth.

        Args:
            client_id: FYERS App ID. Falls back to env var.
            secret_key: FYERS Secret Key. Falls back to env var.
            redirect_uri: OAuth redirect URI. Defaults to localhost:8080.
            env_file: Path to .env file for loading/saving credentials.
            insecure: Disable SSL verification (for corporate networks).
            ca_bundle: Custom CA bundle path.
        """
        self.env_manager = EnvManager(env_file=env_file)
        creds = self.env_manager.load()

        self.client_id = client_id or creds["client_id"] or os.getenv("FYERS_CLIENT_ID", "")
        self.secret_key = secret_key or creds["secret_key"] or os.getenv("FYERS_SECRET_KEY", "")
        self.redirect_uri = redirect_uri or creds["redirect_uri"] or f"http://{DEFAULT_REDIRECT_HOST}:{DEFAULT_REDIRECT_PORT}/"

        self.insecure = insecure
        self.ca_bundle = ca_bundle
        self._configure_ssl()

        # State for CSRF protection
        self._state_token = secrets.token_urlsafe(16)

    def _configure_ssl(self) -> None:
        """Configure SSL/TLS settings."""
        if self.ca_bundle:
            os.environ["REQUESTS_CA_BUNDLE"] = self.ca_bundle
            os.environ["SSL_CERT_FILE"] = self.ca_bundle

        if self.insecure:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _get_verify(self) -> Union[bool, str]:
        """Get verify parameter for requests."""
        if self.insecure:
            return False
        if self.ca_bundle:
            return self.ca_bundle
        return True

    def generate_login_url(self) -> str:
        """Generate FYERS OAuth login URL."""
        if not self.client_id:
            raise ValueError("client_id is required")

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": self._state_token,
        }
        return f"{FYERS_AUTH_URL}?{urlencode(params)}"

    def exchange_token(self, auth_code: str) -> Dict:
        """
        Exchange auth code for access token.

        Args:
            auth_code: Authorization code from OAuth callback.

        Returns:
            Dict with access_token or error details.
        """
        if not self.client_id or not self.secret_key:
            raise ValueError("client_id and secret_key are required")

        # Create app_id_hash
        hash_input = f"{self.client_id}:{self.secret_key}"
        app_id_hash = hashlib.sha256(hash_input.encode()).hexdigest()

        payload = {
            "grant_type": "authorization_code",
            "appIdHash": app_id_hash,
            "code": auth_code,
        }

        try:
            resp = requests.post(
                FYERS_TOKEN_URL,
                json=payload,
                timeout=DEFAULT_REQUEST_TIMEOUT_SEC,
                verify=self._get_verify(),
            )
            return resp.json()
        except requests.RequestException as e:
            return {"error": str(e), "success": False}

    def login(
        self,
        open_browser: bool = True,
        timeout_sec: int = DEFAULT_AUTH_TIMEOUT_SEC,
        save_token: bool = True,
    ) -> Optional[str]:
        """
        Complete OAuth login flow.

        Opens browser for login, waits for callback, exchanges token.

        Args:
            open_browser: Auto-open browser. If False, prints URL.
            timeout_sec: Timeout waiting for callback.
            save_token: Save token to .env file.

        Returns:
            Access token string, or None on failure.
        """
        login_url = self.generate_login_url()

        # Start callback server
        auth_code = self._wait_for_callback(
            login_url=login_url,
            open_browser=open_browser,
            timeout_sec=timeout_sec,
        )

        if not auth_code:
            return None

        # Exchange for token
        result = self.exchange_token(auth_code)
        access_token = (
            result.get("access_token") or
            result.get("AccessToken") or
            result.get("token")
        )

        if not access_token:
            error = result.get("message") or result.get("error") or "Unknown error"
            print(f"Token exchange failed: {error}")
            if result.get("code") == "-5":
                print("Hint: Check if secret_key matches the client_id.")
            return None

        # Save credentials
        if save_token:
            self.env_manager.save(
                client_id=self.client_id,
                secret_key=self.secret_key,
                redirect_uri=self.redirect_uri,
                access_token=access_token,
            )
            print(f"Token saved to: {self.env_manager.get_env_file_path()}")

        return access_token

    def _wait_for_callback(
        self,
        login_url: str,
        open_browser: bool,
        timeout_sec: int,
    ) -> Optional[str]:
        """Start local server and wait for OAuth callback."""
        parsed = urlparse(self.redirect_uri)
        host = parsed.hostname or DEFAULT_REDIRECT_HOST
        port = parsed.port or DEFAULT_REDIRECT_PORT
        expected_path = parsed.path or "/"

        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("Auto callback requires localhost redirect URI")

        # Shared state between handler and main thread
        auth_state = {"code": "", "error": ""}
        done = threading.Event()
        state_token = self._state_token

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                req = urlparse(self.path)
                if req.path != expected_path:
                    self.send_response(404)
                    self.end_headers()
                    return

                q = parse_qs(req.query)
                code = (q.get("auth_code") or [""])[0]
                received_state = (q.get("state") or [""])[0]

                # Verify state token
                if received_state and received_state != state_token:
                    auth_state["error"] = "State mismatch - possible CSRF"
                    self._send_error_page("State verification failed")
                    done.set()
                    return

                if code:
                    auth_state["code"] = code
                    self._send_success_page()
                else:
                    msg = (q.get("message") or q.get("s") or ["auth_code missing"])[0]
                    auth_state["error"] = msg
                    self._send_error_page(msg)

                done.set()

            def _send_success_page(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = """<!DOCTYPE html>
<html><head><title>Auth Success</title>
<style>
body{font-family:system-ui;display:flex;justify-content:center;align-items:center;
height:100vh;margin:0;background:linear-gradient(135deg,#667eea,#764ba2)}
.card{background:#fff;padding:40px 60px;border-radius:16px;text-align:center;
box-shadow:0 20px 60px rgba(0,0,0,.3)}
h1{color:#22c55e}
</style></head>
<body><div class="card">
<div style="font-size:64px">&#10004;</div>
<h1>Authentication Successful</h1>
<p>You can close this window</p>
</div></body></html>"""
                self.wfile.write(html.encode())

            def _send_error_page(self, error):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = f"""<!DOCTYPE html>
<html><head><title>Auth Failed</title>
<style>
body{{font-family:system-ui;display:flex;justify-content:center;align-items:center;
height:100vh;margin:0;background:#fee2e2}}
.card{{background:#fff;padding:40px 60px;border-radius:16px;text-align:center}}
h1{{color:#ef4444}}
</style></head>
<body><div class="card">
<h1>Authentication Failed</h1>
<p>{error}</p>
</div></body></html>"""
                self.wfile.write(html.encode())

            def log_message(self, format, *args):
                pass  # Suppress HTTP logs

        # Start server
        try:
            server = http.server.ThreadingHTTPServer((host, port), CallbackHandler)
        except OSError as e:
            if e.errno in {48, 98}:  # Address in use
                raise ValueError(f"Port {port} is busy. Close existing service or change redirect_uri.") from e
            raise

        server.timeout = 1
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            if open_browser:
                print("Opening browser for FYERS login...")
                webbrowser.open(login_url, new=1)
            else:
                print("Open this URL in browser:")
                print(login_url)

            print(f"Waiting for callback (timeout: {timeout_sec}s)...")
            finished = done.wait(timeout_sec)

            if not finished:
                print("Timeout waiting for callback")
                return None

        finally:
            server.shutdown()
            server.server_close()

        if auth_state["code"]:
            return auth_state["code"]

        if auth_state["error"]:
            print(f"Callback error: {auth_state['error']}")

        return None

    def login_with_code(
        self,
        auth_code: Optional[str] = None,
        redirected_url: Optional[str] = None,
        save_token: bool = True,
    ) -> Optional[str]:
        """
        Login with existing auth code (non-interactive).

        Args:
            auth_code: Direct auth code.
            redirected_url: Full redirected URL containing auth_code.
            save_token: Save token to .env file.

        Returns:
            Access token string, or None on failure.
        """
        if redirected_url and not auth_code:
            auth_code = self._extract_auth_code(redirected_url)

        if not auth_code:
            raise ValueError("auth_code or redirected_url is required")

        result = self.exchange_token(auth_code)
        access_token = result.get("access_token") or result.get("AccessToken")

        if access_token and save_token:
            self.env_manager.save(
                client_id=self.client_id,
                secret_key=self.secret_key,
                redirect_uri=self.redirect_uri,
                access_token=access_token,
            )

        return access_token

    @staticmethod
    def _extract_auth_code(redirected_url: str) -> str:
        """Extract auth_code from redirected URL."""
        parsed = urlparse(redirected_url.strip())
        query = parse_qs(parsed.query)
        codes = query.get("auth_code", [])
        if not codes:
            raise ValueError("auth_code not found in URL")
        return codes[0]


def quick_login(
    client_id: Optional[str] = None,
    secret_key: Optional[str] = None,
    env_file: Optional[str] = None,
    insecure: bool = False,
) -> Optional[str]:
    """
    Quick one-liner login.

    Args:
        client_id: FYERS App ID (or set FYERS_CLIENT_ID env var).
        secret_key: FYERS Secret Key (or set FYERS_SECRET_KEY env var).
        env_file: Path to .env file.
        insecure: Disable SSL verification.

    Returns:
        Access token, or None on failure.

    Example:
        from shared.auth import quick_login
        token = quick_login()
    """
    auth = FyersAuth(
        client_id=client_id,
        secret_key=secret_key,
        env_file=env_file,
        insecure=insecure,
    )
    return auth.login()

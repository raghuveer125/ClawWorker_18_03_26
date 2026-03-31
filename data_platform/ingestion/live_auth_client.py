"""
Concrete AuthClient implementation for live Fyers trading sessions.

Validates the current access token via FyersModel.get_profile(). When the
token is stale or missing, opens a browser for the Fyers OAuth login flow,
spins up a temporary local HTTP server to catch the OAuth redirect automatically,
exchanges the auth code for a fresh access token, and persists it to .env.
"""
from __future__ import annotations

import logging
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

_log = logging.getLogger(__name__)

_DEFAULT_ENV_FILE = ".env"
_CALLBACK_TIMEOUT_SECONDS = 120


def _wait_for_redirect(host: str, port: int, timeout: float) -> str | None:
    captured: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            full_url = f"http://{host}:{port}{self.path}"
            captured.append(full_url)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>BDTS: Login successful!</h2>"
                b"<p>You can close this tab and return to the terminal.</p></body></html>"
            )

        def log_message(self, *args):
            pass

    server = HTTPServer((host, port), _Handler)
    server.timeout = timeout

    def _serve():
        server.handle_request()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    thread.join(timeout=timeout + 2)
    server.server_close()

    return captured[0] if captured else None


class LiveFyersAuthClient:
    """
    Implements the AuthClient protocol (validate_access_token / refresh_access_token / login)
    using the fyers_apiv3 SDK.

    refresh_access_token() always returns None — Fyers v3 does not support
    headless token refresh; a new OAuth login is required each day.

    login() launches the browser, catches the OAuth redirect on a local HTTP
    server, exchanges the auth code automatically, and persists the token to .env.
    """

    def __init__(self, client_id: str, secret_key: str, redirect_uri: str, env_file: str = _DEFAULT_ENV_FILE) -> None:
        self._client_id = client_id
        self._secret_key = secret_key
        self._redirect_uri = redirect_uri
        self._env_file = env_file

    def _callback_host_port(self) -> tuple[str, int]:
        parsed = urlparse(self._redirect_uri)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8080
        return host, port

    def validate_access_token(self, token: str) -> bool:
        if not token:
            return False
        bare_token = token.split(":", 1)[1] if ":" in token else token
        # Fast path: decode JWT exp claim locally — no API call needed.
        try:
            import base64, json as _json, time as _time
            parts = bare_token.split(".")
            if len(parts) == 3:
                padded = parts[1] + "=" * (-len(parts[1]) % 4)
                claims = _json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
                exp = int(claims.get("exp", 0))
                if exp > 0:
                    # Token valid if it expires more than 60 seconds from now
                    return _time.time() < (exp - 60)
        except Exception:
            pass
        # Fallback: call Fyers profile API (only if JWT decode fails)
        try:
            from fyers_apiv3 import fyersModel
            fyers = fyersModel.FyersModel(client_id=self._client_id, token=bare_token, log_level="ERROR")
            response = fyers.get_profile()
            return isinstance(response, dict) and response.get("code") in (200, 0, "200", "0")
        except Exception as exc:
            _log.debug("LiveFyersAuthClient: token validation failed: %s", exc)
            return False

    def refresh_access_token(self) -> str | None:
        return None

    def login(self) -> str | None:
        try:
            from shared_project_engine.auth.token_exchange import build_login_url, exchange_to_access_token, write_access_token
        except ImportError as exc:
            _log.error("LiveFyersAuthClient: cannot import token_exchange: %s", exc)
            return None

        try:
            url = build_login_url(self._env_file)
        except Exception as exc:
            _log.error("LiveFyersAuthClient: failed to build login URL: %s", exc)
            return None

        host, port = self._callback_host_port()

        print("\n" + "=" * 60)
        print("BDTS: Fyers access token expired — re-authenticating.")
        print(f"Opening browser for Fyers login (waiting up to {_CALLBACK_TIMEOUT_SECONDS}s)...")
        print("=" * 60 + "\n")

        webbrowser.open(url)

        redirect_url = _wait_for_redirect(host, port, timeout=_CALLBACK_TIMEOUT_SECONDS)

        if not redirect_url:
            _log.error("LiveFyersAuthClient: timed out waiting for OAuth redirect on %s:%s", host, port)
            return None

        try:
            token = exchange_to_access_token(redirect_url, self._env_file)
        except Exception as exc:
            _log.error("LiveFyersAuthClient: token exchange failed: %s", exc)
            return None

        try:
            path = write_access_token(self._env_file, token)
            print(f"FYERS_ACCESS_TOKEN updated in {path}")
            print("=" * 60 + "\n")
        except Exception as exc:
            _log.warning("LiveFyersAuthClient: could not persist token to %s: %s", self._env_file, exc)

        return token

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, env_file: str = _DEFAULT_ENV_FILE) -> "LiveFyersAuthClient":
        import os
        source = env or dict(os.environ)
        client_id = source.get("FYERS_CLIENT_ID") or source.get("FYERS_APP_ID") or ""
        secret_key = source.get("FYERS_SECRET_KEY") or ""
        redirect_uri = source.get("FYERS_REDIRECT_URI") or ""
        env_file_resolved = str(Path(env_file).expanduser().resolve())
        return cls(
            client_id=client_id.strip(),
            secret_key=secret_key.strip(),
            redirect_uri=redirect_uri.strip(),
            env_file=env_file_resolved,
        )

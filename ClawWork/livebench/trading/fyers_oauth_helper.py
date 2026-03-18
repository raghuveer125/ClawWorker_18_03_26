"""CLI helper for FYERS OAuth auth-code flow.

Usage examples:
  python -m livebench.trading.fyers_oauth_helper login-url --open-browser
  python -m livebench.trading.fyers_oauth_helper exchange --auth-code <code-or-redirect-url> --write-env
  python -m livebench.trading.fyers_oauth_helper interactive --open-browser --write-env
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

try:
    from dotenv import load_dotenv as _load_dotenv_lib
except Exception:
    _load_dotenv_lib = None


DEFAULT_AUTH_BASE_URL = "https://api-t1.fyers.in/api/v3"


def _load_dotenv_files() -> None:
    """Load environment variables from common .env locations."""
    cwd_env = Path.cwd() / ".env"
    project_root_env = Path(__file__).resolve().parents[2] / ".env"

    if _load_dotenv_lib:
        _load_dotenv_lib(dotenv_path=cwd_env, override=False)
        if project_root_env != cwd_env:
            _load_dotenv_lib(dotenv_path=project_root_env, override=False)
        return

    _load_env_file_fallback(cwd_env)
    if project_root_env != cwd_env:
        _load_env_file_fallback(project_root_env)


def _load_env_file_fallback(path: Path) -> None:
    """Minimal .env loader when python-dotenv is unavailable."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"").strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def get_settings(args: argparse.Namespace) -> Dict[str, str]:
    auth_base_url = (args.auth_base_url or _env("FYERS_AUTH_BASE_URL") or _env("FYERS_API_BASE_URL") or DEFAULT_AUTH_BASE_URL).rstrip("/")
    token_url = args.token_url or _env("FYERS_TOKEN_URL") or f"{auth_base_url}/validate-authcode"

    client_id = args.client_id or _env("FYERS_APP_ID")
    secret_candidates = _unique_non_empty(
        [
            args.app_secret,
            _env("FYERS_APP_SECRET"),
            _env("FYERS_SECRET_KEY"),
            _env("FYERS_SECRET_ID"),
        ]
    )
    app_secret = secret_candidates[0] if secret_candidates else None
    redirect_uri = args.redirect_uri or _env("FYERS_REDIRECT_URI")

    missing = []
    if not client_id:
        missing.append("FYERS_APP_ID")
    if not app_secret and args.command in {"exchange", "interactive"}:
        missing.append("FYERS_APP_SECRET (or FYERS_SECRET_KEY)")
    if not redirect_uri:
        missing.append("FYERS_REDIRECT_URI")

    if missing:
        raise ValueError(f"Missing required settings: {', '.join(missing)}")

    return {
        "auth_base_url": auth_base_url,
        "token_url": token_url,
        "client_id": client_id,
        "app_secret": app_secret or "",
        "app_secrets": secret_candidates,
        "redirect_uri": redirect_uri,
    }


def _unique_non_empty(values: list[Optional[str]]) -> list[str]:
    seen = set()
    out: list[str] = []
    for value in values:
        if not value:
            continue
        v = value.strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def build_login_url(client_id: str, redirect_uri: str, auth_base_url: str, state: Optional[str] = None) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state or secrets.token_urlsafe(16),
    }
    return f"{auth_base_url}/generate-authcode?{urlencode(query)}"


def extract_auth_code(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("Auth code input is empty")

    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        params = parse_qs(parsed.query)
        for key in ("auth_code", "code"):
            if key in params and params[key]:
                return params[key][0]
        raise ValueError("Could not find 'auth_code' or 'code' query parameter in redirect URL")

    return raw


def exchange_auth_code(
    token_url: str,
    client_id: str,
    app_secrets: list[str] | str,
    auth_code: str,
    timeout: float = 30.0,
    auth_code_app_id: Optional[str] = None,
) -> Dict[str, Any]:
    if isinstance(app_secrets, str):
        secret_candidates = _unique_non_empty([app_secrets])
    else:
        secret_candidates = _unique_non_empty(app_secrets)

    if not secret_candidates:
        return {
            "success": False,
            "status_code": 0,
            "error": "No app secret candidates provided",
            "response": {},
        }

    app_id_candidates = [client_id]
    # FYERS auth code JWT often carries app_id without app-type suffix (e.g. ABCD1234EF)
    if "-" in client_id:
        base_client_id = client_id.split("-", 1)[0]
        if base_client_id and base_client_id not in app_id_candidates:
            app_id_candidates.append(base_client_id)
    if auth_code_app_id and auth_code_app_id not in app_id_candidates:
        app_id_candidates.insert(0, auth_code_app_id)

    last_error: Dict[str, Any] | None = None

    for app_id in app_id_candidates:
        for app_secret in secret_candidates:
            app_id_hash = hashlib.sha256(f"{app_id}:{app_secret}".encode("utf-8")).hexdigest()
            payload = {
                "grant_type": "authorization_code",
                "appIdHash": app_id_hash,
                "code": auth_code,
            }

            response = requests.post(token_url, json=payload, timeout=timeout)
            try:
                body = response.json()
            except ValueError:
                body = {"raw": response.text}

            if 200 <= response.status_code < 300:
                token = _find_access_token(body)
                if token:
                    return {
                        "success": True,
                        "status_code": response.status_code,
                        "access_token": token,
                        "response": body,
                        "app_id_used_for_hash": app_id,
                    }
                last_error = {
                    "success": False,
                    "status_code": response.status_code,
                    "error": "Access token not found in FYERS response",
                    "response": body,
                    "app_id_used_for_hash": app_id,
                }
                continue

            last_error = {
                "success": False,
                "status_code": response.status_code,
                "error": _extract_error(body),
                "response": body,
                "app_id_used_for_hash": app_id,
            }

            error_text = str(last_error.get("error", "")).lower()
            # Retry combinations only for hash mismatch; otherwise stop early.
            if "invalid app id hash" not in error_text:
                break

        if last_error and "invalid app id hash" not in str(last_error.get("error", "")).lower():
            break

    return last_error or {
        "success": False,
        "status_code": 0,
        "error": "Unknown token exchange failure",
        "response": {},
    }


def _extract_error(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("message", "error", "reason", "s"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return str(body)
    return str(body)


def _find_access_token(data: Any) -> Optional[str]:
    if isinstance(data, dict):
        if "access_token" in data and isinstance(data["access_token"], str):
            return data["access_token"]
        for value in data.values():
            token = _find_access_token(value)
            if token:
                return token
    elif isinstance(data, list):
        for item in data:
            token = _find_access_token(item)
            if token:
                return token
    return None


def extract_app_id_from_auth_code(auth_code: str) -> Optional[str]:
    """Decode JWT payload (without verification) to read `app_id` hint."""
    parts = auth_code.split(".")
    if len(parts) < 2:
        return None

    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
    except Exception:
        return None

    try:
        payload = json.loads(decoded)
    except Exception:
        return None

    app_id = payload.get("app_id")
    if isinstance(app_id, str) and app_id.strip():
        return app_id.strip()
    return None


def upsert_env_var(env_path: Path, key: str, value: str) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    replaced = False
    for idx, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[idx] = f"{key}={value}"
            replaced = True
            break

    if not replaced:
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def cmd_login_url(args: argparse.Namespace) -> int:
    settings = get_settings(args)
    state = args.state or secrets.token_urlsafe(16)
    url = build_login_url(
        client_id=settings["client_id"],
        redirect_uri=settings["redirect_uri"],
        auth_base_url=settings["auth_base_url"],
        state=state,
    )

    print(url)
    print(f"\nSTATE={state}")
    if args.open_browser:
        webbrowser.open(url)
    return 0


def cmd_exchange(args: argparse.Namespace) -> int:
    settings = get_settings(args)
    try:
        auth_code = extract_auth_code(args.auth_code)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1

    auth_code_app_id = extract_app_id_from_auth_code(auth_code)

    try:
        result = exchange_auth_code(
            token_url=settings["token_url"],
            client_id=settings["client_id"],
            app_secrets=settings.get("app_secrets") or [settings["app_secret"]],
            auth_code=auth_code,
            timeout=float(args.timeout),
            auth_code_app_id=auth_code_app_id,
        )
    except requests.RequestException as exc:
        print(f"❌ Token exchange failed: {exc}")
        return 1

    if not result["success"]:
        print("❌ Token exchange failed")
        print(f"Status: {result.get('status_code')}")
        print(f"Error: {result.get('error')}")
        return 1

    token = result["access_token"]
    print("✅ FYERS token generated successfully")
    print(f"FYERS_ACCESS_TOKEN={token}")

    if args.write_env:
        env_file = Path(args.env_file)
        upsert_env_var(env_file, "FYERS_ACCESS_TOKEN", token)
        print(f"Updated {env_file} with FYERS_ACCESS_TOKEN")

    return 0


def cmd_interactive(args: argparse.Namespace) -> int:
    settings = get_settings(args)
    state = args.state or secrets.token_urlsafe(16)
    url = build_login_url(
        client_id=settings["client_id"],
        redirect_uri=settings["redirect_uri"],
        auth_base_url=settings["auth_base_url"],
        state=state,
    )

    print("1) Open this URL and complete FYERS login:")
    print(url)
    print(f"\nExpected state: {state}")

    if args.open_browser:
        webbrowser.open(url)

    redirect_or_code = input("\n2) Paste full redirect URL (or auth code): ").strip()
    if not redirect_or_code:
        print("❌ No input provided")
        return 1

    exchange_args = argparse.Namespace(
        command="exchange",
        auth_base_url=args.auth_base_url,
        token_url=args.token_url,
        client_id=settings["client_id"],
        app_secret=settings["app_secret"],
        redirect_uri=settings["redirect_uri"],
        auth_code=redirect_or_code,
        timeout=args.timeout,
        write_env=args.write_env,
        env_file=args.env_file,
    )
    return cmd_exchange(exchange_args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FYERS OAuth helper for LiveBench")
    parser.add_argument("--auth-base-url", default=None, help="FYERS auth base URL (default: FYERS_AUTH_BASE_URL or FYERS_API_BASE_URL)")
    parser.add_argument("--token-url", default=None, help="FYERS token exchange URL (default: <auth-base-url>/validate-authcode)")
    parser.add_argument("--client-id", default=None, help="FYERS app ID (default: FYERS_APP_ID)")
    parser.add_argument("--app-secret", default=None, help="FYERS app secret (default: FYERS_APP_SECRET or FYERS_SECRET_KEY)")
    parser.add_argument("--redirect-uri", default=None, help="FYERS redirect URI (default: FYERS_REDIRECT_URI)")
    parser.add_argument("--timeout", default="30", help="HTTP timeout in seconds (default: 30)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    login_url = subparsers.add_parser("login-url", help="Print FYERS login URL")
    login_url.add_argument("--state", default=None, help="Custom OAuth state value")
    login_url.add_argument("--open-browser", action="store_true", help="Open login URL in browser")
    login_url.set_defaults(func=cmd_login_url)

    exchange = subparsers.add_parser("exchange", help="Exchange auth code for access token")
    exchange.add_argument("--auth-code", required=True, help="Auth code OR full redirect URL")
    exchange.add_argument("--write-env", action="store_true", help="Write token to env file")
    exchange.add_argument("--env-file", default=".env", help="Env file path (default: .env)")
    exchange.set_defaults(func=cmd_exchange)

    interactive = subparsers.add_parser("interactive", help="Run full auth flow interactively")
    interactive.add_argument("--state", default=None, help="Custom OAuth state value")
    interactive.add_argument("--open-browser", action="store_true", help="Open login URL in browser")
    interactive.add_argument("--write-env", action="store_true", help="Write token to env file")
    interactive.add_argument("--env-file", default=".env", help="Env file path (default: .env)")
    interactive.set_defaults(func=cmd_interactive)

    return parser


def main() -> int:
    _load_dotenv_files()
    parser = build_parser()
    args = parser.parse_args()

    try:
        return args.func(args)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

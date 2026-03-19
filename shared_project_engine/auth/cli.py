#!/usr/bin/env python3
"""
FYERS Authentication CLI

Usage:
    # Auto-login (opens browser)
    python -m shared_project_engine.auth.cli login

    # Login with existing auth code
    python -m shared_project_engine.auth.cli login --redirected-url "http://...?auth_code=..."

    # Check authentication status
    python -m shared_project_engine.auth.cli status

    # Print login URL only
    python -m shared_project_engine.auth.cli url

Examples:
    # From project root
    cd /path/to/ClawWork_FyersN7
    python -m shared_project_engine.auth.cli login

    # With explicit credentials
    python -m shared_project_engine.auth.cli login --client-id "XXX" --secret-key "YYY"

    # For corporate networks with SSL issues
    python -m shared_project_engine.auth.cli login --insecure
"""

import argparse
import sys
import os

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared_project_engine.auth import FyersAuth, FyersClient, EnvManager


def add_tls_args(parser: argparse.ArgumentParser) -> None:
    """Add shared TLS-related CLI options."""
    parser.add_argument("--insecure", action="store_true", help="Disable SSL verification")
    parser.add_argument("--ca-bundle", default="", help="Custom CA bundle path")


def cmd_login(args) -> int:
    """Handle login command."""
    auth = FyersAuth(
        client_id=args.client_id,
        secret_key=args.secret_key,
        redirect_uri=args.redirect_uri,
        env_file=args.env_file,
        insecure=args.insecure,
        ca_bundle=args.ca_bundle,
    )

    # Check if we have redirected URL
    if args.redirected_url:
        token = auth.login_with_code(redirected_url=args.redirected_url)
    elif args.auth_code:
        token = auth.login_with_code(auth_code=args.auth_code)
    else:
        token = auth.login(
            open_browser=not args.no_browser,
            timeout_sec=args.timeout,
        )

    if token:
        print("\nAuthentication successful!")
        print(f"Token: {token[:20]}...{token[-10:]}")
        return 0
    else:
        print("\nAuthentication failed.")
        return 1


def cmd_status(args) -> int:
    """Check authentication status."""
    env_manager = EnvManager(env_file=args.env_file)
    creds = env_manager.load()

    print("FYERS Authentication Status")
    print("=" * 40)
    print(f"Env File: {env_manager.get_env_file_path()}")
    print(f"Client ID: {creds['client_id'] or '(not set)'}")
    print(f"Secret Key: {'*' * 8 if creds['secret_key'] else '(not set)'}")
    print(f"Redirect URI: {creds['redirect_uri'] or '(not set)'}")
    print(f"Access Token: {'*' * 20 + creds['access_token'][-10:] if creds['access_token'] else '(not set)'}")

    if creds['access_token'] and creds['client_id']:
        print("\nVerifying token with API...")
        client = FyersClient(
            access_token=creds['access_token'],
            client_id=creds['client_id'],
            insecure=args.insecure,
            ca_bundle=args.ca_bundle,
        )
        result = client.profile()
        if result.get("success"):
            print("Token is VALID")
            data = result.get("data", {})
            if isinstance(data, dict):
                print(f"  Name: {data.get('name', 'N/A')}")
                print(f"  Email: {data.get('email_id', 'N/A')}")
            return 0
        else:
            print(f"Token is INVALID: {result.get('error', 'Unknown error')}")
            return 1
    else:
        print("\nMissing credentials. Run 'login' first.")
        return 1


def cmd_url(args) -> int:
    """Print login URL."""
    auth = FyersAuth(
        client_id=args.client_id,
        secret_key=args.secret_key,
        redirect_uri=args.redirect_uri,
        env_file=args.env_file,
        insecure=args.insecure,
        ca_bundle=args.ca_bundle,
    )

    try:
        url = auth.generate_login_url()
        print(url)
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_test(args) -> int:
    """Test API connection with a simple quote request."""
    client = FyersClient(
        env_file=args.env_file,
        insecure=args.insecure,
        ca_bundle=args.ca_bundle,
    )

    symbol = args.symbol or "NSE:NIFTY50-INDEX"
    print(f"Testing quote for: {symbol}")

    result = client.quotes(symbol)
    if result.get("success"):
        print("API connection successful!")
        data = result.get("data", {})
        if isinstance(data, dict) and "d" in data:
            for item in data.get("d", []):
                v = item.get("v", {})
                print(f"  Symbol: {v.get('symbol', 'N/A')}")
                print(f"  LTP: {v.get('lp', 'N/A')}")
                print(f"  Change: {v.get('ch', 'N/A')} ({v.get('chp', 'N/A')}%)")
        return 0
    else:
        print(f"API test failed: {result.get('error')}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="FYERS Authentication CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--env-file", default=None, help="Path to .env file")

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Login command
    login_parser = subparsers.add_parser("login", help="Authenticate with FYERS")
    login_parser.add_argument("--client-id", default="", help="FYERS Client/App ID")
    login_parser.add_argument("--secret-key", default="", help="FYERS Secret Key")
    login_parser.add_argument("--redirect-uri", default="", help="OAuth Redirect URI")
    login_parser.add_argument("--redirected-url", default="", help="Full redirected URL with auth_code")
    login_parser.add_argument("--auth-code", default="", help="Auth code directly")
    add_tls_args(login_parser)
    login_parser.add_argument("--no-browser", action="store_true", help="Don't open browser")
    login_parser.add_argument("--timeout", type=int, default=180, help="Callback timeout (seconds)")

    # Status command
    status_parser = subparsers.add_parser("status", help="Check authentication status")
    add_tls_args(status_parser)

    # URL command
    url_parser = subparsers.add_parser("url", help="Print login URL")
    url_parser.add_argument("--client-id", default="", help="FYERS Client/App ID")
    url_parser.add_argument("--secret-key", default="", help="FYERS Secret Key")
    url_parser.add_argument("--redirect-uri", default="", help="OAuth Redirect URI")
    add_tls_args(url_parser)

    # Test command
    test_parser = subparsers.add_parser("test", help="Test API connection")
    test_parser.add_argument("--symbol", default="", help="Symbol to test (default: NSE:NIFTY50-INDEX)")
    add_tls_args(test_parser)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "login":
        return cmd_login(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "url":
        return cmd_url(args)
    elif args.command == "test":
        return cmd_test(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())

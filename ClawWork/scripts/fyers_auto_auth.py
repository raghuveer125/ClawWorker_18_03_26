#!/usr/bin/env python3
"""
FYERS Auto-Auth: One-click token generation
Run this script, click the link, login, and token is automatically saved.
"""

import hashlib
import os
import re
import secrets
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import requests

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
ENV_FILE = os.path.join(PROJECT_DIR, ".env")

# Load env vars
def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env[key] = value.strip('"').strip("'")
    return env

env = load_env()
APP_ID = env.get('FYERS_APP_ID', 'DHEP61AA6F-100')
SECRET_KEY = env.get('FYERS_SECRET_ID', '053V2A0EWO')  # Use SECRET_ID for hash

# Load redirect URI from shared config if available
def _get_default_redirect_uri():
    try:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(PROJECT_DIR)))
        from shared_project_engine.services import get_auth_redirect_uri
        return get_auth_redirect_uri()
    except ImportError:
        return 'http://127.0.0.1:8080/'

REDIRECT_URI = env.get('FYERS_REDIRECT_URI', _get_default_redirect_uri())

# Parse redirect URI to get port
redirect_parsed = urlparse(REDIRECT_URI)
CALLBACK_PORT = redirect_parsed.port or 8080

# Global state
auth_result = {"token": None, "error": None}
state_token = secrets.token_urlsafe(16)


class AuthHandler(BaseHTTPRequestHandler):
    """Handle OAuth callback"""

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs

    def do_GET(self):
        global auth_result

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if 'auth_code' in params:
            auth_code = params['auth_code'][0]
            received_state = params.get('state', [''])[0]

            # Verify state
            if received_state != state_token:
                self.send_error_page("State mismatch - possible CSRF attack")
                auth_result["error"] = "State mismatch"
                return

            # Exchange auth code for token
            token = exchange_token(auth_code)

            if token:
                # Save to .env
                save_token(token)
                auth_result["token"] = token
                self.send_success_page()
            else:
                self.send_error_page("Failed to exchange auth code")
                auth_result["error"] = "Token exchange failed"
        else:
            error = params.get('message', ['Unknown error'])[0]
            self.send_error_page(f"Auth failed: {error}")
            auth_result["error"] = error

    def send_success_page(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>FYERS Auth Success</title>
            <style>
                body { font-family: -apple-system, sans-serif; display: flex;
                       justify-content: center; align-items: center; height: 100vh;
                       margin: 0; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
                .card { background: white; padding: 40px 60px; border-radius: 16px;
                        box-shadow: 0 20px 60px rgba(0,0,0,0.3); text-align: center; }
                h1 { color: #22c55e; margin-bottom: 10px; }
                p { color: #666; }
                .checkmark { font-size: 64px; margin-bottom: 20px; }
            </style>
        </head>
        <body>
            <div class="card">
                <div class="checkmark">✅</div>
                <h1>Authentication Successful!</h1>
                <p>Token saved to .env file</p>
                <p style="color: #999; font-size: 14px;">You can close this window</p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode())

    def send_error_page(self, error):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>FYERS Auth Failed</title>
            <style>
                body {{ font-family: -apple-system, sans-serif; display: flex;
                       justify-content: center; align-items: center; height: 100vh;
                       margin: 0; background: #fee2e2; }}
                .card {{ background: white; padding: 40px 60px; border-radius: 16px;
                        box-shadow: 0 10px 40px rgba(0,0,0,0.1); text-align: center; }}
                h1 {{ color: #ef4444; }}
                p {{ color: #666; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>❌ Authentication Failed</h1>
                <p>{error}</p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode())


def exchange_token(auth_code):
    """Exchange auth code for access token"""
    # Create app_id_hash
    hash_input = f"{APP_ID}:{SECRET_KEY}"
    app_id_hash = hashlib.sha256(hash_input.encode()).hexdigest()

    url = "https://api-t1.fyers.in/api/v3/validate-authcode"
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": app_id_hash,
        "code": auth_code
    }

    try:
        resp = requests.post(url, json=payload, timeout=30)
        data = resp.json()

        if data.get("access_token"):
            return data["access_token"]
        else:
            print(f"❌ Token exchange error: {data.get('message', data)}")
            return None
    except Exception as e:
        print(f"❌ Request error: {e}")
        return None


def save_token(token):
    """Save token to .env file"""
    with open(ENV_FILE, 'r') as f:
        content = f.read()

    # Replace existing token
    new_content = re.sub(
        r'FYERS_ACCESS_TOKEN=.*',
        f'FYERS_ACCESS_TOKEN={token}',
        content
    )

    with open(ENV_FILE, 'w') as f:
        f.write(new_content)

    print("✅ Token saved to .env")


def generate_auth_url():
    """Generate FYERS auth URL"""
    from urllib.parse import urlencode

    params = {
        "client_id": APP_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "state": state_token
    }

    return f"https://api-t1.fyers.in/api/v3/generate-authcode?{urlencode(params)}"


def start_server():
    """Start callback server"""
    server = HTTPServer(('127.0.0.1', CALLBACK_PORT), AuthHandler)
    server.handle_request()  # Handle single request then stop
    return server


def main():
    print("=" * 50)
    print("🔐 FYERS Auto-Auth")
    print("=" * 50)
    print(f"App ID: {APP_ID}")
    print(f"Redirect: {REDIRECT_URI}")
    print()

    # Generate auth URL
    auth_url = generate_auth_url()

    # Start server in background
    print("📡 Starting callback server...")
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    time.sleep(0.5)  # Wait for server to start

    # Open browser
    print("🌐 Opening browser for login...")
    print()
    webbrowser.open(auth_url)

    print("⏳ Waiting for authentication...")
    print("   (Complete login in browser)")
    print()

    # Wait for result
    server_thread.join(timeout=120)  # 2 minute timeout

    if auth_result["token"]:
        print()
        print("=" * 50)
        print("✅ SUCCESS! Token generated and saved")
        print("=" * 50)
        print()
        print("You can now start the dashboard:")
        print("  bash ./start_dashboard.sh")
        return 0
    elif auth_result["error"]:
        print(f"\n❌ Failed: {auth_result['error']}")
        return 1
    else:
        print("\n❌ Timeout - no response received")
        return 1


if __name__ == "__main__":
    exit(main())

#!/usr/bin/env python3
import argparse
import hashlib
import http.server
import json
import os
import sys
import threading
import webbrowser
from typing import Dict
from urllib.parse import parse_qs, urlparse

from fyers_apiv3 import fyersModel
import requests
import urllib3


def extract_auth_code(redirected_url: str) -> str:
    parsed = urlparse(redirected_url.strip())
    query = parse_qs(parsed.query)
    auth_codes = query.get("auth_code", [])
    if not auth_codes:
        raise ValueError("auth_code not found in redirected URL.")
    return auth_codes[0]


def generate_login_url(client_id: str, redirect_uri: str) -> str:
    session = fyersModel.SessionModel(
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code",
        state="office_fyers",
    )
    return session.generate_authcode()


def exchange_access_token(
    client_id: str,
    secret_key: str,
    auth_code: str,
    verify: bool | str,
) -> Dict:
    app_id_hash = hashlib.sha256(f"{client_id}:{secret_key}".encode()).hexdigest()
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": app_id_hash,
        "code": auth_code,
    }
    resp = requests.post(
        "https://api-t1.fyers.in/api/v3/validate-authcode",
        json=payload,
        timeout=25,
        verify=verify,
    ).json()
    if not isinstance(resp, dict):
        raise ValueError("Invalid token response from FYERS.")
    return resp


def resolve_verify(ca_bundle: str, insecure: bool):
    if insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return False
    if ca_bundle:
        os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle
        os.environ["SSL_CERT_FILE"] = ca_bundle
        return ca_bundle
    return True


def write_env(path: str, values: Dict[str, str]) -> None:
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f]
    kv = {}
    for ln in lines:
        if "=" in ln and not ln.strip().startswith("#"):
            k, v = ln.split("=", 1)
            kv[k.strip()] = v.strip()
    kv.update(values)

    out = [
        "# FYERS local credentials",
        f"FYERS_CLIENT_ID={kv.get('FYERS_CLIENT_ID', '')}",
        f"FYERS_SECRET_KEY={kv.get('FYERS_SECRET_KEY', '')}",
        f"FYERS_REDIRECT_URI={kv.get('FYERS_REDIRECT_URI', '')}",
        f"FYERS_ACCESS_TOKEN={kv.get('FYERS_ACCESS_TOKEN', '')}",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def wait_for_auth_code(
    redirect_uri: str,
    login_url: str,
    timeout_sec: int,
    open_browser: bool,
) -> str:
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    expected_path = parsed.path or "/"

    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError(
            "Auto callback requires localhost redirect URI "
            "(127.0.0.1 or localhost)."
        )

    auth_state = {"code": "", "error": ""}
    done = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            req = urlparse(self.path)
            if req.path != expected_path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")
                return

            q = parse_qs(req.query)
            auth_code = (q.get("auth_code") or [""])[0]
            status = (q.get("s") or [""])[0]
            msg = (q.get("message") or [""])[0]

            if auth_code:
                auth_state["code"] = auth_code
            else:
                auth_state["error"] = msg or status or "auth_code missing in callback"

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            body = (
                "<html><body><h3>FYERS auth received. "
                "You can close this tab.</h3></body></html>"
            )
            self.wfile.write(body.encode("utf-8"))
            done.set()

        def log_message(self, format, *args):  # noqa: A003
            return

    try:
        server = http.server.ThreadingHTTPServer((host, port), CallbackHandler)
    except OSError as exc:
        if exc.errno in {48, 98}:  # macOS/Linux address in use
            raise ValueError(
                f"Port {port} is busy. Close existing service on {host}:{port} "
                "or change redirect URI port."
            ) from exc
        raise

    server.timeout = 1
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        if open_browser:
            webbrowser.open(login_url, new=1)
        else:
            print("Open this URL in browser:")
            print(login_url)

        finished = done.wait(timeout_sec)
        if not finished:
            raise TimeoutError(
                f"Timed out waiting for callback at {host}:{port}{expected_path} "
                f"after {timeout_sec}s."
            )
    finally:
        server.shutdown()
        server.server_close()

    if auth_state["code"]:
        return auth_state["code"]
    raise ValueError(f"Callback error: {auth_state['error']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate FYERS login URL, exchange auth code, and save token."
    )
    parser.add_argument("--client-id", default=os.getenv("FYERS_CLIENT_ID", ""))
    parser.add_argument("--secret-key", default=os.getenv("FYERS_SECRET_KEY", ""))
    parser.add_argument("--redirect-uri", default=os.getenv("FYERS_REDIRECT_URI", ""))
    parser.add_argument(
        "--redirected-url",
        default="",
        help="Full redirected URL containing auth_code.",
    )
    parser.add_argument("--auth-code", default="")
    parser.add_argument("--env-file", default=".fyers.env")
    parser.add_argument("--ca-bundle", default=os.getenv("REQUESTS_CA_BUNDLE", ""))
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--auto-callback", action="store_true")
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--print-login-url-only", action="store_true")
    args = parser.parse_args()

    if not args.client_id:
        print("Error: missing client id (--client-id or FYERS_CLIENT_ID)", file=sys.stderr)
        return 1
    if not args.redirect_uri:
        print("Error: missing redirect uri (--redirect-uri or FYERS_REDIRECT_URI)", file=sys.stderr)
        return 1

    login_url = generate_login_url(args.client_id, args.redirect_uri)

    if args.print_login_url_only:
        print(login_url)
        return 0

    auth_code = args.auth_code.strip()
    if not auth_code and args.redirected_url.strip():
        try:
            auth_code = extract_auth_code(args.redirected_url)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    if not auth_code and args.auto_callback:
        try:
            auth_code = wait_for_auth_code(
                redirect_uri=args.redirect_uri,
                login_url=login_url,
                timeout_sec=args.timeout_sec,
                open_browser=not args.no_browser,
            )
            print("Auth code captured via localhost callback.")
        except Exception as exc:
            print(f"Error: auto callback failed: {exc}", file=sys.stderr)
            return 1

    if not auth_code:
        print("Step 1: Open this URL and login/approve app:")
        print(login_url)
        print("\nStep 2: Re-run with redirected URL:")
        print(
            'python scripts/fyers_auth.py --client-id "<id>" --secret-key "<secret>" '
            '--redirect-uri "<uri>" --redirected-url "<full_redirected_url>"'
        )
        return 0

    if not args.secret_key:
        print("Error: missing secret key (--secret-key or FYERS_SECRET_KEY)", file=sys.stderr)
        return 1

    try:
        verify = resolve_verify(args.ca_bundle, args.insecure)
        if args.insecure:
            print("Warning: SSL verification is disabled for this request.")
        token_resp = exchange_access_token(
            args.client_id, args.secret_key, auth_code, verify
        )
    except Exception as exc:
        print(f"Error: token exchange failed: {exc}", file=sys.stderr)
        return 1

    access_token = (
        token_resp.get("access_token")
        or token_resp.get("AccessToken")
        or token_resp.get("token")
        or ""
    )
    if not access_token:
        print("Error: access_token missing in FYERS response.", file=sys.stderr)
        if (
            str(token_resp.get("code")) == "-5"
            and "invalid app id hash" in str(token_resp.get("message", "")).lower()
        ):
            print(
                "Hint: Secret key is incorrect for this App ID. "
                "Use the exact FYERS Secret ID (case-sensitive).",
                file=sys.stderr,
            )
        print(json.dumps(token_resp, indent=2), file=sys.stderr)
        return 1

    write_env(
        args.env_file,
        {
            "FYERS_CLIENT_ID": args.client_id,
            "FYERS_SECRET_KEY": args.secret_key,
            "FYERS_REDIRECT_URI": args.redirect_uri,
            "FYERS_ACCESS_TOKEN": access_token,
        },
    )

    print("Authentication successful.")
    print(f"Saved credentials to: {args.env_file}")
    print("Use signal pull with:")
    print(f"set -a; source {args.env_file}; set +a")
    print(".venv/bin/python scripts/pull_fyers_signal.py --only-approved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

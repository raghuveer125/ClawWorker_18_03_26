#!/usr/bin/env python3
import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Tuple

from generate_live_signal_view import generate_once, ist_today, parse_indices, source_signature

# Get default indices from shared config
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from shared_project_engine.indices import ACTIVE_INDICES
    _DEFAULT_INDICES = ",".join(ACTIVE_INDICES)
except ImportError:
    _DEFAULT_INDICES = "SENSEX,NIFTY50,BANKNIFTY,FINNIFTY"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Serve live signal tables over local HTTP.")
    p.add_argument("--host", default="127.0.0.1", help="Bind host")
    p.add_argument("--port", type=int, default=8787, help="Bind port")
    p.add_argument("--base-dir", default="postmortem", help="Base folder containing date/index folders")
    p.add_argument("--date", default=ist_today(), help="Date folder (YYYY-MM-DD), default IST today")
    p.add_argument("--indices", default=_DEFAULT_INDICES, help="Comma-separated index list")
    p.add_argument("--source-file", default="decision_journal.csv", help="CSV name inside each index folder")
    p.add_argument("--events-file", default="opportunity_events.csv", help="Opportunity events CSV name inside each index folder")
    p.add_argument("--events-limit", type=int, default=20, help="Number of recent opportunity events to render")
    p.add_argument("--output", default="", help="Generated HTML output path")
    p.add_argument("--interval", type=int, default=15, help="Rebuild interval seconds")
    p.add_argument("--poll-interval", type=int, default=2, help="Browser-side change poll interval seconds")
    return p.parse_args()


class LiveViewState:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.base_dir = Path(args.base_dir)
        self.indices = parse_indices(args.indices)
        self.interval = max(1, int(args.interval))

        if args.output:
            self.output = Path(args.output)
        else:
            self.output = self.base_dir / args.date / "live_signal_view.html"

        self.gen_args = SimpleNamespace(
            base_dir=str(self.base_dir),
            date=args.date,
            indices=args.indices,
            source_file=args.source_file,
            events_file=args.events_file,
            events_limit=max(1, int(args.events_limit)),
            output=str(self.output),
            interval=self.interval,
            watch=False,
        )

        self.last_signature: Tuple[Tuple[str, bool, int, int], ...] = tuple()
        self.last_emit = 0.0
        self.last_change = 0.0

    def refresh_if_needed(self, force: bool = False) -> None:
        sig = source_signature(
            self.base_dir,
            self.args.date,
            self.indices,
            self.args.source_file,
            self.args.events_file,
        )
        now = time.time()
        interval_due = (now - self.last_emit) >= self.interval
        changed = sig != self.last_signature

        if force or changed or interval_due or not self.output.exists():
            generate_once(self.gen_args)
            self.last_emit = now
            if changed or force or self.last_change == 0.0:
                self.last_change = now
            self.last_signature = sig

    def version(self) -> Dict[str, int]:
        self.refresh_if_needed()
        return {
            "changedAt": int(self.last_change),
            "generatedAt": int(self.last_emit),
            "interval": self.interval,
        }

    def html(self) -> str:
        self.refresh_if_needed()
        return self.output.read_text(encoding="utf-8")


def inject_reload_script(html_text: str, poll_interval: int) -> str:
    ms = max(1, int(poll_interval)) * 1000
    script = (
        "<script>"
        "(function(){"
        "const key='live_signal_view_version';"
        "async function check(){"
        "try{"
        "const r=await fetch('/version',{cache:'no-store'});"
        "if(!r.ok){return;}"
        "const d=await r.json();"
        "const current=String(d.changedAt||0);"
        "const prev=sessionStorage.getItem(key);"
        "if(prev===null){sessionStorage.setItem(key,current);return;}"
        "if(prev!==current){sessionStorage.setItem(key,current);window.location.reload();}"
        "}catch(_e){}"
        "}"
        f"setInterval(check,{ms});"
        "})();"
        "</script>"
    )

    needle = "</body>"
    pos = html_text.lower().rfind(needle)
    if pos == -1:
        return html_text + script
    return html_text[:pos] + script + html_text[pos:]


class LiveHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()

    def do_GET(self) -> None:
        state: LiveViewState = self.server.live_state  # type: ignore[attr-defined]
        poll_interval: int = self.server.poll_interval  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]

        if path in ("/", "/index.html"):
            try:
                body = state.html()
                body = inject_reload_script(body, poll_interval)
                self._set_headers(200, "text/html; charset=utf-8")
                self.wfile.write(body.encode("utf-8"))
            except Exception as exc:
                self._set_headers(500, "text/plain; charset=utf-8")
                self.wfile.write(f"Failed to build view: {exc}\n".encode("utf-8"))
            return

        if path == "/version":
            try:
                payload = state.version()
                self._set_headers(200, "application/json; charset=utf-8")
                self.wfile.write(json.dumps(payload).encode("utf-8"))
            except Exception as exc:
                self._set_headers(500, "application/json; charset=utf-8")
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if path == "/health":
            self._set_headers(200, "application/json; charset=utf-8")
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            return

        self._set_headers(404, "text/plain; charset=utf-8")
        self.wfile.write(b"Not found\n")

    def log_message(self, format: str, *args) -> None:
        return


def main() -> int:
    args = parse_args()
    state = LiveViewState(args)
    state.refresh_if_needed(force=True)

    server = ThreadingHTTPServer((args.host, int(args.port)), LiveHandler)
    server.live_state = state  # type: ignore[attr-defined]
    server.poll_interval = max(1, int(args.poll_interval))  # type: ignore[attr-defined]

    print(f"Serving live view on http://{args.host}:{args.port}")
    print(
        f"Date={args.date} | Indices={','.join(state.indices)} | "
        f"Source={args.source_file} | Events={args.events_file}"
    )
    print(f"Rebuild interval={state.interval}s | Browser change poll={server.poll_interval}s")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

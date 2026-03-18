"""Runtime report CLI for the localhost market adapter service."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from typing import Any, Dict, Iterable, List, Tuple

warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL 1\.1\.1\+.*")

import requests

from ..services import get_market_adapter_url


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show duplicate-call metrics from the localhost market adapter.")
    parser.add_argument("--service-url", default=os.getenv("MARKET_ADAPTER_URL", get_market_adapter_url()))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("MARKET_ADAPTER_TIMEOUT_SEC", "3")))
    parser.add_argument("--top", type=int, default=10, help="Show this many duplicate keys.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON report.")
    parser.add_argument("--watch", type=float, default=0.0, help="Refresh every N seconds until interrupted.")
    parser.add_argument("--write-json", default="", help="Write the built JSON report to this path.")
    parser.add_argument("--write-text", default="", help="Write the human-readable report to this path.")
    return parser


def fetch_metrics(service_url: str, timeout: float) -> Dict[str, Any]:
    url = f"{service_url.rstrip('/')}/metrics"
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("metrics response is not a JSON object")
    return payload


def trim_top_items(items: Dict[str, Any], limit: int) -> List[Tuple[str, int]]:
    pairs = []
    for key, value in items.items():
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        pairs.append((str(key), count))
    pairs.sort(key=lambda item: (-item[1], item[0]))
    return pairs[: max(0, limit)]


def _format_rows(headers: Iterable[str], rows: List[List[str]]) -> str:
    headers_list = list(headers)
    widths = [len(header) for header in headers_list]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def render(values: List[str]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(values))

    lines = [render(headers_list), render(["-" * width for width in widths])]
    lines.extend(render(row) for row in rows)
    return "\n".join(lines)


def build_report(metrics: Dict[str, Any], top: int) -> Dict[str, Any]:
    endpoint_summary = metrics.get("endpoint_summary", {})
    top_duplicates = trim_top_items(metrics.get("top_duplicate_suppressed", {}), top)
    top_stream_subscribers = trim_top_items(metrics.get("quote_stream_subscribers_by_key", {}), top)

    endpoint_rows = []
    for endpoint, raw_summary in sorted(endpoint_summary.items()):
        summary = raw_summary if isinstance(raw_summary, dict) else {}
        endpoint_rows.append(
            {
                "endpoint": endpoint,
                "requests": int(summary.get("requests", 0) or 0),
                "upstream_fetches": int(summary.get("upstream_fetches", 0) or 0),
                "duplicate_suppressed": int(summary.get("duplicate_suppressed", 0) or 0),
                "cache_hit_rate_pct": float(summary.get("cache_hit_rate_pct", 0.0) or 0.0),
            }
        )

    return {
        "status": metrics.get("status", "unknown"),
        "uptime_sec": float(metrics.get("uptime_sec", 0.0) or 0.0),
        "started_at": metrics.get("started_at"),
        "first_started_at": metrics.get("first_started_at"),
        "last_saved_at": metrics.get("last_saved_at"),
        "total_requests": int(metrics.get("total_requests", 0) or 0),
        "total_upstream_fetches": int(metrics.get("total_upstream_fetches", 0) or 0),
        "total_duplicate_suppressed": int(metrics.get("total_duplicate_suppressed", 0) or 0),
        "cache_hit_rate_pct": float(metrics.get("cache_hit_rate_pct", 0.0) or 0.0),
        "session_total_requests": int(metrics.get("session_total_requests", 0) or 0),
        "session_total_upstream_fetches": int(metrics.get("session_total_upstream_fetches", 0) or 0),
        "session_total_duplicate_suppressed": int(metrics.get("session_total_duplicate_suppressed", 0) or 0),
        "session_cache_hit_rate_pct": float(metrics.get("session_cache_hit_rate_pct", 0.0) or 0.0),
        "active_quote_streams": int(metrics.get("active_quote_streams", 0) or 0),
        "active_quote_stream_subscribers": int(metrics.get("active_quote_stream_subscribers", 0) or 0),
        "metrics_path": str(metrics.get("metrics_path", "") or ""),
        "endpoint_rows": endpoint_rows,
        "top_duplicates": [{"key": key, "count": count} for key, count in top_duplicates],
        "top_stream_subscribers": [{"key": key, "count": count} for key, count in top_stream_subscribers],
    }


def render_text_report(report: Dict[str, Any]) -> str:
    lines = [
        "Market Adapter Runtime Report",
        f"Status: {report['status']}",
        f"Uptime: {report['uptime_sec']:.1f}s",
    ]
    metrics_path = str(report.get("metrics_path", "") or "").strip()
    if metrics_path:
        lines.append(f"Metrics file: {metrics_path}")

    session_total_requests = int(report.get("session_total_requests", 0) or 0)
    total_requests = int(report.get("total_requests", 0) or 0)
    if session_total_requests != total_requests:
        lines.extend(
            [
                f"Session local requests: {session_total_requests}",
                f"Session upstream FYERS fetches: {report['session_total_upstream_fetches']}",
                f"Session duplicate requests suppressed: {report['session_total_duplicate_suppressed']}",
                f"Session cache hit rate: {report['session_cache_hit_rate_pct']:.2f}%",
            ]
        )

    lines.extend(
        [
            f"Total local requests: {report['total_requests']}",
            f"Total upstream FYERS fetches: {report['total_upstream_fetches']}",
            f"Total duplicate requests suppressed: {report['total_duplicate_suppressed']}",
            f"Overall cache hit rate: {report['cache_hit_rate_pct']:.2f}%",
            f"Active quote streams: {report['active_quote_streams']}",
            f"Active quote stream subscribers: {report['active_quote_stream_subscribers']}",
        ]
    )

    endpoint_rows = report.get("endpoint_rows", [])
    if endpoint_rows:
        lines.extend(["", "By endpoint"])
        rows = [
            [
                row["endpoint"],
                str(row["requests"]),
                str(row["upstream_fetches"]),
                str(row["duplicate_suppressed"]),
                f"{row['cache_hit_rate_pct']:.2f}%",
            ]
            for row in endpoint_rows
        ]
        lines.append(_format_rows(["Endpoint", "Requests", "Upstream", "Dup Saved", "Hit Rate"], rows))

    top_duplicates = report.get("top_duplicates", [])
    if top_duplicates:
        lines.extend(["", "Top duplicate keys"])
        rows = [[str(item["count"]), str(item["key"])] for item in top_duplicates]
        lines.append(_format_rows(["Count", "Key"], rows))

    top_stream_subscribers = report.get("top_stream_subscribers", [])
    if top_stream_subscribers:
        lines.extend(["", "Top stream subscriptions"])
        rows = [[str(item["count"]), str(item["key"])] for item in top_stream_subscribers]
        lines.append(_format_rows(["Subs", "Key"], rows))

    return "\n".join(lines)


def print_text_report(report: Dict[str, Any]) -> None:
    print(render_text_report(report))


def _write_output(path: str, content: str) -> None:
    output_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(content)


def run_once(args: argparse.Namespace) -> int:
    try:
        metrics = fetch_metrics(args.service_url, args.timeout)
    except Exception as exc:
        print(f"Failed to fetch adapter metrics from {args.service_url}: {exc}", file=sys.stderr)
        return 1

    report = build_report(metrics, args.top)
    text_report = render_text_report(report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(text_report)

    if args.write_json:
        _write_output(args.write_json, json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.write_text:
        _write_output(args.write_text, text_report + "\n")
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.watch and args.watch > 0:
        while True:
            if not args.json:
                print("\033[2J\033[H", end="")
            rc = run_once(args)
            if rc != 0:
                return rc
            time.sleep(args.watch)

    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Localhost market-data service for sharing FYERS snapshots across engines."""

from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .adapter import MarketDataAdapter


class QuoteStreamHub:
    """Share one polling stream across multiple local subscribers."""

    def __init__(self, service: "MarketDataService") -> None:
        self.service = service
        self._lock = threading.Lock()
        self._streams: Dict[str, Dict[str, Any]] = {}
        self._next_subscriber_id = 0

    @staticmethod
    def _normalize_symbols(symbols: str) -> List[str]:
        normalized: List[str] = []
        seen = set()
        for raw_symbol in str(symbols or "").split(","):
            symbol = raw_symbol.strip()
            if not symbol:
                continue
            upper_symbol = symbol.upper()
            if upper_symbol in seen:
                continue
            seen.add(upper_symbol)
            normalized.append(symbol)
        return normalized

    @classmethod
    def _build_key(
        cls,
        symbols: List[str],
        interval_seconds: float,
        ttl_seconds: Optional[float],
    ) -> str:
        return json.dumps(
            {
                "symbols": sorted(symbols),
                "interval_seconds": round(float(interval_seconds), 3),
                "ttl_seconds": None if ttl_seconds is None else round(float(ttl_seconds), 3),
            },
            sort_keys=True,
        )

    def metrics(self) -> Dict[str, Any]:
        with self._lock:
            active_streams = len(self._streams)
            active_subscribers = sum(len(stream["queues"]) for stream in self._streams.values())
            subscribers_by_key = {
                key: len(stream["queues"])
                for key, stream in sorted(self._streams.items(), key=lambda item: item[0])
            }
        return {
            "active_quote_streams": active_streams,
            "active_quote_stream_subscribers": active_subscribers,
            "quote_stream_subscribers_by_key": subscribers_by_key,
        }

    def subscribe(
        self,
        symbols: str,
        interval_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        normalized_symbols = self._normalize_symbols(symbols)
        if not normalized_symbols:
            raise ValueError("symbols is required for quote stream")

        interval = max(
            0.25,
            float(interval_seconds or os.getenv("MARKET_ADAPTER_STREAM_INTERVAL_SEC", "1")),
        )
        ttl = None if ttl_seconds is None else float(ttl_seconds)
        key = self._build_key(normalized_symbols, interval, ttl)
        subscriber_queue: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=1)

        with self._lock:
            stream = self._streams.get(key)
            if stream is None:
                stop_event = threading.Event()
                thread = threading.Thread(
                    target=self._run_stream,
                    args=(key, normalized_symbols, interval, ttl, stop_event),
                    daemon=True,
                    name=f"market-quote-stream-{len(self._streams) + 1}",
                )
                stream = {
                    "symbols": normalized_symbols,
                    "interval_seconds": interval,
                    "ttl_seconds": ttl,
                    "stop_event": stop_event,
                    "thread": thread,
                    "queues": {},
                    "last_event": None,
                }
                self._streams[key] = stream
                thread.start()

            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            stream["queues"][subscriber_id] = subscriber_queue
            last_event = stream.get("last_event")

        if isinstance(last_event, dict):
            try:
                subscriber_queue.put_nowait(last_event)
            except queue.Full:
                pass

        def close() -> None:
            self.unsubscribe(key, subscriber_id)

        return {
            "subscription_key": key,
            "symbols": normalized_symbols,
            "interval_seconds": interval,
            "ttl_seconds": ttl,
            "queue": subscriber_queue,
            "close": close,
        }

    def unsubscribe(self, key: str, subscriber_id: int) -> None:
        with self._lock:
            stream = self._streams.get(key)
            if stream is None:
                return
            stream["queues"].pop(subscriber_id, None)
            if stream["queues"]:
                return
            stream["stop_event"].set()
            self._streams.pop(key, None)

    def _publish(self, key: str, event: Dict[str, Any]) -> None:
        with self._lock:
            stream = self._streams.get(key)
            if stream is not None:
                stream["last_event"] = event
                queues = list(stream["queues"].values())
            else:
                queues = []

        for subscriber_queue in queues:
            try:
                subscriber_queue.put_nowait(event)
            except queue.Full:
                try:
                    subscriber_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber_queue.put_nowait(event)
                except queue.Full:
                    pass

    def _run_stream(
        self,
        key: str,
        symbols: List[str],
        interval_seconds: float,
        ttl_seconds: Optional[float],
        stop_event: threading.Event,
    ) -> None:
        symbols_csv = ",".join(symbols)
        while not stop_event.is_set():
            try:
                payload = self.service.quotes(symbols_csv, ttl_seconds=ttl_seconds)
                event = {
                    "event": "quotes",
                    "subscription_key": key,
                    "symbols": symbols,
                    "interval_seconds": interval_seconds,
                    "ttl_seconds": ttl_seconds,
                    "emitted_at": time.time(),
                    "payload": payload,
                }
            except Exception as exc:
                event = {
                    "event": "error",
                    "subscription_key": key,
                    "symbols": symbols,
                    "interval_seconds": interval_seconds,
                    "ttl_seconds": ttl_seconds,
                    "emitted_at": time.time(),
                    "error": str(exc),
                }
            self._publish(key, event)
            if stop_event.wait(interval_seconds):
                break


class MarketDataService:
    """Thin HTTP wrapper around MarketDataAdapter with runtime metrics."""

    def __init__(
        self,
        adapter: Optional[MarketDataAdapter] = None,
        metrics_path: Optional[str] = None,
        persist_interval_seconds: Optional[float] = None,
        persist_every_requests: Optional[int] = None,
    ) -> None:
        self.adapter = adapter or MarketDataAdapter()
        self.started_at = time.time()
        self.first_started_at = self.started_at
        self._lock = threading.Lock()
        self.request_counts: Dict[str, int] = {}
        self.cache_hits: Dict[str, int] = {}
        self.cache_misses: Dict[str, int] = {}
        self.key_counts: Dict[str, int] = {}
        self.key_misses: Dict[str, int] = {}
        self.lifetime_request_counts: Dict[str, int] = {}
        self.lifetime_cache_hits: Dict[str, int] = {}
        self.lifetime_cache_misses: Dict[str, int] = {}
        self.lifetime_key_counts: Dict[str, int] = {}
        self.lifetime_key_misses: Dict[str, int] = {}
        default_metrics_path = Path(__file__).resolve().parent / ".cache" / "metrics.json"
        self.metrics_path = Path(metrics_path or os.getenv("MARKET_ADAPTER_METRICS_FILE", "") or default_metrics_path)
        self.persist_interval_seconds = max(
            0.25,
            float(persist_interval_seconds or os.getenv("MARKET_ADAPTER_METRICS_FLUSH_SEC", "5")),
        )
        self.persist_every_requests = max(
            1,
            int(persist_every_requests or os.getenv("MARKET_ADAPTER_METRICS_FLUSH_EVERY", "25")),
        )
        self.last_saved_at = 0.0
        self._last_persist_at = 0.0
        self._persist_pending_records = 0
        self._persist_dirty = False
        self.quote_stream_hub = QuoteStreamHub(self)
        self._load_metrics()

    @staticmethod
    def _counted_key(endpoint: str, key: str) -> str:
        clean_key = str(key or "").strip() or "__default__"
        return f"{endpoint}:{clean_key}"

    @staticmethod
    def _coerce_int_dict(payload: Any) -> Dict[str, int]:
        if not isinstance(payload, dict):
            return {}
        normalized: Dict[str, int] = {}
        for raw_key, raw_value in payload.items():
            key = str(raw_key).strip()
            if not key:
                continue
            try:
                normalized[key] = int(raw_value)
            except (TypeError, ValueError):
                continue
        return normalized

    @staticmethod
    def _record_into(
        request_counts: Dict[str, int],
        cache_hits: Dict[str, int],
        cache_misses: Dict[str, int],
        key_counts: Dict[str, int],
        key_misses: Dict[str, int],
        endpoint: str,
        key: str,
        cache_hit: bool,
    ) -> None:
        request_counts[endpoint] = request_counts.get(endpoint, 0) + 1
        if cache_hit:
            cache_hits[endpoint] = cache_hits.get(endpoint, 0) + 1
        else:
            cache_misses[endpoint] = cache_misses.get(endpoint, 0) + 1

        key_counts[key] = key_counts.get(key, 0) + 1
        if not cache_hit:
            key_misses[key] = key_misses.get(key, 0) + 1

    @staticmethod
    def _build_duplicate_counts(key_counts: Dict[str, int], key_misses: Dict[str, int]) -> Dict[str, int]:
        duplicate_suppressed = {}
        for key, request_count in key_counts.items():
            upstream_count = key_misses.get(key, 0)
            duplicate_suppressed[key] = max(0, request_count - upstream_count)
        return duplicate_suppressed

    @staticmethod
    def _build_endpoint_summary(
        request_counts: Dict[str, int],
        cache_hits: Dict[str, int],
        cache_misses: Dict[str, int],
    ) -> Dict[str, Dict[str, Any]]:
        endpoint_summary: Dict[str, Dict[str, Any]] = {}
        endpoint_names = sorted(set(request_counts) | set(cache_hits) | set(cache_misses))
        for endpoint in endpoint_names:
            requests_total = int(request_counts.get(endpoint, 0))
            hits_total = int(cache_hits.get(endpoint, 0))
            misses_total = int(cache_misses.get(endpoint, 0))
            endpoint_summary[endpoint] = {
                "requests": requests_total,
                "cache_hits": hits_total,
                "cache_misses": misses_total,
                "upstream_fetches": misses_total,
                "duplicate_suppressed": max(0, requests_total - misses_total),
                "cache_hit_rate_pct": round((hits_total / requests_total * 100.0), 2) if requests_total else 0.0,
            }
        return endpoint_summary

    def _load_metrics(self) -> None:
        if not self.metrics_path.exists():
            return
        try:
            payload = json.loads(self.metrics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return

        self.first_started_at = float(payload.get("first_started_at", self.started_at) or self.started_at)
        self.last_saved_at = float(payload.get("saved_at", 0.0) or 0.0)
        self.lifetime_request_counts = self._coerce_int_dict(payload.get("request_counts"))
        self.lifetime_cache_hits = self._coerce_int_dict(payload.get("cache_hits"))
        self.lifetime_cache_misses = self._coerce_int_dict(payload.get("cache_misses"))
        self.lifetime_key_counts = self._coerce_int_dict(payload.get("key_counts"))
        self.lifetime_key_misses = self._coerce_int_dict(payload.get("key_misses"))

    def _snapshot_persisted_metrics_locked(self, saved_at: float) -> Dict[str, Any]:
        return {
            "version": 1,
            "saved_at": saved_at,
            "first_started_at": self.first_started_at,
            "request_counts": dict(self.lifetime_request_counts),
            "cache_hits": dict(self.lifetime_cache_hits),
            "cache_misses": dict(self.lifetime_cache_misses),
            "key_counts": dict(self.lifetime_key_counts),
            "key_misses": dict(self.lifetime_key_misses),
        }

    def persist_metrics(self, force: bool = False) -> None:
        with self._lock:
            now = time.time()
            if not self._persist_dirty and not force:
                return
            if (
                not force
                and self._persist_pending_records < self.persist_every_requests
                and (now - self._last_persist_at) < self.persist_interval_seconds
            ):
                return
            snapshot = self._snapshot_persisted_metrics_locked(now)
            self._persist_dirty = False
            self._persist_pending_records = 0
            self._last_persist_at = now

        try:
            self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.metrics_path.with_suffix(f"{self.metrics_path.suffix}.tmp")
            temp_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
            temp_path.replace(self.metrics_path)
            with self._lock:
                self.last_saved_at = now
        except OSError:
            with self._lock:
                self._persist_dirty = True
                self._persist_pending_records = max(1, self._persist_pending_records)

    def flush_metrics(self) -> None:
        self.persist_metrics(force=True)

    def _record(self, endpoint: str, key: str, cache_hit: bool) -> None:
        counted_key = self._counted_key(endpoint, key)
        should_persist = False
        with self._lock:
            self._record_into(
                self.request_counts,
                self.cache_hits,
                self.cache_misses,
                self.key_counts,
                self.key_misses,
                endpoint,
                counted_key,
                cache_hit,
            )
            self._record_into(
                self.lifetime_request_counts,
                self.lifetime_cache_hits,
                self.lifetime_cache_misses,
                self.lifetime_key_counts,
                self.lifetime_key_misses,
                endpoint,
                counted_key,
                cache_hit,
            )
            self._persist_dirty = True
            self._persist_pending_records += 1
            should_persist = (
                self._persist_pending_records >= self.persist_every_requests
                or (time.time() - self._last_persist_at) >= self.persist_interval_seconds
            )
        if should_persist:
            self.persist_metrics(force=False)

    @staticmethod
    def _top_items(data: Dict[str, int], limit: int = 50) -> Dict[str, int]:
        ordered = sorted(data.items(), key=lambda item: (-item[1], item[0]))
        return dict(ordered[:limit])

    def get_metrics(self) -> Dict[str, Any]:
        with self._lock:
            session_request_counts = dict(self.request_counts)
            session_cache_hits = dict(self.cache_hits)
            session_cache_misses = dict(self.cache_misses)
            session_key_counts = dict(self.key_counts)
            session_key_misses = dict(self.key_misses)
            request_counts = dict(self.lifetime_request_counts)
            cache_hits = dict(self.lifetime_cache_hits)
            cache_misses = dict(self.lifetime_cache_misses)
            key_counts = dict(self.lifetime_key_counts)
            key_misses = dict(self.lifetime_key_misses)
            last_saved_at = self.last_saved_at

        duplicate_suppressed = self._build_duplicate_counts(key_counts, key_misses)
        session_duplicate_suppressed = self._build_duplicate_counts(session_key_counts, session_key_misses)
        endpoint_summary = self._build_endpoint_summary(request_counts, cache_hits, cache_misses)
        session_endpoint_summary = self._build_endpoint_summary(
            session_request_counts,
            session_cache_hits,
            session_cache_misses,
        )

        total_requests = sum(request_counts.values())
        total_cache_hits = sum(cache_hits.values())
        total_cache_misses = sum(cache_misses.values())
        total_duplicate_suppressed = max(0, total_requests - total_cache_misses)
        session_total_requests = sum(session_request_counts.values())
        session_total_cache_hits = sum(session_cache_hits.values())
        session_total_cache_misses = sum(session_cache_misses.values())
        session_total_duplicate_suppressed = max(0, session_total_requests - session_total_cache_misses)
        stream_metrics = self.quote_stream_hub.metrics()

        return {
            "status": "ok",
            "started_at": self.started_at,
            "first_started_at": self.first_started_at,
            "last_saved_at": last_saved_at,
            "uptime_sec": round(time.time() - self.started_at, 3),
            "total_requests": total_requests,
            "total_cache_hits": total_cache_hits,
            "total_cache_misses": total_cache_misses,
            "total_upstream_fetches": total_cache_misses,
            "total_duplicate_suppressed": total_duplicate_suppressed,
            "cache_hit_rate_pct": round((total_cache_hits / total_requests * 100.0), 2) if total_requests else 0.0,
            "session_total_requests": session_total_requests,
            "session_total_cache_hits": session_total_cache_hits,
            "session_total_cache_misses": session_total_cache_misses,
            "session_total_upstream_fetches": session_total_cache_misses,
            "session_total_duplicate_suppressed": session_total_duplicate_suppressed,
            "session_cache_hit_rate_pct": round(
                (session_total_cache_hits / session_total_requests * 100.0),
                2,
            ) if session_total_requests else 0.0,
            "endpoint_summary": endpoint_summary,
            "session_endpoint_summary": session_endpoint_summary,
            "request_counts": request_counts,
            "session_request_counts": session_request_counts,
            "cache_hits": cache_hits,
            "session_cache_hits": session_cache_hits,
            "cache_misses": cache_misses,
            "session_cache_misses": session_cache_misses,
            "upstream_fetches": cache_misses,
            "session_upstream_fetches": session_cache_misses,
            "top_request_keys": self._top_items(key_counts),
            "top_duplicate_suppressed": self._top_items(duplicate_suppressed),
            "session_top_duplicate_suppressed": self._top_items(session_duplicate_suppressed),
            "metrics_path": str(self.metrics_path),
            **stream_metrics,
        }

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "started_at": self.started_at,
            "first_started_at": self.first_started_at,
            "metrics_path": str(self.metrics_path),
            "uptime_sec": round(time.time() - self.started_at, 3),
        }

    def index(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "service": "market_adapter",
            "started_at": self.started_at,
            "first_started_at": self.first_started_at,
            "metrics_path": str(self.metrics_path),
            "uptime_sec": round(time.time() - self.started_at, 3),
            "endpoints": [
                "/health",
                "/metrics",
                "/quote",
                "/quotes",
                "/stream/quotes",
                "/history",
                "/history-range",
                "/option-chain",
                "/future-quote",
                "/index-snapshot",
            ],
        }

    def quote(self, symbol: str, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        payload = self.adapter.get_quote(symbol, ttl_seconds=ttl_seconds)
        self._record("quote", symbol, bool(payload.get("cache_hit", False)))
        return payload

    def quotes(self, symbols: str, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        payload = self.adapter.get_quotes(symbols, ttl_seconds=ttl_seconds)
        key = ",".join(sorted(payload.get("symbols", []))) or str(symbols or "")
        self._record("quotes", key, bool(payload.get("cache_hit", False)))
        return payload

    def open_quote_stream(
        self,
        symbols: str,
        interval_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self.quote_stream_hub.subscribe(
            symbols=symbols,
            interval_seconds=interval_seconds,
            ttl_seconds=ttl_seconds,
        )

    def history(
        self,
        symbol: str,
        resolution: str,
        lookback_days: int,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload = self.adapter.get_history_snapshot(
            symbol=symbol,
            resolution=resolution,
            lookback_days=lookback_days,
            ttl_seconds=ttl_seconds,
        )
        key = json.dumps(
            {"symbol": symbol, "resolution": resolution, "lookback_days": lookback_days},
            sort_keys=True,
        )
        self._record("history", key, bool(payload.get("_cache_hit", False)))
        return payload

    def history_range(
        self,
        symbol: str,
        resolution: str,
        range_from: str,
        range_to: str,
        date_format: str = "1",
        cont_flag: str = "1",
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload = self.adapter.get_history_range(
            symbol=symbol,
            resolution=resolution,
            range_from=range_from,
            range_to=range_to,
            date_format=date_format,
            cont_flag=cont_flag,
            ttl_seconds=ttl_seconds,
        )
        key = json.dumps(
            {
                "symbol": symbol,
                "resolution": resolution,
                "range_from": range_from,
                "range_to": range_to,
                "date_format": date_format,
                "cont_flag": cont_flag,
            },
            sort_keys=True,
        )
        self._record("history_range", key, bool(payload.get("_cache_hit", False)))
        return payload

    def option_chain(
        self,
        symbol: str,
        strike_count: int,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload = self.adapter.get_option_chain_snapshot(
            symbol=symbol,
            strike_count=strike_count,
            ttl_seconds=ttl_seconds,
        )
        key = json.dumps({"symbol": symbol, "strike_count": strike_count}, sort_keys=True)
        self._record("option_chain", key, bool(payload.get("_cache_hit", False)))
        return payload

    def future_quote(
        self,
        index_name: str,
        explicit_symbol: str = "",
        now_local: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        parsed_now = None
        if now_local:
            try:
                parsed_now = datetime.fromisoformat(now_local)
            except ValueError:
                parsed_now = None
        cache_hit = True
        symbol = ""
        ltp = 0.0
        if explicit_symbol:
            quote = self.adapter.get_quote(explicit_symbol, ttl_seconds=ttl_seconds)
            symbol = explicit_symbol
            ltp = float(quote.get("ltp", 0.0) or 0.0)
            cache_hit = bool(quote.get("cache_hit", False))
        else:
            for candidate in self.adapter.futures_candidates(index_name, now_local=parsed_now):
                quote = self.adapter.get_quote(candidate, ttl_seconds=ttl_seconds)
                cache_hit = cache_hit and bool(quote.get("cache_hit", False))
                ltp = float(quote.get("ltp", 0.0) or 0.0)
                if ltp > 0:
                    symbol = candidate
                    break
        key = explicit_symbol or index_name
        self._record("future_quote", key, cache_hit)
        return {"symbol": symbol, "ltp": ltp}

    def index_snapshot(
        self,
        index_name: str,
        resolution: str,
        lookback_days: int,
        strike_count: int,
        fut_symbol: str,
    ) -> Dict[str, Any]:
        payload = self.adapter.get_index_market_data(
            index_name=index_name,
            resolution=resolution,
            lookback_days=lookback_days,
            strike_count=strike_count,
            fut_symbol=fut_symbol,
        )
        history_hit = bool(payload.get("history", {}).get("_cache_hit", False))
        option_hit = bool(payload.get("option_chain", {}).get("_cache_hit", False))
        quote_hit = bool(payload.get("quote", {}).get("cache_hit", False))
        vix_hit = bool(payload.get("vix_quote", {}).get("cache_hit", False))
        self._record("index_snapshot", index_name, history_hit and option_hit and quote_hit and vix_hit)
        return payload


class MarketDataRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for the market-data service."""

    service: MarketDataService
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        if os.getenv("MARKET_ADAPTER_QUIET", "1") == "1":
            return
        super().log_message(format, *args)

    def _parse_float(self, values: Dict[str, list[str]], name: str) -> Optional[float]:
        raw = values.get(name, [""])[0]
        if raw == "":
            return None
        return float(raw)

    def _parse_int(self, values: Dict[str, list[str]], name: str, default: int) -> int:
        raw = values.get(name, [""])[0]
        return int(raw) if raw != "" else default

    def _send_json(self, status_code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

    @staticmethod
    def _encode_sse(event_name: str, payload: Dict[str, Any]) -> bytes:
        body = json.dumps(payload, separators=(",", ":"))
        return f"event: {event_name}\ndata: {body}\n\n".encode("utf-8")

    def _serve_quote_stream(
        self,
        symbols: str,
        interval_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
    ) -> None:
        subscription = self.service.open_quote_stream(
            symbols=symbols,
            interval_seconds=interval_seconds,
            ttl_seconds=ttl_seconds,
        )
        keepalive_seconds = max(
            1.0,
            float(os.getenv("MARKET_ADAPTER_STREAM_KEEPALIVE_SEC", "5")),
        )
        self._send_sse_headers()
        try:
            connected_event = {
                "event": "connected",
                "subscription_key": subscription["subscription_key"],
                "symbols": subscription["symbols"],
                "interval_seconds": subscription["interval_seconds"],
                "ttl_seconds": subscription["ttl_seconds"],
                "emitted_at": time.time(),
            }
            self.wfile.write(self._encode_sse("connected", connected_event))
            self.wfile.flush()

            while True:
                try:
                    event = subscription["queue"].get(timeout=keepalive_seconds)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue

                event_name = str(event.get("event", "message") or "message")
                self.wfile.write(self._encode_sse(event_name, event))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            close = subscription.get("close")
            if callable(close):
                close()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        try:
            if parsed.path in {"", "/"}:
                self._send_json(200, self.service.index())
                return

            if parsed.path == "/health":
                self._send_json(200, self.service.health())
                return

            if parsed.path == "/metrics":
                self._send_json(200, self.service.get_metrics())
                return

            if parsed.path == "/quote":
                symbol = params.get("symbol", [""])[0]
                payload = self.service.quote(symbol, ttl_seconds=self._parse_float(params, "ttl_seconds"))
                self._send_json(200, payload)
                return

            if parsed.path == "/quotes":
                symbols = params.get("symbols", [""])[0]
                payload = self.service.quotes(symbols, ttl_seconds=self._parse_float(params, "ttl_seconds"))
                self._send_json(200, payload)
                return

            if parsed.path == "/stream/quotes":
                self._serve_quote_stream(
                    symbols=params.get("symbols", [""])[0],
                    interval_seconds=self._parse_float(params, "interval_seconds"),
                    ttl_seconds=self._parse_float(params, "ttl_seconds"),
                )
                return

            if parsed.path == "/history":
                symbol = params.get("symbol", [""])[0]
                resolution = params.get("resolution", ["5"])[0]
                lookback_days = self._parse_int(params, "lookback_days", 5)
                payload = self.service.history(
                    symbol=symbol,
                    resolution=resolution,
                    lookback_days=lookback_days,
                    ttl_seconds=self._parse_float(params, "ttl_seconds"),
                )
                self._send_json(200, payload)
                return

            if parsed.path == "/history-range":
                symbol = params.get("symbol", [""])[0]
                resolution = params.get("resolution", ["5"])[0]
                payload = self.service.history_range(
                    symbol=symbol,
                    resolution=resolution,
                    range_from=params.get("range_from", [""])[0],
                    range_to=params.get("range_to", [""])[0],
                    date_format=params.get("date_format", ["1"])[0],
                    cont_flag=params.get("cont_flag", ["1"])[0],
                    ttl_seconds=self._parse_float(params, "ttl_seconds"),
                )
                self._send_json(200, payload)
                return

            if parsed.path == "/option-chain":
                symbol = params.get("symbol", [""])[0]
                strike_count = self._parse_int(params, "strike_count", 10)
                payload = self.service.option_chain(
                    symbol=symbol,
                    strike_count=strike_count,
                    ttl_seconds=self._parse_float(params, "ttl_seconds"),
                )
                self._send_json(200, payload)
                return

            if parsed.path == "/future-quote":
                payload = self.service.future_quote(
                    index_name=params.get("index_name", [""])[0],
                    explicit_symbol=params.get("explicit_symbol", [""])[0],
                    now_local=params.get("now_local", [""])[0] or None,
                    ttl_seconds=self._parse_float(params, "ttl_seconds"),
                )
                self._send_json(200, payload)
                return

            if parsed.path == "/index-snapshot":
                payload = self.service.index_snapshot(
                    index_name=params.get("index_name", [""])[0],
                    resolution=params.get("resolution", ["5"])[0],
                    lookback_days=self._parse_int(params, "lookback_days", 5),
                    strike_count=self._parse_int(params, "strike_count", 8),
                    fut_symbol=params.get("fut_symbol", [""])[0],
                )
                self._send_json(200, payload)
                return

            self._send_json(404, {"status": "error", "error": f"Unknown path: {parsed.path}"})
        except Exception as exc:
            self._send_json(500, {"status": "error", "error": str(exc)})


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the shared localhost market-data service.")
    parser.add_argument("--host", default=os.getenv("MARKET_ADAPTER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MARKET_ADAPTER_PORT", "8765")))
    parser.add_argument("--env-file", default=os.getenv("MARKET_ADAPTER_ENV_FILE", ""))
    parser.add_argument("--metrics-file", default=os.getenv("MARKET_ADAPTER_METRICS_FILE", ""))
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    adapter = MarketDataAdapter(env_file=args.env_file or None)
    service = MarketDataService(
        adapter=adapter,
        metrics_path=args.metrics_file or None,
    )
    server = ThreadingHTTPServer((args.host, args.port), MarketDataRequestHandler)
    MarketDataRequestHandler.service = service
    print(f"Market adapter service listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.flush_metrics()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

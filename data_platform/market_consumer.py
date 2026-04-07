"""
KafkaMarketDataClient — drop-in replacement for shared_project_engine MarketDataClient.

Live quotes:   Kafka topics (market.quotes, market.validated.ticks, per-index ticks)
               maintained in an in-memory cache by a background consumer thread.
History:       PostgreSQL (market_ohlcv_history via OHLCVHistoryConfig).
Option chain:  Kafka topic (market.optionchain) cached per index.
Fallback:      If Kafka is unavailable, falls back to the existing MarketDataClient.

Drop-in usage:
    # Before (HTTP polling)
    from shared_project_engine.market.client import MarketDataClient
    client = MarketDataClient(service_url="http://127.0.0.1:8765")

    # After (Kafka-backed)
    from data_platform.market_consumer import KafkaMarketDataClient
    client = KafkaMarketDataClient.from_env()

All method signatures are identical to MarketDataClient.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional, Tuple

_log = logging.getLogger(__name__)

# ── Topic subscriptions consumed by the background thread ────────────────────
_LIVE_TOPICS = [
    "market.quotes",
    "market.validated.ticks",
    "market.validated.options",
    "market.optionchain",
    "market.vix",
    "market.futures",
    # Per-index tick topics
    "market.nifty50.ticks",
    "market.banknifty.ticks",
    "market.sensex.ticks",
    "market.finnifty.ticks",
]


class KafkaMarketDataClient:
    """
    Kafka-backed market data client.

    Starts a background consumer thread on first use.  All get_quote() calls
    read from the in-memory cache populated by that thread.  History calls
    go to PostgreSQL.  Falls back to the original MarketDataClient when
    Kafka is not reachable.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        consumer_group: str = "clawworker-market-consumer",
        db_host: str = "localhost",
        db_port: int = 5432,
        db_name: str = "clawworker",
        db_user: str = "clawworker",
        db_password: str = "clawworker",
        cache_ttl_seconds: float = 10.0,
        fallback_service_url: str = "",
        # Legacy kwargs from SharedMarketDataClient — accepted and ignored
        env_file: Optional[str] = None,
        fallback_to_local: bool = True,
        strict_mode: bool = False,
        **_legacy_kwargs: Any,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._consumer_group = consumer_group
        self._db_host = db_host
        self._db_port = db_port
        self._db_name = db_name
        self._db_user = db_user
        self._db_password = db_password
        self._cache_ttl = cache_ttl_seconds
        self._fallback_url = fallback_service_url

        # Live quote cache: symbol → {ltp, bid, ask, timestamp, ...}
        self._quotes: dict[str, dict[str, Any]] = {}
        self._option_chains: dict[str, dict[str, Any]] = {}  # index → chain
        self._lock = threading.RLock()

        self._consumer_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._kafka_healthy = False
        self._kafka_unhealthy_logged = False  # log warning only once

        self._start_lock = threading.Lock()  # guard against double-start race

        self._fallback: Any = None  # lazy MarketDataClient

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "KafkaMarketDataClient":
        src = env or dict(os.environ)
        return cls(
            bootstrap_servers=src.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            consumer_group=src.get("KAFKA_CONSUMER_GROUP", "clawworker-market-consumer"),
            db_host=src.get("CLAWWORKER_DB_HOST", "localhost"),
            db_port=int(src.get("CLAWWORKER_DB_PORT", "5432")),
            db_name=src.get("CLAWWORKER_DB_NAME", "clawworker"),
            db_user=src.get("CLAWWORKER_DB_USER", "clawworker"),
            db_password=src.get("CLAWWORKER_DB_PASSWORD", "clawworker"),
            cache_ttl_seconds=float(src.get("MARKET_CACHE_TTL_SEC", "10")),
            fallback_service_url=src.get("MARKET_ADAPTER_URL", "http://127.0.0.1:8765"),
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start background Kafka consumer thread. Safe to call multiple times."""
        with self._start_lock:
            if self._consumer_thread and self._consumer_thread.is_alive():
                return
            self._stop_event.clear()
            self._consumer_thread = threading.Thread(
                target=self._consume_loop, daemon=True, name="kafka-market-consumer"
            )
            self._consumer_thread.start()
            _log.info("KafkaMarketDataClient: consumer thread started")

    def stop(self) -> None:
        self._stop_event.set()
        time.sleep(0.5)  # allow consumer thread to observe stop event and clean up
        if self._consumer_thread:
            self._consumer_thread.join(timeout=5)

    _MAX_CONSUMER_RESTARTS = 5

    def _consume_loop(self) -> None:
        try:
            from confluent_kafka import Consumer, KafkaError  # type: ignore
        except ImportError:
            _log.warning("KafkaMarketDataClient: confluent-kafka not installed — Kafka disabled, using fallback")
            return

        restart_count = 0
        while restart_count <= self._MAX_CONSUMER_RESTARTS and not self._stop_event.is_set():
            cfg = {
                "bootstrap.servers": self._bootstrap_servers,
                "group.id": self._consumer_group,
                "auto.offset.reset": "latest",
                "enable.auto.commit": False,
            }
            consumer = Consumer(cfg)
            try:
                consumer.subscribe(_LIVE_TOPICS)
                self._kafka_healthy = True
                _log.info(
                    "KafkaMarketDataClient: subscribed to %d topics (attempt %d)",
                    len(_LIVE_TOPICS),
                    restart_count + 1,
                )

                while not self._stop_event.is_set():
                    msg = consumer.poll(timeout=0.5)
                    if msg is None:
                        continue
                    if msg.error():
                        if msg.error().code() != KafkaError._PARTITION_EOF:
                            _log.warning("KafkaMarketDataClient: consumer error: %s", msg.error())
                        continue
                    self._handle_message(msg.topic(), msg.value())
                    consumer.commit(asynchronous=True)

                # Clean exit via stop event — do not retry
                return

            except Exception as exc:
                restart_count += 1
                self._kafka_healthy = False
                if restart_count > self._MAX_CONSUMER_RESTARTS:
                    _log.critical(
                        "KafkaMarketDataClient: consumer exhausted %d restarts, "
                        "Kafka permanently unhealthy: %s",
                        self._MAX_CONSUMER_RESTARTS,
                        exc,
                        exc_info=True,
                    )
                    return
                backoff = 2 ** restart_count  # 2, 4, 8, 16, 32
                _log.error(
                    "KafkaMarketDataClient: consumer loop crashed (attempt %d/%d), "
                    "retrying in %ds: %s",
                    restart_count,
                    self._MAX_CONSUMER_RESTARTS,
                    backoff,
                    exc,
                    exc_info=True,
                )
                # Wait with backoff, but respect stop event
                self._stop_event.wait(timeout=backoff)
            finally:
                try:
                    consumer.close()
                except Exception:
                    pass

    def _handle_message(self, topic: str, raw: bytes | None) -> None:
        if not raw:
            return
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return

        payload = data.get("payload", data)
        if not isinstance(payload, dict):
            return

        # Route by topic
        if "ticks" in topic or topic == "market.quotes":
            symbol = str(payload.get("symbol", ""))
            if symbol:
                with self._lock:
                    self._quotes[symbol] = {**payload, "_ts": time.monotonic()}

        elif topic == "market.optionchain" or "optionchain" in topic:
            index = str(data.get("index", payload.get("index", "")))
            if index:
                with self._lock:
                    self._option_chains[index.upper()] = {**payload, "_ts": time.monotonic()}

    # ── Public API — same signatures as MarketDataClient ─────────────────────

    def get_quote(self, symbol: str, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        self._ensure_started()

        # Circuit breaker: if Kafka is permanently unhealthy and consumer is dead,
        # skip cache lookup and go straight to fallback.
        if not self._kafka_healthy and (
            self._consumer_thread is None or not self._consumer_thread.is_alive()
        ):
            if not self._kafka_unhealthy_logged:
                _log.warning(
                    "KafkaMarketDataClient: Kafka is unhealthy and consumer thread is dead — "
                    "routing all get_quote calls to fallback"
                )
                self._kafka_unhealthy_logged = True
            fb = self._get_fallback()
            if fb:
                return fb.get_quote(symbol, ttl_seconds=ttl_seconds)
            return {"symbol": symbol, "ltp": 0.0, "_source": "kafka_circuit_open"}

        ttl = ttl_seconds if ttl_seconds is not None else self._cache_ttl
        with self._lock:
            cached = self._quotes.get(symbol)
            if cached and (time.monotonic() - cached.get("_ts", 0)) <= ttl:
                return {**cached, "_source": "kafka"}

        # Cache miss — try fallback
        fb = self._get_fallback()
        if fb:
            return fb.get_quote(symbol, ttl_seconds=ttl_seconds)
        return {"symbol": symbol, "ltp": 0.0, "_source": "kafka_miss"}

    def get_quotes(self, symbols: Any, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        self._ensure_started()
        if isinstance(symbols, str):
            sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        else:
            sym_list = [str(s).strip() for s in (symbols or []) if str(s).strip()]

        # Circuit breaker: if Kafka is permanently unhealthy and consumer is dead,
        # skip cache lookup and go straight to fallback.
        if not self._kafka_healthy and (
            self._consumer_thread is None or not self._consumer_thread.is_alive()
        ):
            if not self._kafka_unhealthy_logged:
                _log.warning(
                    "KafkaMarketDataClient: Kafka is unhealthy and consumer thread is dead — "
                    "routing all get_quotes calls to fallback"
                )
                self._kafka_unhealthy_logged = True
            fb = self._get_fallback()
            if fb:
                return fb.get_quotes(",".join(sym_list), ttl_seconds=ttl_seconds)
            return {sym: {"symbol": sym, "ltp": 0.0, "_source": "kafka_circuit_open"} for sym in sym_list}

        result: dict[str, Any] = {}
        missing: list[str] = []
        ttl = ttl_seconds if ttl_seconds is not None else self._cache_ttl

        with self._lock:
            for sym in sym_list:
                cached = self._quotes.get(sym)
                if cached and (time.monotonic() - cached.get("_ts", 0)) <= ttl:
                    result[sym] = {**cached, "_source": "kafka"}
                else:
                    missing.append(sym)

        if missing:
            fb = self._get_fallback()
            if fb:
                fb_result = fb.get_quotes(",".join(missing), ttl_seconds=ttl_seconds)
                result.update(fb_result)

        return result

    def quotes(self, symbols: Any, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        return self.get_quotes(symbols, ttl_seconds=ttl_seconds)

    def get_quote_ltp(self, symbol: str, ttl_seconds: Optional[float] = None) -> float:
        return float(self.get_quote(symbol, ttl_seconds=ttl_seconds).get("ltp", 0.0) or 0.0)

    def stream_quotes(
        self,
        symbols: Any,
        interval_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
        read_timeout_seconds: Optional[float] = None,
    ) -> Iterator[Dict[str, Any]]:
        self._ensure_started()
        interval = interval_seconds or 1.0
        deadline = time.monotonic() + (read_timeout_seconds or float("inf"))

        if isinstance(symbols, str):
            sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        else:
            sym_list = [str(s).strip() for s in (symbols or []) if str(s).strip()]

        while time.monotonic() < deadline and not self._stop_event.is_set():
            batch: dict[str, Any] = {}
            with self._lock:
                for sym in sym_list:
                    cached = self._quotes.get(sym)
                    if cached:
                        batch[sym] = {**cached, "_source": "kafka"}
            if batch:
                yield batch
            self._stop_event.wait(timeout=interval)

    def get_history_snapshot(
        self,
        symbol: str,
        resolution: str = "5",
        lookback_days: int = 5,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Fetch OHLCV history from PostgreSQL."""
        try:
            from data_platform.db.ohlcv_history import OHLCVHistoryConfig, sync_ohlcv_for_symbol
            cfg = OHLCVHistoryConfig(
                host=self._db_host, port=self._db_port,
                database=self._db_name, user=self._db_user, password=self._db_password,
            )
            res_map = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "D": "1d"}
            res_str = res_map.get(str(resolution), f"{resolution}m")
            records = sync_ohlcv_for_symbol(cfg, symbol, res_str, days=lookback_days)
            candles = [
                {
                    "t": int(r.ts.timestamp()),
                    "o": r.open, "h": r.high, "l": r.low, "c": r.close, "v": r.volume,
                }
                for r in records
            ]
            return {"symbol": symbol, "resolution": resolution, "candles": candles, "_source": "postgres"}
        except Exception as exc:
            _log.warning("KafkaMarketDataClient: history from DB failed (%s) — using fallback", exc)
            fb = self._get_fallback()
            if fb:
                return fb.get_history_snapshot(symbol, resolution, lookback_days, ttl_seconds)
            return {"symbol": symbol, "resolution": resolution, "candles": [], "_source": "error"}

    def get_history_range(
        self,
        symbol: str,
        resolution: str,
        range_from: str,
        range_to: str,
        date_format: str = "1",
        cont_flag: str = "1",
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        fb = self._get_fallback()
        if fb:
            return fb.get_history_range(symbol, resolution, range_from, range_to,
                                        date_format, cont_flag, ttl_seconds)
        return {"symbol": symbol, "candles": [], "_source": "unavailable"}

    def get_option_chain_snapshot(
        self,
        symbol: str,
        strike_count: int = 10,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        self._ensure_started()
        index_key = symbol.split(":")[1].split("-")[0] if ":" in symbol else symbol
        index_key = index_key.upper()
        ttl = ttl_seconds if ttl_seconds is not None else 30.0

        with self._lock:
            cached = self._option_chains.get(index_key)
            if cached and (time.monotonic() - cached.get("_ts", 0)) <= ttl:
                return {**cached, "_source": "kafka"}

        fb = self._get_fallback()
        if fb:
            return fb.get_option_chain_snapshot(symbol, strike_count, ttl_seconds)
        return {"symbol": symbol, "contracts": [], "_source": "kafka_miss"}

    def resolve_future_quote(
        self,
        index_name: str,
        explicit_symbol: str = "",
        now_local: Optional[datetime] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Tuple[str, float]:
        # Try to resolve from Kafka cache first via futures topic
        with self._lock:
            for sym, data in self._quotes.items():
                if "FUT" in sym and index_name.upper() in sym:
                    return sym, float(data.get("ltp", 0.0))

        fb = self._get_fallback()
        if fb:
            return fb.resolve_future_quote(index_name, explicit_symbol, now_local, ttl_seconds)
        return "", 0.0

    def get_index_market_data(
        self,
        index_name: str,
        resolution: str = "5",
        lookback_days: int = 5,
        strike_count: int = 8,
        fut_symbol: str = "",
    ) -> Dict[str, Any]:
        fb = self._get_fallback()
        if fb:
            return fb.get_index_market_data(index_name, resolution, lookback_days, strike_count, fut_symbol)
        return {"index_name": index_name, "_source": "unavailable"}

    # ── Internals ─────────────────────────────────────────────────────────────

    def _ensure_started(self) -> None:
        if self._consumer_thread is None or not self._consumer_thread.is_alive():
            self.start()

    def _get_fallback(self) -> Any:
        if not self._fallback_url:
            return None
        if self._fallback is None:
            try:
                from shared_project_engine.market.client import MarketDataClient
                self._fallback = MarketDataClient(service_url=self._fallback_url)
                _log.debug("KafkaMarketDataClient: fallback to MarketDataClient at %s", self._fallback_url)
            except Exception as exc:
                _log.warning("KafkaMarketDataClient: could not init fallback: %s", exc)
                return None
        return self._fallback


def build_kafka_market_client(env: dict[str, str] | None = None) -> KafkaMarketDataClient:
    """
    Factory used by engines and bots — mirrors build_market_data_client() in shared_project_engine.
    Starts the consumer thread immediately.
    """
    client = KafkaMarketDataClient.from_env(env)
    client.start()
    return client

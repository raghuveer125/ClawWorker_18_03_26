"""
Layer 1 live Fyers connector and self-healing tick ingestion components.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

import requests

from data_platform.kafka.producer import MessageProducer

_log = logging.getLogger(__name__)


ACTION_RETRY = "retry"
ACTION_REFRESH_TOKEN = "refresh_token"
ACTION_SKIP = "skip"
ACTION_FAIL = "fail"


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = value / 1000.0 if value > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError("timestamp missing or invalid")


def map_provider_error(code: str | None = None, http_status: int | None = None) -> str:
    normalized = (code or "").upper()
    if http_status in {401, 403}:
        return ACTION_REFRESH_TOKEN
    if http_status is not None and http_status >= 500:
        return ACTION_RETRY
    if http_status == 429:
        return ACTION_RETRY
    if "AUTH" in normalized or "TOKEN" in normalized:
        return ACTION_REFRESH_TOKEN
    if "NETWORK" in normalized or "TIMEOUT" in normalized:
        return ACTION_RETRY
    if "INVALID_SYMBOL" in normalized or "SYMBOL" in normalized:
        return ACTION_SKIP
    return ACTION_FAIL


@dataclass(frozen=True)
class FyersConnectorSettings:
    client_id: str
    secret_key: str
    redirect_uri: str
    access_token: str
    watchlist: tuple[str, ...]
    rest_base_url: str = "https://api-t1.fyers.in"
    request_timeout_seconds: float = 5.0
    retry_backoff_seconds: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0)
    heartbeat_interval_seconds: int = 5

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "FyersConnectorSettings":
        source = env or os.environ

        def _required(*keys: str) -> str:
            for key in keys:
                value = source.get(key, "").strip()
                if value:
                    return value
            raise ValueError(f"missing required environment keys: {', '.join(keys)}")

        watchlist_raw = source.get("FYERS_WATCHLIST", "").strip()
        watchlist = tuple(item.strip() for item in watchlist_raw.split(",") if item.strip())
        return cls(
            client_id=_required("FYERS_CLIENT_ID", "FYERS_APP_ID"),
            secret_key=_required("FYERS_SECRET_KEY"),
            redirect_uri=_required("FYERS_REDIRECT_URI"),
            access_token=source.get("FYERS_ACCESS_TOKEN", "").strip(),
            watchlist=watchlist,
        )


class AuthClient(Protocol):
    def validate_access_token(self, token: str) -> bool: ...

    def refresh_access_token(self) -> str | None: ...

    def login(self) -> str | None: ...


class WebSocketClient(Protocol):
    def connect(self, access_token: str) -> None: ...

    def subscribe(self, symbols: Sequence[str]) -> None: ...

    def run_forever(self, on_tick: Callable[[Mapping[str, Any]], None]) -> None: ...

    def close(self) -> None: ...


class RetryableIngestionError(RuntimeError):
    pass


class ProviderResponseError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider_code: str | None = None,
        http_status: int | None = None,
        action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_code = provider_code
        self.http_status = http_status
        self.action = action or map_provider_error(provider_code, http_status)


class RetryManager:
    def __init__(
        self,
        backoff_seconds: Sequence[float] = (1.0, 2.0, 5.0, 10.0, 30.0),
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not backoff_seconds:
            raise ValueError("backoff_seconds must not be empty")
        self._backoff = tuple(float(x) for x in backoff_seconds)
        self._sleep = sleep_fn

    def run(self, operation: Callable[[], Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(len(self._backoff) + 1):
            try:
                return operation()
            except RetryableIngestionError as exc:
                last_error = exc
                if attempt >= len(self._backoff):
                    break
                self._sleep(self._backoff[attempt])
        if last_error is not None:
            raise last_error
        raise RuntimeError("retry manager reached impossible state")


class AuthManager:
    def __init__(self, client: AuthClient, initial_access_token: str = "") -> None:
        self._client = client
        self._access_token = initial_access_token.strip()

    @property
    def access_token(self) -> str:
        return self._access_token

    def invalidate(self) -> None:
        self._access_token = ""

    def ensure_valid_token(self) -> str:
        if self._access_token and self._client.validate_access_token(self._access_token):
            return self._access_token

        refreshed = (self._client.refresh_access_token() or "").strip()
        if refreshed and self._client.validate_access_token(refreshed):
            self._access_token = refreshed
            return refreshed

        relogin = (self._client.login() or "").strip()
        if relogin and self._client.validate_access_token(relogin):
            self._access_token = relogin
            return relogin

        raise RuntimeError("unable to acquire valid Fyers access token")


class LiveFyersConnector:
    """
    Concrete connector for REST snapshots used by Layer 1 fetchers.
    """

    def __init__(
        self,
        settings: FyersConnectorSettings,
        auth_manager: AuthManager,
        session: requests.Session | None = None,
        retry_manager: RetryManager | None = None,
    ) -> None:
        self._settings = settings
        self._auth = auth_manager
        self._session = session or requests.Session()
        self._retry = retry_manager or RetryManager(settings.retry_backoff_seconds)

    def get_quote(self, symbol: str) -> Mapping[str, Any]:
        payload = self._get("/data/quotes", params={"symbols": symbol})
        if isinstance(payload, Mapping):
            entries = payload.get("d")
            if isinstance(entries, list) and entries:
                first = entries[0]
                if isinstance(first, Mapping):
                    values = first.get("v")
                    if isinstance(values, Mapping):
                        normalized = dict(values)
                        normalized.setdefault("symbol", str(first.get("n", symbol)))
                        if "tt" in normalized and "timestamp" not in normalized:
                            normalized["timestamp"] = normalized["tt"]
                        if "lp" in normalized and "ltp" not in normalized:
                            normalized["ltp"] = normalized["lp"]
                        return normalized
        return payload

    def get_option_chain(self, symbol: str) -> Mapping[str, Any]:
        return self._get("/data/options-chain-v3", params={"symbol": symbol, "strikecount": 20})

    def get_vix(self) -> Mapping[str, Any]:
        return self.get_quote("NSE:INDIAVIX-INDEX")

    def get_futures(self, index: str) -> Mapping[str, Any]:
        _FUTURES_SYMBOLS = {
            "NIFTY50": "NSE:NIFTY{yy}{mon}FUT",
            "BANKNIFTY": "NSE:BANKNIFTY{yy}{mon}FUT",
            "FINNIFTY": "NSE:FINNIFTY{yy}{mon}FUT",
            "MIDCPNIFTY": "NSE:MIDCPNIFTY{yy}{mon}FUT",
        }
        _MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                   "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        now = datetime.now(timezone.utc)
        pattern = _FUTURES_SYMBOLS.get(index.upper(), "NSE:{index}{yy}{mon}FUT")
        symbol = pattern.format(
            index=index.upper(),
            yy=str(now.year)[2:],
            mon=_MONTHS[now.month - 1],
        )
        return self.get_quote(symbol)

    def get_history(
        self,
        symbol: str,
        resolution: str,
        bars: int = 100,
        range_from: int | None = None,
        range_to: int | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        import time as _time
        to_epoch = range_to or int(_time.time())
        if range_from is not None:
            from_epoch = range_from
        else:
            seconds_per_bar = {
                "1": 60, "2": 120, "3": 180, "5": 300, "10": 600,
                "15": 900, "30": 1800, "60": 3600, "D": 86400, "W": 604800,
            }.get(str(resolution), 60)
            from_epoch = to_epoch - max(1, int(bars)) * seconds_per_bar * 2
        payload = self._get(
            "/data/history",
            params={
                "symbol": symbol,
                "resolution": str(resolution),
                "date_format": "0",
                "range_from": str(from_epoch),
                "range_to": str(to_epoch),
                "cont_flag": "1",
            },
        )
        candles = payload.get("candles", [])
        if isinstance(candles, list):
            return candles if range_from is not None else candles[-max(1, int(bars)):]
        return []

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        def _request_once() -> Mapping[str, Any]:
            token = self._auth.ensure_valid_token()
            headers = {
                "Authorization": f"{self._settings.client_id}:{token}",
                "Content-Type": "application/json",
                "version": "3",
            }
            try:
                response = self._session.request(
                    method="GET",
                    url=f"{self._settings.rest_base_url.rstrip('/')}{path}",
                    params=dict(params or {}),
                    headers=headers,
                    timeout=self._settings.request_timeout_seconds,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                raise RetryableIngestionError(str(exc)) from exc

            try:
                body = response.json() if response.content else {}
            except Exception:
                body = {}
            status = int(getattr(response, "status_code", 0) or 0)
            provider_code = str(body.get("code", "")) if isinstance(body, Mapping) else ""
            if status >= 400:
                _log.error(
                    "HTTP %s | url=%s params=%s | body=%s",
                    status, response.url, dict(params or {}), body,
                )
                action = map_provider_error(provider_code, status)
                if action == ACTION_SKIP:
                    return {}
                if action == ACTION_REFRESH_TOKEN:
                    self._auth.invalidate()
                    raise RetryableIngestionError("auth token requires refresh")
                if action == ACTION_RETRY:
                    raise RetryableIngestionError(f"provider retryable error ({status})")
                raise ProviderResponseError(
                    f"provider request failed ({status})",
                    provider_code=provider_code,
                    http_status=status,
                    action=action,
                )

            if isinstance(body, Mapping):
                if body.get("s") == "error":
                    action = map_provider_error(str(body.get("code", "")), status or None)
                    if action == ACTION_SKIP:
                        return {}
                    if action in {ACTION_RETRY, ACTION_REFRESH_TOKEN}:
                        if action == ACTION_REFRESH_TOKEN:
                            self._auth.invalidate()
                        raise RetryableIngestionError(str(body.get("message", "provider error")))
                    raise ProviderResponseError(
                        str(body.get("message", "provider error")),
                        provider_code=str(body.get("code", "")),
                        http_status=status or None,
                        action=action,
                    )
                data = body.get("data")
                if isinstance(data, Mapping):
                    return data
                return body

            return {}

        return self._retry.run(_request_once)


class TickNormalizer:
    SOURCE = "FYERS"

    def __init__(self, alias_map: dict[str, str] | None = None) -> None:
        self._alias_map: dict[str, str] = alias_map or {}

    @classmethod
    def from_db(cls, config: Any) -> "TickNormalizer":
        """
        Build a TickNormalizer whose alias map is loaded from the
        market_field_schema DB table (stream_type='tick').
        Falls back to an empty alias map on any error.
        """
        try:
            from data_platform.db.field_schema import build_alias_map, sync_list_fields_for_stream
            fields = sync_list_fields_for_stream(config, "tick")
            by_stream = build_alias_map(fields)
            return cls(alias_map=by_stream.get("tick", {}))
        except Exception as exc:
            _log.warning("TickNormalizer.from_db: could not load aliases from DB (%s) — using defaults", exc)
            return cls()

    def _resolve(self, raw: Mapping[str, Any], canonical: str, *extra_defaults: str) -> Any:
        if canonical in raw:
            return raw[canonical]
        if self._alias_map:
            for raw_key, canonical_key in self._alias_map.items():
                if canonical_key == canonical and raw_key in raw:
                    return raw[raw_key]
        for fallback in extra_defaults:
            if fallback in raw:
                return raw[fallback]
        return None

    def normalize(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        symbol = str(
            self._resolve(raw, "symbol", "ticker") or ""
        )
        if not symbol:
            raise ValueError(f"tick symbol missing | keys={list(raw.keys())} raw={dict(raw)}")
        ltp_raw = self._resolve(raw, "ltp", "last_price", "lp")
        ltp = float(ltp_raw) if ltp_raw is not None else 0.0
        if ltp <= 0:
            raise ValueError("tick ltp must be positive")
        ts_raw = self._resolve(raw, "timestamp", "ts", "t", "tt")
        timestamp = _parse_timestamp(ts_raw) if ts_raw is not None else datetime.now(timezone.utc)
        vol_raw = self._resolve(raw, "volume", "vol")
        volume = int(vol_raw) if vol_raw is not None else 0
        bid_raw = self._resolve(raw, "bid", "bid_price")
        bid = float(bid_raw) if bid_raw is not None else ltp
        ask_raw = self._resolve(raw, "ask", "ask_price")
        ask = float(ask_raw) if ask_raw is not None else ltp
        out: dict[str, Any] = {
            "timestamp": timestamp.isoformat(),
            "symbol": symbol,
            "ltp": ltp,
            "volume": volume,
            "bid": bid,
            "ask": ask,
            "source": self.SOURCE,
        }
        # Scalping fields — included only when broker sends them
        ltq_raw = self._resolve(raw, "ltq", "last_traded_qty")
        if ltq_raw is not None:
            out["ltq"] = int(ltq_raw)
        tbq_raw = self._resolve(raw, "total_buy_qty", "tbq", "tot_buy_qty")
        if tbq_raw is not None:
            out["total_buy_qty"] = int(tbq_raw)
        tsq_raw = self._resolve(raw, "total_sell_qty", "tsq", "tot_sell_qty")
        if tsq_raw is not None:
            out["total_sell_qty"] = int(tsq_raw)
        atp_raw = self._resolve(raw, "atp", "avg_trade_price")
        if atp_raw is not None:
            out["atp"] = float(atp_raw)
        return out


class TickPublisher:
    def __init__(self, producer: MessageProducer, topic: str = "market.nifty50.ticks") -> None:
        self._producer = producer
        self._topic = topic

    def publish(self, tick: Mapping[str, Any]) -> None:
        symbol = str(tick.get("symbol", ""))
        if not symbol:
            raise ValueError("tick symbol is required")
        self._producer.publish(topic=self._topic, key=symbol, value=dict(tick))


class DepthPublisher:
    def __init__(self, producer: MessageProducer, topic: str = "market.depth") -> None:
        self._producer = producer
        self._topic = topic

    def publish(self, depth: Mapping[str, Any]) -> None:
        symbol = str(depth.get("symbol", ""))
        if not symbol:
            raise ValueError("depth symbol is required")
        self._producer.publish(topic=self._topic, key=symbol, value=dict(depth))


class HeartbeatPublisher:
    def __init__(self, producer: MessageProducer, topic: str = "system.heartbeat") -> None:
        self._producer = producer
        self._topic = topic

    def publish(self, service: str = "fyers_connector", now: datetime | None = None) -> None:
        ts = now or datetime.now(timezone.utc)
        payload = {
            "service": service,
            "status": "alive",
            "timestamp": ts.isoformat(),
        }
        self._producer.publish(topic=self._topic, key=service, value=payload)


class SelfHealingFyersIngestionEngine:
    def __init__(
        self,
        auth_manager: AuthManager,
        websocket_client: WebSocketClient,
        tick_publisher: TickPublisher,
        heartbeat_publisher: HeartbeatPublisher,
        watchlist: Sequence[str],
        tick_normalizer: TickNormalizer | None = None,
        retry_manager: RetryManager | None = None,
        heartbeat_interval_seconds: int = 5,
        monotonic: Callable[[], float] = time.monotonic,
        depth_publisher: DepthPublisher | None = None,
    ) -> None:
        self._auth_manager = auth_manager
        self._websocket_client = websocket_client
        self._tick_publisher = tick_publisher
        self._heartbeat_publisher = heartbeat_publisher
        self._watchlist = tuple(watchlist)
        self._tick_normalizer = tick_normalizer or TickNormalizer()
        self._retry_manager = retry_manager or RetryManager()
        self._heartbeat_interval_seconds = max(1, heartbeat_interval_seconds)
        self._monotonic = monotonic
        self._last_heartbeat_monotonic = 0.0
        self._depth_publisher = depth_publisher

    def _on_tick(self, raw_tick: Mapping[str, Any]) -> None:
        if raw_tick.get("stream_type") == "depth":
            if self._depth_publisher is not None:
                self._depth_publisher.publish(raw_tick)
            return
        normalized = self._tick_normalizer.normalize(raw_tick)
        self._tick_publisher.publish(normalized)
        self._emit_heartbeat_if_due()

    def _emit_heartbeat_if_due(self) -> None:
        now = self._monotonic()
        if self._last_heartbeat_monotonic == 0 or (
            now - self._last_heartbeat_monotonic >= self._heartbeat_interval_seconds
        ):
            self._heartbeat_publisher.publish()
            self._last_heartbeat_monotonic = now

    def connect_and_stream(self) -> None:
        token = self._auth_manager.ensure_valid_token()
        self._websocket_client.connect(token)
        self._websocket_client.subscribe(self._watchlist)
        if self._depth_publisher is not None and hasattr(self._websocket_client, "subscribe_depth"):
            self._websocket_client.subscribe_depth(self._watchlist)
        self._websocket_client.run_forever(on_tick=self._on_tick)

    def run(self, stop_requested: Callable[[], bool] | None = None) -> None:
        _stop = stop_requested or (lambda: False)
        while not _stop():
            try:
                self._retry_manager.run(self.connect_and_stream)
            except Exception as exc:
                _log.error(
                    "SelfHealingFyersIngestionEngine: fatal error — ingestion thread exiting. reason=%s",
                    exc,
                    exc_info=True,
                )
                raise
            finally:
                try:
                    self._websocket_client.close()
                except Exception:
                    pass

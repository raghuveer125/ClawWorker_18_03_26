"""
Concrete WebSocketClient adapter wrapping fyers_apiv3.FyersWebsocket.data_ws.FyersDataSocket.

Implements the WebSocketClient protocol defined in fyers_live.py:
    connect(access_token)  → authenticates and opens the WS connection
    subscribe(symbols)     → subscribes to SymbolUpdate for each symbol
    run_forever(on_tick)   → blocks until close() is called, delivering ticks
    close()                → tears down the WS connection cleanly
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Mapping, Sequence

_log = logging.getLogger(__name__)

_STOP_SENTINEL = object()

_DEFAULT_WS_ALIASES: dict[str, str] = {
    "last_traded_time": "timestamp",
    "exch_feed_time": "timestamp",
    "vol_traded_today": "volume",
    "bid_price": "bid",
    "ask_price": "ask",
    # Scalping fields — available in Fyers SymbolUpdate (full/non-lite mode)
    "last_traded_qty": "ltq",
    "tot_buy_qty": "total_buy_qty",
    "tot_sell_qty": "total_sell_qty",
    "avg_trade_price": "atp",
}


def _load_ws_aliases(config: Any) -> dict[str, str]:
    """
    Load broker alias → canonical field map for stream_type='tick' from DB.
    Falls back to _DEFAULT_WS_ALIASES on any error.
    """
    try:
        from data_platform.db.field_schema import build_alias_map, sync_list_fields_for_stream
        fields = sync_list_fields_for_stream(config, "tick")
        by_stream = build_alias_map(fields)
        return by_stream.get("tick", _DEFAULT_WS_ALIASES)
    except Exception as exc:
        _log.warning("fyers_websocket: could not load aliases from DB (%s) — using defaults", exc)
        return _DEFAULT_WS_ALIASES


def _normalize_ws_tick(raw: Mapping[str, Any], alias_map: dict[str, str] | None = None) -> dict[str, Any]:
    """
    Map FyersDataSocket on_message payload to the shape expected by TickNormalizer.
    Uses alias_map (loaded from DB) when provided, otherwise _DEFAULT_WS_ALIASES.
    """
    aliases = alias_map if alias_map is not None else _DEFAULT_WS_ALIASES
    out: dict[str, Any] = {}
    for raw_key, value in raw.items():
        canonical = aliases.get(raw_key, raw_key)
        if canonical not in out:
            out[canonical] = value
    return out


def _normalize_depth_update(raw: Mapping[str, Any]) -> dict[str, Any]:
    """
    Flatten a Fyers DepthUpdate message into the canonical depth stream shape.

    Fyers sends depth as:
        { "symbol": "NSE:...", "timestamp": ...,
          "bids": [{"price": p, "volume": v}, ...],   # up to 5 levels
          "asks": [{"price": p, "volume": v}, ...] }

    Output uses bid_price_1..5 / bid_qty_1..5 / ask_price_1..5 / ask_qty_1..5
    matching the depth stream fields in market_field_schema.
    """
    out: dict[str, Any] = {
        "stream_type": "depth",
        "symbol": raw.get("symbol", raw.get("n", "")),
        "timestamp": raw.get("timestamp", raw.get("ts", raw.get("t", 0))),
    }
    for side, prefix in (("bids", "bid"), ("asks", "ask")):
        levels = raw.get(side, [])
        for i, level in enumerate(levels[:5], start=1):
            out[f"{prefix}_price_{i}"] = float(level.get("price", level.get("p", 0)))
            out[f"{prefix}_qty_{i}"] = int(level.get("volume", level.get("v", level.get("qty", 0))))
    return out


class FyersWebSocketAdapter:
    """
    Wraps FyersDataSocket and implements the WebSocketClient protocol.

    Pass a field_schema_config (FieldSchemaConfig) at construction to load
    broker aliases from the market_field_schema DB table automatically.

    Usage:
        from data_platform.db.field_schema import FieldSchemaConfig
        adapter = FyersWebSocketAdapter(field_schema_config=FieldSchemaConfig.from_env())
        adapter.connect(access_token)
        adapter.subscribe(["NSE:NIFTY50-INDEX", "NSE:SBIN-EQ"])
        adapter.run_forever(on_tick=my_callback)   # blocks
        adapter.close()                            # call from another thread to stop
    """

    def __init__(
        self,
        log_path: str | None = None,
        litemode: bool = False,
        reconnect: bool = True,
        reconnect_retry: int = 10,
        data_type: str = "SymbolUpdate",
        channel: int = 11,
        field_schema_config: Any = None,
    ) -> None:
        self._log_path = log_path
        self._litemode = litemode
        self._reconnect = reconnect
        self._reconnect_retry = reconnect_retry
        self._data_type = data_type
        self._channel = channel
        self._alias_map: dict[str, str] = (
            _load_ws_aliases(field_schema_config) if field_schema_config is not None
            else _DEFAULT_WS_ALIASES
        )

        self._socket: Any = None
        self._on_tick: Callable[[Mapping[str, Any]], None] | None = None
        self._tick_queue: list[Mapping[str, Any]] = []
        self._queue_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._symbols: tuple[str, ...] = ()

    def connect(self, access_token: str) -> None:
        from fyers_apiv3.FyersWebsocket import data_ws

        self._stop_event.clear()

        self._socket = data_ws.FyersDataSocket(
            access_token=access_token,
            log_path=self._log_path or "",
            write_to_file=False,
            litemode=self._litemode,
            reconnect=self._reconnect,
            reconnect_retry=self._reconnect_retry,
            on_message=self._handle_message,
            on_error=self._handle_error,
            on_connect=self._handle_connect,
            on_close=self._handle_close,
        )

        self._socket.connect()
        time.sleep(1)
        _log.info("FyersWebSocketAdapter: connected")

    def subscribe(self, symbols: Sequence[str]) -> None:
        with self._state_lock:
            if self._socket is None:
                raise RuntimeError("FyersWebSocketAdapter: connect() must be called before subscribe()")
            self._symbols = tuple(symbols)
            sock = self._socket
        sock.subscribe(
            symbols=list(symbols),
            data_type=self._data_type,
            channel=self._channel,
        )
        sock.keep_running()
        _log.info("FyersWebSocketAdapter: subscribed to %d symbol(s)", len(symbols))

    def update_subscriptions(self, new_symbols: Sequence[str]) -> tuple[frozenset[str], frozenset[str]]:
        """
        Diff the current subscription against new_symbols and apply the delta.

        Returns (added, removed) sets so the caller can log or audit changes.
        Only calls subscribe/unsubscribe for the diff — avoids redundant API calls.
        """
        with self._state_lock:
            if self._socket is None:
                raise RuntimeError("FyersWebSocketAdapter: connect() must be called before update_subscriptions()")
            current = frozenset(self._symbols)
            desired = frozenset(new_symbols)
            added   = desired - current
            removed = current - desired
            sock = self._socket

        if added:
            sock.subscribe(
                symbols=list(added),
                data_type=self._data_type,
                channel=self._channel,
            )
            _log.info("FyersWebSocketAdapter: +subscribed %d symbol(s): %s", len(added), sorted(added))

        if removed:
            try:
                sock.unsubscribe(
                    symbols=list(removed),
                    data_type=self._data_type,
                    channel=self._channel,
                )
            except Exception as exc:
                _log.warning("FyersWebSocketAdapter: unsubscribe error (ignored): %s", exc)
            _log.info("FyersWebSocketAdapter: -unsubscribed %d symbol(s): %s", len(removed), sorted(removed))

        with self._state_lock:
            self._symbols = tuple(desired)
        return added, removed

    def subscribe_depth(self, symbols: Sequence[str], channel: int = 12) -> None:
        """
        Subscribe to Level 2 market depth (DepthUpdate) for the given symbols.

        Depth messages will be normalized by _normalize_depth_update() and
        delivered to the same on_tick callback as SymbolUpdate ticks.
        They carry stream_type="depth" so callers can route them separately.

        Call this after subscribe() and before run_forever().
        """
        with self._state_lock:
            if self._socket is None:
                raise RuntimeError("FyersWebSocketAdapter: connect() must be called before subscribe_depth()")
            sock = self._socket
        sock.subscribe(
            symbols=list(symbols),
            data_type="DepthUpdate",
            channel=channel,
        )
        _log.info("FyersWebSocketAdapter: subscribed to depth for %d symbol(s)", len(symbols))

    def run_forever(self, on_tick: Callable[[Mapping[str, Any]], None]) -> None:
        with self._state_lock:
            self._on_tick = on_tick
        _log.info("FyersWebSocketAdapter: entering run_forever loop")

        while not self._stop_event.is_set():
            with self._queue_lock:
                pending = self._tick_queue[:]
                self._tick_queue.clear()

            for raw in pending:
                try:
                    if raw.get("type") == "depth_update" or raw.get("data_type") == "DepthUpdate" or "bids" in raw:
                        normalized = _normalize_depth_update(raw)
                    else:
                        normalized = _normalize_ws_tick(raw, alias_map=self._alias_map)
                    on_tick(normalized)
                except Exception as exc:
                    _log.warning(
                        "FyersWebSocketAdapter: tick callback error: %s | raw_keys=%s raw=%s",
                        exc, list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__, raw,
                        exc_info=True,
                    )

            self._stop_event.wait(timeout=0.05)

        _log.info("FyersWebSocketAdapter: run_forever exited")

    def close(self) -> None:
        self._stop_event.set()
        with self._state_lock:
            sock = self._socket
            self._socket = None
        if sock is not None:
            try:
                sock.close_connection()
            except Exception as exc:
                _log.debug("FyersWebSocketAdapter: close_connection error (ignored): %s", exc)
        _log.info("FyersWebSocketAdapter: closed")

    # Fyers control message types — status/ack frames, never tick data
    _CONTROL_TYPES = frozenset({"cn", "ful", "sub", "unsub", "ck", "dp_sub", "error", "auth_failed"})

    def _handle_message(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        msg_type = str(message.get("type", ""))
        # Control frames: log and discard — do not pass to tick pipeline
        if msg_type in self._CONTROL_TYPES:
            if message.get("s") == "error" or msg_type == "error":
                _log.warning("FyersWebSocketAdapter: control error: %s", message)
            else:
                _log.debug("FyersWebSocketAdapter: control msg type=%s: %s", msg_type, message)
            return
        # Any message without a symbol is not a tick — discard silently
        if not message.get("symbol") and not message.get("n") and not message.get("bids"):
            _log.debug("FyersWebSocketAdapter: skipping non-tick message: %s", message)
            return
        with self._queue_lock:
            self._tick_queue.append(message)

    def _handle_error(self, *args: Any, **kwargs: Any) -> None:
        error = args[0] if args else kwargs
        _log.error("FyersWebSocketAdapter: WS error: %s | args=%s kwargs=%s", error, args, kwargs)

    def _handle_connect(self, *args: Any, **kwargs: Any) -> None:
        message = args[0] if args else kwargs
        _log.info("FyersWebSocketAdapter: WS on_connect: %s | args=%s kwargs=%s", message, args, kwargs)

    def _handle_close(self, *args: Any, **kwargs: Any) -> None:
        message = args[0] if args else kwargs
        _log.info("FyersWebSocketAdapter: WS on_close: %s | args=%s kwargs=%s", message, args, kwargs)
        self._stop_event.set()

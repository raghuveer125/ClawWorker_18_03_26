"""FYERS WebSocket adapter — live spot price streaming without rate limits.

Uses FyersDataSocket for real-time spot updates.
Option chain is refreshed via REST at configurable intervals (not every second).

Strategy:
- WebSocket: spot price every tick (sub-second)
- REST: option chain every N seconds (default 30s) to avoid rate limits
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from ..config import LotteryConfig

logger = logging.getLogger(__name__)


class FyersWebSocketClient:
    """WebSocket client for real-time FYERS data.

    Subscribes to index spot + option candidates via WebSocket.
    Calls on_tick callback with each update.
    Supports dynamic re-subscription when candidates change.
    """

    def __init__(
        self,
        config: LotteryConfig,
        access_token: str,
        client_id: str,
        on_tick: Optional[Callable] = None,
    ) -> None:
        self._config = config
        self._access_token = access_token
        self._client_id = client_id
        self._on_tick = on_tick
        self._ws = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_ltp: Optional[float] = None
        self._last_tick_time: Optional[datetime] = None
        self._subscribed_symbols: list[str] = []
        self._lock = threading.Lock()

    @property
    def last_ltp(self) -> Optional[float]:
        return self._last_ltp

    @property
    def last_tick_time(self) -> Optional[datetime]:
        return self._last_tick_time

    @property
    def is_connected(self) -> bool:
        return self._running and self._ws is not None

    def start(self, symbols: list[str]) -> None:
        """Start WebSocket connection and subscribe to symbols.

        Args:
            symbols: FYERS symbols to subscribe (e.g., ["NSE:NIFTY50-INDEX"])
        """
        if self._running:
            logger.warning("WebSocket already running")
            return

        self._running = True
        self._subscribed_symbols = list(symbols)
        self._thread = threading.Thread(
            target=self._run_ws,
            args=(symbols,),
            daemon=True,
            name="fyers-ws",
        )
        self._thread.start()
        logger.info("FYERS WebSocket starting for %s", symbols)

    def stop(self) -> None:
        """Stop WebSocket connection."""
        self._running = False
        if self._ws:
            try:
                self._ws.close_connection()
            except Exception:
                pass
        logger.info("FYERS WebSocket stopped")

    def update_subscriptions(self, option_symbols: list[str]) -> None:
        """Dynamically subscribe to option candidate symbols.

        Unsubscribes from old option symbols and subscribes to new ones.
        Keeps the index spot symbol always subscribed.
        Max ~10 option symbols to stay well under FYERS 50-symbol limit.
        """
        if not self._ws or not self._running:
            return

        with self._lock:
            # Keep only first symbol (index spot) + new option symbols
            base_symbols = self._subscribed_symbols[:1]  # index spot
            new_option_syms = option_symbols[:10]  # cap at 10

            # Find what to unsub/sub
            old_option_syms = self._subscribed_symbols[1:]
            to_unsub = [s for s in old_option_syms if s not in new_option_syms]
            to_sub = [s for s in new_option_syms if s not in old_option_syms]

            try:
                if to_unsub:
                    self._ws.unsubscribe(symbols=to_unsub)
                    logger.debug("WS unsubscribed: %s", to_unsub)

                if to_sub:
                    self._ws.subscribe(symbols=to_sub, data_type="symbolUpdate")
                    logger.debug("WS subscribed: %s", to_sub)

                self._subscribed_symbols = base_symbols + new_option_syms
            except Exception as e:
                logger.warning("WS subscription update failed: %s", e)

    def _run_ws(self, symbols: list[str]) -> None:
        """WebSocket connection loop with auto-reconnect."""
        while self._running:
            try:
                self._connect(symbols)
            except Exception as e:
                logger.error("WebSocket error: %s", e)
                if self._running:
                    time.sleep(5)  # reconnect delay

    def _connect(self, symbols: list[str]) -> None:
        """Establish WebSocket connection."""
        try:
            from fyers_apiv3.FyersWebsocket.data_ws import FyersDataSocket

            def on_message(msg):
                self._handle_message(msg)

            def on_error(msg):
                logger.warning("WS error: %s", msg)

            def on_close(msg):
                logger.info("WS closed: %s", msg)

            def on_open():
                logger.info("WS connected, subscribing to %s", symbols)
                if self._ws:
                    self._ws.subscribe(symbols=symbols, data_type="symbolUpdate")

            self._ws = FyersDataSocket(
                access_token=f"{self._client_id}:{self._access_token}",
                log_path="",
                litemode=True,
                write_to_file=False,
                reconnect=True,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_connect=on_open,
            )

            self._ws.connect()

            # Keep thread alive while connected
            while self._running:
                time.sleep(1)

        except ImportError:
            logger.error("fyers_apiv3 WebSocket module not available")
            self._running = False
        except Exception as e:
            logger.error("WS connect failed: %s", e)
            raise

    def _handle_message(self, msg: dict) -> None:
        """Process incoming WebSocket tick (spot or option)."""
        try:
            if isinstance(msg, dict):
                ltp = msg.get("ltp") or msg.get("lp")
                symbol = msg.get("symbol", "")
                now = datetime.now(timezone.utc)

                if ltp and ltp > 0:
                    self._last_ltp = float(ltp)
                    self._last_tick_time = now

                    if self._on_tick:
                        self._on_tick({
                            "ltp": float(ltp),
                            "timestamp": now,
                            "symbol": symbol,
                            "bid": msg.get("bid") or msg.get("bp"),
                            "ask": msg.get("ask") or msg.get("sp"),
                            "bid_qty": msg.get("bid_qty") or msg.get("bq"),
                            "ask_qty": msg.get("ask_qty") or msg.get("sq"),
                            "high": msg.get("high_price"),
                            "low": msg.get("low_price"),
                            "open": msg.get("open_price"),
                            "prev_close": msg.get("prev_close_price"),
                            "volume": msg.get("vol_traded_today"),
                        })
            elif isinstance(msg, list):
                for item in msg:
                    self._handle_message(item)
        except Exception as e:
            logger.debug("Tick parse error: %s", e)

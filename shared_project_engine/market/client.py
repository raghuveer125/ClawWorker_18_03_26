"""Service-aware market-data client with local fallback."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Dict, Iterator, Optional, Tuple

import requests

from .adapter import MarketDataAdapter


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class MarketDataClient:
    """Use the localhost adapter service when available, else fall back locally."""

    def __init__(
        self,
        service_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        fallback_to_local: bool = True,
        strict_mode: Optional[bool] = None,
        **adapter_kwargs: Any,
    ) -> None:
        self.service_url = (service_url or os.getenv("MARKET_ADAPTER_URL", "")).rstrip("/")
        self.timeout_seconds = float(timeout_seconds or os.getenv("MARKET_ADAPTER_TIMEOUT_SEC", "3"))
        self.strict_mode = _env_flag("MARKET_ADAPTER_STRICT", False) if strict_mode is None else bool(strict_mode)
        self.fallback_to_local = bool(fallback_to_local) and not self.strict_mode
        self.adapter = MarketDataAdapter(**adapter_kwargs) if self.fallback_to_local else None
        self.access_token = getattr(self.adapter, "access_token", "") or os.getenv("FYERS_ACCESS_TOKEN", "")
        self.client_id = getattr(self.adapter, "client_id", "") or os.getenv("FYERS_CLIENT_ID", "")
        self._service_unavailable_until = 0.0

    def _service_allowed(self) -> bool:
        return bool(self.service_url) and time.time() >= self._service_unavailable_until

    def _mark_service_unavailable(self) -> None:
        cooldown = float(os.getenv("MARKET_ADAPTER_SERVICE_RETRY_SEC", "5"))
        self._service_unavailable_until = time.time() + max(1.0, cooldown)

    def _remote_get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self._service_allowed():
            raise RuntimeError("market adapter service unavailable")

        filtered_params = {k: v for k, v in params.items() if v not in (None, "", [])}
        url = f"{self.service_url}{path}"
        try:
            response = requests.get(url, params=filtered_params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self._mark_service_unavailable()
            raise RuntimeError(f"market adapter service request failed: {exc}") from exc

        if not isinstance(payload, dict):
            self._mark_service_unavailable()
            raise RuntimeError("market adapter service returned non-dict payload")

        return payload

    def _dispatch(
        self,
        path: str,
        params: Dict[str, Any],
        fallback: Optional[Any],
    ) -> Dict[str, Any]:
        if self._service_allowed():
            try:
                payload = self._remote_get(path, params)
                payload["_source"] = "service"
                return payload
            except RuntimeError:
                pass

        if fallback is not None:
            payload = fallback()
            if isinstance(payload, dict):
                payload["_source"] = "local"
                return payload
            return {"_source": "local"}

        raise RuntimeError("market adapter service unavailable and local fallback disabled")

    def get_quote(self, symbol: str, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        return self._dispatch(
            "/quote",
            {"symbol": symbol, "ttl_seconds": ttl_seconds},
            fallback=(lambda: self.adapter.get_quote(symbol, ttl_seconds=ttl_seconds)) if self.adapter else None,
        )

    def get_quotes(self, symbols: Any, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        if isinstance(symbols, str):
            serialized_symbols = symbols
        else:
            serialized_symbols = ",".join(str(symbol).strip() for symbol in (symbols or []) if str(symbol).strip())
        return self._dispatch(
            "/quotes",
            {"symbols": serialized_symbols, "ttl_seconds": ttl_seconds},
            fallback=(
                lambda: self.adapter.get_quotes(serialized_symbols, ttl_seconds=ttl_seconds)
            ) if self.adapter else None,
        )

    def quotes(self, symbols: Any, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        return self.get_quotes(symbols, ttl_seconds=ttl_seconds)

    def stream_quotes(
        self,
        symbols: Any,
        interval_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
        read_timeout_seconds: Optional[float] = None,
    ) -> Iterator[Dict[str, Any]]:
        if isinstance(symbols, str):
            serialized_symbols = symbols
        else:
            serialized_symbols = ",".join(str(symbol).strip() for symbol in (symbols or []) if str(symbol).strip())

        if self._service_allowed():
            try:
                yield from self._remote_stream_quotes(
                    serialized_symbols,
                    interval_seconds=interval_seconds,
                    ttl_seconds=ttl_seconds,
                    read_timeout_seconds=read_timeout_seconds,
                )
                return
            except RuntimeError:
                pass

        if self.adapter is not None:
            yield from self._local_stream_quotes(
                serialized_symbols,
                interval_seconds=interval_seconds,
                ttl_seconds=ttl_seconds,
            )
            return

        raise RuntimeError("market adapter service unavailable and local fallback disabled")

    def get_quote_ltp(self, symbol: str, ttl_seconds: Optional[float] = None) -> float:
        return float(self.get_quote(symbol, ttl_seconds=ttl_seconds).get("ltp", 0.0) or 0.0)

    def get_history_snapshot(
        self,
        symbol: str,
        resolution: str = "5",
        lookback_days: int = 5,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._dispatch(
            "/history",
            {
                "symbol": symbol,
                "resolution": resolution,
                "lookback_days": lookback_days,
                "ttl_seconds": ttl_seconds,
            },
            fallback=(
                lambda: self.adapter.get_history_snapshot(
                    symbol=symbol,
                    resolution=resolution,
                    lookback_days=lookback_days,
                    ttl_seconds=ttl_seconds,
                )
            )
            if self.adapter
            else None,
        )

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
        return self._dispatch(
            "/history-range",
            {
                "symbol": symbol,
                "resolution": resolution,
                "range_from": range_from,
                "range_to": range_to,
                "date_format": date_format,
                "cont_flag": cont_flag,
                "ttl_seconds": ttl_seconds,
            },
            fallback=(
                lambda: self.adapter.get_history_range(
                    symbol=symbol,
                    resolution=resolution,
                    range_from=range_from,
                    range_to=range_to,
                    date_format=date_format,
                    cont_flag=cont_flag,
                    ttl_seconds=ttl_seconds,
                )
            )
            if self.adapter
            else None,
        )

    def get_option_chain_snapshot(
        self,
        symbol: str,
        strike_count: int = 10,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._dispatch(
            "/option-chain",
            {
                "symbol": symbol,
                "strike_count": strike_count,
                "ttl_seconds": ttl_seconds,
            },
            fallback=(
                lambda: self.adapter.get_option_chain_snapshot(
                    symbol=symbol,
                    strike_count=strike_count,
                    ttl_seconds=ttl_seconds,
                )
            )
            if self.adapter
            else None,
        )

    def resolve_future_quote(
        self,
        index_name: str,
        explicit_symbol: str = "",
        now_local: Optional[datetime] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Tuple[str, float]:
        payload = self._dispatch(
            "/future-quote",
            {
                "index_name": index_name,
                "explicit_symbol": explicit_symbol,
                "now_local": now_local.isoformat() if now_local is not None else "",
                "ttl_seconds": ttl_seconds,
            },
            fallback=(
                lambda: self._resolve_future_quote_local(
                    index_name=index_name,
                    explicit_symbol=explicit_symbol,
                    now_local=now_local,
                    ttl_seconds=ttl_seconds,
                )
            ) if self.adapter else None,
        )
        return str(payload.get("symbol", "")), float(payload.get("ltp", 0.0) or 0.0)

    def get_index_market_data(
        self,
        index_name: str,
        resolution: str = "5",
        lookback_days: int = 5,
        strike_count: int = 8,
        fut_symbol: str = "",
    ) -> Dict[str, Any]:
        return self._dispatch(
            "/index-snapshot",
            {
                "index_name": index_name,
                "resolution": resolution,
                "lookback_days": lookback_days,
                "strike_count": strike_count,
                "fut_symbol": fut_symbol,
            },
            fallback=(
                lambda: self.adapter.get_index_market_data(
                    index_name=index_name,
                    resolution=resolution,
                    lookback_days=lookback_days,
                    strike_count=strike_count,
                    fut_symbol=fut_symbol,
                )
            )
            if self.adapter
            else None,
        )

    def get_metrics(self) -> Dict[str, Any]:
        return self._dispatch(
            "/metrics",
            {},
            fallback=lambda: {"service_enabled": False},
        )

    def healthcheck(self) -> Dict[str, Any]:
        if not self.service_url:
            return {"status": "disabled", "service_url": ""}
        return self._dispatch(
            "/health",
            {},
            fallback=lambda: {"status": "fallback", "service_url": self.service_url},
        )

    def _resolve_future_quote_local(
        self,
        index_name: str,
        explicit_symbol: str = "",
        now_local: Optional[datetime] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        if self.adapter is None:
            return {"symbol": "", "ltp": 0.0}
        symbol, ltp = self.adapter.resolve_future_quote(
            index_name=index_name,
            explicit_symbol=explicit_symbol,
            now_local=now_local,
            ttl_seconds=ttl_seconds,
        )
        return {"symbol": symbol, "ltp": ltp}

    def _remote_stream_quotes(
        self,
        symbols: str,
        interval_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
        read_timeout_seconds: Optional[float] = None,
    ) -> Iterator[Dict[str, Any]]:
        if not self._service_allowed():
            raise RuntimeError("market adapter service unavailable")

        url = f"{self.service_url}/stream/quotes"
        params = {
            "symbols": symbols,
            "interval_seconds": interval_seconds,
            "ttl_seconds": ttl_seconds,
        }
        filtered_params = {k: v for k, v in params.items() if v not in (None, "", [])}
        read_timeout = float(
            read_timeout_seconds
            or os.getenv("MARKET_ADAPTER_STREAM_READ_TIMEOUT_SEC", "20")
        )

        try:
            with requests.get(
                url,
                params=filtered_params,
                stream=True,
                timeout=(self.timeout_seconds, read_timeout),
            ) as response:
                response.raise_for_status()
                event_name = "message"
                data_lines = []

                for raw_line in response.iter_lines(decode_unicode=True, chunk_size=1):
                    if raw_line is None:
                        continue
                    line = raw_line.rstrip("\r")
                    if not line:
                        if not data_lines:
                            event_name = "message"
                            continue
                        payload = json.loads("\n".join(data_lines))
                        if isinstance(payload, dict):
                            payload.setdefault("event", event_name)
                            payload["_source"] = "service_stream"
                            yield payload
                        else:
                            yield {
                                "event": event_name,
                                "data": payload,
                                "_source": "service_stream",
                            }
                        event_name = "message"
                        data_lines = []
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line.split(":", 1)[1].strip() or "message"
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line.split(":", 1)[1].lstrip())
        except Exception as exc:
            self._mark_service_unavailable()
            raise RuntimeError(f"market adapter quote stream failed: {exc}") from exc

    def _local_stream_quotes(
        self,
        symbols: str,
        interval_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Iterator[Dict[str, Any]]:
        interval = max(
            0.25,
            float(interval_seconds or os.getenv("MARKET_ADAPTER_STREAM_INTERVAL_SEC", "1")),
        )
        while True:
            payload = self.adapter.get_quotes(symbols, ttl_seconds=ttl_seconds)
            payload["_source"] = "local_stream"
            yield {
                "event": "quotes",
                "symbols": [symbol.strip() for symbol in symbols.split(",") if symbol.strip()],
                "interval_seconds": interval,
                "ttl_seconds": ttl_seconds,
                "emitted_at": time.time(),
                "payload": payload,
                "_source": "local_stream",
            }
            time.sleep(interval)

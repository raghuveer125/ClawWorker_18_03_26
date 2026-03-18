"""
Shared FYERS market-data adapter with cross-process caching.

The adapter centralizes FYERS access for local engines and uses file-backed
cache entries plus file locks so concurrent local processes can reuse the same
snapshots instead of polling FYERS independently.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

try:
    from fyers_apiv3 import fyersModel

    HAS_FYERS_SDK = True
except ImportError:
    HAS_FYERS_SDK = False

from ..auth import FyersClient
from ..indices import canonicalize_index_name, get_market_index_config


def _to_num(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:
        return None
    return result


class _SharedJsonCache:
    """Small JSON cache guarded by per-key file locks."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _paths(self, namespace: str, key: str) -> Tuple[Path, Path]:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        namespace_dir = self.cache_dir / namespace
        namespace_dir.mkdir(parents=True, exist_ok=True)
        return namespace_dir / f"{digest}.json", namespace_dir / f"{digest}.lock"

    @staticmethod
    def _load(path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _is_fresh(payload: Optional[Dict[str, Any]], ttl_seconds: float) -> bool:
        if not payload:
            return False
        updated_at = _to_num(payload.get("updated_at"))
        if updated_at is None:
            return False
        return (time.time() - updated_at) <= ttl_seconds

    @staticmethod
    def _write(path: Path, payload: Dict[str, Any]) -> None:
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        tmp_path.replace(path)

    def get_or_set(
        self,
        namespace: str,
        key: str,
        ttl_seconds: float,
        fetcher: Callable[[], Dict[str, Any]],
        validator: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> Tuple[Dict[str, Any], bool, float]:
        cache_path, lock_path = self._paths(namespace, key)

        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                payload = self._load(cache_path)
                cached_data = payload.get("data", {}) if payload else {}
                cache_valid = validator(cached_data) if validator is not None else True
                if self._is_fresh(payload, ttl_seconds) and cache_valid:
                    updated_at = float(payload["updated_at"])
                    return cached_data, True, updated_at

                stale_payload = payload if cache_valid else None
                fresh_data = fetcher()
                fresh_payload_data = fresh_data if isinstance(fresh_data, dict) else {}
                fresh_updated_at = time.time()
                fresh_valid = validator(fresh_payload_data) if validator is not None else True
                if fresh_valid:
                    fresh_payload = {
                        "updated_at": fresh_updated_at,
                        "data": fresh_payload_data,
                    }
                    self._write(cache_path, fresh_payload)
                return fresh_payload_data, False, float(fresh_updated_at)
            except Exception:
                if self._is_fresh(stale_payload, ttl_seconds * 4 if ttl_seconds > 0 else 0):
                    updated_at = float(stale_payload["updated_at"])
                    return stale_payload.get("data", {}), True, updated_at
                raise
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def get(
        self,
        namespace: str,
        key: str,
        ttl_seconds: float,
        validator: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], bool, Optional[float]]:
        cache_path, _ = self._paths(namespace, key)
        payload = self._load(cache_path)
        if not self._is_fresh(payload, ttl_seconds):
            return None, False, _to_num(payload.get("updated_at")) if payload else None
        cached_data = payload.get("data", {})
        if validator is not None and not validator(cached_data):
            return None, False, float(payload["updated_at"])
        return cached_data, True, float(payload["updated_at"])

    def set(self, namespace: str, key: str, data: Dict[str, Any]) -> float:
        cache_path, lock_path = self._paths(namespace, key)
        payload = {
            "updated_at": time.time(),
            "data": data if isinstance(data, dict) else {},
        }
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                self._write(cache_path, payload)
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return float(payload["updated_at"])


class MarketDataAdapter:
    """Shared entry point for quotes, history, and option-chain snapshots."""

    _MONTH_CODES = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

    @staticmethod
    def _resolution_bucket_seconds(resolution: str) -> Optional[int]:
        try:
            minutes = int(str(resolution).strip())
        except (TypeError, ValueError):
            return None
        if minutes <= 0:
            return None
        return minutes * 60

    @classmethod
    def _history_snapshot_ttl_seconds(
        cls,
        resolution: str,
        requested_ttl_seconds: Optional[float],
        fallback_ttl_seconds: float,
    ) -> float:
        if requested_ttl_seconds is not None:
            return float(requested_ttl_seconds)

        bucket_seconds = cls._resolution_bucket_seconds(resolution)
        if bucket_seconds != 60:
            return float(fallback_ttl_seconds)

        seconds_until_next_bucket = bucket_seconds - (time.time() % bucket_seconds)
        return max(2.0, seconds_until_next_bucket - 1.0)

    def __init__(
        self,
        access_token: Optional[str] = None,
        client_id: Optional[str] = None,
        env_file: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        quote_ttl_seconds: Optional[float] = None,
        history_ttl_seconds: Optional[float] = None,
        option_chain_ttl_seconds: Optional[float] = None,
    ) -> None:
        self.client = FyersClient(
            access_token=access_token,
            client_id=client_id,
            env_file=env_file,
        )
        self.access_token = self.client.access_token
        self.client_id = self.client.client_id
        self.quote_ttl_seconds = float(
            quote_ttl_seconds
            if quote_ttl_seconds is not None
            else os.getenv("MARKET_ADAPTER_QUOTE_TTL_SEC", "5")
        )
        self.history_ttl_seconds = float(
            history_ttl_seconds
            if history_ttl_seconds is not None
            else os.getenv("MARKET_ADAPTER_HISTORY_TTL_SEC", "15")
        )
        self.option_chain_ttl_seconds = float(
            option_chain_ttl_seconds
            if option_chain_ttl_seconds is not None
            else os.getenv("MARKET_ADAPTER_OPTION_CHAIN_TTL_SEC", "10")
        )
        resolved_cache_dir = cache_dir or Path(
            os.getenv(
                "MARKET_ADAPTER_CACHE_DIR",
                Path(__file__).resolve().parent / ".cache",
            )
        )
        self.cache = _SharedJsonCache(Path(resolved_cache_dir))
        self._sdk = self._build_sdk_client()

    def _build_sdk_client(self) -> Optional[Any]:
        if not HAS_FYERS_SDK or not self.access_token or not self.client_id:
            return None

    @staticmethod
    def _is_valid_history_payload(payload: Dict[str, Any]) -> bool:
        candles = payload.get("candles")
        return isinstance(candles, list)
        try:
            return fyersModel.FyersModel(
                client_id=self.client_id,
                is_async=False,
                token=self.access_token,
                log_path="",
            )
        except Exception:
            return None

    @staticmethod
    def _unwrap_response(response: Any) -> Dict[str, Any]:
        if isinstance(response, dict):
            if "success" in response and "data" in response and isinstance(response["data"], dict):
                return response["data"]
            return response
        return {}

    @staticmethod
    def _parse_quote_fields(payload: Dict[str, Any], symbol: str) -> Dict[str, float]:
        target = (symbol or "").strip().upper()
        rows = payload.get("d", [])
        if not isinstance(rows, list):
            return {}

        for row in rows:
            if not isinstance(row, dict):
                continue
            row_name = str(row.get("n", "") or row.get("symbol", "")).upper()
            if target and row_name and target not in row_name:
                continue

            values = row.get("v", {}) if isinstance(row.get("v"), dict) else row
            ltp = (
                _to_num(values.get("lp"))
                or _to_num(values.get("ltp"))
                or _to_num(values.get("last_price"))
                or _to_num(values.get("close"))
                or 0.0
            )
            prev_close = (
                _to_num(values.get("prev_close_price"))
                or _to_num(values.get("prev_close"))
                or _to_num(values.get("close"))
                or ltp
                or 0.0
            )
            return {
                "ltp": float(ltp),
                "high": float(_to_num(values.get("high_price")) or _to_num(values.get("high")) or ltp),
                "low": float(_to_num(values.get("low_price")) or _to_num(values.get("low")) or ltp),
                "open": float(_to_num(values.get("open_price")) or _to_num(values.get("open")) or ltp),
                "prev_close": float(prev_close),
                "volume": float(_to_num(values.get("volume")) or 0.0),
            }
        return {}

    @staticmethod
    def _normalize_symbols(symbols: Any) -> List[str]:
        if isinstance(symbols, str):
            raw_symbols = symbols.split(",")
        elif isinstance(symbols, (list, tuple, set)):
            raw_symbols = list(symbols)
        else:
            raw_symbols = []

        normalized: List[str] = []
        seen = set()
        for raw_symbol in raw_symbols:
            symbol = str(raw_symbol or "").strip()
            if not symbol:
                continue
            upper_symbol = symbol.upper()
            if upper_symbol in seen:
                continue
            seen.add(upper_symbol)
            normalized.append(symbol)
        return normalized

    @staticmethod
    def _extract_quote_row(payload: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        target = (symbol or "").strip().upper()
        rows = payload.get("d", [])
        if not isinstance(rows, list):
            return {}

        for row in rows:
            if not isinstance(row, dict):
                continue
            row_name = str(row.get("n", "") or row.get("symbol", "") or row.get("name", "")).strip()
            if row_name.upper() == target:
                return dict(row)

        for row in rows:
            if not isinstance(row, dict):
                continue
            row_name = str(row.get("n", "") or row.get("symbol", "") or row.get("name", "")).strip().upper()
            if target and row_name and target in row_name:
                return dict(row)
        return {}

    @staticmethod
    def _build_quote_row(symbol: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        ltp = float(_to_num(fields.get("ltp")) or 0.0)
        return {
            "n": symbol,
            "v": {
                "symbol": symbol,
                "lp": ltp,
                "open_price": float(_to_num(fields.get("open")) or ltp),
                "high_price": float(_to_num(fields.get("high")) or ltp),
                "low_price": float(_to_num(fields.get("low")) or ltp),
                "prev_close_price": float(_to_num(fields.get("prev_close")) or ltp),
                "volume": float(_to_num(fields.get("volume")) or 0.0),
            },
        }

    def _fetch_quote_payload(self, symbol: str) -> Dict[str, Any]:
        if self._sdk is not None:
            try:
                response = self._sdk.quotes(data={"symbols": symbol})
                payload = self._unwrap_response(response)
                if payload.get("d"):
                    return payload
            except Exception:
                pass

        response = self.client.quotes(symbol)
        return self._unwrap_response(response)

    def get_quote(self, symbol: str, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        symbol = (symbol or "").strip()
        if not symbol:
            return {"symbol": "", "ltp": 0.0, "raw": {}, "cache_hit": True}

        ttl = self.quote_ttl_seconds if ttl_seconds is None else float(ttl_seconds)
        raw_payload, cache_hit, updated_at = self.cache.get_or_set(
            "quotes",
            symbol.upper(),
            ttl,
            lambda: self._fetch_quote_payload(symbol),
        )
        fields = self._parse_quote_fields(raw_payload, symbol)
        return {
            "symbol": symbol,
            "raw": raw_payload,
            "cache_hit": cache_hit,
            "updated_at": updated_at,
            **fields,
        }

    def get_quotes(self, symbols: Any, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        requested_symbols = self._normalize_symbols(symbols)
        ttl = self.quote_ttl_seconds if ttl_seconds is None else float(ttl_seconds)
        if not requested_symbols:
            return {
                "success": True,
                "data": {"s": "ok", "d": []},
                "cache_hit": True,
                "updated_at": time.time(),
                "symbols": [],
                "missing_symbols": [],
            }

        rows_by_symbol: Dict[str, Dict[str, Any]] = {}
        cache_hits: Dict[str, bool] = {}
        updated_at_values: List[float] = []
        missing_symbols: List[str] = []

        for symbol in requested_symbols:
            raw_payload, cache_hit, updated_at = self.cache.get("quotes", symbol.upper(), ttl)
            row = self._extract_quote_row(raw_payload or {}, symbol) if raw_payload else {}
            if row:
                rows_by_symbol[symbol.upper()] = row
                cache_hits[symbol.upper()] = cache_hit
                if updated_at is not None:
                    updated_at_values.append(float(updated_at))
                continue
            missing_symbols.append(symbol)

        if missing_symbols:
            try:
                batch_payload = self._fetch_quote_payload(",".join(missing_symbols))
            except Exception:
                batch_payload = {}
            for symbol in missing_symbols:
                row = self._extract_quote_row(batch_payload, symbol)
                if row:
                    cache_payload = {"s": batch_payload.get("s", "ok"), "d": [row]}
                    if batch_payload.get("code") is not None:
                        cache_payload["code"] = batch_payload.get("code")
                    updated_at = self.cache.set("quotes", symbol.upper(), cache_payload)
                    rows_by_symbol[symbol.upper()] = row
                    cache_hits[symbol.upper()] = False
                    updated_at_values.append(updated_at)
                    continue

                single_quote = self.get_quote(symbol, ttl_seconds=ttl)
                fields = {
                    "ltp": single_quote.get("ltp", 0.0),
                    "open": single_quote.get("open", 0.0),
                    "high": single_quote.get("high", 0.0),
                    "low": single_quote.get("low", 0.0),
                    "prev_close": single_quote.get("prev_close", 0.0),
                    "volume": single_quote.get("volume", 0.0),
                }
                rows_by_symbol[symbol.upper()] = self._build_quote_row(symbol, fields)
                cache_hits[symbol.upper()] = bool(single_quote.get("cache_hit", False))
                updated_at = _to_num(single_quote.get("updated_at"))
                if updated_at is not None:
                    updated_at_values.append(float(updated_at))

        rows = []
        unresolved = []
        for symbol in requested_symbols:
            row = rows_by_symbol.get(symbol.upper())
            if row:
                rows.append(row)
            else:
                unresolved.append(symbol)

        return {
            "success": True,
            "data": {"s": "ok", "d": rows},
            "cache_hit": all(cache_hits.get(symbol.upper(), False) for symbol in requested_symbols),
            "updated_at": max(updated_at_values) if updated_at_values else time.time(),
            "symbols": requested_symbols,
            "missing_symbols": unresolved,
        }

    def get_quote_ltp(self, symbol: str, ttl_seconds: Optional[float] = None) -> float:
        return float(self.get_quote(symbol, ttl_seconds=ttl_seconds).get("ltp", 0.0) or 0.0)

    def get_index_quote(self, index_name: str, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        config = get_market_index_config(index_name)
        return self.get_quote(str(config.get("spot_symbol", "")), ttl_seconds=ttl_seconds)

    def _fetch_history_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._sdk is not None:
            try:
                response = self._sdk.history(data=payload)
                data = self._unwrap_response(response)
                if data.get("candles") is not None:
                    return data
            except Exception:
                pass

        response = self.client._request(  # noqa: SLF001
            "GET",
            self.client._data_url("/history"),  # noqa: SLF001
            params=payload,
        )
        return self._unwrap_response(response)

    def get_history_snapshot(
        self,
        symbol: str,
        resolution: str = "5",
        lookback_days: int = 5,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        ttl = self._history_snapshot_ttl_seconds(
            resolution=resolution,
            requested_ttl_seconds=ttl_seconds,
            fallback_ttl_seconds=self.history_ttl_seconds,
        )
        key = json.dumps(
            {
                "symbol": symbol,
                "resolution": str(resolution),
                "lookback_days": int(lookback_days),
            },
            sort_keys=True,
        )
        payload, cache_hit, updated_at = self.cache.get_or_set(
            "history",
            key,
            ttl,
            lambda: self._fetch_history_payload(
                {
                    "symbol": symbol,
                    "resolution": str(resolution),
                    "date_format": "0",
                    "range_from": str(int(time.time()) - max(1, int(lookback_days)) * 24 * 60 * 60),
                    "range_to": str(int(time.time())),
                    "cont_flag": "1",
                }
            ),
            validator=self._is_valid_history_payload,
        )
        result = dict(payload) if isinstance(payload, dict) else {}
        result["_cache_hit"] = cache_hit
        result["_updated_at"] = updated_at
        return result

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
        ttl = self.history_ttl_seconds if ttl_seconds is None else float(ttl_seconds)
        key = json.dumps(
            {
                "symbol": symbol,
                "resolution": str(resolution),
                "date_format": str(date_format),
                "range_from": str(range_from),
                "range_to": str(range_to),
                "cont_flag": str(cont_flag),
            },
            sort_keys=True,
        )
        payload, cache_hit, updated_at = self.cache.get_or_set(
            "history_range",
            key,
            ttl,
            lambda: self._fetch_history_payload(
                {
                    "symbol": symbol,
                    "resolution": str(resolution),
                    "date_format": str(date_format),
                    "range_from": str(range_from),
                    "range_to": str(range_to),
                    "cont_flag": str(cont_flag),
                }
            ),
            validator=self._is_valid_history_payload,
        )
        result = dict(payload) if isinstance(payload, dict) else {}
        result["_cache_hit"] = cache_hit
        result["_updated_at"] = updated_at
        return result

    def _fetch_option_chain_payload(self, symbol: str, strike_count: int) -> Dict[str, Any]:
        payload = {
            "symbol": symbol,
            "timestamp": "",
            "strikecount": int(strike_count),
        }

        if self._sdk is not None:
            try:
                response = self._sdk.optionchain(data=payload)
                data = self._unwrap_response(response)
                if data:
                    return data
            except Exception:
                pass

        response = self.client.option_chain(symbol=symbol, strike_count=int(strike_count))
        return self._unwrap_response(response)

    def get_option_chain_snapshot(
        self,
        symbol: str,
        strike_count: int = 10,
        ttl_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        ttl = self.option_chain_ttl_seconds if ttl_seconds is None else float(ttl_seconds)
        key = json.dumps(
            {"symbol": symbol, "strike_count": int(strike_count)},
            sort_keys=True,
        )
        payload, cache_hit, updated_at = self.cache.get_or_set(
            "option_chain",
            key,
            ttl,
            lambda: self._fetch_option_chain_payload(symbol, int(strike_count)),
        )
        result = dict(payload) if isinstance(payload, dict) else {}
        result["_cache_hit"] = cache_hit
        result["_updated_at"] = updated_at
        return result

    def futures_candidates(self, index_name: str, now_local: Optional[datetime] = None) -> list[str]:
        config = get_market_index_config(index_name)
        prefix = str(config.get("fut_prefix", "")).strip()
        if not prefix:
            return []

        when = now_local or datetime.now()
        year = when.year % 100
        month = when.month
        candidates = []
        for offset in (0, 1):
            candidate_month = month + offset
            candidate_year = year
            if candidate_month > 12:
                candidate_month -= 12
                candidate_year = (year + 1) % 100
            month_code = self._MONTH_CODES[candidate_month - 1]
            candidates.append(f"{prefix}{candidate_year:02d}{month_code}FUT")
        return candidates

    def resolve_future_quote(
        self,
        index_name: str,
        explicit_symbol: str = "",
        now_local: Optional[datetime] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Tuple[str, float]:
        explicit = (explicit_symbol or "").strip()
        if explicit:
            return explicit, self.get_quote_ltp(explicit, ttl_seconds=ttl_seconds)

        for symbol in self.futures_candidates(index_name, now_local=now_local):
            ltp = self.get_quote_ltp(symbol, ttl_seconds=ttl_seconds)
            if ltp > 0:
                return symbol, ltp
        return "", 0.0

    def get_index_market_data(
        self,
        index_name: str,
        resolution: str = "5",
        lookback_days: int = 5,
        strike_count: int = 8,
        fut_symbol: str = "",
    ) -> Dict[str, Any]:
        canonical_name = canonicalize_index_name(index_name)
        config = get_market_index_config(canonical_name)
        now_local = datetime.now()
        future_symbol, future_ltp = self.resolve_future_quote(
            canonical_name,
            explicit_symbol=fut_symbol,
            now_local=now_local,
        )
        return {
            "index": canonical_name,
            "config": config,
            "history": self.get_history_snapshot(
                str(config.get("spot_symbol", "")),
                resolution=resolution,
                lookback_days=lookback_days,
            ),
            "option_chain": self.get_option_chain_snapshot(
                str(config.get("spot_symbol", "")),
                strike_count=strike_count,
            ),
            "quote": self.get_quote(str(config.get("spot_symbol", ""))),
            "vix_quote": self.get_quote(str(config.get("vix_symbol", ""))),
            "future_symbol": future_symbol,
            "future_ltp": future_ltp,
        }

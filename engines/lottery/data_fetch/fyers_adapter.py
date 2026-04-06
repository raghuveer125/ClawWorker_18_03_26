"""FYERS adapter — fetch spot and option chain using the shared FYERS client.

Reuses shared_project_engine.auth.FyersClient for all API calls.
Symbol mapping is config-driven, never hardcoded.
"""

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure shared_project_engine is importable
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared_project_engine.auth.fyers_client import FyersClient

from ..config import LotteryConfig
from ..models import (
    ChainSnapshot,
    ExpiryInfo,
    OptionRow,
    OptionType,
    UnderlyingTick,
)
from .provider import DataProvider

logger = logging.getLogger(__name__)

# Symbol → FYERS API symbol mapping (config-driven, extensible)
_DEFAULT_FYERS_SYMBOLS: dict[str, str] = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
}

# Default lot sizes (fetched dynamically when possible)
_DEFAULT_LOT_SIZES: dict[str, int] = {
    "NIFTY": 75,
    "NIFTY50": 75,
    "BANKNIFTY": 30,
    "FINNIFTY": 40,
    "MIDCPNIFTY": 50,
    "SENSEX": 10,
}


class FyersAdapter(DataProvider):
    """FYERS data provider for the lottery pipeline.

    Uses shared FyersClient for spot quotes and option chain fetching.
    All symbol references are resolved from a mapping dict, never hardcoded.
    """

    def __init__(
        self,
        config: LotteryConfig,
        env_file: Optional[str] = None,
        symbol_map: Optional[dict[str, str]] = None,
    ) -> None:
        self._config = config
        self._symbol_map = {**_DEFAULT_FYERS_SYMBOLS, **(symbol_map or {})}
        self._lot_sizes: dict[str, int] = dict(_DEFAULT_LOT_SIZES)
        self._client: Optional[FyersClient] = None
        self._env_file = env_file
        self._lot_sizes_fetched = False

    def _get_client(self) -> FyersClient:
        """Lazy-init the FYERS client."""
        if self._client is None:
            self._client = FyersClient(env_file=self._env_file)
            logger.info(
                "FyersClient initialized, token=%s",
                "SET" if self._client.access_token else "MISSING",
            )
        return self._client

    def _resolve_fyers_symbol(self, symbol: str) -> Optional[str]:
        """Map a config symbol to FYERS API symbol."""
        fyers_sym = self._symbol_map.get(symbol.upper())
        if not fyers_sym:
            logger.warning("No FYERS symbol mapping for '%s'", symbol)
        return fyers_sym

    # ── DataProvider Interface ─────────────────────────────────────────

    def fetch_spot(self, symbol: str, exchange: str) -> Optional[UnderlyingTick]:
        """Fetch spot LTP via FYERS quotes API."""
        fyers_sym = self._resolve_fyers_symbol(symbol)
        if not fyers_sym:
            return None

        client = self._get_client()
        now = datetime.now(timezone.utc)

        result = client.quotes(fyers_sym)
        if not result.get("success"):
            logger.error("Spot fetch failed for %s: %s", symbol, result.get("error"))
            return None

        data = result.get("data", {})
        # FYERS returns quotes in data.d[] array
        quotes_list = data.get("d", [])
        if not quotes_list:
            # Try alternate structure
            quotes_list = data.get("data", {}).get("d", [])
        if not quotes_list:
            logger.error("Empty quotes response for %s", symbol)
            return None

        q = quotes_list[0]
        v = q.get("v", {})

        return UnderlyingTick(
            symbol=symbol,
            exchange=exchange,
            ltp=float(v.get("lp", 0)),
            open=_safe_float(v.get("open_price")),
            high=_safe_float(v.get("high_price")),
            low=_safe_float(v.get("low_price")),
            prev_close=_safe_float(v.get("prev_close_price")),
            timestamp=now,
            source_timestamp=_parse_epoch(v.get("tt")),
            ingested_at=now,
        )

    def fetch_option_chain(
        self,
        symbol: str,
        exchange: str,
        expiry: str,
        strike_count: int = 50,
    ) -> Optional[ChainSnapshot]:
        """Fetch option chain via FYERS option-chain API."""
        fyers_sym = self._resolve_fyers_symbol(symbol)
        if not fyers_sym:
            return None

        client = self._get_client()
        now = datetime.now(timezone.utc)

        result = client.option_chain(fyers_sym, strike_count=strike_count)
        if not result.get("success"):
            logger.error(
                "Option chain fetch failed for %s: %s",
                symbol,
                result.get("error"),
            )
            return None

        raw_data = result.get("data", {})
        # FYERS nests chain data under data.data
        chain_data = raw_data
        if isinstance(raw_data, dict) and "data" in raw_data:
            chain_data = raw_data["data"]

        contracts = chain_data.get("optionsChain", [])
        if not contracts:
            logger.error("Empty option chain for %s", symbol)
            return None

        # Extract spot from chain response if available
        spot_ltp = _safe_float(chain_data.get("ltp")) or 0.0

        # Parse contracts into OptionRow tuples
        rows: list[OptionRow] = []
        for c in contracts:
            strike = _safe_float(c.get("strike_price", 0))
            if strike <= 0:
                continue

            opt_type_str = c.get("option_type", "")
            if opt_type_str not in ("CE", "PE"):
                continue

            option_type = OptionType.CE if opt_type_str == "CE" else OptionType.PE

            # Extract expiry from contract if available
            contract_expiry = c.get("expiry", expiry)
            # Filter to target expiry if specified and contract has expiry info
            if expiry and contract_expiry and contract_expiry != expiry:
                # Normalize date formats for comparison
                norm_target = _normalize_date(expiry)
                norm_contract = _normalize_date(str(contract_expiry))
                if norm_target and norm_contract and norm_target != norm_contract:
                    continue

            row = OptionRow(
                symbol=symbol,
                expiry=str(contract_expiry or expiry),
                strike=strike,
                option_type=option_type,
                ltp=_safe_float(c.get("ltp", 0)),
                change=_safe_float(c.get("ch")),
                change_percent=_safe_float(c.get("chp")),
                volume=_safe_int(c.get("volume")),
                oi=_safe_int(c.get("oi")),
                oi_change=_safe_int(c.get("oiChange")),
                bid=_safe_float(c.get("bid")),
                ask=_safe_float(c.get("ask")),
                bid_qty=_safe_int(c.get("bidQty")),
                ask_qty=_safe_int(c.get("askQty")),
                iv=_safe_float(c.get("iv")),
                last_trade_time=_parse_epoch(c.get("ltt")),
                source_timestamp=now,
                ingested_at=now,
            )
            rows.append(row)

        if not rows:
            logger.warning("No valid option rows parsed for %s", symbol)
            return None

        # If spot not in chain response, fetch separately
        if spot_ltp <= 0:
            spot_tick = self.fetch_spot(symbol, exchange)
            if spot_tick:
                spot_ltp = spot_tick.ltp
            else:
                logger.warning("Could not determine spot for %s", symbol)
                return None
        else:
            spot_tick = UnderlyingTick(
                symbol=symbol,
                exchange=exchange,
                ltp=spot_ltp,
                timestamp=now,
                ingested_at=now,
            )

        # Determine expiry from rows if not explicitly provided
        resolved_expiry = expiry
        if not resolved_expiry and rows:
            resolved_expiry = rows[0].expiry

        return ChainSnapshot(
            symbol=symbol,
            expiry=resolved_expiry,
            spot_ltp=spot_ltp,
            snapshot_timestamp=now,
            rows=tuple(rows),
            spot_tick=spot_tick,
        )

    def fetch_expiries(self, symbol: str, exchange: str) -> list[ExpiryInfo]:
        """Fetch available expiries from the option chain response.

        FYERS option chain returns contracts across expiries.
        We fetch with a large strike count and extract unique expiries.
        """
        fyers_sym = self._resolve_fyers_symbol(symbol)
        if not fyers_sym:
            return []

        client = self._get_client()
        result = client.option_chain(fyers_sym, strike_count=5)
        if not result.get("success"):
            logger.error("Expiry fetch failed for %s: %s", symbol, result.get("error"))
            return []

        raw_data = result.get("data", {})
        chain_data = raw_data.get("data", raw_data) if isinstance(raw_data, dict) else raw_data

        contracts = chain_data.get("optionsChain", [])
        expiry_set: set[str] = set()
        for c in contracts:
            exp = c.get("expiry")
            if exp:
                expiry_set.add(str(exp))

        today = datetime.now(timezone.utc).date()
        expiries: list[ExpiryInfo] = []
        for exp_str in sorted(expiry_set):
            norm = _normalize_date(exp_str)
            if not norm:
                continue
            try:
                exp_date = datetime.strptime(norm, "%Y-%m-%d").date()
            except ValueError:
                continue
            dte = (exp_date - today).days
            if dte < 0:
                continue
            expiries.append(
                ExpiryInfo(
                    symbol=symbol,
                    expiry_date=norm,
                    days_to_expiry=dte,
                    expiry_type="WEEKLY" if dte <= 7 else "MONTHLY",
                )
            )

        return sorted(expiries, key=lambda e: e.days_to_expiry)

    def get_lot_size(self, symbol: str) -> int:
        """Get lot size — tries dynamic fetch first, falls back to defaults."""
        sym_upper = symbol.upper()

        # Try dynamic fetch once
        if not self._lot_sizes_fetched:
            try:
                client = self._get_client()
                fetched = client.get_lot_sizes()
                if fetched:
                    self._lot_sizes.update(fetched)
                    logger.info("Lot sizes fetched: %s", fetched)
            except Exception as e:
                logger.warning("Lot size fetch failed, using defaults: %s", e)
            self._lot_sizes_fetched = True

        # Try exact match, then common aliases
        for key in (sym_upper, f"{sym_upper}50", sym_upper.replace("50", "")):
            if key in self._lot_sizes:
                return self._lot_sizes[key]

        logger.warning("No lot size for '%s', defaulting to config value", symbol)
        return self._config.paper_trading.lot_size

    def is_connected(self) -> bool:
        """Check if FYERS client has valid auth."""
        try:
            client = self._get_client()
            return bool(client.access_token)
        except Exception:
            return False


# ── Helpers ────────────────────────────────────────────────────────────────

def _safe_float(val: object) -> Optional[float]:
    """Safely convert to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: object) -> Optional[int]:
    """Safely convert to int, returning None on failure."""
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _parse_epoch(val: object) -> Optional[datetime]:
    """Parse a UNIX epoch timestamp to UTC datetime."""
    if val is None:
        return None
    try:
        ts = float(val)
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _normalize_date(date_str: str) -> Optional[str]:
    """Normalize various date formats to YYYY-MM-DD."""
    if not date_str:
        return None

    # Already YYYY-MM-DD
    if len(date_str) == 10 and date_str[4] == "-":
        return date_str

    # Try common formats
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y%m%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Epoch timestamp
    try:
        ts = int(date_str)
        if ts > 1_000_000_000:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        pass

    return None

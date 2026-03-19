"""
Centralized Index Configuration
================================
Single source of truth for all index-related settings.

Usage:
    from shared_project_engine.indices import INDEX_CONFIG, get_watchlist

    # Get SENSEX config
    sensex = INDEX_CONFIG["SENSEX"]
    print(sensex["lot_size"])  # 10

    # Get watchlist for an index
    symbols = get_watchlist("BANKNIFTY")
"""

import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

try:
    import requests
except ImportError:  # pragma: no cover - exercised in environments without requests
    requests = None

# =============================================================================
# INDEX CONFIGURATION
# =============================================================================

INDEX_CONFIG: Dict[str, Dict[str, Any]] = {
    "SENSEX": {
        "name": "SENSEX",
        "display_name": "BSE SENSEX",
        "exchange": "BSE",
        "symbol": "BSE:SENSEX-INDEX",
        "futures_symbol": "BSE:SENSEX{expiry}-FUT",
        "options_prefix": "BSE:SENSEX",
        "lot_size": 20,  # Verified Mar 2026
        "tick_size": 0.05,
        "expiry_weekday": 4,  # Friday (0=Monday)
        "strike_gap": 100,
        "enabled": True,
        "watchlist": [
            "NSE:RELIANCE-EQ", "NSE:HDFCBANK-EQ", "NSE:TCS-EQ", "NSE:INFY-EQ",
            "NSE:ICICIBANK-EQ", "NSE:SBIN-EQ", "NSE:BHARTIARTL-EQ", "NSE:ITC-EQ",
            "NSE:AXISBANK-EQ", "NSE:BAJFINANCE-EQ", "NSE:BAJAJFINSV-EQ", "NSE:LT-EQ",
            "NSE:MARUTI-EQ", "NSE:NTPC-EQ", "NSE:POWERGRID-EQ", "NSE:SUNPHARMA-EQ",
            "NSE:HINDUNILVR-EQ", "NSE:M&M-EQ", "NSE:TITAN-EQ", "NSE:ULTRACEMCO-EQ",
            "NSE:TATASTEEL-EQ", "NSE:DRREDDY-EQ", "NSE:ONGC-EQ", "NSE:TECHM-EQ",
            "NSE:NESTLEIND-EQ", "NSE:INDUSINDBK-EQ", "NSE:KOTAKBANK-EQ",
            "NSE:ADANIPORTS-EQ", "NSE:BEL-EQ", "NSE:TRENT-EQ"
        ],
    },
    "NIFTY50": {
        "name": "NIFTY50",
        "display_name": "NIFTY 50",
        "exchange": "NSE",
        "symbol": "NSE:NIFTY50-INDEX",
        "futures_symbol": "NSE:NIFTY{expiry}-FUT",
        "options_prefix": "NSE:NIFTY",
        "lot_size": 65,  # Verified Mar 2026
        "tick_size": 0.05,
        "expiry_weekday": 3,  # Thursday
        "strike_gap": 50,
        "enabled": True,
        "watchlist": [
            "NSE:RELIANCE-EQ", "NSE:HDFCBANK-EQ", "NSE:ICICIBANK-EQ", "NSE:INFY-EQ",
            "NSE:TCS-EQ", "NSE:SBIN-EQ", "NSE:BHARTIARTL-EQ", "NSE:ITC-EQ",
            "NSE:LT-EQ", "NSE:KOTAKBANK-EQ", "NSE:HINDUNILVR-EQ", "NSE:ASIANPAINT-EQ",
            "NSE:AXISBANK-EQ", "NSE:BAJFINANCE-EQ", "NSE:BAJAJFINSV-EQ", "NSE:HCLTECH-EQ",
            "NSE:MARUTI-EQ", "NSE:SUNPHARMA-EQ", "NSE:TITAN-EQ", "NSE:ULTRACEMCO-EQ",
            "NSE:TATASTEEL-EQ", "NSE:TECHM-EQ", "NSE:NESTLEIND-EQ", "NSE:POWERGRID-EQ",
            "NSE:ONGC-EQ", "NSE:ADANIPORTS-EQ", "NSE:INDUSINDBK-EQ", "NSE:NTPC-EQ",
            "NSE:WIPRO-EQ", "NSE:M&M-EQ", "NSE:DRREDDY-EQ", "NSE:ADANIENT-EQ",
            "NSE:APOLLOHOSP-EQ", "NSE:BPCL-EQ", "NSE:BRITANNIA-EQ", "NSE:CIPLA-EQ",
            "NSE:COALINDIA-EQ", "NSE:DIVISLAB-EQ", "NSE:EICHERMOT-EQ", "NSE:GRASIM-EQ",
            "NSE:HEROMOTOCO-EQ", "NSE:HINDALCO-EQ", "NSE:JSWSTEEL-EQ", "NSE:JIOFIN-EQ",
            "NSE:LTIM-EQ", "NSE:SBILIFE-EQ", "NSE:SHRIRAMFIN-EQ", "NSE:TATAMOTORS-EQ",
            "NSE:BEL-EQ", "NSE:HDFCLIFE-EQ"
        ],
    },
    "BANKNIFTY": {
        "name": "BANKNIFTY",
        "display_name": "BANK NIFTY",
        "exchange": "NSE",
        "symbol": "NSE:NIFTYBANK-INDEX",
        "futures_symbol": "NSE:BANKNIFTY{expiry}-FUT",
        "options_prefix": "NSE:BANKNIFTY",
        "lot_size": 30,  # Verified Mar 2026
        "tick_size": 0.05,
        "expiry_weekday": 2,  # Wednesday
        "strike_gap": 100,
        "enabled": True,
        "watchlist": [
            "NSE:HDFCBANK-EQ", "NSE:ICICIBANK-EQ", "NSE:AXISBANK-EQ", "NSE:KOTAKBANK-EQ",
            "NSE:SBIN-EQ", "NSE:INDUSINDBK-EQ", "NSE:BANKBARODA-EQ", "NSE:PNB-EQ",
            "NSE:AUBANK-EQ", "NSE:IDFCFIRSTB-EQ", "NSE:FEDERALBNK-EQ", "NSE:CANBK-EQ"
        ],
    },
    "FINNIFTY": {
        "name": "FINNIFTY",
        "display_name": "NIFTY FIN SERVICE",
        "exchange": "NSE",
        "symbol": "NSE:FINNIFTY-INDEX",
        "futures_symbol": "NSE:FINNIFTY{expiry}-FUT",
        "options_prefix": "NSE:FINNIFTY",
        "lot_size": 60,  # Verified Mar 2026
        "tick_size": 0.05,
        "expiry_weekday": 1,  # Tuesday
        "strike_gap": 50,
        "enabled": True,
        "watchlist": [
            "NSE:HDFCBANK-EQ", "NSE:ICICIBANK-EQ", "NSE:KOTAKBANK-EQ", "NSE:AXISBANK-EQ",
            "NSE:SBIN-EQ", "NSE:BAJFINANCE-EQ", "NSE:BAJAJFINSV-EQ", "NSE:HDFCLIFE-EQ",
            "NSE:SBILIFE-EQ", "NSE:ICICIPRULI-EQ", "NSE:MUTHOOTFIN-EQ", "NSE:CHOLAFIN-EQ",
            "NSE:ICICIGI-EQ", "NSE:SHRIRAMFIN-EQ", "NSE:PFC-EQ", "NSE:RECLTD-EQ"
        ],
    },
    "MIDCPNIFTY": {
        "name": "MIDCPNIFTY",
        "display_name": "NIFTY MIDCAP SELECT",
        "exchange": "NSE",
        "symbol": "NSE:MIDCPNIFTY-INDEX",
        "futures_symbol": "NSE:MIDCPNIFTY{expiry}-FUT",
        "options_prefix": "NSE:MIDCPNIFTY",
        "lot_size": 120,  # Verified Mar 2026
        "tick_size": 0.05,
        "expiry_weekday": 0,  # Monday
        "strike_gap": 25,
        "enabled": False,  # Disabled by default (less liquid)
        "watchlist": [
            "NSE:PERSISTENT-EQ", "NSE:MPHASIS-EQ", "NSE:COFORGE-EQ", "NSE:LTTS-EQ",
            "NSE:TATACOMM-EQ", "NSE:MINDTREE-EQ", "NSE:POLYCAB-EQ", "NSE:VOLTAS-EQ",
            "NSE:GODREJPROP-EQ", "NSE:OBEROIRLTY-EQ", "NSE:PHOENIXLTD-EQ"
        ],
    },
}

VIX_SYMBOL = "NSE:INDIAVIX-INDEX"

INDEX_ALIASES: Dict[str, str] = {
    "SENSEX": "SENSEX",
    "BSESENSEX": "SENSEX",
    "BANKNIFTY": "BANKNIFTY",
    "NIFTYBANK": "BANKNIFTY",
    "NIFTY": "NIFTY50",
    "NIFTY50": "NIFTY50",
    "NIFTYINDEX": "NIFTY50",
    "FINNIFTY": "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
}

# Historical monthly expiry dates used by backdated parsers and archived views.
# Live dashboard expiry badges must not rely on this table.
MONTHLY_EXPIRY_DATES = {
    "2026-03": {
        "SENSEX": "2026-03-12",
        "BANKNIFTY": "2026-03-30",
        "NIFTY50": "2026-03-10",
        "FINNIFTY": "2026-03-10",
        "MIDCPNIFTY": "2026-03-09",
    },
    "2026-04": {
        "SENSEX": "2026-04-09",
        "BANKNIFTY": "2026-04-29",
        "NIFTY50": "2026-04-07",
        "FINNIFTY": "2026-04-07",
        "MIDCPNIFTY": "2026-04-06",
    },
}

# Active indices for trading (order matters for display)
ACTIVE_INDICES: List[str] = ["SENSEX", "NIFTY50", "BANKNIFTY", "FINNIFTY"]

IST = ZoneInfo("Asia/Kolkata")
_EXPIRY_CACHE_TTL_SECONDS = 3600
_EXPIRY_CACHE_DIR = Path(__file__).parent / ".cache"
_EXPIRY_CACHE_FILE = _EXPIRY_CACHE_DIR / "expiry_schedule.json"
_EXCHANGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
_NSE_OPTION_CHAIN_BOOTSTRAP_URL = "https://www.nseindia.com/option-chain"
_NSE_OPTION_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices"
_BSE_DERIVATIVES_PAGE_URL = "https://m.bseindia.com/derivatives.aspx"
_NSE_OPTION_CHAIN_SYMBOLS: Dict[str, str] = {
    "NIFTY50": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "FINNIFTY": "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
}
_MONTH_TEXT_TO_NUMBER = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
_SINGLE_CHAR_MONTH_TO_NUMBER = {"O": 10, "N": 11, "D": 12}
_SUPPORTED_EXPIRY_SOURCES = {"exchange", "fyers"}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _ist_now() -> datetime:
    """Current timestamp in IST for exchange-aligned date calculations."""
    return datetime.now(IST)


def _ist_today() -> date:
    return _ist_now().date()


def _build_exchange_session():
    if requests is None:
        return None
    session = requests.Session()
    session.headers.update(_EXCHANGE_HEADERS)
    return session


def _parse_exchange_date(value: Any) -> Optional[date]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw_value, fmt).date()
        except ValueError:
            continue

    return None


def _format_weekday_info(expiry_date: date) -> Dict[str, Any]:
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday = expiry_date.weekday()
    return {
        "weekday": weekday,
        "weekday_name": weekday_names[weekday],
        "weekday_short": weekday_names[weekday][:3],
    }


def _extract_nse_expiry_dates(payload: Dict[str, Any], today: date) -> List[str]:
    expiry_values = (
        ((payload or {}).get("records") or {}).get("expiryDates")
        or ((payload or {}).get("filtered") or {}).get("expiryDates")
        or []
    )
    dates = {
        parsed.isoformat()
        for parsed in (_parse_exchange_date(value) for value in expiry_values)
        if parsed and parsed >= today
    }
    return sorted(dates)


def _extract_bse_series_expiry_dates(page_text: str, today: date) -> List[str]:
    expiry_dates = set()
    patterns = (
        re.compile(r"SENSEX(?P<yy>\d{2})(?P<m>[1-9OND])(?P<dd>\d{2})\d+(?:CE|PE)"),
        re.compile(r"SENSEX(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})\d+(?:CE|PE)"),
    )

    for pattern in patterns:
        for match in pattern.finditer(page_text or ""):
            year = 2000 + int(match.group("yy"))
            if "mm" in match.groupdict() and match.group("mm"):
                month = int(match.group("mm"))
            else:
                raw_month = match.group("m")
                month = _SINGLE_CHAR_MONTH_TO_NUMBER.get(raw_month)
                if month is None:
                    month = int(raw_month)
            day = int(match.group("dd"))
            try:
                parsed = date(year, month, day)
            except ValueError:
                continue
            if parsed >= today:
                expiry_dates.add(parsed.isoformat())

    return sorted(expiry_dates)


def _parse_option_symbol_expiry(symbol: str) -> Optional[date]:
    raw_symbol = str(symbol or "").upper().split(":")[-1]
    if not raw_symbol:
        return None

    weekly_y_m_dd = re.search(
        r"^[A-Z]+(?P<yy>\d{2})(?P<m>[1-9OND])(?P<dd>\d{2})\d+(CE|PE)$",
        raw_symbol,
    )
    if weekly_y_m_dd:
        yy = int(weekly_y_m_dd.group("yy"))
        month_token = weekly_y_m_dd.group("m")
        month = _SINGLE_CHAR_MONTH_TO_NUMBER.get(month_token)
        if month is None:
            month = int(month_token)
        day = int(weekly_y_m_dd.group("dd"))
        try:
            return date(2000 + yy, month, day)
        except ValueError:
            return None

    weekly_y_mm_dd = re.search(
        r"^[A-Z]+(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})\d+(CE|PE)$",
        raw_symbol,
    )
    if weekly_y_mm_dd:
        try:
            return date(
                2000 + int(weekly_y_mm_dd.group("yy")),
                int(weekly_y_mm_dd.group("mm")),
                int(weekly_y_mm_dd.group("dd")),
            )
        except ValueError:
            return None

    weekly_dd_mon_yy = re.search(
        r"^[A-Z]+(?P<dd>\d{2})(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?P<yy>\d{2})\d+(CE|PE)$",
        raw_symbol,
    )
    if weekly_dd_mon_yy:
        try:
            return date(
                2000 + int(weekly_dd_mon_yy.group("yy")),
                _MONTH_TEXT_TO_NUMBER[weekly_dd_mon_yy.group("mon")],
                int(weekly_dd_mon_yy.group("dd")),
            )
        except ValueError:
            return None

    return None


def _extract_fyers_chain_expiry_dates(payload: Dict[str, Any], today: date) -> List[str]:
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if isinstance(data.get("data"), dict):
        data = data["data"]

    options = data.get("optionsChain", data.get("options", []))
    expiry_dates = set()

    for option in options or []:
        if not isinstance(option, dict):
            continue
        parsed = (
            _parse_exchange_date(option.get("expiry"))
            or _parse_exchange_date(option.get("expiryDate"))
            or _parse_option_symbol_expiry(option.get("symbol"))
        )
        if parsed and parsed >= today:
            expiry_dates.add(parsed.isoformat())

    return sorted(expiry_dates)


def _load_expiry_cache() -> Optional[Dict[str, Any]]:
    try:
        if not _EXPIRY_CACHE_FILE.exists():
            return None
        with open(_EXPIRY_CACHE_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if time.time() - float(payload.get("timestamp", 0) or 0) > _EXPIRY_CACHE_TTL_SECONDS:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        return {
            "data": data,
            "fetched_at": payload.get("fetched_at"),
        }
    except Exception:
        return None


def _save_expiry_cache(data: Dict[str, Any]) -> str:
    fetched_at = _ist_now().isoformat()
    try:
        _EXPIRY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_EXPIRY_CACHE_FILE, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "data": data,
                    "fetched_at": fetched_at,
                    "timestamp": time.time(),
                },
                handle,
            )
    except Exception:
        pass
    return fetched_at


def clear_expiry_cache() -> None:
    """Remove the on-disk exchange expiry cache."""
    try:
        _EXPIRY_CACHE_FILE.unlink(missing_ok=True)
    except Exception:
        pass

def canonicalize_index_name(index_name: str) -> str:
    """Normalize user-provided names to the shared canonical key."""
    key = re.sub(r"[^A-Z0-9]", "", (index_name or "").upper())
    if not key:
        return "SENSEX"
    return INDEX_ALIASES.get(key, key)


def get_index_config(index_name: str) -> Optional[Dict[str, Any]]:
    """Get configuration for a specific index."""
    return INDEX_CONFIG.get(canonicalize_index_name(index_name))


def get_market_index_config(index_name: str) -> Dict[str, Any]:
    """
    Return index config with legacy compatibility keys used by live engines.

    This keeps INDEX_CONFIG as the source of truth while exposing the older
    field names still expected by fyersN7 and ClawWork code.
    """
    canonical_name = canonicalize_index_name(index_name)
    config = INDEX_CONFIG.get(canonical_name) or INDEX_CONFIG["SENSEX"]
    fut_prefix = str(config.get("futures_symbol", "")).split("{expiry}", 1)[0]

    return {
        **config,
        "canonical_name": canonical_name,
        "name": canonical_name,
        "display_name": canonical_name,
        "spot_symbol": config.get("symbol", ""),
        "vix_symbol": VIX_SYMBOL,
        "option_prefix": str(config.get("options_prefix", "")).split(":", 1)[-1],
        "fut_prefix": fut_prefix,
        "fut_env_prefix": "NIFTY" if canonical_name == "NIFTY50" else canonical_name,
        "strike_step": config.get("strike_gap"),
        "expiry_day": config.get("expiry_weekday"),
    }


def get_watchlist(index_name: str) -> List[str]:
    """Get watchlist symbols for an index."""
    config = get_index_config(index_name)
    return config.get("watchlist", []) if config else []


def get_all_watchlists() -> Dict[str, List[str]]:
    """Get all watchlists as a dict."""
    return {name: cfg["watchlist"] for name, cfg in INDEX_CONFIG.items() if cfg.get("watchlist")}


def get_expiry_info(index_name: str, month_key: str = None) -> Optional[str]:
    """
    Get expiry date for an index.

    Args:
        index_name: Index name (e.g., "SENSEX")
        month_key: Month in YYYY-MM format (defaults to current month)

    Returns:
        Expiry date string (YYYY-MM-DD) or None
    """
    if month_key is None:
        from datetime import datetime
        month_key = datetime.now().strftime("%Y-%m")

    monthly = MONTHLY_EXPIRY_DATES.get(month_key, {})
    idx = index_name.upper()
    if idx == "NIFTY":
        idx = "NIFTY50"
    return monthly.get(idx)


def get_enabled_indices() -> List[str]:
    """Get list of enabled indices for trading."""
    return [name for name, cfg in INDEX_CONFIG.items() if cfg.get("enabled", True)]


def is_expiry_today(index_name: str, check_date: Optional[Any] = None) -> bool:
    """
    Check if today is weekly expiry day for an index.

    Args:
        index_name: Index name (e.g., "NIFTY50")
        check_date: Optional date to check (defaults to today)

    Returns:
        True if today is expiry day for this index
    """
    from datetime import datetime, date

    config = get_index_config(index_name)
    if not config:
        return False

    expiry_weekday = config.get("expiry_weekday")
    if expiry_weekday is None:
        return False

    # Get the day to check
    if check_date is None:
        check_date = date.today()
    elif isinstance(check_date, datetime):
        check_date = check_date.date()

    # Config uses 0=Monday, Python weekday() also uses 0=Monday
    return check_date.weekday() == expiry_weekday


def fetch_nse_exchange_expiries(session=None, today: Optional[date] = None) -> Dict[str, str]:
    """
    Fetch upcoming expiries for NSE index options from the official NSE option-chain response.
    """
    if requests is None:
        return {}

    today = today or _ist_today()
    own_session = session is None
    session = session or _build_exchange_session()
    if session is None:
        return {}

    result: Dict[str, str] = {}
    try:
        session.get(_NSE_OPTION_CHAIN_BOOTSTRAP_URL, timeout=20)
    except Exception:
        pass

    for index_name, symbol in _NSE_OPTION_CHAIN_SYMBOLS.items():
        try:
            response = session.get(_NSE_OPTION_CHAIN_URL, params={"symbol": symbol}, timeout=20)
            response.raise_for_status()
            expiries = _extract_nse_expiry_dates(response.json(), today)
            if expiries:
                result[index_name] = expiries[0]
        except Exception:
            continue

    if own_session:
        session.close()

    return result


def fetch_bse_exchange_expiries(session=None, today: Optional[date] = None) -> Dict[str, str]:
    """
    Fetch upcoming SENSEX expiries from the official BSE derivatives page.
    """
    if requests is None:
        return {}

    today = today or _ist_today()
    own_session = session is None
    session = session or _build_exchange_session()
    if session is None:
        return {}

    result: Dict[str, str] = {}
    try:
        response = session.get(_BSE_DERIVATIVES_PAGE_URL, timeout=20)
        response.raise_for_status()
        expiries = _extract_bse_series_expiry_dates(response.text, today)
        if expiries:
            result["SENSEX"] = expiries[0]
    except Exception:
        pass

    if own_session:
        session.close()

    return result


def fetch_fyers_expiry_dates(today: Optional[date] = None) -> Dict[str, str]:
    """
    Fetch upcoming expiries from authenticated FYERS option-chain data.
    """
    today = today or _ist_today()

    try:
        from shared_project_engine.auth import get_client
    except Exception:
        return {}

    try:
        client = get_client()
    except Exception:
        return {}

    result: Dict[str, str] = {}
    for name, cfg in INDEX_CONFIG.items():
        symbol = cfg.get("symbol")
        if not symbol:
            continue
        try:
            response = client.option_chain(symbol=symbol, strike_count=2)
            if not response.get("success"):
                continue
            expiries = _extract_fyers_chain_expiry_dates(response.get("data", {}), today)
            if expiries:
                result[name] = expiries[0]
        except Exception:
            continue

    return result


def fetch_live_expiry_dates() -> Dict[str, str]:
    """
    Backwards-compatible alias for exchange-published expiry discovery.
    """
    result = {}
    result.update(fetch_nse_exchange_expiries())
    result.update(fetch_bse_exchange_expiries())
    return result


def _fetch_exchange_expiry_snapshot(today: date) -> Dict[str, Any]:
    data: Dict[str, Dict[str, str]] = {}
    try:
        session = _build_exchange_session()
        if session is not None:
            for index_name, expiry in fetch_nse_exchange_expiries(session=session, today=today).items():
                data[index_name] = {"next_expiry": expiry, "source": "exchange"}
            for index_name, expiry in fetch_bse_exchange_expiries(session=session, today=today).items():
                data[index_name] = {"next_expiry": expiry, "source": "exchange"}
            session.close()
    except Exception:
        data = {}

    missing_indices = [name for name in INDEX_CONFIG if name not in data]
    if missing_indices:
        fyers_data = fetch_fyers_expiry_dates(today=today)
        for index_name in missing_indices:
            expiry = fyers_data.get(index_name)
            if expiry:
                data[index_name] = {"next_expiry": expiry, "source": "fyers"}

    if not data:
        return {"data": {}, "fetched_at": None}

    return {
        "data": data,
        "fetched_at": _save_expiry_cache(data),
    }


def _build_static_expiry_schedule(today: date) -> Dict[str, Any]:
    schedule = {}
    todays_expiry = []

    for name, cfg in INDEX_CONFIG.items():
        expiry_weekday = cfg.get("expiry_weekday")
        if expiry_weekday is None:
            continue
        weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        is_expiry_today_value = today.weekday() == expiry_weekday
        if is_expiry_today_value:
            todays_expiry.append(name)
        schedule[name] = {
            "exchange": cfg.get("exchange"),
            "source": "static",
            "next_expiry": None,
            "weekday": expiry_weekday,
            "weekday_name": weekday_names[expiry_weekday],
            "weekday_short": weekday_names[expiry_weekday][:3],
            "is_expiry_today": is_expiry_today_value,
        }

    return {
        "expirySchedule": schedule,
        "todaysExpiry": todays_expiry,
        "sourceStatus": "static",
        "fetchedAt": None,
    }


def get_expiry_snapshot(use_live: bool = True, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Return the exchange-backed expiry snapshot used by live dashboards.
    """
    today = _ist_today()

    if not use_live:
        return _build_static_expiry_schedule(today)

    snapshot = None if force_refresh else _load_expiry_cache()
    if snapshot is None:
        snapshot = _fetch_exchange_expiry_snapshot(today)

    live_expiries = snapshot.get("data", {}) if isinstance(snapshot, dict) else {}
    fetched_at = snapshot.get("fetched_at") if isinstance(snapshot, dict) else None

    schedule: Dict[str, Dict[str, Any]] = {}
    available_count = 0
    exchange_count = 0
    fyers_count = 0
    todays_expiry: List[str] = []
    today_str = today.isoformat()

    for name, cfg in INDEX_CONFIG.items():
        raw_expiry_entry = live_expiries.get(name)
        if isinstance(raw_expiry_entry, dict):
            next_expiry = raw_expiry_entry.get("next_expiry")
            source = raw_expiry_entry.get("source", "unavailable")
        else:
            next_expiry = raw_expiry_entry
            source = "exchange" if next_expiry else "unavailable"
        parsed_expiry = _parse_exchange_date(next_expiry)
        if parsed_expiry and parsed_expiry >= today:
            available_count += 1
            if source == "exchange":
                exchange_count += 1
            elif source == "fyers":
                fyers_count += 1
            weekday_info = _format_weekday_info(parsed_expiry)
            is_expiry_today_value = parsed_expiry.isoformat() == today_str
            if is_expiry_today_value:
                todays_expiry.append(name)
            schedule[name] = {
                "exchange": cfg.get("exchange"),
                "source": source,
                "next_expiry": parsed_expiry.isoformat(),
                "is_expiry_today": is_expiry_today_value,
                **weekday_info,
            }
            continue

        schedule[name] = {
            "exchange": cfg.get("exchange"),
            "source": "unavailable",
            "next_expiry": None,
            "weekday": None,
            "weekday_name": None,
            "weekday_short": None,
            "is_expiry_today": False,
        }

    source_status = "exchange"
    if available_count == 0:
        source_status = "unavailable"
    elif fyers_count and not exchange_count:
        source_status = "fyers"
    elif fyers_count and exchange_count:
        source_status = "mixed"
    elif available_count < len(INDEX_CONFIG):
        source_status = "partial"

    return {
        "expirySchedule": schedule,
        "todaysExpiry": todays_expiry,
        "sourceStatus": source_status,
        "fetchedAt": fetched_at,
    }


def get_expiry_schedule(use_live: bool = True) -> Dict[str, Dict[str, Any]]:
    """
    Get expiry schedule for all indices.

    Live callers receive exchange-backed contract dates only. Static weekday
    values remain available only for non-live/historical consumers.
    """
    return get_expiry_snapshot(use_live=use_live)["expirySchedule"]


def get_todays_expiring_indices(use_live: bool = True) -> List[str]:
    """Get list of indices expiring today."""
    return list(get_expiry_snapshot(use_live=use_live)["todaysExpiry"])


def export_for_frontend() -> Dict[str, Any]:
    """Export config in a format suitable for frontend JSON."""
    expiry_snapshot = get_expiry_snapshot()
    return {
        "indices": {
            name: {
                "name": cfg["name"],
                "displayName": cfg["display_name"],
                "exchange": cfg["exchange"],
                "lotSize": cfg["lot_size"],
                "strikeGap": cfg["strike_gap"],
                "expiryWeekday": cfg["expiry_weekday"],
                "enabled": cfg.get("enabled", True),
            }
            for name, cfg in INDEX_CONFIG.items()
        },
        "activeIndices": ACTIVE_INDICES,
        "monthlyExpiry": MONTHLY_EXPIRY_DATES,
        "expirySchedule": expiry_snapshot["expirySchedule"],
        "todaysExpiry": expiry_snapshot["todaysExpiry"],
        "sourceStatus": expiry_snapshot["sourceStatus"],
        "fetchedAt": expiry_snapshot["fetchedAt"],
    }

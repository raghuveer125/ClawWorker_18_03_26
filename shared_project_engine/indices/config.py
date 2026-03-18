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

import re
from typing import Dict, List, Optional, Any

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

# Monthly expiry dates (actual dates from exchange calendar)
# Update this dict monthly with actual expiry dates
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


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

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


def fetch_live_expiry_dates() -> Dict[str, str]:
    """
    Fetch actual next expiry dates from FYERS symbol master CSV.

    Returns:
        Dict of index name -> next expiry date (YYYY-MM-DD)
    """
    import csv
    import io
    import requests
    from datetime import datetime, timedelta

    NSE_FO_URL = "https://public.fyers.in/sym_details/NSE_FO.csv"
    BSE_FO_URL = "https://public.fyers.in/sym_details/BSE_FO.csv"

    result = {}
    now = datetime.now()
    # Look for expiries within next 7 days for weekly options
    max_date = now + timedelta(days=7)

    # Index patterns to match in symbol descriptions
    index_patterns = {
        "NIFTY50": lambda desc: "NIFTY" in desc and "BANKNIFTY" not in desc and "FINNIFTY" not in desc and "MIDCPNIFTY" not in desc,
        "BANKNIFTY": lambda desc: "BANKNIFTY" in desc,
        "FINNIFTY": lambda desc: "FINNIFTY" in desc,
        "MIDCPNIFTY": lambda desc: "MIDCPNIFTY" in desc,
    }

    try:
        # Fetch NSE F&O symbols
        resp = requests.get(NSE_FO_URL, timeout=30)
        if resp.status_code == 200:
            reader = csv.reader(io.StringIO(resp.text))
            for row in reader:
                if len(row) < 10:
                    continue
                desc = row[1] if len(row) > 1 else ""
                expiry_ts = row[8] if len(row) > 8 else ""

                # Only process options (CE/PE)
                if "CE" not in desc and "PE" not in desc:
                    continue

                try:
                    exp_ts = int(expiry_ts)
                    exp_date = datetime.fromtimestamp(exp_ts)
                    # Skip past expiries and far future expiries
                    if exp_date <= now:
                        continue

                    # Check which index this belongs to
                    for idx_name, matcher in index_patterns.items():
                        if matcher(desc):
                            exp_str = exp_date.strftime("%Y-%m-%d")
                            # Keep earliest upcoming expiry
                            if idx_name not in result or exp_str < result[idx_name]:
                                result[idx_name] = exp_str
                            break
                except (ValueError, OSError):
                    continue
    except Exception:
        pass

    try:
        # Fetch BSE F&O for SENSEX
        resp = requests.get(BSE_FO_URL, timeout=30)
        if resp.status_code == 200:
            reader = csv.reader(io.StringIO(resp.text))
            for row in reader:
                if len(row) < 10:
                    continue
                desc = row[1] if len(row) > 1 else ""
                expiry_ts = row[8] if len(row) > 8 else ""

                if "SENSEX" not in desc:
                    continue
                if "CE" not in desc and "PE" not in desc:
                    continue

                try:
                    exp_ts = int(expiry_ts)
                    exp_date = datetime.fromtimestamp(exp_ts)
                    if exp_date <= now:
                        continue

                    exp_str = exp_date.strftime("%Y-%m-%d")
                    if "SENSEX" not in result or exp_str < result["SENSEX"]:
                        result["SENSEX"] = exp_str
                except (ValueError, OSError):
                    continue
    except Exception:
        pass

    return result


# Cache for live expiry data (refreshed every hour)
_live_expiry_cache: Dict[str, Any] = {"data": {}, "timestamp": None}


def get_expiry_schedule(use_live: bool = True) -> Dict[str, Dict[str, Any]]:
    """
    Get expiry schedule for all indices.

    Args:
        use_live: If True, fetch actual expiry dates from FYERS (default True)

    Returns:
        Dict with index name -> {weekday, weekday_name, is_expiry_today, next_expiry}
    """
    from datetime import date, datetime, timedelta

    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    today = date.today()
    today_weekday = today.weekday()
    today_str = today.strftime("%Y-%m-%d")

    # Try to get live expiry data (with caching)
    live_expiries = {}
    if use_live:
        global _live_expiry_cache
        cache_age = None
        if _live_expiry_cache["timestamp"]:
            cache_age = datetime.now() - _live_expiry_cache["timestamp"]

        # Refresh cache if older than 1 hour or empty
        if not _live_expiry_cache["data"] or (cache_age and cache_age > timedelta(hours=1)):
            try:
                _live_expiry_cache["data"] = fetch_live_expiry_dates()
                _live_expiry_cache["timestamp"] = datetime.now()
            except Exception:
                pass

        live_expiries = _live_expiry_cache.get("data", {})

    result = {}
    for name, cfg in INDEX_CONFIG.items():
        expiry_weekday = cfg.get("expiry_weekday")
        if expiry_weekday is None:
            continue

        # Check if we have live expiry data
        next_expiry = live_expiries.get(name)
        is_expiry_today = False

        if next_expiry:
            # Use actual expiry date
            is_expiry_today = (next_expiry == today_str)
            try:
                exp_date = datetime.strptime(next_expiry, "%Y-%m-%d").date()
                actual_weekday = exp_date.weekday()
            except ValueError:
                actual_weekday = expiry_weekday
        else:
            # Fallback to weekday pattern
            is_expiry_today = (today_weekday == expiry_weekday)
            actual_weekday = expiry_weekday

        result[name] = {
            "weekday": actual_weekday,
            "weekday_name": weekday_names[actual_weekday],
            "weekday_short": weekday_names[actual_weekday][:3],
            "is_expiry_today": is_expiry_today,
            "next_expiry": next_expiry,
        }

    return result


def get_todays_expiring_indices() -> List[str]:
    """Get list of indices expiring today."""
    schedule = get_expiry_schedule()
    return [name for name, info in schedule.items() if info.get("is_expiry_today")]


def export_for_frontend() -> Dict[str, Any]:
    """Export config in a format suitable for frontend JSON."""
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
        "expirySchedule": get_expiry_schedule(),
        "todaysExpiry": get_todays_expiring_indices(),
    }

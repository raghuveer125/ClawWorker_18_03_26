#!/usr/bin/env python3
import argparse
import calendar
import csv
import datetime as dt
import json
import math
import tempfile
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import requests
import urllib3
from zoneinfo import ZoneInfo

_SHARED_ROOT = Path(__file__).resolve().parents[3]
if str(_SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(_SHARED_ROOT))

from shared_project_engine.indices import canonicalize_index_name, get_market_index_config
from shared_project_engine.market import MarketDataClient

_CSV_HEADER_CACHE: set[str] = set()


def load_simple_env(path: str) -> None:
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            line = ln.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k and k not in os.environ:
                os.environ[k] = v


def configure_requests_tls(ca_bundle: str, insecure: bool) -> None:
    if ca_bundle:
        os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle
        os.environ["SSL_CERT_FILE"] = ca_bundle
    if insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        original = requests.sessions.Session.request

        def patched(self, method, url, **kwargs):
            kwargs.setdefault("verify", False)
            return original(self, method, url, **kwargs)

        requests.sessions.Session.request = patched


def now_ist() -> dt.datetime:
    return dt.datetime.now(ZoneInfo("Asia/Kolkata"))


def is_expiry_day_fallback(now: dt.datetime, expiry_weekday: int = 3) -> bool:
    """Fallback check using typical expiry weekday (used before option chain is fetched).
    expiry_weekday: 0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday
    """
    return now.weekday() == expiry_weekday


def detect_nearest_expiry(
    contracts: List[Dict[str, Any]], now_local: dt.datetime
) -> Optional[dt.datetime]:
    """Parse contract symbols to find the nearest expiry date from market data."""
    expiry_dates: List[dt.datetime] = []
    today_date = now_local.date()

    for c in contracts:
        symbol = str(c.get("symbol", ""))
        if not symbol:
            continue
        exp_dt = parse_expiry_from_symbol(symbol, now_local)
        if exp_dt and exp_dt.date() >= today_date:
            expiry_dates.append(exp_dt)

    if not expiry_dates:
        return None

    # Return the nearest future expiry
    expiry_dates.sort()
    return expiry_dates[0]


def is_expiry_today(
    contracts: List[Dict[str, Any]], now_local: dt.datetime
) -> bool:
    """Check if today is expiry day based on actual contract expiry dates from market."""
    nearest = detect_nearest_expiry(contracts, now_local)
    if nearest is None:
        return False
    return nearest.date() == now_local.date()


def resolve_profile(profile: str, expiry_day: bool) -> str:
    if profile == "auto":
        return "expiry" if expiry_day else "balanced"
    return profile


def profile_config(profile: str) -> Dict[str, float]:
    configs: Dict[str, Dict[str, float]] = {
        "strict": {
            "min_total": 7,
            "min_diff": 2,
            "min_conf": 82,
            "risk_pct": 0.12,
            "t1_mult": 1.16,
            "t2_mult": 1.30,
        },
        "balanced": {
            "min_total": 6,
            "min_diff": 1,
            "min_conf": 74,
            "risk_pct": 0.11,
            "t1_mult": 1.14,
            "t2_mult": 1.26,
        },
        "aggressive": {
            "min_total": 5,
            "min_diff": 1,
            "min_conf": 68,
            "risk_pct": 0.10,
            "t1_mult": 1.12,
            "t2_mult": 1.24,
        },
        "expiry": {
            "min_total": 4,
            "min_diff": 0,
            "min_conf": 64,
            "risk_pct": 0.09,
            "t1_mult": 1.11,
            "t2_mult": 1.22,
        },
    }
    return configs.get(profile, configs["balanced"])


def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for i in range(1, len(values)):
        out.append(values[i] * k + out[-1] * (1 - k))
    return out


def rsi(values: List[float], period: int = 14) -> List[float]:
    if len(values) < period + 1:
        return []
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out = [50.0] * period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100.0 - (100.0 / (1.0 + rs)))
    return out


def atr(candles: List[List[float]], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs: List[float] = []
    for i in range(1, len(candles)):
        _, _, high, low, close, _ = candles[i]
        prev_close = candles[i - 1][4]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    recent = trs[-period:]
    return sum(recent) / len(recent) if recent else 0.0


def normalize_option_type(value: Any) -> Optional[str]:
    if value is None:
        return None
    val = str(value).strip().upper()
    if val in {"CE", "CALL", "C"}:
        return "CE"
    if val in {"PE", "PUT", "P"}:
        return "PE"
    return None


def to_num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_option_candidates(obj: Any, out: List[Dict[str, Any]]) -> None:
    if isinstance(obj, list):
        for item in obj:
            extract_option_candidates(item, out)
        return

    if not isinstance(obj, dict):
        return

    kmap = {str(k).lower(): k for k in obj.keys()}
    symbol = obj.get(kmap.get("symbol")) or obj.get(kmap.get("fy_token_symbol"))
    strike = (
        obj.get(kmap.get("strike_price"))
        or obj.get(kmap.get("strike"))
        or obj.get(kmap.get("strikeprice"))
    )
    option_type = (
        obj.get(kmap.get("option_type"))
        or obj.get(kmap.get("opt_type"))
        or obj.get(kmap.get("type"))
        or obj.get(kmap.get("right"))
    )
    ltp = (
        obj.get(kmap.get("ltp"))
        or obj.get(kmap.get("last_price"))
        or obj.get(kmap.get("lp"))
        or obj.get(kmap.get("close"))
    )
    ltpchp = obj.get(kmap.get("ltpchp")) or obj.get(kmap.get("chp"))

    side = normalize_option_type(option_type)
    strike_num = to_num(strike)
    ltp_num = to_num(ltp)
    ltpchp_num = to_num(ltpchp) or 0.0
    symbol_val = str(symbol).strip() if symbol is not None else ""

    if side and strike_num and ltp_num and ltp_num > 0 and symbol_val:
        bid_num = to_num(obj.get(kmap.get("bid")))
        ask_num = to_num(obj.get(kmap.get("ask")))
        vol_num = to_num(obj.get(kmap.get("volume"))) or 0.0
        oi_num = to_num(obj.get(kmap.get("oi"))) or 0.0
        oich_num = to_num(obj.get(kmap.get("oich"))) or 0.0
        out.append(
            {
                "symbol": symbol_val,
                "side": side,
                "strike": strike_num,
                "ltp": ltp_num,
                "bid": bid_num if bid_num is not None else 0.0,
                "ask": ask_num if ask_num is not None else 0.0,
                "volume": vol_num,
                "oi": oi_num,
                "oich": oich_num,
                "ltpchp": ltpchp_num,
            }
        )

    for v in obj.values():
        extract_option_candidates(v, out)


def get_unique_contracts(optionchain_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    extract_option_candidates(optionchain_data, candidates)
    if not candidates:
        return []

    uniq: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for c in candidates:
        key = (c["symbol"], c["side"], int(round(c["strike"])))
        if key not in uniq or c["ltp"] > uniq[key]["ltp"]:
            uniq[key] = c

    return list(uniq.values())


def select_contract(
    contracts: List[Dict[str, Any]], side: str, spot: float
) -> Optional[Dict[str, Any]]:
    filtered = [c for c in contracts if c["side"] == side]
    if not filtered:
        return None

    filtered.sort(key=lambda c: (abs(c["strike"] - spot), -c["ltp"]))
    return filtered[0]


def select_otm_ladder(
    contracts: List[Dict[str, Any]],
    side: str,
    spot: float,
    count: int,
    otm_start: int = 1,
) -> List[Dict[str, Any]]:
    """Select OTM strikes for display (no premium filtering here)."""
    filtered = [c for c in contracts if c["side"] == side and c["ltp"] > 0]
    if side == "CE":
        pool = sorted([c for c in filtered if c["strike"] > spot], key=lambda x: x["strike"])
    else:
        pool = sorted([c for c in filtered if c["strike"] < spot], key=lambda x: x["strike"], reverse=True)

    if not pool:
        # fallback when no strict OTM data is available
        pool = sorted(filtered, key=lambda c: abs(c["strike"] - spot))

    start = max(0, otm_start - 1)
    selected = pool[start : start + max(1, count)]

    return selected


def check_premium_ok(ltp: float, min_premium: float, max_premium: float) -> bool:
    """Check if premium is within acceptable range."""
    if min_premium > 0 and ltp < min_premium:
        return False
    if max_premium > 0 and ltp > max_premium:
        return False
    return True


def pcr_level_label(pcr: float) -> str:
    """Convert PCR value to support/resistance label."""
    if pcr >= 5.0:
        return "SUP++"
    elif pcr >= 2.0:
        return "SUP+"
    elif pcr >= 1.2:
        return "SUP"
    elif pcr >= 0.8:
        return "NEUT"
    elif pcr >= 0.5:
        return "RES"
    elif pcr >= 0.2:
        return "RES+"
    else:
        return "RES++"


def calc_preliminary_score(
    entry: float, sl: float, t1: float, t2: float, confidence: int
) -> int:
    """Calculate preliminary quality score (same logic as add_fia_signal.py)."""
    if entry <= 0 or sl <= 0 or t1 <= 0 or t2 <= 0:
        return 0
    if sl >= entry or t1 <= entry:
        return 0

    risk = entry - sl
    reward1 = t1 - entry
    reward2 = t2 - entry
    rr1 = reward1 / risk if risk > 0 else 0.0
    rr2 = reward2 / risk if risk > 0 else 0.0
    risk_pct = (risk / entry) * 100 if entry > 0 else 0.0

    score = confidence
    if confidence < 80:
        score -= 12
    if rr1 < 1.0:
        score -= 8
    else:
        score += 4
    if rr2 < 1.5:
        score -= 10
    else:
        score += 6
    if risk_pct < 3.0:
        score -= 6
    elif risk_pct > 15.0:
        score -= 8
    else:
        score += 4

    return max(0, min(100, score))


def compute_flow_metrics(contracts: List[Dict[str, Any]]) -> Dict[str, Any]:
    ce_vol = sum(float(c.get("volume", 0) or 0) for c in contracts if c.get("side") == "CE")
    pe_vol = sum(float(c.get("volume", 0) or 0) for c in contracts if c.get("side") == "PE")
    ce_oich = sum(float(c.get("oich", 0) or 0) for c in contracts if c.get("side") == "CE")
    pe_oich = sum(float(c.get("oich", 0) or 0) for c in contracts if c.get("side") == "PE")

    vol_dom = "NEUTRAL"
    if ce_vol > pe_vol * 1.08:
        vol_dom = "CE"
    elif pe_vol > ce_vol * 1.08:
        vol_dom = "PE"

    return {
        "ce_vol": ce_vol,
        "pe_vol": pe_vol,
        "ce_oich": ce_oich,
        "pe_oich": pe_oich,
        "vol_dom": vol_dom,
    }


def compute_option_context(contracts: List[Dict[str, Any]], spot: float) -> Dict[str, Any]:
    by_strike: Dict[int, Dict[str, float]] = {}
    for c in contracts:
        strike = int(round(float(c.get("strike", 0) or 0)))
        side = str(c.get("side", "")).upper()
        if strike <= 0 or side not in {"CE", "PE"}:
            continue
        row = by_strike.setdefault(
            strike,
            {"ce_oi": 0.0, "pe_oi": 0.0, "ce_oich": 0.0, "pe_oich": 0.0},
        )
        oi = float(c.get("oi", 0) or 0)
        oich = float(c.get("oich", 0) or 0)
        if side == "CE":
            row["ce_oi"] += oi
            row["ce_oich"] += oich
        else:
            row["pe_oi"] += oi
            row["pe_oich"] += oich

    strikes = sorted(by_strike.keys())
    total_ce_oi = sum(v["ce_oi"] for v in by_strike.values())
    total_pe_oi = sum(v["pe_oi"] for v in by_strike.values())
    net_pcr = (total_pe_oi / total_ce_oi) if total_ce_oi > 0 else 0.0

    max_pain = 0
    if strikes:
        best = None
        for s in strikes:
            pain = 0.0
            for k in strikes:
                d = by_strike[k]
                pain += max(0.0, s - k) * d["ce_oi"]
                pain += max(0.0, k - s) * d["pe_oi"]
            if best is None or pain < best[1]:
                best = (s, pain)
        if best is not None:
            max_pain = int(best[0])
    max_pain_dist = float(spot) - float(max_pain) if max_pain > 0 else 0.0

    strike_pcr_map: Dict[int, float] = {}
    for k, d in by_strike.items():
        strike_pcr_map[k] = (d["pe_oi"] / d["ce_oi"]) if d["ce_oi"] > 0 else 0.0

    return {
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi,
        "net_pcr": net_pcr,
        "max_pain": max_pain,
        "max_pain_dist": max_pain_dist,
        "strike_pcr_map": strike_pcr_map,
    }


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def parse_expiry_from_symbol(symbol: str, now_local: dt.datetime) -> Optional[dt.datetime]:
    s = symbol.upper().split(":")[-1]
    month_text_map = {
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

    # Weekly style examples:
    # - SENSEX2630580300PE (YY M DD)
    # - NIFTY2530624700CE  (YY M DD)
    weekly_y_m_dd = re.search(
        r"^[A-Z]+(?P<yy>\d{2})(?P<m>[1-9OND])(?P<dd>\d{2})\d+(CE|PE)$",
        s,
    )
    if weekly_y_m_dd:
        yy = int(weekly_y_m_dd.group("yy"))
        mm_raw = weekly_y_m_dd.group("m")
        dd = int(weekly_y_m_dd.group("dd"))
        mm = {"O": 10, "N": 11, "D": 12}.get(mm_raw, int(mm_raw))
        year = 2000 + yy
        try:
            return dt.datetime(year, mm, dd, 15, 30, tzinfo=now_local.tzinfo)
        except ValueError:
            return None

    # Alternate weekly style: YY MM DD
    weekly_y_mm_dd = re.search(
        r"^[A-Z]+(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})\d+(CE|PE)$",
        s,
    )
    if weekly_y_mm_dd:
        yy = int(weekly_y_mm_dd.group("yy"))
        mm = int(weekly_y_mm_dd.group("mm"))
        dd = int(weekly_y_mm_dd.group("dd"))
        year = 2000 + yy
        try:
            return dt.datetime(year, mm, dd, 15, 30, tzinfo=now_local.tzinfo)
        except ValueError:
            return None

    # Alternate weekly style: DD MON YY
    weekly_dd_mon_yy = re.search(
        r"^[A-Z]+(?P<dd>\d{2})(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?P<yy>\d{2})\d+(CE|PE)$",
        s,
    )
    if weekly_dd_mon_yy:
        yy = int(weekly_dd_mon_yy.group("yy"))
        mm = month_text_map[weekly_dd_mon_yy.group("mon")]
        dd = int(weekly_dd_mon_yy.group("dd"))
        year = 2000 + yy
        try:
            parsed_dt = dt.datetime(year, mm, dd, 15, 30, tzinfo=now_local.tzinfo)
            # Avoid misclassifying monthly symbols like BANKNIFTY26MAR50000CE as year 2050.
            if (now_local.year - 1) <= parsed_dt.year <= (now_local.year + 2):
                return parsed_dt
        except ValueError:
            pass

    # Monthly style fallback: SENSEX26MAR80300CE / NIFTY26MAR22500CE
    monthly_y_mon = re.search(
        r"^[A-Z]+(?P<yy>\d{2})(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d+(CE|PE)$",
        s,
    )
    if monthly_y_mon:
        yy = int(monthly_y_mon.group("yy"))
        mm = month_text_map[monthly_y_mon.group("mon")]
        year = 2000 + yy
        month_end = calendar.monthrange(year, mm)[1]
        return dt.datetime(year, mm, month_end, 15, 30, tzinfo=now_local.tzinfo)

    return None


def format_expiry_date(expiry_dt: Optional[dt.datetime]) -> str:
    if expiry_dt is None:
        return ""
    return expiry_dt.strftime("%Y-%m-%d")


def format_expiry_code(expiry_dt: Optional[dt.datetime]) -> str:
    if expiry_dt is None:
        return ""
    return expiry_dt.strftime("%d%b%y").upper()


def bs_price(spot: float, strike: float, t_years: float, rate: float, sigma: float, side: str) -> float:
    if spot <= 0 or strike <= 0 or t_years <= 0 or sigma <= 0:
        intrinsic = max(0.0, spot - strike) if side == "CE" else max(0.0, strike - spot)
        return intrinsic
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if side == "CE":
        return spot * normal_cdf(d1) - strike * math.exp(-rate * t_years) * normal_cdf(d2)
    return strike * math.exp(-rate * t_years) * normal_cdf(-d2) - spot * normal_cdf(-d1)


def implied_volatility(
    market_price: float, spot: float, strike: float, t_years: float, rate: float, side: str
) -> Optional[float]:
    if market_price <= 0 or spot <= 0 or strike <= 0 or t_years <= 0:
        return None
    intrinsic = max(0.0, spot - strike) if side == "CE" else max(0.0, strike - spot)
    target = max(market_price, intrinsic + 1e-6)
    lo, hi = 0.01, 5.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        price = bs_price(spot, strike, t_years, rate, mid, side)
        if abs(price - target) < 1e-5:
            return mid
        if price > target:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def option_greeks(
    spot: float, strike: float, t_years: float, rate: float, sigma: float, side: str
) -> Dict[str, float]:
    if spot <= 0 or strike <= 0 or t_years <= 0 or sigma <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta_day": 0.0}
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf = normal_pdf(d1)
    gamma = pdf / (spot * sigma * sqrt_t)
    if side == "CE":
        delta = normal_cdf(d1)
        theta = -(spot * pdf * sigma) / (2.0 * sqrt_t) - rate * strike * math.exp(-rate * t_years) * normal_cdf(d2)
    else:
        delta = normal_cdf(d1) - 1.0
        theta = -(spot * pdf * sigma) / (2.0 * sqrt_t) + rate * strike * math.exp(-rate * t_years) * normal_cdf(-d2)
    return {"delta": delta, "gamma": gamma, "theta_day": theta / 365.0}


def compute_contract_greeks(
    contract: Dict[str, Any], spot: float, now_local: dt.datetime, rate: float = 0.06
) -> Dict[str, float]:
    strike = float(contract.get("strike", 0) or 0)
    ltp = float(contract.get("ltp", 0) or 0)
    side = str(contract.get("side", ""))
    symbol = str(contract.get("symbol", ""))
    expiry_dt = parse_expiry_from_symbol(symbol, now_local)
    if not expiry_dt:
        return {"iv": 0.0, "delta": 0.0, "gamma": 0.0, "theta_day": 0.0, "decay_pct_day": 0.0}
    secs = max(300.0, (expiry_dt - now_local).total_seconds())
    t_years = secs / (365.0 * 24.0 * 3600.0)
    iv = implied_volatility(ltp, spot, strike, t_years, rate, side)
    if iv is None:
        return {"iv": 0.0, "delta": 0.0, "gamma": 0.0, "theta_day": 0.0, "decay_pct_day": 0.0}
    g = option_greeks(spot, strike, t_years, rate, iv, side)
    decay_pct = abs(g["theta_day"]) / max(ltp, 1e-9) * 100.0
    return {
        "iv": iv * 100.0,
        "delta": g["delta"],
        "gamma": g["gamma"],
        "theta_day": g["theta_day"],
        "decay_pct_day": decay_pct,
    }


def compute_side_vote(
    contracts: List[Dict[str, Any]],
    spot: float,
    now_local: dt.datetime,
    trend_side: str,
    vol_dom: str,
    min_vote_diff: int,
) -> Dict[str, Any]:
    ce = [c for c in contracts if c.get("side") == "CE"]
    pe = [c for c in contracts if c.get("side") == "PE"]
    ce.sort(key=lambda c: abs(float(c.get("strike", 0)) - spot))
    pe.sort(key=lambda c: abs(float(c.get("strike", 0)) - spot))
    ce_ref = ce[:3]
    pe_ref = pe[:3]

    def avg(items: List[Dict[str, Any]], key: str) -> float:
        if not items:
            return 0.0
        return sum(float(x.get(key, 0) or 0) for x in items) / len(items)

    ce_chg = avg(ce_ref, "ltpchp")
    pe_chg = avg(pe_ref, "ltpchp")
    ce_g = [compute_contract_greeks(c, spot, now_local) for c in ce_ref]
    pe_g = [compute_contract_greeks(c, spot, now_local) for c in pe_ref]

    def avg_g(gs: List[Dict[str, float]], key: str) -> float:
        if not gs:
            return 0.0
        return sum(float(g.get(key, 0.0)) for g in gs) / len(gs)

    ce_gamma = avg_g(ce_g, "gamma")
    pe_gamma = avg_g(pe_g, "gamma")
    ce_decay = avg_g(ce_g, "decay_pct_day")
    pe_decay = avg_g(pe_g, "decay_pct_day")

    ce_vote = 0
    pe_vote = 0
    if trend_side == "CE":
        ce_vote += 3
    elif trend_side == "PE":
        pe_vote += 3
    if vol_dom == "CE":
        ce_vote += 2
    elif vol_dom == "PE":
        pe_vote += 2
    if ce_chg > pe_chg + 0.2:
        ce_vote += 2
    elif pe_chg > ce_chg + 0.2:
        pe_vote += 2
    if ce_gamma > pe_gamma * 1.05:
        ce_vote += 1
    elif pe_gamma > ce_gamma * 1.05:
        pe_vote += 1
    if ce_decay < pe_decay:
        ce_vote += 1
    elif pe_decay < ce_decay:
        pe_vote += 1

    vote_side = "CE" if ce_vote > pe_vote else "PE" if pe_vote > ce_vote else ""
    vote_diff = abs(ce_vote - pe_vote)
    if vote_diff < min_vote_diff:
        vote_side = ""

    return {
        "ce_vote": ce_vote,
        "pe_vote": pe_vote,
        "vote_side": vote_side,
        "vote_diff": vote_diff,
        "ce_chg": ce_chg,
        "pe_chg": pe_chg,
        "ce_gamma": ce_gamma,
        "pe_gamma": pe_gamma,
        "ce_decay": ce_decay,
        "pe_decay": pe_decay,
    }


def compute_signal_inputs(candles: List[List[float]], cfg: Dict[str, float]) -> Dict[str, Any]:
    closes = [float(c[4]) for c in candles]
    vols = [float(c[5]) for c in candles]
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    r = rsi(closes, 14)
    a = atr(candles, 14)

    close = closes[-1]
    prev_close = closes[-2] if len(closes) > 1 else close
    ema9 = e9[-1] if e9 else close
    ema21 = e21[-1] if e21 else close
    rsi14 = r[-1] if r else 50.0
    vol_avg20 = sum(vols[-20:]) / min(20, len(vols))
    vol_now = vols[-1]

    hh20 = max(closes[-20:]) if len(closes) >= 20 else max(closes)
    ll20 = min(closes[-20:]) if len(closes) >= 20 else min(closes)

    breakout_up = close >= hh20 * 0.999
    breakdown = close <= ll20 * 1.001
    vol_boost = vol_now > vol_avg20

    bull = 0
    bear = 0
    tags: List[str] = []

    if close > ema21:
        bull += 2
    else:
        bear += 2
    if ema9 > ema21:
        bull += 2
    else:
        bear += 2
    if close > ema9:
        bull += 1
    else:
        bear += 1
    if close >= prev_close:
        bull += 1
    else:
        bear += 1

    if rsi14 >= 58:
        bull += 2
        tags.append("rsi_bull")
    elif rsi14 <= 42:
        bear += 2
        tags.append("rsi_bear")
    elif rsi14 > 50:
        bull += 1
    elif rsi14 < 50:
        bear += 1

    if breakout_up:
        bull += 1
        tags.append("breakout")
    if breakdown:
        bear += 1
        tags.append("breakdown")
    if vol_boost:
        if close >= prev_close:
            bull += 1
        else:
            bear += 1
        tags.append("vol_boost")

    side = "CE" if bull >= bear else "PE"
    total = max(bull, bear)
    diff = abs(bull - bear)

    confidence = 52 + (total * 5) + (diff * 3)
    if vol_boost:
        confidence += 3
    if (side == "CE" and breakout_up) or (side == "PE" and breakdown):
        confidence += 4
    confidence = max(0, min(95, int(confidence)))

    if total < int(cfg["min_total"]) or diff < int(cfg["min_diff"]) or confidence < int(cfg["min_conf"]):
        side = ""

    def compute_fvg_context() -> Dict[str, Any]:
        if len(candles) < 3:
            return {
                "fvg_side": "",
                "fvg_active": "N",
                "fvg_gap": 0.0,
                "fvg_distance": 0.0,
                "fvg_distance_atr": 0.0,
                "fvg_plus": "N",
            }

        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        close_now = float(candles[-1][4])
        atr_base = max(float(a or 0.0), 1e-9)
        min_gap = max(close_now * 0.0001, atr_base * 0.08)

        zones: List[Dict[str, Any]] = []
        start = max(2, len(candles) - 40)
        for i in range(start, len(candles)):
            bull_gap = lows[i] - highs[i - 2]
            if bull_gap > min_gap:
                zones.append(
                    {
                        "idx": i,
                        "side": "BULL",
                        "top": lows[i],
                        "bottom": highs[i - 2],
                        "gap": bull_gap,
                    }
                )

            bear_gap = lows[i - 2] - highs[i]
            if bear_gap > min_gap:
                zones.append(
                    {
                        "idx": i,
                        "side": "BEAR",
                        "top": lows[i - 2],
                        "bottom": highs[i],
                        "gap": bear_gap,
                    }
                )

        if not zones:
            return {
                "fvg_side": "",
                "fvg_active": "N",
                "fvg_gap": 0.0,
                "fvg_distance": 0.0,
                "fvg_distance_atr": 0.0,
                "fvg_plus": "N",
            }

        for zone in zones:
            zone["active"] = True
            for j in range(int(zone["idx"]) + 1, len(candles)):
                if zone["side"] == "BULL" and lows[j] <= float(zone["bottom"]):
                    zone["active"] = False
                    break
                if zone["side"] == "BEAR" and highs[j] >= float(zone["top"]):
                    zone["active"] = False
                    break

        active_zones = [z for z in zones if z.get("active")]
        aligned_side = "BULL" if side == "CE" else "BEAR" if side == "PE" else ""
        aligned_active = [z for z in active_zones if z.get("side") == aligned_side] if aligned_side else []

        if aligned_active:
            chosen = aligned_active[-1]
        elif active_zones:
            chosen = active_zones[-1]
        else:
            chosen = zones[-1]

        top = float(chosen["top"])
        bottom = float(chosen["bottom"])
        if close_now < bottom:
            distance = bottom - close_now
        elif close_now > top:
            distance = close_now - top
        else:
            distance = 0.0

        distance_atr = distance / atr_base
        age = (len(candles) - 1) - int(chosen["idx"])
        is_aligned = bool(aligned_side and chosen.get("side") == aligned_side)
        fvg_plus = (
            bool(chosen.get("active"))
            and is_aligned
            and float(chosen.get("gap", 0.0)) >= atr_base * 0.15
            and distance_atr <= 0.35
            and age <= 20
        )

        return {
            "fvg_side": str(chosen.get("side", "")),
            "fvg_active": "Y" if chosen.get("active") else "N",
            "fvg_gap": float(chosen.get("gap", 0.0)),
            "fvg_distance": float(distance),
            "fvg_distance_atr": float(distance_atr),
            "fvg_plus": "Y" if fvg_plus else "N",
        }

    fvg_ctx = compute_fvg_context()

    if fvg_ctx.get("fvg_active") == "Y":
        if (side == "CE" and fvg_ctx.get("fvg_side") == "BULL") or (side == "PE" and fvg_ctx.get("fvg_side") == "BEAR"):
            tags.append("fvg")
        if fvg_ctx.get("fvg_plus") == "Y":
            tags.append("fvg_plus")

    tags.append(f"score_{bull}_{bear}")

    return {
        "close": close,
        "ema9": ema9,
        "ema21": ema21,
        "rsi14": rsi14,
        "atr14": a,
        "volume_boost": vol_boost,
        "breakout_up": breakout_up,
        "breakdown": breakdown,
        "confidence": confidence,
        "side": side,
        "bull_score": bull,
        "bear_score": bear,
        "fvg_side": fvg_ctx.get("fvg_side", ""),
        "fvg_active": fvg_ctx.get("fvg_active", "N"),
        "fvg_gap": fvg_ctx.get("fvg_gap", 0.0),
        "fvg_distance": fvg_ctx.get("fvg_distance", 0.0),
        "fvg_distance_atr": fvg_ctx.get("fvg_distance_atr", 0.0),
        "fvg_plus": fvg_ctx.get("fvg_plus", "N"),
        "tags": tags,
    }


def now_parts() -> Tuple[str, str]:
    now = dt.datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")


def build_no_trade(reason: str) -> str:
    return f"NO TRADE | {reason}"


def build_signal_line(
    symbol: str,
    side: str,
    strike: int,
    entry: float,
    sl: float,
    t1: float,
    t2: float,
    invalidation: str,
    confidence: int,
    reason: str,
) -> str:
    d, t = now_parts()
    return (
        f"{d} | {t} | {symbol} | {side} | {strike} | {entry:.2f} | {sl:.2f} | "
        f"{t1:.2f} | {t2:.2f} | {invalidation} | {confidence} | {reason}"
    )


def parse_signal_line_fields(line: str) -> Dict[str, str]:
    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 12:
        return {}
    return {
        "date": parts[0],
        "time": parts[1],
        "symbol": parts[2],
        "side": parts[3],
        "strike": parts[4],
        "entry": parts[5],
        "sl": parts[6],
        "t1": parts[7],
        "t2": parts[8],
        "invalidation": parts[9],
        "confidence": parts[10],
        "reason": parts[11],
    }


def extract_field(text: str, pattern: str, default: str = "") -> str:
    m = re.search(pattern, text, flags=re.MULTILINE)
    return m.group(1).strip() if m else default


def print_text_table(headers: List[str], rows: List[List[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def fmt(row: List[str]) -> str:
        return " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))

    sep = "-+-".join("-" * w for w in widths)
    print(fmt(headers))
    print(sep)
    for row in rows:
        print(fmt(row))


def table_width(headers: List[str], rows: List[List[str]]) -> int:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    return sum(widths) + (3 * max(0, len(headers) - 1))


def print_adaptive_table(headers: List[str], rows: List[List[str]]) -> None:
    if not headers:
        return
    term_width = shutil.get_terminal_size((160, 24)).columns
    if table_width(headers, rows) <= term_width:
        print_text_table(headers, rows)
        return

    # Keep identity columns in each chunk for readability.
    key_cols = [h for h in ["Time", "Side", "Strike"] if h in headers]
    idx = {h: i for i, h in enumerate(headers)}
    key_set = set(key_cols)
    rest_cols = [h for h in headers if h not in key_set]

    chunks: List[List[str]] = []
    while rest_cols:
        chunk = list(key_cols)
        remaining = list(rest_cols)
        for col in remaining:
            trial = chunk + [col]
            trial_rows = [[str(r[idx[h]]) for h in trial] for r in rows]
            if table_width(trial, trial_rows) <= term_width or len(chunk) == len(key_cols):
                chunk.append(col)
                rest_cols.remove(col)
            else:
                break
        chunks.append(chunk)

    for i, ch in enumerate(chunks, start=1):
        if len(chunks) > 1:
            print(f"Table {i}/{len(chunks)}")
        ch_rows = [[str(r[idx[h]]) for h in ch] for r in rows]
        print_text_table(ch, ch_rows)
        if i < len(chunks):
            print()


def print_refresh_banner(now_local: dt.datetime) -> None:
    print(f"RefreshAt: {now_local.strftime('%Y-%m-%d %H:%M:%S')}")


def load_state(path: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {
            "last_side": "",
            "same_side_count": 0,
            "last_flip_ts": 0,
            "last_vol_dom": "NEUTRAL",
            "learn_gate_fail_pull_streak": 0,
            "learn_gate_relax_until_ts": 0,
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {
                "last_side": "",
                "same_side_count": 0,
                "last_flip_ts": 0,
                "last_vol_dom": "NEUTRAL",
                "learn_gate_fail_pull_streak": 0,
                "learn_gate_relax_until_ts": 0,
            }
        return {
            "last_side": str(data.get("last_side", "")),
            "same_side_count": int(data.get("same_side_count", 0)),
            "last_flip_ts": int(data.get("last_flip_ts", 0)),
            "last_vol_dom": str(data.get("last_vol_dom", "NEUTRAL")),
            "learn_gate_fail_pull_streak": int(data.get("learn_gate_fail_pull_streak", 0)),
            "learn_gate_relax_until_ts": int(data.get("learn_gate_relax_until_ts", 0)),
        }
    except Exception:
        return {
            "last_side": "",
            "same_side_count": 0,
            "last_flip_ts": 0,
            "last_vol_dom": "NEUTRAL",
            "learn_gate_fail_pull_streak": 0,
            "learn_gate_relax_until_ts": 0,
        }


def save_state(path: str, state: Dict[str, Any]) -> None:
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)


def ensure_csv_headers(path: str, headers: List[str]) -> None:
    if path in _CSV_HEADER_CACHE:
        return
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
        _CSV_HEADER_CACHE.add(path)
        return

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        current_headers = next(reader, [])
    if current_headers == headers:
        _CSV_HEADER_CACHE.add(path)
        return

    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        dr = csv.DictReader(f)
        for r in dr:
            rows.append(r)

    tmp_fd, tmp_path = tempfile.mkstemp(prefix="csv_migrate_", suffix=".csv", dir=os.path.dirname(path) or ".")
    os.close(tmp_fd)
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="") as f:
            dw = csv.DictWriter(f, fieldnames=headers)
            dw.writeheader()
            for r in rows:
                dw.writerow({h: r.get(h, "") for h in headers})
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    _CSV_HEADER_CACHE.add(path)


def append_csv_row(path: str, headers: List[str], row: Dict[str, Any]) -> None:
    ensure_csv_headers(path, headers)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writerow({h: row.get(h, "") for h in headers})


def load_adaptive_model(path: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        if "weights" not in data or "means" not in data or "stds" not in data:
            return {}
        return data
    except Exception:
        return {}


def sigmoid(x: float) -> float:
    """Sigmoid activation function."""
    import math
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    else:
        # For numerical stability with large negative values
        exp_x = math.exp(x)
        return exp_x / (1.0 + exp_x)


def adaptive_probability(
    model: Dict[str, Any], feature_raw: Dict[str, float]
) -> Optional[float]:
    if not model:
        return None
    weights = model.get("weights", {})
    means = model.get("means", {})
    stds = model.get("stds", {})
    bias = float(model.get("bias", 0.0))
    if not isinstance(weights, dict) or not isinstance(means, dict) or not isinstance(stds, dict):
        return None

    z = bias
    for k, x in feature_raw.items():
        if k not in weights:
            continue
        m = float(means.get(k, 0.0))
        s = float(stds.get(k, 1.0))
        if abs(s) < 1e-9:
            s = 1.0
        x_std = (float(x) - m) / s
        z += float(weights.get(k, 0.0)) * x_std
    return sigmoid(z)


def update_side_state(
    state: Dict[str, Any], side: str, now_ts: int
) -> Tuple[Dict[str, Any], bool, int, int]:
    last_side = str(state.get("last_side", ""))
    same_count = int(state.get("same_side_count", 0))
    last_flip = int(state.get("last_flip_ts", 0))

    if not side:
        state["same_side_count"] = 0
        return state, False, 0, last_flip

    if side == last_side:
        same_count += 1
    else:
        same_count = 1
        last_flip = now_ts
        last_side = side

    state["last_side"] = last_side
    state["same_side_count"] = same_count
    state["last_flip_ts"] = last_flip
    return state, True, same_count, last_flip


def run_validator(
    line: str,
    csv_path: str,
    only_approved: bool,
    min_score: int,
    silent: bool = False,
) -> Tuple[int, str]:
    here = os.path.dirname(os.path.abspath(__file__))
    validator = os.path.join(here, "add_fia_signal.py")
    cmd = [sys.executable, validator, "--csv", csv_path]
    if only_approved:
        cmd.append("--only-approved")
    cmd.extend(["--min-score", str(min_score)])
    cmd.append(line)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    if not silent and output:
        print(output.rstrip("\n"))
    return proc.returncode, output


def main() -> int:
    # Get index from environment (set by shell scripts)
    requested_index = os.getenv("INDEX", "SENSEX")
    index_key = canonicalize_index_name(requested_index)
    idx_cfg = get_market_index_config(index_key)
    index_display = str(idx_cfg.get("display_name", index_key))
    index_tag = re.sub(r"[^a-z0-9]", "", index_display.lower()) or "sensex"

    parser = argparse.ArgumentParser(
        description=f"Auto-pull {index_display} signal from FYERS data and format like FIA line."
    )
    parser.add_argument("--index", default=index_display, help="Index name (SENSEX, BANKNIFTY, NIFTY/NIFTY50, etc.)")
    parser.add_argument("--env-file", default=".fyers.env")
    parser.add_argument("--client-id", default=os.getenv("FYERS_CLIENT_ID", ""))
    parser.add_argument("--access-token", default=os.getenv("FYERS_ACCESS_TOKEN", ""))
    parser.add_argument("--ca-bundle", default=os.getenv("REQUESTS_CA_BUNDLE", ""))
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--symbol", default=idx_cfg["spot_symbol"])
    parser.add_argument("--vix-symbol", default=idx_cfg["vix_symbol"])
    parser.add_argument(
        "--fut-symbol",
        default=os.getenv(f"{idx_cfg.get('fut_env_prefix', index_key)}_FUT_SYMBOL", ""),
    )
    parser.add_argument(
        "--profile",
        default="auto",
        choices=["auto", "strict", "balanced", "aggressive", "expiry"],
    )
    parser.add_argument("--force-expiry-day", action="store_true")
    parser.add_argument("--resolution", default="5")
    parser.add_argument("--lookback-days", type=int, default=5)
    parser.add_argument("--strikecount", type=int, default=8)
    parser.add_argument("--ladder-count", type=int, default=5)
    parser.add_argument("--otm-start", type=int, default=1)
    parser.add_argument("--max-premium", type=float, default=0.0)
    parser.add_argument("--min-premium", type=float, default=0.0)
    parser.add_argument("--max-select-strikes", type=int, default=3)
    parser.add_argument("--min-confidence", type=int, default=88)
    parser.add_argument("--min-score", type=int, default=95)
    parser.add_argument("--max-spread-pct", type=float, default=2.5)
    parser.add_argument("--min-abs-delta", type=float, default=0.10)
    parser.add_argument("--min-vote-diff", type=int, default=2)
    parser.add_argument("--adaptive-model-file", default=".adaptive_model.json")
    parser.add_argument("--enable-adaptive", action="store_true")
    parser.add_argument("--min-learn-prob", type=float, default=0.55)
    parser.add_argument("--min-model-samples", type=int, default=20)
    parser.add_argument(
        "--hard-gate-min-model-samples",
        type=int,
        default=100,
        help="Minimum adaptive samples required before enforcing learn gate.",
    )
    parser.add_argument(
        "--learn-gate-lock-streak",
        type=int,
        default=8,
        help="Consecutive pull-level learn-gate lockouts before temporary relax.",
    )
    parser.add_argument(
        "--learn-gate-relax-sec",
        type=int,
        default=300,
        help="Temporary learn-gate relax duration after lockout detection.",
    )
    parser.add_argument(
        "--journal-csv",
        default=os.getenv("JOURNAL_CSV", f"decision_journal_{index_tag}.csv"),
    )
    parser.add_argument("--confirm-pulls", type=int, default=2)
    parser.add_argument("--flip-cooldown-sec", type=int, default=45)
    parser.add_argument(
        "--state-file",
        default=os.getenv("SIGNAL_STATE_FILE", f".signal_state_{index_tag}.json"),
    )
    parser.add_argument("--csv", default=os.getenv("SIGNALS_CSV", f"signals_{index_tag}.csv"))
    parser.add_argument("--only-approved", action="store_true")
    parser.add_argument("--print-line-only", action="store_true")
    parser.add_argument("--table", action="store_true")
    args = parser.parse_args()

    # Re-resolve index config from final --index value.
    index_key = canonicalize_index_name(args.index)
    idx_cfg = get_market_index_config(index_key)
    index_display = str(idx_cfg.get("display_name", index_key))
    os.environ["INDEX"] = index_display

    # Honor explicit symbol overrides while keeping sensible per-index defaults.
    if not any(a == "--symbol" or a.startswith("--symbol=") for a in sys.argv[1:]):
        args.symbol = idx_cfg["spot_symbol"]
    if not any(a == "--vix-symbol" or a.startswith("--vix-symbol=") for a in sys.argv[1:]):
        args.vix_symbol = idx_cfg["vix_symbol"]
    if not any(a == "--fut-symbol" or a.startswith("--fut-symbol=") for a in sys.argv[1:]):
        args.fut_symbol = os.getenv(f"{idx_cfg.get('fut_env_prefix', index_key)}_FUT_SYMBOL", "")

    load_simple_env(args.env_file)
    if not args.client_id:
        args.client_id = os.getenv("FYERS_CLIENT_ID", "")
    if not args.access_token:
        args.access_token = os.getenv("FYERS_ACCESS_TOKEN", "")
    if not args.ca_bundle:
        args.ca_bundle = os.getenv("REQUESTS_CA_BUNDLE", "")

    configure_requests_tls(args.ca_bundle, args.insecure)
    model = load_adaptive_model(args.adaptive_model_file)
    model_samples = int(model.get("sample_count", 0)) if model else 0
    adaptive_enabled = bool(args.enable_adaptive and model and model_samples >= int(args.min_model_samples))
    hard_gate_samples = max(int(args.min_model_samples), int(args.hard_gate_min_model_samples))
    hard_gate_ready = bool(adaptive_enabled and model_samples >= hard_gate_samples)

    if not args.client_id or not args.access_token:
        print(
            "Error: set FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN "
            f"(or pass flags / use {args.env_file}).",
            file=sys.stderr,
        )
        return 1

    adapter = MarketDataClient(
        access_token=args.access_token,
        client_id=args.client_id,
        env_file=args.env_file,
    )

    now_local = now_ist()
    # Initial fallback using typical weekday - will be refined after option chain fetch
    expiry_day_fallback = is_expiry_day_fallback(now_local, int(idx_cfg.get("expiry_day", 3))) or args.force_expiry_day
    active_profile = resolve_profile(args.profile, expiry_day_fallback)
    cfg = profile_config(active_profile)

    # Check trading window - continue collecting data but block entries
    outside_window = False
    window_reason = ""
    if now_local.hour < 9 or (now_local.hour == 9 and now_local.minute < 20):
        outside_window = True
        window_reason = "pre_market"
    elif now_local.hour > 15 or (now_local.hour == 15 and now_local.minute > 20):
        outside_window = True
        window_reason = "late_session"

    hresp = adapter.get_history_snapshot(
        symbol=args.symbol,
        resolution=args.resolution,
        lookback_days=args.lookback_days,
    )
    candles = hresp.get("candles", []) if isinstance(hresp, dict) else []
    if not candles or len(candles) < 40:
        line = build_no_trade("Not enough market data")
        if args.print_line_only:
            print(line)
            return 0
        rc, out = run_validator(
            line, args.csv, args.only_approved, args.min_score, silent=args.table
        )
        if args.table:
            print_refresh_banner(now_local)
            print_adaptive_table(
                ["Date", "Time", "Status", "Reason", "Action"],
                [[now_local.strftime("%Y-%m-%d"), now_local.strftime("%H:%M:%S"), "NO TRADE", "Not enough market data", "Skip"]],
            )
        return rc

    inputs = compute_signal_inputs(candles, cfg)
    now_ts = int(now_local.timestamp())
    state = load_state(args.state_file)
    if "learn_gate_fail_pull_streak" not in state:
        state["learn_gate_fail_pull_streak"] = 0
    if "learn_gate_relax_until_ts" not in state:
        state["learn_gate_relax_until_ts"] = 0
    learn_gate_relax_active = bool(
        adaptive_enabled and now_ts < int(state.get("learn_gate_relax_until_ts", 0))
    )
    learn_gate_enforced = bool(adaptive_enabled and hard_gate_ready and not learn_gate_relax_active)

    if not inputs["side"]:
        reason_text = (
            f"Low edge in {active_profile} profile "
            f"(bull={inputs['bull_score']},bear={inputs['bear_score']},conf={inputs['confidence']})"
        )
        line = build_no_trade(
            reason_text
        )
        if args.print_line_only:
            print(line)
            return 0
        rc, out = run_validator(
            line, args.csv, args.only_approved, args.min_score, silent=args.table
        )
        if args.table:
            print_refresh_banner(now_local)
            print_adaptive_table(
                ["Date", "Time", "Status", "Reason", "Action"],
                [[now_local.strftime("%Y-%m-%d"), now_local.strftime("%H:%M:%S"), "NO TRADE", reason_text, "Skip"]],
            )
        return rc

    oc_resp = adapter.get_option_chain_snapshot(
        symbol=args.symbol,
        strike_count=args.strikecount,
    )
    if not isinstance(oc_resp, dict):
        line = build_no_trade("Option chain unavailable")
        if args.print_line_only:
            print(line)
            return 0
        rc, out = run_validator(
            line, args.csv, args.only_approved, args.min_score, silent=args.table
        )
        if args.table:
            print_refresh_banner(now_local)
            print_adaptive_table(
                ["Date", "Time", "Status", "Reason", "Action"],
                [[now_local.strftime("%Y-%m-%d"), now_local.strftime("%H:%M:%S"), "NO TRADE", "Option chain unavailable", "Skip"]],
            )
        return rc

    spot = float(inputs["close"])
    contracts = get_unique_contracts(oc_resp)
    if not contracts:
        line = build_no_trade("No liquid contract found")
        if args.print_line_only:
            print(line)
            return 0
        rc, out = run_validator(
            line, args.csv, args.only_approved, args.min_score, silent=args.table
        )
        if args.table:
            print_refresh_banner(now_local)
            print_adaptive_table(
                ["Date", "Time", "Status", "Reason", "Action"],
                [[now_local.strftime("%Y-%m-%d"), now_local.strftime("%H:%M:%S"), "NO TRADE", "No liquid contract found", "Skip"]],
            )
        return rc

    # Detect actual expiry day from market contract data (replaces hardcoded weekday)
    expiry_day = is_expiry_today(contracts, now_local) or args.force_expiry_day
    if expiry_day and not expiry_day_fallback:
        # Market shows today is expiry but fallback didn't detect it (e.g., holiday shift)
        active_profile = resolve_profile(args.profile, expiry_day)
        cfg = profile_config(active_profile)

    opt_ctx = compute_option_context(contracts, spot)
    vix = adapter.get_quote_ltp(args.vix_symbol)
    fut_symbol, fut_ltp = adapter.resolve_future_quote(
        index_name=index_key,
        explicit_symbol=args.fut_symbol,
        now_local=now_local,
    )
    fut_basis = (fut_ltp - spot) if fut_ltp > 0 else 0.0
    fut_basis_pct = (fut_basis / max(spot, 1e-9) * 100.0) if fut_ltp > 0 else 0.0

    flow = compute_flow_metrics(contracts)
    prev_vol_dom = str(state.get("last_vol_dom", "NEUTRAL"))
    vol_dom = str(flow.get("vol_dom", "NEUTRAL"))
    vol_switch = (
        prev_vol_dom not in {"", "NEUTRAL"}
        and vol_dom not in {"", "NEUTRAL"}
        and prev_vol_dom != vol_dom
    )
    vote_info = compute_side_vote(
        contracts=contracts,
        spot=spot,
        now_local=now_local,
        trend_side=str(inputs.get("side", "")),
        vol_dom=vol_dom,
        min_vote_diff=int(args.min_vote_diff),
    )
    voted_side = str(vote_info.get("vote_side", ""))
    state, has_side, same_side_count, last_flip_ts = update_side_state(
        state, voted_side, now_ts
    )
    stable_side = has_side and same_side_count >= max(1, int(args.confirm_pulls))
    cooldown_left = 0
    if has_side:
        elapsed = max(0, now_ts - last_flip_ts)
        cooldown_left = max(0, int(args.flip_cooldown_sec) - elapsed)

    if not voted_side:
        state["last_vol_dom"] = vol_dom
        save_state(args.state_file, state)
        reason = (
            f"Vote unclear ce={vote_info.get('ce_vote',0)} "
            f"pe={vote_info.get('pe_vote',0)} diff<{int(args.min_vote_diff)}"
        )
        line = build_no_trade(reason)
        if args.print_line_only:
            print(line)
            return 0
        rc, out = run_validator(
            line, args.csv, args.only_approved, args.min_score, silent=args.table
        )
        # Write market context to journal even when vote is unclear
        unclear_headers = [
            "date", "time", "symbol", "spot", "vix", "total_ce_oi", "total_pe_oi",
            "net_pcr", "max_pain", "max_pain_dist", "fut_symbol", "contract_symbol",
            "option_expiry", "option_expiry_code", "fut_ltp",
            "fut_basis", "fut_basis_pct", "side", "strike", "strike_pcr", "entry",
            "sl", "t1", "t2", "confidence", "status", "score", "action", "stable",
            "cooldown_sec", "entry_ready", "selected", "spread_pct", "bid", "ask",
            "volume", "oi", "oich", "vol_oi_ratio", "iv", "delta", "gamma",
            "theta_day", "decay_pct", "vote_ce", "vote_pe", "vote_side", "vote_diff",
            "vol_dom", "vol_switch", "flow_match",
            "fvg_side", "fvg_active", "fvg_gap", "fvg_distance", "fvg_distance_atr", "fvg_plus",
            "learn_prob", "learn_gate",
            "outside_window", "reason", "outcome",
        ]
        unclear_row = {
            "date": now_local.strftime("%Y-%m-%d"),
            "time": now_local.strftime("%H:%M:%S"),
            "symbol": index_display,
            "spot": f"{spot:.2f}",
            "vix": f"{vix:.2f}",
            "total_ce_oi": f"{float(opt_ctx.get('total_ce_oi', 0.0)):.0f}",
            "total_pe_oi": f"{float(opt_ctx.get('total_pe_oi', 0.0)):.0f}",
            "net_pcr": f"{float(opt_ctx.get('net_pcr', 0.0)):.4f}",
            "max_pain": str(int(float(opt_ctx.get("max_pain", 0) or 0))),
            "max_pain_dist": f"{float(opt_ctx.get('max_pain_dist', 0.0)):.2f}",
            "fut_symbol": fut_symbol,
            "contract_symbol": "",
            "option_expiry": "",
            "option_expiry_code": "",
            "fut_ltp": f"{fut_ltp:.2f}" if fut_ltp > 0 else "",
            "fut_basis": f"{fut_basis:.2f}" if fut_ltp > 0 else "",
            "fut_basis_pct": f"{fut_basis_pct:.4f}" if fut_ltp > 0 else "",
            "side": "",
            "strike": "",
            "strike_pcr": "",
            "entry": "",
            "sl": "",
            "t1": "",
            "t2": "",
            "confidence": str(int(inputs.get("confidence", 0))),
            "status": "VOTE_UNCLEAR",
            "score": "",
            "action": "Skip",
            "stable": "Y" if stable_side else "N",
            "cooldown_sec": str(cooldown_left),
            "entry_ready": "N",
            "selected": "N",
            "spread_pct": "",
            "bid": "",
            "ask": "",
            "volume": "",
            "oi": "",
            "oich": "",
            "vol_oi_ratio": f"{flow.get('vol_oi_ratio', 0):.4f}",
            "iv": "",
            "delta": "",
            "gamma": "",
            "theta_day": "",
            "decay_pct": "",
            "vote_ce": str(vote_info.get("ce_vote", 0)),
            "vote_pe": str(vote_info.get("pe_vote", 0)),
            "vote_side": "",
            "vote_diff": str(vote_info.get("vote_diff", 0)),
            "vol_dom": vol_dom,
            "vol_switch": "Y" if vol_switch else "N",
            "flow_match": "",
            "fvg_side": str(inputs.get("fvg_side", "")),
            "fvg_active": str(inputs.get("fvg_active", "N")),
            "fvg_gap": f"{float(inputs.get('fvg_gap', 0.0)):.4f}",
            "fvg_distance": f"{float(inputs.get('fvg_distance', 0.0)):.4f}",
            "fvg_distance_atr": f"{float(inputs.get('fvg_distance_atr', 0.0)):.4f}",
            "fvg_plus": str(inputs.get("fvg_plus", "N")),
            "learn_prob": "",
            "learn_gate": "",
            "outside_window": "Y" if outside_window else "N",
            "reason": "vote_unclear",
            "outcome": "",
        }
        append_csv_row(args.journal_csv, unclear_headers, unclear_row)
        if args.table:
            print_refresh_banner(now_local)
            print_adaptive_table(
                ["Date", "Time", "Status", "Reason", "Action"],
                [[now_local.strftime("%Y-%m-%d"), now_local.strftime("%H:%M:%S"), "NO TRADE", reason, "Skip"]],
            )
            print()
            print("Market Context (vote unclear):")
            ctx_headers = ["Spot", "VIX", "NetPCR", "MaxPain", "MP_Dist", "FutBasis%", "VolDom", "VoteCE", "VotePE"]
            ctx_row = [
                f"{spot:.2f}",
                f"{vix:.2f}",
                f"{float(opt_ctx.get('net_pcr', 0.0)):.3f}",
                str(int(float(opt_ctx.get('max_pain', 0) or 0))),
                f"{float(opt_ctx.get('max_pain_dist', 0.0)):.1f}",
                f"{fut_basis_pct:.3f}" if fut_ltp > 0 else "-",
                vol_dom,
                str(vote_info.get("ce_vote", 0)),
                str(vote_info.get("pe_vote", 0)),
            ]
            print_adaptive_table(ctx_headers, [ctx_row])
            print()
            print(
                f"CE_Vol: {flow.get('ce_vol', 0):.0f} | PE_Vol: {flow.get('pe_vol', 0):.0f} | "
                f"CE_OI: {float(opt_ctx.get('total_ce_oi', 0.0)):.0f} | PE_OI: {float(opt_ctx.get('total_pe_oi', 0.0)):.0f}"
            )
            print(f"CE_OICh: {flow.get('ce_oich', 0):.0f} | PE_OICh: {flow.get('pe_oich', 0):.0f} | VolOI_Ratio: {flow.get('vol_oi_ratio', 0):.3f}")
        return rc

    state["last_vol_dom"] = vol_dom
    save_state(args.state_file, state)

    if args.ladder_count >= 1 or args.otm_start >= 1:
        selected_contracts = select_otm_ladder(
            contracts=contracts,
            side=voted_side,
            spot=spot,
            count=args.ladder_count,
            otm_start=args.otm_start,
        )
    else:
        c = select_contract(contracts, voted_side, spot)
        selected_contracts = [c] if c else []

    if not selected_contracts:
        # No OTM strikes available at all (rare edge case)
        line = build_no_trade("No OTM strikes available")
        if args.print_line_only:
            print(line)
            return 0
        rc, out = run_validator(
            line, args.csv, args.only_approved, args.min_score, silent=args.table
        )
        if args.table:
            print_refresh_banner(now_local)
            print_adaptive_table(
                ["Date", "Time", "Status", "Reason", "Action"],
                [[now_local.strftime("%Y-%m-%d"), now_local.strftime("%H:%M:%S"), "NO TRADE", "No OTM strikes", "Skip"]],
            )
            print()
            print("Market Context:")
            ctx_headers = ["Spot", "VIX", "NetPCR", "MaxPain", "MP_Dist", "FutBasis%", "VolDom", "VoteSide", "VoteDiff"]
            ctx_row = [
                f"{spot:.2f}",
                f"{vix:.2f}",
                f"{float(opt_ctx.get('net_pcr', 0.0)):.3f}",
                str(int(float(opt_ctx.get('max_pain', 0) or 0))),
                f"{float(opt_ctx.get('max_pain_dist', 0.0)):.1f}",
                f"{fut_basis_pct:.3f}" if fut_ltp > 0 else "-",
                vol_dom,
                vote_info.get("vote_side", "-"),
                str(vote_info.get("vote_diff", 0)),
            ]
            print_adaptive_table(ctx_headers, [ctx_row])
        return rc

    atr14 = max(float(inputs["atr14"]), 1.0)
    if voted_side == "CE":
        invalidation = f"below_{math.floor(spot - atr14):.0f}"
    else:
        invalidation = f"above_{math.ceil(spot + atr14):.0f}"

    reasons = [active_profile]
    if expiry_day:
        reasons.append("expiry_day")
    reasons.append(f"flow_{vol_dom.lower()}")
    reasons.append("trend_aligned")
    if inputs["volume_boost"]:
        reasons.append("volume_support")
    if inputs["breakout_up"] and voted_side == "CE":
        reasons.append("breakout")
    if inputs["breakdown"] and voted_side == "PE":
        reasons.append("breakdown")

    signal_lines: List[str] = []
    for idx, contract in enumerate(selected_contracts, start=1):
        entry = float(contract["ltp"])
        risk_pct = float(cfg["risk_pct"])
        sl = entry * (1.0 - risk_pct)
        t1 = entry * float(cfg["t1_mult"])
        t2 = entry * float(cfg["t2_mult"])
        confidence = max(50, int(inputs["confidence"]) - ((idx - 1) * 3))
        reason = "_".join(reasons + [f"otm{args.otm_start + idx - 1}"] + inputs["tags"])[:60]

        line = build_signal_line(
            symbol=index_display,
            side=voted_side,
            strike=int(round(contract["strike"])),
            entry=entry,
            sl=sl,
            t1=t1,
            t2=t2,
            invalidation=invalidation,
            confidence=confidence,
            reason=reason,
        )
        signal_lines.append(line)

    if args.print_line_only:
        print("\n".join(signal_lines))
        return 0

    contract_by_strike = {
        int(round(float(c.get("strike", 0) or 0))): c for c in selected_contracts
    }
    greeks_by_strike = {
        int(round(float(c.get("strike", 0) or 0))): compute_contract_greeks(c, spot, now_local)
        for c in selected_contracts
    }

    status = 0
    table_rows: List[List[str]] = []
    journal_headers = [
        "date",
        "time",
        "symbol",
        "spot",
        "vix",
        "total_ce_oi",
        "total_pe_oi",
        "net_pcr",
        "max_pain",
        "max_pain_dist",
        "fut_symbol",
        "contract_symbol",
        "option_expiry",
        "option_expiry_code",
        "fut_ltp",
        "fut_basis",
        "fut_basis_pct",
        "side",
        "strike",
        "strike_pcr",
        "entry",
        "sl",
        "t1",
        "t2",
        "confidence",
        "status",
        "score",
        "action",
        "stable",
        "cooldown_sec",
        "entry_ready",
        "selected",
        "spread_pct",
        "bid",
        "ask",
        "volume",
        "oi",
        "oich",
        "vol_oi_ratio",
        "iv",
        "delta",
        "gamma",
        "theta_day",
        "decay_pct",
        "vote_ce",
        "vote_pe",
        "vote_side",
        "vote_diff",
        "vol_dom",
        "vol_switch",
        "flow_match",
        "fvg_side",
        "fvg_active",
        "fvg_gap",
        "fvg_distance",
        "fvg_distance_atr",
        "fvg_plus",
        "learn_prob",
        "learn_gate",
        "outside_window",
        "ems_score",
        "reason",
        "outcome",
    ]
    learn_gate_candidate_pull = False
    learn_gate_pass_pull = False
    learn_gate_blocked_pull = False

    # Re-rank signal_lines by EMS before the idx-based selected_strike assignment.
    # Without this, idx=1 is always nearest OTM (positional), not best risk/reward.
    # After sort, idx=1 = highest (confidence * |delta| * 100 / premium).
    def _signal_ems(ln: str) -> float:
        _f = parse_signal_line_fields(ln)
        _strike = int(float(_f.get("strike", "0") or 0))
        _premium = float((contract_by_strike.get(_strike) or {}).get("ltp", 0) or 0)
        if _premium <= 0:
            return 0.0
        _conf = int(float(_f.get("confidence", "0") or 0)) / 100.0
        _delta = abs(float((greeks_by_strike.get(_strike) or {}).get("delta", 0) or 0))
        return _conf * _delta * 100.0 / _premium

    signal_lines.sort(key=_signal_ems, reverse=True)

    for idx, line in enumerate(signal_lines, start=1):
        f = parse_signal_line_fields(line)
        conf_val = int(float(f.get("confidence", "0") or 0))
        strike_i = int(float(f.get("strike", "0") or 0))
        contract = contract_by_strike.get(strike_i, {})
        contract_symbol = str(contract.get("symbol", "") or "")
        option_expiry_dt = parse_expiry_from_symbol(contract_symbol, now_local) if contract_symbol else None
        g = greeks_by_strike.get(strike_i, {})
        strike_pcr = float(opt_ctx.get("strike_pcr_map", {}).get(strike_i, 0.0) or 0.0)
        bid = float(contract.get("bid", 0) or 0)
        ask = float(contract.get("ask", 0) or 0)
        ltp = float(contract.get("ltp", 0) or 0)
        volume = float(contract.get("volume", 0) or 0)
        oi = float(contract.get("oi", 0) or 0)
        oich = float(contract.get("oich", 0) or 0)
        vol_oi_ratio = volume / max(oi, 1e-9) if oi > 0 else 0.0
        iv = float(g.get("iv", 0.0) or 0.0)
        delta = float(g.get("delta", 0.0) or 0.0)
        gamma = float(g.get("gamma", 0.0) or 0.0)
        theta_day = float(g.get("theta_day", 0.0) or 0.0)
        decay_pct = float(g.get("decay_pct_day", 0.0) or 0.0)
        spread_pct = 0.0
        if bid > 0 and ask > 0 and ask >= bid:
            spread_pct = ((ask - bid) / max(ltp, 1e-9)) * 100.0

        selected_strike = idx <= max(1, int(args.max_select_strikes))
        flow_conflict = vol_dom in {"CE", "PE"} and vol_dom != f.get("side", "")
        flow_match = not flow_conflict
        spread_ok = (spread_pct == 0.0) or (spread_pct <= float(args.max_spread_pct))
        delta_ok = abs(delta) >= float(args.min_abs_delta)
        premium_ok = check_premium_ok(ltp, args.min_premium, args.max_premium)
        feature_raw = {
            "confidence": conf_val / 100.0,
            "score": min(1.0, max(0.0, conf_val / 100.0)),
            "vote_diff": min(1.0, max(0.0, float(vote_info.get("vote_diff", 0)) / 5.0)),
            "spread_pct": max(0.0, 1.0 - min(1.0, spread_pct / 5.0)),
            "abs_delta": abs(delta),
            "gamma": min(1.0, max(0.0, gamma * 1000.0)),
            "decay_pct": max(0.0, 1.0 - min(1.0, decay_pct / 3000.0)),
            "stable": 1.0 if stable_side else 0.0,
            "cooldown_ready": 1.0 if cooldown_left == 0 else 0.0,
            "flow_match": 1.0 if flow_match else 0.0,
            "selected": 1.0 if selected_strike else 0.0,
        }
        learn_prob = adaptive_probability(model, feature_raw) if adaptive_enabled else None
        learn_gate_raw_ok = (
            (learn_prob is None)
            or (float(learn_prob) >= float(args.min_learn_prob))
        )
        eligible_except_learn = (
            selected_strike
            and stable_side
            and cooldown_left == 0
            and conf_val >= int(args.min_confidence)
            and (not flow_conflict)
            and spread_ok
            and delta_ok
            and premium_ok
            and (not outside_window)
        )
        if adaptive_enabled and eligible_except_learn:
            learn_gate_candidate_pull = True
            if learn_gate_raw_ok:
                learn_gate_pass_pull = True
            elif learn_gate_enforced:
                learn_gate_blocked_pull = True

        learn_gate_ok = learn_gate_raw_ok if learn_gate_enforced else True
        if learn_prob is None:
            learn_gate_label = ""
        elif learn_gate_enforced:
            learn_gate_label = "Y" if learn_gate_ok else "N"
        elif hard_gate_ready:
            learn_gate_label = "BYPASS"
        else:
            learn_gate_label = "MATURITY"
        entry_ready = (
            selected_strike
            and stable_side
            and cooldown_left == 0
            and conf_val >= int(args.min_confidence)
            and (not flow_conflict)
            and spread_ok
            and delta_ok
            and premium_ok
            and learn_gate_ok
            and (not outside_window)
        )

        prefilter_notes: List[str] = []
        if outside_window:
            prefilter_notes.append(window_reason)
        if not selected_strike:
            prefilter_notes.append("rank")
        if not stable_side:
            prefilter_notes.append("unstable_side")
        if cooldown_left > 0:
            prefilter_notes.append(f"cooldown_{cooldown_left}s")
        if conf_val < int(args.min_confidence):
            prefilter_notes.append("low_conf")
        if flow_conflict:
            prefilter_notes.append("flow_conflict")
        if not spread_ok:
            prefilter_notes.append("spread_high")
        if not delta_ok:
            prefilter_notes.append("low_delta")
        if not premium_ok:
            prefilter_notes.append("premium_out")
        if learn_gate_enforced and not learn_gate_ok:
            prefilter_notes.append("low_learn_prob")

        journal_common = {
            "date": f.get("date", ""),
            "time": f.get("time", ""),
            "symbol": f.get("symbol", ""),
            "spot": f"{spot:.2f}",
            "vix": f"{vix:.2f}",
            "total_ce_oi": f"{float(opt_ctx.get('total_ce_oi', 0.0)):.0f}",
            "total_pe_oi": f"{float(opt_ctx.get('total_pe_oi', 0.0)):.0f}",
            "net_pcr": f"{float(opt_ctx.get('net_pcr', 0.0)):.4f}",
            "max_pain": str(int(float(opt_ctx.get("max_pain", 0) or 0))),
            "max_pain_dist": f"{float(opt_ctx.get('max_pain_dist', 0.0)):.2f}",
            "fut_symbol": fut_symbol,
            "contract_symbol": contract_symbol,
            "option_expiry": format_expiry_date(option_expiry_dt),
            "option_expiry_code": format_expiry_code(option_expiry_dt),
            "fut_ltp": f"{fut_ltp:.2f}" if fut_ltp > 0 else "",
            "fut_basis": f"{fut_basis:.2f}" if fut_ltp > 0 else "",
            "fut_basis_pct": f"{fut_basis_pct:.4f}" if fut_ltp > 0 else "",
            "side": f.get("side", ""),
            "strike": f.get("strike", ""),
            "strike_pcr": f"{strike_pcr:.4f}",
            "entry": f.get("entry", ""),
            "sl": f.get("sl", ""),
            "t1": f.get("t1", ""),
            "t2": f.get("t2", ""),
            "confidence": f.get("confidence", ""),
            "stable": "Y" if stable_side else "N",
            "cooldown_sec": str(cooldown_left),
            "entry_ready": "Y" if entry_ready else "N",
            "selected": "Y" if (selected_strike and conf_val >= int(args.min_confidence)) else "N",
            "spread_pct": f"{spread_pct:.4f}",
            "bid": f"{bid:.4f}",
            "ask": f"{ask:.4f}",
            "volume": f"{volume:.0f}",
            "oi": f"{oi:.0f}",
            "oich": f"{oich:.0f}",
            "vol_oi_ratio": f"{vol_oi_ratio:.4f}",
            "iv": f"{iv:.4f}",
            "delta": f"{delta:.6f}",
            "gamma": f"{gamma:.8f}",
            "theta_day": f"{theta_day:.6f}",
            "decay_pct": f"{decay_pct:.4f}",
            "vote_ce": str(vote_info.get("ce_vote", 0)),
            "vote_pe": str(vote_info.get("pe_vote", 0)),
            "vote_side": str(vote_info.get("vote_side", "")),
            "vote_diff": str(vote_info.get("vote_diff", 0)),
            "vol_dom": vol_dom,
            "vol_switch": "Y" if vol_switch else "N",
            "flow_match": "Y" if flow_match else "N",
            "fvg_side": str(inputs.get("fvg_side", "")),
            "fvg_active": str(inputs.get("fvg_active", "N")),
            "fvg_gap": f"{float(inputs.get('fvg_gap', 0.0)):.4f}",
            "fvg_distance": f"{float(inputs.get('fvg_distance', 0.0)):.4f}",
            "fvg_distance_atr": f"{float(inputs.get('fvg_distance_atr', 0.0)):.4f}",
            "fvg_plus": str(inputs.get("fvg_plus", "N")),
            "learn_prob": f"{learn_prob:.4f}" if learn_prob is not None else "",
            "learn_gate": learn_gate_label,
            "outside_window": "Y" if outside_window else "N",
            "ems_score": f"{(conf_val / 100.0) * (abs(delta) * 100.0) / ltp:.6f}" if ltp > 0 else "0.000000",
        }

        if prefilter_notes:
            # Calculate preliminary score even for prefiltered signals
            try:
                pre_entry = float(f.get("entry", 0) or 0)
                pre_sl = float(f.get("sl", 0) or 0)
                pre_t1 = float(f.get("t1", 0) or 0)
                pre_t2 = float(f.get("t2", 0) or 0)
                pre_conf = int(float(f.get("confidence", 0) or 0))
                prelim_score = calc_preliminary_score(pre_entry, pre_sl, pre_t1, pre_t2, pre_conf)
            except (ValueError, TypeError):
                prelim_score = 0
            prelim_score_str = f"{prelim_score}/100"

            append_csv_row(
                args.journal_csv,
                journal_headers,
                {
                    **journal_common,
                    "status": "PREFILTER",
                    "score": prelim_score_str,
                    "action": "Skip",
                    "reason": ",".join(prefilter_notes),
                    "outcome": "",
                },
            )
            if args.table:
                table_rows.append(
                    [
                        f.get("time", ""),
                        f.get("side", ""),
                        f.get("strike", ""),
                        f"{strike_pcr:.2f}",
                        pcr_level_label(strike_pcr),
                        f.get("entry", ""),
                        f.get("sl", ""),
                        f.get("t1", ""),
                        f.get("t2", ""),
                        f.get("confidence", ""),
                        "PREFILTER",
                        prelim_score_str,
                        "Skip",
                        "Y" if stable_side else "N",
                        str(cooldown_left),
                        "Y" if entry_ready else "N",
                        "Y" if selected_strike else "N",
                        f"{bid:.2f}" if bid > 0 else "",
                        f"{ask:.2f}" if ask > 0 else "",
                        f"{spread_pct:.2f}",
                        f"{iv:.2f}",
                        f"{delta:.3f}",
                        f"{gamma:.5f}",
                        f"{theta_day:.3f}",
                        f"{decay_pct:.2f}",
                        str(vote_info.get("ce_vote", 0)),
                        str(vote_info.get("pe_vote", 0)),
                        str(vote_info.get("vote_side", "")),
                        str(vote_info.get("vote_diff", 0)),
                        f"{learn_prob:.2f}" if learn_prob is not None else "",
                        learn_gate_label,
                        vol_dom,
                        "Y" if vol_switch else "N",
                        ",".join(prefilter_notes),
                    ]
                )
            elif not args.print_line_only:
                print(
                    f"Prefilter skip strike {f.get('strike','')}: "
                    + ",".join(prefilter_notes)
                )
            continue

        rc, output = run_validator(
            line, args.csv, args.only_approved, args.min_score, silent=args.table
        )
        if rc != 0:
            status = rc
        parsed_status = extract_field(output, r"Status\s*:\s*([A-Z_ ]+)", "NA")
        parsed_score = extract_field(output, r"QualityScore\s*:\s*([0-9]+/[0-9]+)", "NA")
        parsed_action = extract_field(output, r"CSV Action\s*:\s*([A-Za-z ]+)", "NA")
        append_csv_row(
            args.journal_csv,
            journal_headers,
            {
                **journal_common,
                "status": parsed_status,
                "score": parsed_score,
                "action": parsed_action,
                "reason": "",
                "outcome": "",
            },
        )
        if args.table:
            table_rows.append(
                [
                    f.get("time", ""),
                    f.get("side", ""),
                    f.get("strike", ""),
                    f"{strike_pcr:.2f}",
                    pcr_level_label(strike_pcr),
                    f.get("entry", ""),
                    f.get("sl", ""),
                    f.get("t1", ""),
                    f.get("t2", ""),
                    f.get("confidence", ""),
                    parsed_status,
                    parsed_score,
                    parsed_action,
                    "Y" if stable_side else "N",
                    str(cooldown_left),
                    "Y" if entry_ready else "N",
                    "Y" if selected_strike else "N",
                    f"{bid:.2f}" if bid > 0 else "",
                    f"{ask:.2f}" if ask > 0 else "",
                    f"{spread_pct:.2f}",
                    f"{iv:.2f}",
                    f"{delta:.3f}",
                    f"{gamma:.5f}",
                    f"{theta_day:.3f}",
                    f"{decay_pct:.2f}",
                    str(vote_info.get("ce_vote", 0)),
                    str(vote_info.get("pe_vote", 0)),
                    str(vote_info.get("vote_side", "")),
                    str(vote_info.get("vote_diff", 0)),
                    f"{learn_prob:.2f}" if learn_prob is not None else "",
                    learn_gate_label,
                    vol_dom,
                    "Y" if vol_switch else "N",
                    "",
                ]
            )
    if adaptive_enabled:
        if learn_gate_enforced and learn_gate_candidate_pull:
            if learn_gate_blocked_pull and not learn_gate_pass_pull:
                state["learn_gate_fail_pull_streak"] = int(state.get("learn_gate_fail_pull_streak", 0)) + 1
            else:
                state["learn_gate_fail_pull_streak"] = 0

            if int(state["learn_gate_fail_pull_streak"]) >= max(1, int(args.learn_gate_lock_streak)):
                relax_sec = max(30, int(args.learn_gate_relax_sec))
                state["learn_gate_relax_until_ts"] = now_ts + relax_sec
                state["learn_gate_fail_pull_streak"] = 0
                if not args.table and not args.print_line_only:
                    print(
                        "Learn gate auto-relaxed "
                        f"for {relax_sec}s after sustained blocking."
                    )
        elif learn_gate_relax_active:
            state["learn_gate_fail_pull_streak"] = 0
        else:
            state["learn_gate_fail_pull_streak"] = 0
            state["learn_gate_relax_until_ts"] = 0
    else:
        state["learn_gate_fail_pull_streak"] = 0
        state["learn_gate_relax_until_ts"] = 0

    save_state(args.state_file, state)

    if args.table:
        headers = [
            "Time",
            "Side",
            "Strike",
            "StrPCR",
            "Level",
            "Entry",
            "SL",
            "T1",
            "T2",
            "Conf",
            "Status",
            "Score",
            "Action",
            "Stable",
            "CooldownS",
            "EntryReady",
            "Selected",
            "Bid",
            "Ask",
            "Spr%",
            "IV%",
            "Delta",
            "Gamma",
            "ThetaD",
            "Decay%",
            "VoteCE",
            "VotePE",
            "VoteSide",
            "VoteDiff",
            "LearnP",
            "LearnGate",
            "VolDom",
            "VolSwitch",
            "Note",
        ]
        print_refresh_banner(now_local)
        print_adaptive_table(headers, table_rows)

        # Add opportunity status footer
        opp_state_file = os.getenv(
            "OPP_STATE_FILE",
            os.path.join(os.path.dirname(args.state_file), ".opportunity_engine_state.json"),
        )
        opp_open = 0
        if os.path.exists(opp_state_file):
            try:
                with open(opp_state_file, "r", encoding="utf-8") as f:
                    opp_data = json.load(f)
                opp_open = len(opp_data.get("open_positions", {}))
            except Exception:
                pass
        print()
        print(
            f"Opp: Open={opp_open} | "
            f"Spot={spot:.2f} | VIX={vix:.2f} | PCR={opt_ctx.get('net_pcr', 0):.3f} | "
            f"MaxPain={opt_ctx.get('max_pain', 0)}({opt_ctx.get('max_pain_dist', 0):.0f}) | "
            f"Basis={fut_basis_pct:.3f}% | Vol={vol_dom} | Vote={vote_info.get('vote_side', '')}({vote_info.get('vote_diff', 0)})"
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())

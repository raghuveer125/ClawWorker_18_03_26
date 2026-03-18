"""
Institutional Trading Rules Module
65+ years of trading wisdom encoded into rules

Features:
- Time-based trading filters
- Risk management & position sizing
- PCR (Put-Call Ratio) analysis
- Max Pain calculation
- Open Interest analysis
"""

from __future__ import annotations

import os
from datetime import datetime, time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum


class MarketSession(Enum):
    PRE_OPEN = "PRE_OPEN"
    OPENING_NOISE = "OPENING_NOISE"  # First 15 minutes - AVOID
    PRIME_TIME = "PRIME_TIME"  # Best trading window
    CLOSING_RISK = "CLOSING_RISK"  # Last 30 minutes - CAUTION
    CLOSED = "CLOSED"


class TradingDay(Enum):
    NORMAL = "NORMAL"
    WEEKLY_EXPIRY = "WEEKLY_EXPIRY"  # Thursday
    MONTHLY_EXPIRY = "MONTHLY_EXPIRY"  # Last Thursday
    PRE_EXPIRY = "PRE_EXPIRY"  # Day before expiry


@dataclass
class RiskConfig:
    """Risk management configuration"""
    capital: float = 100000.0  # Total trading capital
    max_risk_per_trade_pct: float = 1.0  # Max 1% risk per trade
    max_daily_loss_pct: float = 3.0  # Max 3% daily loss
    max_concurrent_positions: int = 3
    min_risk_reward: float = 1.5  # Minimum 1:1.5 R:R
    lot_sizes: Dict[str, int] = None  # Index lot sizes

    def __post_init__(self):
        if self.lot_sizes is None:
            self.lot_sizes = {
                "NIFTY50": 25,
                "BANKNIFTY": 15,
                "FINNIFTY": 25,
                "MIDCPNIFTY": 50,
                "SENSEX": 10,
            }


@dataclass
class TimeFilter:
    """Time-based trading filter result"""
    session: MarketSession
    can_trade: bool
    warning: Optional[str]
    reason: str
    recommended_action: str


@dataclass
class PositionSize:
    """Position sizing calculation result"""
    lots: int
    quantity: int
    risk_amount: float
    max_loss: float
    capital_required: float
    is_valid: bool
    warning: Optional[str]


@dataclass
class OIAnalysis:
    """Open Interest analysis result"""
    ce_oi: int
    pe_oi: int
    ce_oi_change: int
    pe_oi_change: int
    pcr: float
    max_pain: Optional[int]
    signal: str  # BULLISH_BUILDUP, BEARISH_BUILDUP, LONG_UNWINDING, SHORT_COVERING, NEUTRAL
    confidence: int
    interpretation: str


def load_risk_config() -> RiskConfig:
    """Load risk configuration from environment"""
    return RiskConfig(
        capital=float(os.getenv("TRADING_CAPITAL", "100000")),
        max_risk_per_trade_pct=float(os.getenv("MAX_RISK_PER_TRADE_PCT", "1.0")),
        max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0")),
        max_concurrent_positions=int(os.getenv("MAX_CONCURRENT_POSITIONS", "3")),
        min_risk_reward=float(os.getenv("MIN_RISK_REWARD", "1.5")),
    )


# =============================================================================
# TIME-BASED FILTERS
# =============================================================================

def get_market_session(current_time: Optional[datetime] = None) -> TimeFilter:
    """
    Determine current market session and trading recommendation

    Market Hours (IST):
    - Pre-open: 9:00 - 9:15
    - Opening noise: 9:15 - 9:30 (AVOID)
    - Prime time: 9:30 - 15:00 (BEST)
    - Closing risk: 15:00 - 15:30 (CAUTION)
    - Closed: After 15:30
    """
    if current_time is None:
        current_time = datetime.now()

    current = current_time.time()

    # Market closed
    if current < time(9, 0) or current >= time(15, 30):
        return TimeFilter(
            session=MarketSession.CLOSED,
            can_trade=False,
            warning="Market is closed",
            reason="Outside market hours",
            recommended_action="WAIT for market open"
        )

    # Pre-open auction
    if current < time(9, 15):
        return TimeFilter(
            session=MarketSession.PRE_OPEN,
            can_trade=False,
            warning="Pre-open auction in progress",
            reason="No regular trading during pre-open",
            recommended_action="WAIT for 9:15 AM"
        )

    # Opening noise - First 15 minutes (9:15 - 9:30)
    if current < time(9, 30):
        return TimeFilter(
            session=MarketSession.OPENING_NOISE,
            can_trade=False,
            warning="Opening noise period - High volatility, gaps, and fake moves",
            reason="First 15 minutes have unpredictable price action due to overnight gaps and order imbalance",
            recommended_action="WAIT until 9:30 AM for clearer direction"
        )

    # Closing risk - Last 30 minutes (15:00 - 15:30)
    if current >= time(15, 0):
        return TimeFilter(
            session=MarketSession.CLOSING_RISK,
            can_trade=True,
            warning="Closing period - Exercise caution. Theta decay accelerates, manipulation possible",
            reason="Last 30 minutes can have erratic moves due to position squaring",
            recommended_action="TRADE with caution, tighter stops, or CLOSE existing positions"
        )

    # Prime trading time (9:30 - 15:00)
    return TimeFilter(
        session=MarketSession.PRIME_TIME,
        can_trade=True,
        warning=None,
        reason="Prime trading window - Market has established direction",
        recommended_action="TRADE normally with standard risk management"
    )


def get_trading_day_type(current_date: Optional[datetime] = None) -> TradingDay:
    """Determine if today is expiry day or special day"""
    if current_date is None:
        current_date = datetime.now()

    weekday = current_date.weekday()

    # Thursday is weekly expiry (0=Monday, 3=Thursday)
    if weekday == 3:
        # Check if it's the last Thursday of the month (monthly expiry)
        next_week = current_date.day + 7
        if next_week > 28:  # Rough check for last week
            return TradingDay.MONTHLY_EXPIRY
        return TradingDay.WEEKLY_EXPIRY

    # Wednesday is pre-expiry
    if weekday == 2:
        return TradingDay.PRE_EXPIRY

    return TradingDay.NORMAL


def get_expiry_day_rules(day_type: TradingDay) -> Dict[str, Any]:
    """Get special rules for expiry days"""
    rules = {
        TradingDay.NORMAL: {
            "theta_warning": False,
            "recommended_strike": "ATM or 1 OTM",
            "position_size_modifier": 1.0,
            "notes": "Normal trading day"
        },
        TradingDay.WEEKLY_EXPIRY: {
            "theta_warning": True,
            "recommended_strike": "ATM only (avoid OTM)",
            "position_size_modifier": 0.5,  # Half size on expiry
            "notes": "Weekly expiry - Theta decay is extreme. OTM options can go to zero quickly."
        },
        TradingDay.MONTHLY_EXPIRY: {
            "theta_warning": True,
            "recommended_strike": "ATM only or consider next month",
            "position_size_modifier": 0.5,
            "notes": "Monthly expiry - Maximum theta decay. Consider rolling to next expiry."
        },
        TradingDay.PRE_EXPIRY: {
            "theta_warning": True,
            "recommended_strike": "ATM or 1 ITM",
            "position_size_modifier": 0.75,
            "notes": "Pre-expiry day - Theta starts accelerating. Be cautious with OTM."
        }
    }
    return rules.get(day_type, rules[TradingDay.NORMAL])


# =============================================================================
# RISK MANAGEMENT
# =============================================================================

def calculate_position_size(
    index: str,
    entry_price: float,
    stop_loss: float,
    config: Optional[RiskConfig] = None
) -> PositionSize:
    """
    Calculate position size based on risk management rules

    Formula:
    Risk Amount = Capital * Max Risk %
    Points at Risk = Entry - Stop Loss
    Quantity = Risk Amount / (Points at Risk * Lot Size)
    """
    if config is None:
        config = load_risk_config()

    lot_size = config.lot_sizes.get(index, 25)

    # Calculate risk
    points_at_risk = abs(entry_price - stop_loss)
    if points_at_risk == 0:
        return PositionSize(
            lots=0, quantity=0, risk_amount=0, max_loss=0,
            capital_required=0, is_valid=False,
            warning="Invalid stop loss - same as entry"
        )

    max_risk_amount = config.capital * (config.max_risk_per_trade_pct / 100)

    # Calculate lots
    risk_per_lot = points_at_risk * lot_size
    lots = int(max_risk_amount / risk_per_lot)

    if lots == 0:
        lots = 1  # Minimum 1 lot

    quantity = lots * lot_size
    actual_risk = points_at_risk * quantity
    capital_required = entry_price * quantity

    # Validation
    warning = None
    is_valid = True

    if actual_risk > max_risk_amount * 1.5:
        warning = f"Risk ({actual_risk:.0f}) exceeds limit ({max_risk_amount:.0f}). Consider reducing size."
        is_valid = False

    if capital_required > config.capital * 0.5:
        warning = f"Position requires {capital_required/config.capital*100:.0f}% of capital. Too concentrated."
        is_valid = False

    return PositionSize(
        lots=lots,
        quantity=quantity,
        risk_amount=actual_risk,
        max_loss=actual_risk,
        capital_required=capital_required,
        is_valid=is_valid,
        warning=warning
    )


def check_daily_loss_limit(
    realized_pnl_today: float,
    config: Optional[RiskConfig] = None
) -> Tuple[bool, str]:
    """Check if daily loss limit has been hit"""
    if config is None:
        config = load_risk_config()

    max_daily_loss = config.capital * (config.max_daily_loss_pct / 100)

    if realized_pnl_today <= -max_daily_loss:
        return False, f"STOP TRADING: Daily loss limit hit ({realized_pnl_today:.0f} <= -{max_daily_loss:.0f})"

    remaining = max_daily_loss + realized_pnl_today
    if remaining < max_daily_loss * 0.3:
        return True, f"WARNING: Approaching daily loss limit. Only {remaining:.0f} remaining."

    return True, f"OK: Daily loss limit not reached. {remaining:.0f} remaining."


def validate_risk_reward(
    entry: float,
    target: float,
    stop_loss: float,
    config: Optional[RiskConfig] = None
) -> Tuple[bool, float, str]:
    """Validate if trade meets minimum risk:reward ratio"""
    if config is None:
        config = load_risk_config()

    risk = abs(entry - stop_loss)
    reward = abs(target - entry)

    if risk == 0:
        return False, 0, "Invalid: Risk is zero"

    rr_ratio = reward / risk

    if rr_ratio < config.min_risk_reward:
        return False, rr_ratio, f"REJECT: R:R ratio {rr_ratio:.2f} below minimum {config.min_risk_reward}"

    return True, rr_ratio, f"VALID: R:R ratio {rr_ratio:.2f} meets minimum {config.min_risk_reward}"


# =============================================================================
# PCR & MAX PAIN
# =============================================================================

def calculate_pcr(ce_oi: int, pe_oi: int) -> Tuple[float, str]:
    """
    Calculate Put-Call Ratio and interpret it

    PCR = PE OI / CE OI
    - PCR > 1.2: Bullish (more puts = support)
    - PCR < 0.8: Bearish (more calls = resistance)
    - PCR 0.8-1.2: Neutral
    """
    if ce_oi == 0:
        return 0, "INVALID"

    pcr = pe_oi / ce_oi

    if pcr > 1.5:
        return pcr, "STRONGLY_BULLISH"
    elif pcr > 1.2:
        return pcr, "BULLISH"
    elif pcr < 0.5:
        return pcr, "STRONGLY_BEARISH"
    elif pcr < 0.8:
        return pcr, "BEARISH"
    else:
        return pcr, "NEUTRAL"


def calculate_max_pain(option_chain: List[Dict[str, Any]], spot_price: float) -> Optional[int]:
    """
    Calculate Max Pain strike

    Max Pain = Strike where total loss to option buyers is maximum
             = Strike where option writers profit most

    Market tends to gravitate towards Max Pain on expiry
    """
    if not option_chain:
        return None

    strikes = {}

    for opt in option_chain:
        strike = opt.get("strike")
        if not strike:
            continue

        ce_oi = opt.get("ce_oi", 0)
        pe_oi = opt.get("pe_oi", 0)

        if strike not in strikes:
            strikes[strike] = {"ce_oi": 0, "pe_oi": 0}

        strikes[strike]["ce_oi"] += ce_oi
        strikes[strike]["pe_oi"] += pe_oi

    if not strikes:
        return None

    min_pain = float('inf')
    max_pain_strike = None

    for strike, data in strikes.items():
        # Calculate pain at this strike
        ce_pain = sum(
            max(0, strike - s) * strikes[s]["ce_oi"]
            for s in strikes if s < strike
        )
        pe_pain = sum(
            max(0, s - strike) * strikes[s]["pe_oi"]
            for s in strikes if s > strike
        )

        total_pain = ce_pain + pe_pain

        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = strike

    return max_pain_strike


# =============================================================================
# OPEN INTEREST ANALYSIS
# =============================================================================

def analyze_oi_change(
    ce_oi: int,
    pe_oi: int,
    ce_oi_prev: int,
    pe_oi_prev: int,
    price_change_pct: float
) -> OIAnalysis:
    """
    Analyze Open Interest changes for institutional flow

    | Price | OI     | Interpretation      |
    |-------|--------|---------------------|
    | Up    | Up     | BULLISH BUILDUP     |
    | Up    | Down   | SHORT COVERING      |
    | Down  | Up     | BEARISH BUILDUP     |
    | Down  | Down   | LONG UNWINDING      |
    """
    ce_oi_change = ce_oi - ce_oi_prev
    pe_oi_change = pe_oi - pe_oi_prev
    total_oi_change = (ce_oi_change + pe_oi_change)

    pcr, pcr_signal = calculate_pcr(ce_oi, pe_oi)

    # Determine signal based on OI change and price
    oi_increasing = total_oi_change > 0
    price_up = price_change_pct > 0

    if price_up and oi_increasing:
        signal = "BULLISH_BUILDUP"
        confidence = min(80, 50 + abs(price_change_pct) * 10)
        interpretation = "Fresh longs being added. Strong bullish signal. Institutions are buying."
    elif price_up and not oi_increasing:
        signal = "SHORT_COVERING"
        confidence = min(60, 40 + abs(price_change_pct) * 5)
        interpretation = "Shorts are covering. Rally may be temporary. Wait for fresh longs."
    elif not price_up and oi_increasing:
        signal = "BEARISH_BUILDUP"
        confidence = min(80, 50 + abs(price_change_pct) * 10)
        interpretation = "Fresh shorts being added. Strong bearish signal. Institutions are selling."
    elif not price_up and not oi_increasing:
        signal = "LONG_UNWINDING"
        confidence = min(60, 40 + abs(price_change_pct) * 5)
        interpretation = "Longs are exiting. Fall may be temporary. Wait for fresh shorts."
    else:
        signal = "NEUTRAL"
        confidence = 35
        interpretation = "No clear institutional activity. Wait for clearer signal."

    return OIAnalysis(
        ce_oi=ce_oi,
        pe_oi=pe_oi,
        ce_oi_change=ce_oi_change,
        pe_oi_change=pe_oi_change,
        pcr=round(pcr, 2),
        max_pain=None,  # Needs option chain data
        signal=signal,
        confidence=int(confidence),
        interpretation=interpretation
    )


# =============================================================================
# COMPREHENSIVE TRADE VALIDATION
# =============================================================================

def validate_trade(
    index: str,
    direction: str,  # "CE" or "PE"
    entry: float,
    target: float,
    stop_loss: float,
    current_time: Optional[datetime] = None,
    realized_pnl_today: float = 0,
    config: Optional[RiskConfig] = None
) -> Dict[str, Any]:
    """
    Comprehensive trade validation with all institutional rules

    Returns validation result with all checks
    """
    if config is None:
        config = load_risk_config()

    checks = []
    can_trade = True
    warnings = []

    # 1. Time-based check
    time_filter = get_market_session(current_time)
    checks.append({
        "check": "Time Filter",
        "passed": time_filter.can_trade,
        "details": time_filter.reason
    })
    if not time_filter.can_trade:
        can_trade = False
    if time_filter.warning:
        warnings.append(time_filter.warning)

    # 2. Trading day check
    day_type = get_trading_day_type(current_time)
    day_rules = get_expiry_day_rules(day_type)
    checks.append({
        "check": "Trading Day",
        "passed": True,
        "details": f"{day_type.value}: {day_rules['notes']}"
    })
    if day_rules["theta_warning"]:
        warnings.append(f"Expiry warning: {day_rules['notes']}")

    # 3. Daily loss limit check
    can_continue, loss_msg = check_daily_loss_limit(realized_pnl_today, config)
    checks.append({
        "check": "Daily Loss Limit",
        "passed": can_continue,
        "details": loss_msg
    })
    if not can_continue:
        can_trade = False

    # 4. Risk:Reward check
    rr_valid, rr_ratio, rr_msg = validate_risk_reward(entry, target, stop_loss, config)
    checks.append({
        "check": "Risk:Reward",
        "passed": rr_valid,
        "details": rr_msg
    })
    if not rr_valid:
        can_trade = False

    # 5. Position sizing
    position = calculate_position_size(index, entry, stop_loss, config)
    checks.append({
        "check": "Position Size",
        "passed": position.is_valid,
        "details": f"{position.lots} lots, Risk: {position.risk_amount:.0f}"
    })
    if not position.is_valid:
        can_trade = False
    if position.warning:
        warnings.append(position.warning)

    # Apply expiry day modifier
    if day_rules["position_size_modifier"] < 1.0:
        adjusted_lots = max(1, int(position.lots * day_rules["position_size_modifier"]))
        position = PositionSize(
            lots=adjusted_lots,
            quantity=adjusted_lots * config.lot_sizes.get(index, 25),
            risk_amount=position.risk_amount * day_rules["position_size_modifier"],
            max_loss=position.max_loss * day_rules["position_size_modifier"],
            capital_required=position.capital_required * day_rules["position_size_modifier"],
            is_valid=position.is_valid,
            warning=f"Reduced to {adjusted_lots} lots due to {day_type.value}"
        )

    return {
        "can_trade": can_trade,
        "checks": checks,
        "warnings": warnings,
        "position": asdict(position),
        "time_filter": asdict(time_filter),
        "day_type": day_type.value,
        "day_rules": day_rules,
        "risk_reward_ratio": rr_ratio,
        "summary": "APPROVED" if can_trade else "REJECTED",
        "recommendation": time_filter.recommended_action
    }

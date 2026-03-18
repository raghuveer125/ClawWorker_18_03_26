"""
Microstructure helpers for the fast execution loop.
"""

from datetime import datetime
from typing import Any, Dict, List, Tuple


def compute_momentum_strength(
    futures_strength: float,
    imbalance: float,
    volume_spike: float,
    strong_threshold: float,
    moderate_threshold: float,
) -> Dict[str, Any]:
    score = (
        min(max(futures_strength, 0.0), 1.0) * 0.5
        + min(abs(imbalance), 1.0) * 0.3
        + min(max(volume_spike, 0.0), 1.0) * 0.2
    )
    if score >= strong_threshold:
        timing = "immediate"
    elif score >= moderate_threshold:
        timing = "confirm_window"
    else:
        timing = "reject"
    return {
        "score": round(score, 4),
        "timing": timing,
        "futures_strength": round(float(futures_strength or 0.0), 4),
        "imbalance_strength": round(float(abs(imbalance) or 0.0), 4),
        "volume_spike_strength": round(float(volume_spike or 0.0), 4),
    }


def detect_liquidity_vacuum(
    previous_depth: Dict[str, float],
    current_depth: Dict[str, float],
    threshold: float,
) -> Dict[str, Any]:
    prev_bid = max(float(previous_depth.get("bid_total", 0.0) or 0.0), 1e-9)
    prev_ask = max(float(previous_depth.get("ask_total", 0.0) or 0.0), 1e-9)
    bid_total = float(current_depth.get("bid_total", 0.0) or 0.0)
    ask_total = float(current_depth.get("ask_total", 0.0) or 0.0)
    bid_drop = max(0.0, (prev_bid - bid_total) / prev_bid)
    ask_drop = max(0.0, (prev_ask - ask_total) / prev_ask)
    active = bid_drop >= threshold or ask_drop >= threshold
    side = "bid" if bid_drop >= ask_drop else "ask"
    return {
        "active": active,
        "bid_depth_drop": round(bid_drop, 4),
        "ask_depth_drop": round(ask_drop, 4),
        "side": side if active else "neutral",
        "bid_total": round(bid_total, 2),
        "ask_total": round(ask_total, 2),
    }


def estimate_queue_risk(
    qty_ahead: float,
    order_size: float,
    ratio_threshold: float,
    reduce_threshold: float,
) -> Dict[str, Any]:
    safe_order_size = max(float(order_size or 0.0), 1.0)
    ratio = float(qty_ahead or 0.0) / safe_order_size
    if ratio >= ratio_threshold:
        risk = "high"
        size_scale = 0.0
    elif ratio >= reduce_threshold:
        risk = "medium"
        size_scale = 0.5
    else:
        risk = "low"
        size_scale = 1.0
    return {
        "risk": risk,
        "queue_ratio": round(ratio, 4),
        "qty_ahead": round(float(qty_ahead or 0.0), 2),
        "size_scale": size_scale,
    }


def detect_volatility_burst(
    tick_returns: List[float],
    spread_changes: List[float],
    vol_threshold: float,
    spread_threshold: float,
) -> Dict[str, Any]:
    recent_returns = tick_returns[-5:]
    recent_spread_changes = spread_changes[-3:]
    short_window_volatility = (
        sum(abs(float(value or 0.0)) for value in recent_returns) / max(len(recent_returns), 1)
    )
    spread_jump = max((float(value or 0.0) for value in recent_spread_changes), default=0.0)
    active = short_window_volatility >= vol_threshold or spread_jump >= spread_threshold
    return {
        "active": active,
        "short_window_volatility": round(short_window_volatility, 6),
        "spread_jump": round(spread_jump, 6),
        "burst_otm_scale": 0.9 if active else 1.0,
        "burst_stop_scale": 0.85 if active else 1.0,
        "burst_timing": "fast" if active else "normal",
    }


def run_pre_entry_confirmation(
    direction_support: bool,
    momentum_active: bool,
    spread_stable: bool,
    price_not_reversed: bool,
    state: Dict[str, Any],
    now: datetime,
    window_ms: int,
) -> Tuple[bool, Dict[str, Any], List[str]]:
    reasons: List[str] = []
    started_at_raw = state.get("started_at")
    started_at = None
    if isinstance(started_at_raw, str):
        try:
            started_at = datetime.fromisoformat(started_at_raw)
        except ValueError:
            started_at = None

    if started_at is None:
        state = {
            **state,
            "status": "pending",
            "started_at": now.isoformat(),
            "window_ms": int(window_ms),
            "last_checked_at": now.isoformat(),
        }
        return False, state, ["confirmation_window_started"]

    if not direction_support:
        reasons.append("direction_support_failed")
    if not momentum_active:
        reasons.append("momentum_failed")
    if not spread_stable:
        reasons.append("spread_widened")
    if not price_not_reversed:
        reasons.append("price_reversed")

    if reasons:
        state = {
            **state,
            "status": "cancelled",
            "last_checked_at": now.isoformat(),
            "reasons": reasons,
        }
        return False, state, reasons

    age_ms = max(0.0, (now - started_at).total_seconds() * 1000.0)
    if age_ms >= float(window_ms):
        state = {
            **state,
            "status": "confirmed",
            "last_checked_at": now.isoformat(),
            "reasons": [],
        }
        return True, state, []

    state = {
        **state,
        "status": "pending",
        "last_checked_at": now.isoformat(),
        "reasons": ["confirmation_pending"],
    }
    return False, state, ["confirmation_pending"]

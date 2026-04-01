"""
Production Risk Engine — Institutional-grade entry/exit/sizing/slippage controls.

Implements all CRITICAL and HIGH priority fixes from INSTITUTIONAL_REVIEW.md.
Drop-in replacement for scattered risk logic across execution_agents.py.

Usage:
    from scalping.risk_engine import (
        validate_entry, compute_position_size, calculate_sl_target,
        validate_exit, estimate_slippage, KillSwitch, trade_logger,
    )
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, time as dtime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .config import ScalpingConfig, IndexConfig, IndexType, get_index_config

logger = logging.getLogger("scalping.risk_engine")


# ============================================================================
# DATA STRUCTURES
# ============================================================================

class ExitReason(str, Enum):
    SL_HIT = "sl_hit"
    MAX_LOSS = "max_loss_per_trade"
    TARGET_HIT = "target_hit"
    TIME_STOP = "time_stop"
    MOMENTUM_REVERSAL = "momentum_reversal"
    SPREAD_EXIT = "spread_exit"
    THESIS_INVALIDATED = "thesis_invalidated"
    KILL_SWITCH = "kill_switch"
    EOD = "end_of_day"
    MANUAL = "manual"


@dataclass
class SlippageEstimate:
    expected_fill: float
    slippage_cost: float
    slippage_pct: float
    market_impact: float
    fill_confidence: float  # 0-1, likelihood of full fill


@dataclass
class EntryDecision:
    approved: bool
    reason: str
    lots: int = 0
    sl_price: float = 0.0
    target_price: float = 0.0
    adjusted_rr: float = 0.0
    slippage_estimate: Optional[SlippageEstimate] = None
    vix_scale: float = 1.0
    regime_scale: float = 1.0
    drawdown_scale: float = 1.0
    multiplier: float = 0.0
    log: Optional[Dict[str, Any]] = None


@dataclass
class ExitDecision:
    should_exit: bool
    reason: ExitReason = ExitReason.MANUAL
    exit_qty: int = 0
    exit_price: float = 0.0
    urgency: int = 0  # 0=normal, 1=high, 2=immediate


# ============================================================================
# SLIPPAGE MODEL
# ============================================================================

def estimate_slippage(
    order_qty: int,
    bid_qty: int,
    ask_qty: int,
    spread: float,
    spread_pct: float,
    volatility: float,
    side: str = "buy",
    entry_price: float = 0.0,
) -> SlippageEstimate:
    """Realistic slippage model based on spread + depth + volatility.

    For buys: slippage = how much above mid we pay.
    For sells: slippage = how much below mid we receive.
    """
    mid_price = spread / 2.0 if spread > 0 else 0.0
    depth = ask_qty if side == "buy" else bid_qty
    depth = max(depth, 1)

    # Base: half the spread (best case, we fill at ask for buys)
    base_slippage = spread * 0.5

    # Market impact: order size relative to available depth
    fill_ratio = order_qty / depth
    if fill_ratio <= 0.3:
        impact_multiplier = 1.0  # Small order, negligible impact
    elif fill_ratio <= 0.7:
        impact_multiplier = 1.0 + (fill_ratio - 0.3) * 1.5  # Linear ramp
    else:
        impact_multiplier = 1.6 + (fill_ratio - 0.7) * 3.0  # Aggressive ramp

    market_impact = base_slippage * (impact_multiplier - 1.0)

    # Volatility premium: high vol = wider effective spread
    vol_premium = base_slippage * max(0, volatility - 0.01) * 10.0

    total_slippage = base_slippage + market_impact + vol_premium
    total_slippage = max(0, total_slippage)

    # Fill confidence: can we fill the full order?
    if depth >= order_qty * 3:
        fill_confidence = 0.99
    elif depth >= order_qty:
        fill_confidence = 0.90
    elif depth >= order_qty * 0.5:
        fill_confidence = 0.70
    else:
        fill_confidence = 0.40

    # Slippage as % of entry price
    ref_price = entry_price if entry_price > 0 else max(spread * 100, 1.0)
    slippage_pct_val = (total_slippage / ref_price) * 100

    return SlippageEstimate(
        expected_fill=total_slippage,
        slippage_cost=total_slippage * order_qty,
        slippage_pct=slippage_pct_val,
        market_impact=market_impact,
        fill_confidence=fill_confidence,
    )


# ============================================================================
# SL / TARGET CALCULATION (ATR + IV based)
# ============================================================================

def calculate_sl_target(
    entry_price: float,
    symbol: str,
    option_type: str,
    context: Dict[str, Any],
    config: ScalpingConfig,
) -> Tuple[float, float]:
    """Volatility-adjusted SL/target using underlying ATR, not option ATR.

    Returns (sl_price, target_price).
    """
    # H1: True ATR (not average range) — includes gap-opens
    candles = context.get("candles_1m", {}).get(symbol, [])
    if len(candles) >= 5:
        true_ranges = []
        for i in range(-5, 0):
            h = candles[i]["high"]
            l = candles[i]["low"]
            prev_c = candles[i - 1]["close"] if abs(i) < len(candles) else h
            true_ranges.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
        atr = sum(true_ranges) / len(true_ranges)
    else:
        # Fallback: estimate from spot data
        spot = context.get("spot_data", {}).get(symbol)
        if spot and hasattr(spot, "high") and hasattr(spot, "low"):
            day_range = float(getattr(spot, "high", 0) or 0) - float(getattr(spot, "low", 0) or 0)
            atr = max(day_range * 0.03, entry_price * 0.015)  # ~3% of day range
        else:
            atr = entry_price * 0.02  # 2% fallback

    # IV rank scaling (VIX as proxy)
    vix = float(context.get("vix", 15) or 15)
    iv_rank = min(1.0, max(0.0, (vix - 10) / 30))  # Normalize 10-40

    if iv_rank > 0.7:
        sl_multiplier = 2.0     # Wider stop in high vol
        target_multiplier = 1.4  # Bigger targets (premiums swing more)
    elif iv_rank < 0.3:
        sl_multiplier = 1.0     # Tighter stop in low vol
        target_multiplier = 0.9  # Modest targets
    else:
        sl_multiplier = 1.5
        target_multiplier = 1.1

    # Convert underlying ATR to option premium movement
    # Rough delta-based: option moves = ATR * abs(delta) * lot_adjustment
    delta = abs(float(context.get("signal_delta", 0.20) or 0.20))
    option_atr = atr * delta  # How much the option premium moves per ATR of underlying

    sl_distance = option_atr * sl_multiplier
    target_distance = max(config.first_target_points, option_atr * target_multiplier * 2.0)

    # Floor / ceiling
    sl_distance = max(sl_distance, entry_price * 0.08)   # Min 8% SL
    sl_distance = min(sl_distance, entry_price * 0.30)   # Max 30% SL
    target_distance = max(target_distance, entry_price * 0.12)  # Min 12% target
    target_distance = min(target_distance, entry_price * 0.50)  # Max 50% target

    sl_price = round(entry_price - sl_distance, 2)
    target_price = round(entry_price + target_distance, 2)

    # Force minimum R:R of 1.1
    if sl_distance > 0:
        rr = target_distance / sl_distance
        if rr < 1.1:
            sl_distance = target_distance / 1.1
            sl_price = round(entry_price - sl_distance, 2)

    return max(0.05, sl_price), target_price


# ============================================================================
# POSITION SIZING (Dynamic, VIX-adjusted)
# ============================================================================

def compute_position_size(
    signal: Dict[str, Any],
    context: Dict[str, Any],
    config: ScalpingConfig,
) -> int:
    """Dynamic position sizing based on VIX, regime, drawdown, liquidity.

    Returns number of lots (0 = reject).
    """
    entry_price = float(signal.get("entry", signal.get("premium", 0)) or 0)
    if entry_price <= 0:
        return 0

    symbol = signal.get("symbol", "")
    idx_config = _resolve_idx(symbol)
    lot_size = idx_config.lot_size if idx_config else 25

    # ── Base from setup tag ──
    tag = str(signal.get("setup_tag", "C") or "C")
    rr = float(signal.get("adjusted_rr", signal.get("rr_ratio", 0)) or 0)
    base = {"A+": 0.65, "B": 0.35, "C": 0.25}.get(tag, 0.25)
    if tag == "A+" and rr >= 1.6:
        base = min(0.70, base + 0.05)
    if tag == "C" and rr < 1.1:
        return 0

    # ── VIX scaling ──
    vix = float(context.get("vix", 15) or 15)
    if vix >= 40:
        return 0  # Halt at extreme VIX
    vix_scale = min(1.5, max(0.4, 15.0 / max(vix, 8)))

    # ── Regime scaling ──
    regime = str(context.get("market_regime", context.get("market_regimes", {}).get(symbol, "RANGE_BOUND")) or "RANGE_BOUND")
    regime_scales = {
        "VOLATILE_EXPANSION": 0.6,
        "VOLATILE_CONTRACTION": 1.1,
        "TRENDING_BULLISH": 1.0,
        "TRENDING_BEARISH": 1.0,
        "RANGE_BOUND": 1.0,
        "EXPIRY_PINNING": 0.7,
    }
    regime_scale = regime_scales.get(regime, 0.8)

    # ── Drawdown scaling (progressive) ──
    daily_pnl = float(context.get("daily_pnl", 0) or 0)
    daily_limit = config.total_capital * config.daily_loss_limit_pct / 100
    if daily_pnl < 0:
        loss_ratio = abs(daily_pnl) / max(daily_limit, 1)
        drawdown_scale = max(0.25, 1.0 - loss_ratio)
    else:
        drawdown_scale = 1.0

    # ── Spread penalty ──
    spread_pct = float(signal.get("spread_pct", 0) or 0)
    if spread_pct > 3.0:
        spread_scale = 0.5
    elif spread_pct > 1.0:
        spread_scale = 0.7
    else:
        spread_scale = 1.0

    # ── Correlation penalty ──
    correlation_scale = float(signal.get("correlation_size_scale", 1.0) or 1.0)

    # ── Final multiplier ──
    multiplier = base * vix_scale * regime_scale * drawdown_scale * spread_scale * correlation_scale
    multiplier = max(0.0, min(1.0, multiplier))

    if multiplier < 0.15:
        return 0

    lots = max(1, round(config.entry_lots * multiplier))

    # ── Hard cap: max 15% of capital per position ──
    max_notional = config.total_capital * 0.15
    max_lots = max(1, int(max_notional / (lot_size * max(entry_price, 1))))
    lots = min(lots, max_lots)

    return lots


# ============================================================================
# ENTRY VALIDATION PIPELINE
# ============================================================================

def validate_entry(
    signal: Dict[str, Any],
    context: Dict[str, Any],
    config: ScalpingConfig,
) -> EntryDecision:
    """Full entry validation pipeline. Returns EntryDecision."""

    log_data: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "event": "entry_validation",
        "symbol": signal.get("symbol", ""),
        "strike": signal.get("strike", 0),
        "option_type": signal.get("option_type", ""),
    }

    def reject(reason: str) -> EntryDecision:
        log_data["decision"] = "rejected"
        log_data["rejection_reason"] = reason
        trade_logger.log_decision(log_data)
        return EntryDecision(approved=False, reason=reason, log=log_data)

    # ── Gate 0: VIX must be present (CRIT-4) ──
    vix_raw = context.get("vix")
    if vix_raw is None or float(vix_raw or 0) <= 0:
        return reject("vix_missing:cannot_size_without_volatility")

    # ── Gate 1: Trade disabled ──
    if context.get("trade_disabled"):
        return reject(f"trade_disabled:{context.get('trade_disabled_reason', '')}")

    # ── Gate 2: Time check ──
    now = context.get("cycle_now", datetime.now())
    if isinstance(now, str):
        try:
            now = datetime.fromisoformat(now)
        except ValueError:
            now = datetime.now()
    cutoff_str = str(config.late_entry_cutoff_time or "14:50")
    cutoff_parts = cutoff_str.split(":")
    cutoff = dtime(int(cutoff_parts[0]), int(cutoff_parts[1]))
    if now.time() > cutoff:
        return reject(f"past_late_entry_cutoff:{cutoff_str}")

    # ── Gate 3: Max positions (CRIT-7: track same-cycle approvals) ──
    positions = context.get("positions", [])
    open_count = sum(1 for p in positions if hasattr(p, "status") and p.status != "closed")
    approved_this_cycle = int(context.get("_approved_entries_this_cycle", 0) or 0)
    effective_count = open_count + approved_this_cycle
    if effective_count >= config.max_positions:
        return reject(f"max_positions:{effective_count}/{config.max_positions}")

    # ── Gate 4: Daily loss limit ──
    daily_pnl = float(context.get("daily_pnl", 0) or 0)
    daily_limit = config.total_capital * config.daily_loss_limit_pct / 100
    if daily_pnl < -daily_limit:
        return reject(f"daily_loss_limit:{daily_pnl:.0f}<-{daily_limit:.0f}")

    # ── Gate 5: Hourly drawdown (NEW) ──
    hourly_pnl = _compute_hourly_pnl(context)
    hourly_limit = config.total_capital * 0.03
    if hourly_pnl < -hourly_limit:
        return reject(f"hourly_drawdown:{hourly_pnl:.0f}_in_2h")

    # ── Gate 6: Data freshness (CRIT-2: reject if timestamp missing) ──
    spot_ts = context.get("spot_timestamp")
    if not spot_ts:
        return reject("spot_timestamp_missing:no_data_freshness_available")
    if isinstance(spot_ts, str):
        try:
            spot_ts = datetime.fromisoformat(spot_ts)
        except ValueError:
            return reject("spot_timestamp_invalid:cannot_parse")
    if (now - spot_ts).total_seconds() > 3.0:
        return reject(f"spot_stale:{(now - spot_ts).total_seconds():.1f}s")

    # ── Gate 7: Quality score ──
    quality_score = float(signal.get("quality_score", 0) or 0)
    grade = str(signal.get("quality_grade", "F") or "F")
    if quality_score < 0.55:
        return reject(f"quality_low:{quality_score:.3f}<0.55")
    if grade == "F":
        return reject("grade_F")
    if grade == "D" and float(signal.get("rr_ratio", 0) or 0) < 1.5:
        return reject(f"grade_D_rr_low:{signal.get('rr_ratio', 0):.2f}<1.5")

    # ── Gate 8: Directional consistency (HIGH-3: check option_type vs structure) ──
    symbol = signal.get("symbol", "")
    conditions = signal.get("conditions_met", [])
    has_bullish = any("bullish" in str(c).lower() for c in conditions)
    has_bearish = any("bearish" in str(c).lower() for c in conditions)
    if has_bullish and has_bearish:
        return reject(f"contradictory_signals:{conditions}")

    # Also check option_type alignment with structure breaks
    opt_type = str(signal.get("option_type", "")).upper()
    structure_breaks = context.get("structure_breaks", [])
    for brk in structure_breaks:
        brk_sym = getattr(brk, "symbol", brk.get("symbol", "") if isinstance(brk, dict) else "")
        brk_type = getattr(brk, "break_type", brk.get("break_type", "") if isinstance(brk, dict) else "")
        if brk_sym == symbol:
            if opt_type == "CE" and "bearish" in str(brk_type).lower():
                return reject(f"direction_mismatch:CE_with_bearish_structure:{brk_type}")
            if opt_type == "PE" and "bullish" in str(brk_type).lower():
                return reject(f"direction_mismatch:PE_with_bullish_structure:{brk_type}")
            break  # Only check first matching break

    # ── Gate 9: Minimum conditions ──
    if len(conditions) < 2:
        return reject(f"insufficient_conditions:{len(conditions)}/2")

    # ── Gate 10: Gamma cannot substitute for futures momentum (NEW) ──
    has_real_futures_momentum = any(
        "futures_momentum" in str(c) for c in conditions
    )
    # Check if the momentum came from gamma proxy
    momentum_signals = context.get("momentum_signals", [])
    symbol = signal.get("symbol", "")
    has_actual_futures_surge = any(
        getattr(m, "signal_type", "") == "futures_surge"
        and getattr(m, "strength", 0) >= 0.7
        and getattr(m, "symbol", "") == symbol
        for m in momentum_signals
    )
    if has_real_futures_momentum and not has_actual_futures_surge:
        # Gamma was used as proxy — downgrade, don't count as momentum
        conditions_without_gamma_proxy = [
            c for c in conditions if c != "futures_momentum"
        ]
        if len(conditions_without_gamma_proxy) < 2:
            return reject("gamma_proxy_not_real_momentum:conditions_insufficient_without_proxy")

    # ── Gate 11: Slippage-adjusted R:R (NEW) ──
    entry = float(signal.get("entry", signal.get("premium", 0)) or 0)
    sl = float(signal.get("sl", 0) or 0)
    target = float(signal.get("t1", signal.get("target", 0)) or 0)
    spread = float(signal.get("spread", 0) or 0)

    if entry <= 0 or sl <= 0 or target <= 0:
        return reject(f"missing_pricing:entry={entry},sl={sl},target={target}")

    entry_slippage = spread * 0.5
    exit_slippage = spread * 0.8
    actual_risk = abs(entry - sl) + exit_slippage
    actual_reward = abs(target - entry) - entry_slippage

    if actual_risk <= 0:
        return reject("zero_actual_risk")

    adjusted_rr = actual_reward / actual_risk
    if adjusted_rr < 1.1:
        return reject(f"rr_after_slippage:{adjusted_rr:.2f}<1.1")

    # ── Gate 12: Liquidity depth (NEW) ──
    bid_qty = int(signal.get("bid_qty", signal.get("volume", 0)) or 0)
    ask_qty = int(signal.get("ask_qty", signal.get("volume", 0)) or 0)
    idx_config = _resolve_idx(symbol)
    lot_size = idx_config.lot_size if idx_config else 25

    # Compute size first to check depth
    lots = compute_position_size(signal, context, config)
    if lots <= 0:
        return reject("position_size_zero")

    order_qty = lots * lot_size
    required_depth = order_qty * 0.3
    if ask_qty > 0 and ask_qty < required_depth:
        return reject(f"insufficient_ask_depth:{ask_qty}<{required_depth:.0f}")

    # ── Gate 13: Slippage estimate ──
    vix = float(context.get("vix", 15) or 15)
    volatility = max(0.005, (vix - 10) / 1000)
    slip = estimate_slippage(
        order_qty=order_qty,
        bid_qty=max(bid_qty, 1),
        ask_qty=max(ask_qty, 1),
        spread=spread,
        spread_pct=float(signal.get("spread_pct", 0) or 0),
        volatility=volatility,
        side="buy",
        entry_price=entry,
    )
    if slip.fill_confidence < 0.60:
        return reject(f"low_fill_confidence:{slip.fill_confidence:.2f}")
    if slip.slippage_pct > 3.0:
        return reject(f"excessive_slippage:{slip.slippage_pct:.1f}%")

    # ── Gate 14: Per-trade max loss check ──
    max_loss_per_trade = config.total_capital * config.risk_per_trade_pct / 100
    potential_loss = actual_risk * order_qty
    if potential_loss > max_loss_per_trade:
        # Reduce lots to fit within max loss
        safe_lots = max(1, int(max_loss_per_trade / (actual_risk * lot_size)))
        lots = min(lots, safe_lots)
        order_qty = lots * lot_size

    # ── Compute SL/target ──
    sl_price, target_price = calculate_sl_target(entry, symbol, signal.get("option_type", "PE"), context, config)

    # ── Build log ──
    vix_scale = min(1.5, max(0.4, 15.0 / max(vix, 8)))
    regime = str(context.get("market_regime", "RANGE_BOUND") or "RANGE_BOUND")
    regime_scale = {"VOLATILE_EXPANSION": 0.6, "TRENDING_BULLISH": 1.0,
                    "TRENDING_BEARISH": 1.0, "RANGE_BOUND": 1.0}.get(regime, 0.8)
    drawdown_scale = max(0.25, 1.0 - abs(min(0, daily_pnl)) / max(daily_limit, 1))

    # CRIT-7: Track approved entries this cycle to prevent multi-entry bypass
    context["_approved_entries_this_cycle"] = approved_this_cycle + 1

    log_data.update({
        "decision": "approved",
        "lots": lots,
        "adjusted_rr": round(adjusted_rr, 3),
        "sl_price": sl_price,
        "target_price": target_price,
        "vix": vix,
        "vix_scale": round(vix_scale, 3),
        "regime": regime,
        "regime_scale": regime_scale,
        "drawdown_scale": round(drawdown_scale, 3),
        "slippage": asdict(slip),
        "quality_score": quality_score,
        "grade": grade,
        "conditions": conditions,
    })
    trade_logger.log_decision(log_data)

    return EntryDecision(
        approved=True,
        reason="approved",
        lots=lots,
        sl_price=sl_price,
        target_price=target_price,
        adjusted_rr=adjusted_rr,
        slippage_estimate=slip,
        vix_scale=vix_scale,
        regime_scale=regime_scale,
        drawdown_scale=drawdown_scale,
        multiplier=lots / max(config.entry_lots, 1),
        log=log_data,
    )


# ============================================================================
# EXIT VALIDATION
# ============================================================================

def validate_exit(
    position: Any,
    current_price: float,
    context: Dict[str, Any],
    config: ScalpingConfig,
) -> ExitDecision:
    """Validate all exit conditions for a position.

    Returns ExitDecision with should_exit, reason, and urgency.
    Priority order: max_loss > sl_hit > momentum_reversal > spread > thesis > time_stop
    """
    # CRIT-6: Price=0 means broken data — track consecutive zeros, force exit after 3
    if current_price <= 0:
        pos_id = str(getattr(position, "position_id", ""))
        zero_key = f"_zero_price_count:{pos_id}"
        zero_count = int(context.get(zero_key, 0) or 0) + 1
        context[zero_key] = zero_count
        cycle_now = context.get("cycle_now", datetime.now())
        trade_logger.log_decision({
            "event": "exit_price_zero",
            "position": pos_id,
            "consecutive_zeros": zero_count,
            "timestamp": cycle_now.isoformat() if hasattr(cycle_now, "isoformat") else str(cycle_now),
        })
        if zero_count >= 3:
            last_known = float(getattr(position, "current_price", 0) or getattr(position, "entry_price", 0) or 0)
            return ExitDecision(
                should_exit=True, reason=ExitReason.KILL_SWITCH,
                exit_qty=int(getattr(position, "quantity", 0) or 0),
                exit_price=last_known,
                urgency=2,
            )
        return ExitDecision(should_exit=False)

    entry = float(getattr(position, "entry_price", 0) or 0)
    qty = int(getattr(position, "quantity", 0) or 0)
    sl = float(getattr(position, "sl_price", 0) or 0)
    symbol = str(getattr(position, "symbol", "") or "")
    option_type = str(getattr(position, "option_type", "") or "")
    partial_done = bool(getattr(position, "partial_exit_done", False))

    if entry <= 0 or qty <= 0:
        return ExitDecision(should_exit=False)

    unrealized = (current_price - entry) * qty

    # ── Check 1: Per-trade max loss (CRIT-3: include exit slippage buffer) ──
    max_loss = config.total_capital * config.risk_per_trade_pct / 100
    exit_slippage_buffer = entry * 0.01 * qty  # 1% conservative exit slippage
    if unrealized - exit_slippage_buffer < -max_loss:
        return ExitDecision(
            should_exit=True,
            reason=ExitReason.MAX_LOSS,
            exit_qty=qty,
            exit_price=current_price,
            urgency=2,
        )

    # ── Check 2: SL hit (CRIT-1: log gap-through events) ──
    if sl > 0 and current_price <= sl and not partial_done:
        gap_distance = sl - current_price
        if gap_distance > sl * 0.10:
            trade_logger.log_decision({
                "event": "sl_gap_through",
                "position": str(getattr(position, "position_id", "")),
                "expected_exit": sl,
                "actual_exit": current_price,
                "gap_pct": round(gap_distance / sl * 100, 2),
                "extra_loss": round(gap_distance * qty, 2),
                "timestamp": datetime.now().isoformat(),
            })
        return ExitDecision(
            should_exit=True,
            reason=ExitReason.SL_HIT,
            exit_qty=qty,
            exit_price=current_price,
            urgency=2,
        )

    # ── Check 3: Momentum reversal (index-specific threshold) ──
    momentum_signals = context.get("momentum_signals", [])
    idx_config = _resolve_idx(symbol)
    reversal_threshold = (idx_config.momentum_threshold * 1.2) if idx_config else 30
    for m in momentum_signals:
        if getattr(m, "symbol", "") != symbol:
            continue
        if getattr(m, "signal_type", "") != "futures_surge":
            continue
        if getattr(m, "strength", 0) < 0.8:
            continue
        price_move = float(getattr(m, "price_move", 0) or 0)
        if option_type == "PE" and price_move > reversal_threshold:
            return ExitDecision(
                should_exit=True, reason=ExitReason.MOMENTUM_REVERSAL,
                exit_qty=qty, exit_price=current_price, urgency=1,
            )
        if option_type == "CE" and price_move < -reversal_threshold:
            return ExitDecision(
                should_exit=True, reason=ExitReason.MOMENTUM_REVERSAL,
                exit_qty=qty, exit_price=current_price, urgency=1,
            )

    # ── Check 3b: Spread widening exit (H5) ──
    entry_spread_pct = float(getattr(position, "_entry_spread_pct", 0) or 0)
    current_spread_pct = float(context.get("current_spread_pct", {}).get(
        f"{symbol}|{getattr(position, 'strike', 0)}|{option_type}", 0) or 0)
    if entry_spread_pct > 0 and current_spread_pct > entry_spread_pct * 2.0 and unrealized < 0:
        return ExitDecision(
            should_exit=True, reason=ExitReason.SPREAD_EXIT,
            exit_qty=qty, exit_price=current_price, urgency=1,
        )

    # ── Check 4: Thesis invalidation (strength-based, not fixed delay) ──
    if not context.get("risk_blocked_new_entries") and not context.get("trade_disabled"):
        thesis_support = _check_thesis_support(position, context)
        if not thesis_support["supported"]:
            reversal_strength = float(thesis_support.get("reversal_strength", 0))
            if reversal_strength > 2.0:
                return ExitDecision(
                    should_exit=True, reason=ExitReason.THESIS_INVALIDATED,
                    exit_qty=qty, exit_price=current_price, urgency=1,
                )
            # For weaker reversals, caller should track streak and exit after 1-3 cycles

    # ── Check 5: Time stop (only losers, 30 min) ──
    entry_time = getattr(position, "entry_time", None)
    cycle_now = context.get("cycle_now", datetime.now())
    if isinstance(cycle_now, str):
        try:
            cycle_now = datetime.fromisoformat(cycle_now)
        except ValueError:
            cycle_now = datetime.now()
    if entry_time:
        age_minutes = (cycle_now - entry_time).total_seconds() / 60
        if age_minutes >= config.exit_time_stop_minutes and current_price < entry:
            return ExitDecision(
                should_exit=True, reason=ExitReason.TIME_STOP,
                exit_qty=qty, exit_price=current_price, urgency=0,
            )

    # ── Check 6: EOD exit ──
    if cycle_now.time() > dtime(15, 15):
        return ExitDecision(
            should_exit=True, reason=ExitReason.EOD,
            exit_qty=qty, exit_price=current_price, urgency=1,
        )

    return ExitDecision(should_exit=False)


# ============================================================================
# KILL SWITCH (Tightened thresholds)
# ============================================================================

class KillSwitch:
    """Real-time kill switch with institutional thresholds."""

    LATENCY_HALT_MS = 1000          # Was 5000
    SPOT_STALE_HALT_S = 3.0         # Was 15
    OPTION_STALE_HALT_S = 5.0       # Was 15
    FUTURES_STALE_HALT_S = 3.0      # Was 15
    VOLATILITY_HALT_PCT = 1.5       # Was 3.0
    CONSECUTIVE_LOSS_HALT = 2       # Was 4
    RAPID_DRAWDOWN_PCT = 3.0        # Was 5.0
    API_FAILURE_HALT = 1            # Was 3
    ORDER_ACK_TIMEOUT_S = 10.0
    VIX_HALT_THRESHOLD = 40.0

    def __init__(self) -> None:
        self.active = False
        self.reason = ""
        self.triggered_at: Optional[datetime] = None
        self.trigger_count = 0

    def check(self, context: Dict[str, Any], config: ScalpingConfig) -> Tuple[bool, str]:
        """Returns (should_halt, reason). Call every cycle."""

        # VIX extreme
        vix = float(context.get("vix", 15) or 15)
        if vix >= self.VIX_HALT_THRESHOLD:
            return self._trigger(f"vix_extreme:{vix:.1f}>={self.VIX_HALT_THRESHOLD}")

        # Spot data staleness
        spot_age = float(context.get("spot_data_age_seconds", 0) or 0)
        if spot_age > self.SPOT_STALE_HALT_S:
            return self._trigger(f"spot_stale:{spot_age:.1f}s>{self.SPOT_STALE_HALT_S}s")

        # API latency
        latency_ms = float(context.get("api_latency_ms", 0) or 0)
        if latency_ms > self.LATENCY_HALT_MS:
            return self._trigger(f"latency:{latency_ms:.0f}ms>{self.LATENCY_HALT_MS}ms")

        # Consecutive losses
        consecutive = int(context.get("consecutive_losses", 0) or 0)
        if consecutive >= self.CONSECUTIVE_LOSS_HALT:
            return self._trigger(f"consecutive_losses:{consecutive}>={self.CONSECUTIVE_LOSS_HALT}")

        # Rapid drawdown
        daily_pnl = float(context.get("daily_pnl", 0) or 0)
        daily_pct = abs(daily_pnl) / max(config.total_capital, 1) * 100
        if daily_pnl < 0 and daily_pct >= self.RAPID_DRAWDOWN_PCT:
            return self._trigger(f"rapid_drawdown:{daily_pct:.1f}%>={self.RAPID_DRAWDOWN_PCT}%")

        # Volatility spike
        vol_change = float(context.get("volatility_change_pct", 0) or 0)
        if abs(vol_change) >= self.VOLATILITY_HALT_PCT:
            return self._trigger(f"volatility_spike:{vol_change:.1f}%>={self.VOLATILITY_HALT_PCT}%")

        # API failures
        api_failures = int(context.get("api_failures", 0) or 0)
        if api_failures >= self.API_FAILURE_HALT:
            return self._trigger(f"api_failure:{api_failures}>={self.API_FAILURE_HALT}")

        return False, ""

    def _trigger(self, reason: str) -> Tuple[bool, str]:
        self.active = True
        self.reason = reason
        self.triggered_at = datetime.now()
        self.trigger_count += 1
        trade_logger.log_decision({
            "event": "kill_switch_triggered",
            "reason": reason,
            "trigger_count": self.trigger_count,
            "timestamp": datetime.now().isoformat(),
        })
        return True, reason

    COOLDOWN_SECONDS = 900  # 15 minutes minimum before reset

    def reset(self) -> bool:
        """Reset kill switch. Returns False if cooldown hasn't elapsed."""
        if self.triggered_at:
            elapsed = (datetime.now() - self.triggered_at).total_seconds()
            if elapsed < self.COOLDOWN_SECONDS:
                return False
        self.active = False
        self.reason = ""
        return True

    def force_reset(self) -> None:
        """Force reset (manual override only)."""
        self.active = False
        self.reason = ""


# ============================================================================
# STRUCTURED LOGGING
# ============================================================================

class TradeLogger:
    """Structured JSON logger for every trading decision."""

    def __init__(self, log_file: str = "logs/trade_decisions.jsonl") -> None:
        self._file_path = log_file
        self._buffer: List[Dict[str, Any]] = []

    def log_decision(self, data: Dict[str, Any]) -> None:
        """Log a trade decision as structured JSON."""
        record = {
            "ts": data.get("timestamp", datetime.now().isoformat()),
            **data,
        }
        self._buffer.append(record)
        try:
            logger.info(json.dumps(record, default=str))
        except Exception:
            pass

        # Flush every 10 records
        if len(self._buffer) >= 10:
            self.flush()

    def flush(self) -> None:
        """Write buffered records to disk."""
        if not self._buffer:
            return
        try:
            import os
            os.makedirs(os.path.dirname(self._file_path) or ".", exist_ok=True)
            with open(self._file_path, "a") as f:
                for record in self._buffer:
                    f.write(json.dumps(record, default=str) + "\n")
            self._buffer.clear()
        except Exception:
            pass

    def get_recent(self, n: int = 50) -> List[Dict[str, Any]]:
        return list(self._buffer[-n:])


# Singleton
trade_logger = TradeLogger()


# ============================================================================
# INTERNAL HELPERS
# ============================================================================

def _resolve_idx(symbol: str) -> Optional[IndexConfig]:
    """Resolve IndexConfig from symbol string."""
    normalized = str(symbol or "").upper()
    for idx_type in IndexType:
        cfg = get_index_config(idx_type)
        if normalized in (str(idx_type.value).upper(), str(cfg.symbol).upper()):
            return cfg
        if normalized.startswith(str(cfg.option_prefix).upper()):
            return cfg
    return None


def _compute_hourly_pnl(context: Dict[str, Any]) -> float:
    """Compute P&L over last 2 hours from trade records."""
    trades = context.get("executed_trades", context.get("recent_trades", []))
    if not trades:
        return float(context.get("daily_pnl", 0) or 0)

    # HIGH-4: Use cycle_now, not wall clock (for replay compatibility)
    now = context.get("cycle_now", datetime.now())
    if isinstance(now, str):
        try:
            now = datetime.fromisoformat(now)
        except ValueError:
            now = datetime.now()
    cutoff = now - timedelta(hours=2)
    recent_pnl = 0.0
    for t in trades:
        if not isinstance(t, dict):
            continue
        exit_time = t.get("exit_time")
        if exit_time:
            if isinstance(exit_time, str):
                try:
                    exit_time = datetime.fromisoformat(exit_time)
                except ValueError:
                    continue
            if exit_time >= cutoff:
                recent_pnl += float(t.get("realized_pnl", 0) or 0)

    # Add unrealized from open positions
    recent_pnl += float(context.get("unrealized_pnl", 0) or 0)
    return recent_pnl


def _check_thesis_support(position: Any, context: Dict[str, Any]) -> Dict[str, Any]:
    """Check if position's thesis is still supported by current signals."""
    symbol = getattr(position, "symbol", "")
    strike = getattr(position, "strike", 0)
    option_type = getattr(position, "option_type", "")

    selections = context.get("strike_selections", {})
    quality = context.get("quality_filtered_signals", [])

    # Check if still in selections
    sym_selections = selections.get(symbol, []) if isinstance(selections, dict) else []
    in_selections = any(
        (getattr(s, "strike", s.get("strike", 0) if isinstance(s, dict) else 0) == strike
         and (getattr(s, "option_type", s.get("option_type", "") if isinstance(s, dict) else "") == option_type))
        for s in sym_selections
    )

    in_quality = any(
        (s.get("strike", 0) == strike and s.get("option_type", "") == option_type)
        for s in quality if isinstance(s, dict) and s.get("symbol", "") == symbol
    )

    supported = in_selections or in_quality

    # Compute reversal strength from momentum (HIGH-5: index-specific denominator)
    momentum = context.get("momentum_signals", [])
    reversal_strength = 0.0
    idx_cfg = _resolve_idx(symbol)
    reversal_norm = float(idx_cfg.momentum_threshold) if idx_cfg else 25.0
    for m in momentum:
        if getattr(m, "symbol", "") != symbol:
            continue
        if getattr(m, "signal_type", "") != "futures_surge":
            continue
        move = float(getattr(m, "price_move", 0) or 0)
        strength = float(getattr(m, "strength", 0) or 0)
        if option_type == "CE" and move < 0:
            reversal_strength = max(reversal_strength, strength * abs(move) / reversal_norm)
        if option_type == "PE" and move > 0:
            reversal_strength = max(reversal_strength, strength * abs(move) / reversal_norm)

    return {
        "supported": supported,
        "in_selections": in_selections,
        "in_quality": in_quality,
        "reversal_strength": reversal_strength,
    }

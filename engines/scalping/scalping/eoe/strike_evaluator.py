"""Strike evaluator for EOE — finds best cheap option candidate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class StrikeCandidate:
    strike: int
    option_type: str
    premium: float
    bid: float
    ask: float
    spread_pct: float
    bid_qty: int
    ask_qty: int
    otm_distance: int
    tradable: bool
    tradable_reason: str


MIN_PREMIUM = 2.0
MAX_PREMIUM = 15.0
MIN_OTM = 100
MAX_OTM = 600
MAX_ITM = 100


def evaluate_strikes(
    option_chain: Any,
    spot: float,
    reversal_direction: str,
    lot_size: int = 10,
) -> List[StrikeCandidate]:
    """Evaluate option chain for EOE-eligible strikes.

    Args:
        option_chain: OptionChainData object with .options list
        spot: current underlying LTP
        reversal_direction: "bullish" (look at CE) or "bearish" (look at PE)
        lot_size: index lot size for depth check

    Returns sorted list of candidates (best first).
    """
    if not option_chain or not hasattr(option_chain, "options"):
        return []

    target_type = "CE" if reversal_direction == "bullish" else "PE"
    candidates = []

    for opt in option_chain.options:
        if str(getattr(opt, "option_type", "")).upper() != target_type:
            continue

        strike = int(getattr(opt, "strike", 0) or 0)
        premium = float(getattr(opt, "ltp", 0) or 0)
        bid = float(getattr(opt, "bid", 0) or 0)
        ask = float(getattr(opt, "ask", 0) or 0)
        volume = int(getattr(opt, "volume", 0) or 0)
        oi = int(getattr(opt, "oi", 0) or 0)

        if premium <= 0:
            continue

        # Distance from spot
        if target_type == "CE":
            otm_distance = strike - spot
        else:
            otm_distance = spot - strike

        # Allow slight ITM (up to MAX_ITM)
        if otm_distance < -MAX_ITM:
            continue
        if otm_distance > MAX_OTM:
            continue

        spread = max(0, ask - bid) if ask > 0 and bid > 0 else premium * 0.3
        spread_pct = (spread / premium * 100) if premium > 0 else 100.0
        bid_qty = max(1, volume // 10)  # Rough depth estimate from volume
        ask_qty = max(1, int(bid_qty * 0.6))

        # Tradability
        tradable = True
        reason = "ok"
        if premium < MIN_PREMIUM:
            tradable = False
            reason = f"premium_{premium:.1f}<{MIN_PREMIUM}"
        elif premium > MAX_PREMIUM:
            tradable = False
            reason = f"premium_{premium:.1f}>{MAX_PREMIUM}"
        elif spread_pct > 30:
            tradable = False
            reason = f"spread_{spread_pct:.0f}%>30%"
        elif bid_qty < 3 * lot_size:
            tradable = False
            reason = f"depth_{bid_qty}<{3*lot_size}"

        candidates.append(StrikeCandidate(
            strike=strike,
            option_type=target_type,
            premium=round(premium, 2),
            bid=round(bid, 2),
            ask=round(ask, 2),
            spread_pct=round(spread_pct, 1),
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            otm_distance=int(otm_distance),
            tradable=tradable,
            tradable_reason=reason,
        ))

    # Sort: tradable first, then by premium (sweet spot ₹3-8 preferred)
    def score(c: StrikeCandidate) -> float:
        s = 0.0
        if c.tradable:
            s += 100
        if 3 <= c.premium <= 8:
            s += 50  # Sweet spot
        elif 2 <= c.premium <= 15:
            s += 20
        if c.spread_pct <= 20:
            s += 10
        return s

    candidates.sort(key=score, reverse=True)
    return candidates


def best_tradable(candidates: List[StrikeCandidate]) -> Optional[StrikeCandidate]:
    """Return the best tradable candidate, or None."""
    for c in candidates:
        if c.tradable:
            return c
    return None

"""Tradability checks for EOE candidates."""

from __future__ import annotations


def is_tradable(
    premium: float,
    spread_pct: float,
    bid_qty: int,
    ask_qty: int,
    lot_size: int = 10,
    min_premium: float = 2.0,
    max_premium: float = 15.0,
    max_spread_pct: float = 30.0,
    min_depth_multiple: int = 3,
) -> bool:
    """Check if an option is tradable for EOE purposes."""
    if premium < min_premium or premium > max_premium:
        return False
    if spread_pct > max_spread_pct:
        return False
    if bid_qty < min_depth_multiple * lot_size:
        return False
    if ask_qty < min_depth_multiple * lot_size:
        return False
    return True


def spread_trap(
    entry_spread_pct: float,
    entry_premium: float,
    exit_spread_pct: float,
    exit_premium: float,
    threshold: float = 0.30,
) -> bool:
    """Check if round-trip spread cost exceeds threshold of profit."""
    entry_cost = entry_spread_pct / 100 * entry_premium / 2
    exit_cost = exit_spread_pct / 100 * exit_premium / 2
    profit = exit_premium - entry_premium
    if profit <= 0:
        return False  # No profit to trap
    return (entry_cost + exit_cost) / profit > threshold

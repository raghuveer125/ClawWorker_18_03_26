"""
Strategy Seeder - Initialize trade memory with real trading strategies.

Seeds:
- ICT-based strategies (OB, FVG, Liquidity)
- Mean reversion strategies
- Trend following strategies
- Momentum strategies
"""

import uuid
from datetime import datetime, timedelta
from typing import List
import random

from .trade_memory import (
    TradeMemory,
    TradeRecord,
    StrategyInsight,
    get_trade_memory,
)


# Strategy definitions with their characteristics
STRATEGIES = {
    # ICT Strategies (Inner Circle Trader)
    "ict_order_block": {
        "name": "ICT Order Block",
        "description": "Trade from institutional order blocks (supply/demand zones)",
        "category": "ict",
        "best_regimes": ["trending_up", "trending_down"],
        "expected_win_rate": 0.55,
        "avg_rr": 2.5,
    },
    "ict_fvg": {
        "name": "ICT Fair Value Gap",
        "description": "Enter on fair value gap fills during trends",
        "category": "ict",
        "best_regimes": ["trending_up", "trending_down"],
        "expected_win_rate": 0.52,
        "avg_rr": 2.0,
    },
    "ict_liquidity_sweep": {
        "name": "ICT Liquidity Sweep",
        "description": "Fade liquidity grabs at key levels",
        "category": "ict",
        "best_regimes": ["ranging", "volatile"],
        "expected_win_rate": 0.48,
        "avg_rr": 3.0,
    },
    "ict_breaker": {
        "name": "ICT Breaker Block",
        "description": "Trade breaker blocks after structure breaks",
        "category": "ict",
        "best_regimes": ["trending_up", "trending_down"],
        "expected_win_rate": 0.50,
        "avg_rr": 2.2,
    },

    # Mean Reversion
    "mean_rev_bb": {
        "name": "Bollinger Band Mean Reversion",
        "description": "Fade moves to Bollinger Band extremes",
        "category": "mean_reversion",
        "best_regimes": ["ranging", "low_volatility"],
        "expected_win_rate": 0.60,
        "avg_rr": 1.2,
    },
    "mean_rev_rsi": {
        "name": "RSI Oversold/Overbought",
        "description": "Fade RSI extremes with confirmation",
        "category": "mean_reversion",
        "best_regimes": ["ranging"],
        "expected_win_rate": 0.58,
        "avg_rr": 1.5,
    },
    "mean_rev_vwap": {
        "name": "VWAP Reversion",
        "description": "Trade reversion to VWAP after extended moves",
        "category": "mean_reversion",
        "best_regimes": ["ranging", "trending_up"],
        "expected_win_rate": 0.55,
        "avg_rr": 1.3,
    },

    # Trend Following
    "trend_ma_cross": {
        "name": "Moving Average Crossover",
        "description": "Enter on 20/50 EMA crossovers",
        "category": "trend",
        "best_regimes": ["trending_up", "trending_down"],
        "expected_win_rate": 0.42,
        "avg_rr": 3.5,
    },
    "trend_breakout": {
        "name": "Range Breakout",
        "description": "Trade breakouts from consolidation",
        "category": "trend",
        "best_regimes": ["volatile", "trending_up"],
        "expected_win_rate": 0.38,
        "avg_rr": 4.0,
    },
    "trend_pullback": {
        "name": "Trend Pullback",
        "description": "Enter on pullbacks to moving averages in trends",
        "category": "trend",
        "best_regimes": ["trending_up", "trending_down"],
        "expected_win_rate": 0.52,
        "avg_rr": 2.0,
    },

    # Momentum
    "momentum_orb": {
        "name": "Opening Range Breakout",
        "description": "Trade breakouts from first 15min range",
        "category": "momentum",
        "best_regimes": ["volatile", "trending_up"],
        "expected_win_rate": 0.45,
        "avg_rr": 2.5,
    },
    "momentum_gap": {
        "name": "Gap Trading",
        "description": "Trade gap fills or continuations",
        "category": "momentum",
        "best_regimes": ["volatile"],
        "expected_win_rate": 0.50,
        "avg_rr": 1.8,
    },
}


def seed_strategies(memory: TradeMemory = None) -> dict:
    """
    Seed the trade memory with strategy definitions and sample trades.

    Returns summary of seeded data.
    """
    if memory is None:
        memory = get_trade_memory()

    seeded = {
        "strategies": 0,
        "trades": 0,
        "insights": 0,
    }

    regimes = ["trending_up", "trending_down", "ranging", "volatile", "low_volatility"]

    for strategy_id, config in STRATEGIES.items():
        # Add insights for each strategy
        insight = StrategyInsight(
            strategy=strategy_id,
            insight_type="regime_fit",
            description=f"{config['name']}: Best in {', '.join(config['best_regimes'])}",
            confidence=0.7,
            evidence={
                "category": config["category"],
                "expected_win_rate": config["expected_win_rate"],
                "avg_rr": config["avg_rr"],
                "best_regimes": config["best_regimes"],
            },
        )
        memory.add_insight(insight)
        seeded["insights"] += 1

        # Generate sample trades for each regime
        for regime in regimes:
            # Adjust performance based on regime fit
            if regime in config["best_regimes"]:
                win_rate = config["expected_win_rate"] + 0.05
                multiplier = 1.2
            else:
                win_rate = config["expected_win_rate"] - 0.10
                multiplier = 0.7

            # Generate 10-20 trades per regime
            num_trades = random.randint(10, 20)

            for i in range(num_trades):
                # Random time in last 30 days
                days_ago = random.randint(1, 30)
                entry_time = (datetime.now() - timedelta(days=days_ago)).isoformat()
                exit_time = (datetime.now() - timedelta(days=days_ago - 0.1)).isoformat()

                # Simulate trade outcome
                is_win = random.random() < win_rate
                direction = random.choice(["long", "short"])

                entry_price = 22000 + random.uniform(-500, 500)  # NIFTY range

                if is_win:
                    pnl_pct = random.uniform(0.5, 3.0) * multiplier
                else:
                    pnl_pct = -random.uniform(0.3, 1.5)

                if direction == "long":
                    exit_price = entry_price * (1 + pnl_pct / 100)
                else:
                    exit_price = entry_price * (1 - pnl_pct / 100)

                quantity = random.randint(50, 200)
                pnl = (exit_price - entry_price) * quantity if direction == "long" else (entry_price - exit_price) * quantity

                trade = TradeRecord(
                    trade_id=f"{strategy_id}_{regime}_{uuid.uuid4().hex[:8]}",
                    symbol="NSE:NIFTY50-INDEX",
                    direction=direction,
                    entry_time=entry_time,
                    exit_time=exit_time,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=quantity,
                    strategy=strategy_id,
                    regime=regime,
                    setup={
                        "signal_strength": random.uniform(0.5, 1.0),
                        "confluence_count": random.randint(1, 4),
                    },
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    status="closed",
                )
                memory.record_trade(trade)
                seeded["trades"] += 1

        seeded["strategies"] += 1

    return seeded


def get_strategy_list() -> List[dict]:
    """Get list of all available strategies with their configs."""
    return [
        {"id": k, **v}
        for k, v in STRATEGIES.items()
    ]


def get_strategies_by_category(category: str) -> List[dict]:
    """Get strategies filtered by category."""
    return [
        {"id": k, **v}
        for k, v in STRATEGIES.items()
        if v["category"] == category
    ]


def get_strategies_for_regime(regime: str) -> List[dict]:
    """Get strategies that work well in a given regime."""
    return [
        {"id": k, **v}
        for k, v in STRATEGIES.items()
        if regime in v["best_regimes"]
    ]


if __name__ == "__main__":
    # Run seeder directly
    print("Seeding strategies...")
    result = seed_strategies()
    print(f"Seeded: {result}")

    # Show summary
    memory = get_trade_memory()
    summary = memory.get_summary()
    print(f"\nTrade Memory Summary: {summary}")

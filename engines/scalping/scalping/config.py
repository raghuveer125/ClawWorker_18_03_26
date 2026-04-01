"""
Scalping Configuration - Index-specific parameters and thresholds.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum


class IndexType(Enum):
    NIFTY50 = "NSE:NIFTY50-INDEX"
    BANKNIFTY = "NSE:NIFTYBANK-INDEX"
    SENSEX = "BSE:SENSEX-INDEX"
    FINNIFTY = "NSE:FINNIFTY-INDEX"
    MIDCPNIFTY = "NSE:MIDCPNIFTY-INDEX"


@dataclass
class IndexConfig:
    """Configuration for a specific index."""
    symbol: str
    futures_symbol: str
    option_prefix: str
    lot_size: int
    tick_size: float

    # Strike selection
    strike_interval: int  # Points between strikes
    otm_distance_min: int  # Min OTM distance in points
    otm_distance_max: int  # Max OTM distance in points

    # Momentum thresholds (index-specific)
    momentum_threshold: int  # Points move to trigger
    volume_spike_multiplier: float  # Volume spike threshold

    # Premium targets
    premium_min: float  # Min option price (₹)
    premium_max: float  # Max option price (₹)
    delta_min: float
    delta_max: float


# Pre-configured index settings
INDEX_CONFIGS: Dict[IndexType, IndexConfig] = {
    IndexType.NIFTY50: IndexConfig(
        symbol="NSE:NIFTY50-INDEX",
        futures_symbol="NSE:NIFTY-FUT",
        option_prefix="NSE:NIFTY",
        lot_size=25,  # Updated lot size
        tick_size=0.05,
        strike_interval=50,
        otm_distance_min=150,
        otm_distance_max=300,
        momentum_threshold=25,
        volume_spike_multiplier=5.0,
        premium_min=10.0,
        premium_max=25.0,
        delta_min=0.15,
        delta_max=0.25,
    ),
    IndexType.BANKNIFTY: IndexConfig(
        symbol="NSE:NIFTYBANK-INDEX",
        futures_symbol="NSE:BANKNIFTY-FUT",
        option_prefix="NSE:BANKNIFTY",
        lot_size=15,
        tick_size=0.05,
        strike_interval=100,
        otm_distance_min=300,
        otm_distance_max=600,
        momentum_threshold=50,
        volume_spike_multiplier=5.0,
        premium_min=10.0,
        premium_max=30.0,
        delta_min=0.12,
        delta_max=0.22,
    ),
    IndexType.SENSEX: IndexConfig(
        symbol="BSE:SENSEX-INDEX",
        futures_symbol="BSE:SENSEX-FUT",
        option_prefix="BSE:SENSEX",
        lot_size=10,
        tick_size=0.05,
        strike_interval=100,
        otm_distance_min=400,
        otm_distance_max=800,
        momentum_threshold=80,
        volume_spike_multiplier=4.0,
        premium_min=10.0,
        premium_max=25.0,
        delta_min=0.15,
        delta_max=0.25,
    ),
}


@dataclass
class ScalpingConfig:
    """Master configuration for the scalping system."""

    # Active indices
    indices: List[IndexType] = field(default_factory=lambda: [
        IndexType.NIFTY50,
        IndexType.BANKNIFTY,
        IndexType.SENSEX,
    ])

    # Capital Management
    total_capital: float = 100000.0  # Total trading capital
    risk_per_trade_pct: float = 5.0  # Max risk per trade
    daily_loss_limit_pct: float = 10.0  # Daily loss limit
    max_positions: int = 3  # Max concurrent positions
    max_symbol_exposure_pct: float = 40.0  # Max exposure in a single index/symbol
    max_consecutive_losses: int = 3  # Pause after cluster of losses

    # Entry Rules
    entry_lots: int = 4  # Lots per entry (4-6)
    require_structure_break: bool = True
    require_futures_confirm: bool = True
    require_volume_burst: bool = True
    require_trap_confirm: bool = False  # Optional
    max_entry_slippage_pct: float = 2.0
    max_bid_ask_drift_pct: float = 1.0
    max_spread_widening_ratio: float = 1.5
    execution_loop_interval_ms: int = 300
    micro_momentum_window_ticks: int = 3
    micro_imbalance_threshold: float = 0.10
    micro_spread_cancel_ratio: float = 1.2
    micro_signal_confirmation_ttl_seconds: float = 6.0
    entry_confirmation_window_ms: int = 500
    entry_confirmation_window_min_ms: int = 300
    entry_confirmation_window_max_ms: int = 700
    price_reversal_pct_threshold: float = 0.4
    liquidity_vacuum_drop_threshold: float = 0.40
    liquidity_vacuum_window_ms: int = 200
    adaptive_entry_strong_momentum: float = 0.75
    adaptive_entry_moderate_momentum: float = 0.45
    queue_risk_ratio_threshold: float = 3.0
    queue_risk_reduce_threshold: float = 1.8
    volatility_burst_vol_threshold: float = 0.012
    volatility_burst_spread_threshold: float = 0.25
    volatility_burst_stop_scale: float = 0.85
    volatility_burst_fast_track: bool = True
    replay_min_rr_ratio: float = 1.0
    replay_require_market_conditions: bool = True
    strict_a_plus_only: bool = False
    strict_a_plus_rr_ratio: float = 1.3
    strict_b_rr_ratio: float = 1.1
    strict_a_plus_size_fraction: float = 0.65
    strict_b_size_fraction: float = 0.35
    correlation_high_risk_size_scale: float = 0.5
    correlation_medium_risk_size_scale: float = 0.75
    dealer_extreme_pinning_score: float = 0.85

    # Exit Rules - Partial Exit (Capital Protection)
    partial_exit_pct: float = 0.55  # Exit 55% at first target
    first_target_points: float = 4.0  # ₹3-5 profit
    move_sl_to_entry: bool = True  # After partial exit
    exit_time_stop_minutes: int = 30  # Only exits losing positions after this duration
    exit_spread_widening_pct: float = 8.0
    profit_lock_trigger_points: float = 6.0
    profit_lock_buffer_points: float = 2.0

    # Runner Management
    trail_method: str = "candle_hl"  # candle_hl, vwap, atr
    runner_target_min: float = 8.0  # Min target for runner
    runner_target_max: float = 15.0  # Normal target
    runner_moon_target: float = 80.0  # Rare explosive move

    # Market Structure
    structure_timeframes: List[str] = field(default_factory=lambda: ["1m", "3m", "5m"])
    vwap_deviation_threshold: float = 0.5  # % deviation from VWAP

    # Momentum Detection
    option_expansion_threshold: float = 0.15  # 15% expansion trigger
    gamma_zone_delta_range: tuple = (0.40, 0.60)  # Near ATM

    # Option Filtering
    max_bid_ask_spread_pct: float = 5.0  # Max spread %
    min_volume_threshold: int = 1000  # Min option volume
    min_oi_threshold: int = 5000  # Min OI
    high_vix_level: float = 25.0
    low_vix_level: float = 15.0
    high_vix_otm_scale: float = 0.8
    low_vix_otm_scale: float = 1.15
    vol_surface_iv_lookback: int = 200
    vol_surface_realized_window: int = 20
    high_realized_vol_level: float = 0.012
    dealer_pin_proximity_pct: float = 0.15
    dealer_short_gamma_boost: float = 1.05
    dealer_long_gamma_penalty: float = 0.90

    # Trap Detection
    oi_buildup_threshold: float = 1.5  # 50% OI increase
    pcr_spike_threshold: float = 0.3  # PCR change
    bid_ask_imbalance_ratio: float = 2.0  # Bid >> Ask

    # Risk Controls
    disable_low_liquidity: bool = True
    disable_high_spread: bool = True
    max_spread_to_trade: float = 3.0  # ₹
    late_entry_cutoff_time: str = "14:50"
    thesis_invalidation_cycles: int = 3
    spot_stale_threshold_seconds: float = 15.0
    option_stale_threshold_seconds: float = 15.0
    futures_stale_threshold_seconds: float = 15.0
    tick_heartbeat_threshold_seconds: float = 15.0
    trading_hours: tuple = ("09:20", "15:15")  # IST
    no_trade_zones: List[tuple] = field(default_factory=lambda: [
        ("09:15", "09:20"),  # Opening chaos
        ("15:20", "15:30"),  # Closing
    ])
    engine_watchdog_factor: float = 6.0  # Cycles take 10-25s with 3 indices; only trip on anomalous overruns

    # Learning Parameters
    log_all_signals: bool = True
    min_trades_for_learning: int = 50
    backtest_lookback_days: int = 30
    learning_mode_default: str = "hybrid"  # off | daily | hybrid | immediate
    learning_intraday_min_multiplier: float = 0.95
    learning_intraday_max_multiplier: float = 1.05

    # LLM Debate Integration
    # LLM Debate Configuration
    # Only use debate in learning layers to avoid execution latency
    use_debate_for_unclear: bool = True  # Master switch
    debate_in_execution_path: bool = False  # DISABLED - causes 15s+ latency
    debate_in_learning_only: bool = True  # Recommended: only Layer 17-18
    debate_confidence_threshold: float = 0.6


def get_index_config(index: IndexType) -> IndexConfig:
    """Get configuration for a specific index."""
    return INDEX_CONFIGS.get(index, INDEX_CONFIGS[IndexType.NIFTY50])


def get_default_config() -> ScalpingConfig:
    """Get default scalping configuration."""
    return ScalpingConfig()

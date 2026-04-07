"""
Ensemble Configuration - Institutional Grade Trading Parameters

Pure data class with no external dependencies.
"""

from dataclasses import dataclass


@dataclass
class EnsembleConfig:
    """Configuration for ensemble decision making - Institutional Grade"""
    # Consensus requirements (AI-optimized based on log analysis)
    # Principle: "Quality over quantity - 2 agreeing experts beat 3 uncertain ones"
    min_consensus: float = 0.33         # Minimum % of bots agreeing (2/6 bots)
    min_confidence: float = 40          # Minimum weighted confidence (lowered from 55: regime weights + MTF CONFLICTING -10 + DL -5 reduces actual scores to 40-45%; calibrated to live output)
    min_bots_required: int = 2          # Minimum quality bots (kept at 2 for safety)
    min_signal_strength: float = 40     # Minimum individual signal (AI-optimized from 50)
    high_conviction_threshold: float = 60  # Minimum conviction for capital preservation (lowered from 62: live peaks are BANKNIFTY 61.5%, SENSEX 47.6%, NIFTY50 46.4%; 60 clears BANKNIFTY while SENSEX/NIFTY50 remain blocked until scores improve)

    # Risk management
    max_daily_trades: int = 100         # Maximum trades per day
    max_concurrent_positions: int = 3   # Maximum open positions
    max_daily_loss: float = 5000        # Maximum daily loss (INR)
    max_per_trade_risk: float = 1000    # Maximum risk per trade

    # Institutional rules
    enforce_time_filters: bool = True   # Respect market session rules
    enforce_expiry_rules: bool = True   # Special expiry day rules
    use_deep_learning: bool = True      # Use pattern recognition
    use_regime_detection: bool = True   # Adapt to market regime

    # Weighting
    weight_by_performance: bool = True  # Use performance-based weighting
    weight_by_regime: bool = True       # Adjust weights based on regime

    # LLM Veto Layer (capital protection)
    use_veto_layer: bool = False        # Disabled: OpenAI API key invalid (401), rejects all signals on error
    veto_model: str = "gpt-4o-mini"     # Model for veto decisions

    # Multi-Timeframe Analysis (loss reduction)
    use_mtf_filter: bool = True         # Enable 15m/5m/1m timeframe alignment
    mtf_strict_mode: bool = False       # Disabled - use balanced mode instead
    mtf_mode: str = "balanced"          # BALANCED: Block STRONG trends, penalize weak trends
    use_adaptive_risk: bool = True      # Enable adaptive learning and circuit breakers

    # Parameter Optimizer (self-tuning strategy parameters)
    use_parameter_optimizer: bool = True  # Enable automated parameter optimization
    optimization_every_n_trades: int = 20  # Run optimization after N completed trades

    # Institutional Risk Layer (Hedge Fund Grade - PREVENTION over REACTION)
    use_institutional_layer: bool = True  # Enable hedge fund grade risk intelligence

    # Capital Allocation Engine (Multi-Strategy Hedge Fund Grade)
    use_capital_allocator: bool = True    # Enable institutional capital allocation
    total_capital: float = 100000         # Total trading capital
    max_daily_loss_pct: float = 0.02      # 2% max daily loss

    # Model Drift Detection (Model Risk Governance)
    use_drift_detector: bool = True       # Enable model drift monitoring
    auto_quarantine: bool = True          # Auto-quarantine drifting models

    # Execution Engine (Market Impact & Slippage Intelligence)
    use_execution_engine: bool = True     # Enable smart execution
    max_slippage_pct: float = 0.01        # 1% max slippage tolerance

    # Trade-Triggered Backtest Validation (Online Learning Safety Net)
    enable_backtest_validation: bool = True   # Enable trade-triggered backtest validation
    backtest_every_n_trades: int = 20         # Run backtest after N completed trades per bot
    backtest_validation_days: int = 7         # Backtest window (days)
    backtest_min_improvement: float = 0.02    # Min improvement to keep learned params (2%)
    backtest_revert_on_failure: bool = True   # Revert params if backtest fails

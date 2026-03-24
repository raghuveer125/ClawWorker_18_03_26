"""
Ensemble Coordinator - Institutional-Grade Multi-Bot Trading System

Integrates:
- 5 specialized trading bots + 1 ML bot (auto-activates after training)
- LLM Trading Bot (TRUE AI reasoning)
- LLM Veto Layer (reviews and filters all signals before execution)
- Institutional trading rules (time filters, risk management)
- Deep learning for pattern recognition
- Market regime detection
- Persistent learning (all data on disk)

Philosophy: "Trade like a 65-year veteran institutional trader"
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import json
from pathlib import Path
import hashlib
import logging

logger = logging.getLogger(__name__)

from .base import (
    TradingBot, BotSignal, BotDecision, TradeRecord, SharedMemory,
    SignalType, OptionType, BotPerformance, get_strike_gap
)
from .trend_follower import TrendFollowerBot
from .reversal_hunter import ReversalHunterBot
from .momentum_scalper import MomentumScalperBot
from .oi_analyst import OIAnalystBot
from .volatility_trader import VolatilityTraderBot
from .ict_sniper import ICTSniperBot
from .regime_hunter import RegimeHunterBot
from .deep_learning import DeepLearningEngine, TradeContext
from .regime_detector import RegimeDetector, MarketRegime
from .ml_features import MLFeatureExtractor
from .ml_bot import MLTradingBot
from .llm_veto import LLMVetoLayer
from .multi_timeframe import MultiTimeframeEngine, TimeframeAlignment
from .adaptive_risk_controller import AdaptiveRiskController, TradeOutcome, RiskLevel
from .parameter_optimizer import ParameterOptimizer
from .institutional_risk_layer import InstitutionalRiskLayer, TradingCondition, MarketRegime as InstRegime
from .capital_allocator import InstitutionalCapitalAllocator, StrategySleeve, CapitalDecision
from .model_drift_detector import ModelDriftDetector, ModelStatus, DriftSeverity
from .execution_engine import ExecutionEngine, ExecutionStrategy, LiquidityLevel


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


class EnsembleCoordinator:
    """
    Institutional-Grade Multi-Bot Trading Coordinator

    Decision Flow:
    1. GATE: Check institutional rules (time, risk, expiry)
    2. REGIME: Detect market regime and adjust bot weights
    3. SIGNALS: Collect weighted signals from all bots
    4. PATTERNS: Check deep learning for historical patterns
    5. VETO: LLM reviews and filters signals (capital protection)
    6. CONSENSUS: Calculate weighted consensus
    7. VALIDATE: Final validation against risk limits
    8. EXECUTE: Generate decision with full context
    9. LEARN: Record everything for pattern discovery
    """

    def __init__(
        self,
        shared_memory: Optional[SharedMemory] = None,
        config: Optional[EnsembleConfig] = None
    ):
        self.memory = shared_memory or SharedMemory()
        self.config = config or EnsembleConfig()
        self.capital_preservation_mode = True
        self.runtime_overrides_file = self.memory.data_dir / "ensemble_runtime_overrides.json"
        self.runtime_overrides: Dict[str, Any] = {}
        self._apply_runtime_overrides()

        # Initialize all bots with shared memory
        self.bots: List[TradingBot] = [
            TrendFollowerBot(self.memory),
            ReversalHunterBot(self.memory),
            MomentumScalperBot(self.memory),
            OIAnalystBot(self.memory),
            VolatilityTraderBot(self.memory),
            ICTSniperBot(self.memory),  # NEW: ICT Sniper Strategy
            RegimeHunterBot(self.memory),  # Regime transition detector from forensic analysis
        ]
        self.bot_map = {bot.name: bot for bot in self.bots}

        # Apply backtest-optimized initial weights
        # Based on backtest results: TrendFollower 67%, OIAnalyst 61%, MomentumScalper 100%,
        # VolatilityTrader 40%, ReversalHunter 22%
        backtest_weights = {
            "TrendFollower": 1.5,      # Strong performer: 67% win rate, +18K P&L
            "OIAnalyst": 1.4,          # Strong performer: 61% win rate, +20K P&L
            "MomentumScalper": 1.8,    # Best performer: 100% win rate, +3.6K P&L
            "VolatilityTrader": 0.3,   # Needs tuning: 40% win rate, -1K P&L
            "ReversalHunter": 0.0,     # DISABLED: 22% win rate consistently loses money
            "ICTSniper": 1.2,          # NEW: ICT Sniper - High-quality setups, starts active
            "RegimeHunter": 1.3,       # Regime shift detector - high conviction entries only
        }
        for bot in self.bots:
            if bot.name in backtest_weights:
                bot.performance.weight = backtest_weights[bot.name]

        # Track disabled bots (weight=0 means disabled)
        self.disabled_bots = {"ReversalHunter"}  # Disabled due to 22% win rate

        # Capital Preservation Mode (prioritizes not losing over making money)

        # Initialize ML bot (6th bot - auto-activates when trained)
        self.ml_bot = MLTradingBot(str(self.memory.data_dir))
        self.ml_bot_active = self.ml_bot.is_trained

        # Initialize LLM bot (7th bot - TRUE AI reasoning, optional)
        try:
            from .llm_trading_bot import LLMTradingBot
            self.llm_bot = LLMTradingBot(self.memory)
            self.llm_bot_active = self.llm_bot.enabled
            if self.llm_bot_active:
                print("[Ensemble] LLM Trading Bot enabled - TRUE AI reasoning active")
        except Exception as e:
            print(f"[Ensemble] LLM Trading Bot not available: {e}")
            self.llm_bot = None
            self.llm_bot_active = False

        # Initialize LLM Veto Layer (capital protection)
        if self.config.use_veto_layer:
            try:
                self.veto_layer = LLMVetoLayer(model=self.config.veto_model)
                self.veto_active = self.veto_layer.enabled
                if self.veto_active:
                    print("[Ensemble] LLM Veto Layer enabled - capital protection active")
            except Exception as e:
                print(f"[Ensemble] LLM Veto Layer not available: {e}")
                self.veto_layer = None
                self.veto_active = False
        else:
            self.veto_layer = None
            self.veto_active = False

        # Initialize Multi-Timeframe Engine (loss reduction)
        if self.config.use_mtf_filter:
            self.mtf_engine = MultiTimeframeEngine()
            self.mtf_engine.set_mode(self.config.mtf_mode)
            self.mtf_active = True
            print(f"[Ensemble] Multi-Timeframe Filter enabled ({self.config.mtf_mode.upper()} mode) - Confidence-gated, ≥80% preserved")
        else:
            self.mtf_engine = None
            self.mtf_active = False

        # Initialize Adaptive Risk Controller (independent learning layer)
        if self.config.use_adaptive_risk:
            self.risk_controller = AdaptiveRiskController(str(self.memory.data_dir))
            self.risk_controller_active = True
            print("[Ensemble] Adaptive Risk Controller enabled - Independent learning active")
        else:
            self.risk_controller = None
            self.risk_controller_active = False

        # Initialize Parameter Optimizer (self-tuning strategy parameters)
        if self.config.use_parameter_optimizer:
            self.parameter_optimizer = ParameterOptimizer(str(self.memory.data_dir / "optimizer"))
            self.optimizer_active = True
            # Apply optimized parameters to all bots on startup
            self._apply_optimized_parameters()
            print("[Ensemble] Parameter Optimizer enabled - Self-tuning active")
        else:
            self.parameter_optimizer = None
            self.optimizer_active = False

        # Initialize Institutional Risk Layer (HEDGE FUND GRADE - PRIMARY GATEKEEPER)
        if self.config.use_institutional_layer:
            self.institutional_layer = InstitutionalRiskLayer(str(self.memory.data_dir / "institutional"))
            self.institutional_active = True
            print("[Ensemble] ═══════════════════════════════════════════════════════════")
            print("[Ensemble] INSTITUTIONAL RISK LAYER ENABLED - Hedge Fund Grade")
            print("[Ensemble] Prevention > Reaction | Regime Gate | Expectancy Metrics")
            print("[Ensemble] ═══════════════════════════════════════════════════════════")
        else:
            self.institutional_layer = None
            self.institutional_active = False

        # Initialize Capital Allocator (MULTI-STRATEGY HEDGE FUND GRADE)
        if self.config.use_capital_allocator:
            self.capital_allocator = InstitutionalCapitalAllocator(
                total_capital=self.config.total_capital,
                max_daily_loss_pct=self.config.max_daily_loss_pct,
                enable_drawdown_protection=True,
            )
            self.allocator_active = True
            print("[Ensemble] Capital Allocator ENABLED - Strategy sleeves, Kelly sizing, drawdown protection")
        else:
            self.capital_allocator = None
            self.allocator_active = False

        # Initialize Model Drift Detector (MODEL RISK GOVERNANCE)
        if self.config.use_drift_detector:
            self.drift_detector = ModelDriftDetector(
                auto_quarantine=self.config.auto_quarantine,
            )
            self.drift_active = True
            # Register baselines for all bots
            for bot in self.bots:
                self.drift_detector.register_baseline(
                    bot_name=bot.name,
                    backtest_results={
                        "win_rate": bot.performance.win_rate / 100,
                        "avg_return": 0.02,  # 2% average
                        "sharpe_ratio": 1.0,
                        "max_drawdown": 0.10,
                        "profit_factor": bot.performance.profit_factor,
                    }
                )
            print("[Ensemble] Drift Detector ENABLED - Live vs backtest monitoring, auto-quarantine")
        else:
            self.drift_detector = None
            self.drift_active = False

        # Initialize Execution Engine (MARKET IMPACT & SLIPPAGE INTELLIGENCE)
        if self.config.use_execution_engine:
            self.execution_engine = ExecutionEngine(
                default_max_slippage=self.config.max_slippage_pct,
                volatility_throttle=True,
                impact_aware_sizing=True,
            )
            self.execution_active = True
            print("[Ensemble] Execution Engine ENABLED - Slippage prediction, market impact modeling")
        else:
            self.execution_engine = None
            self.execution_active = False

        # Initialize Trade-Triggered Backtest Validation (ONLINE LEARNING SAFETY)
        if self.config.enable_backtest_validation:
            self.backtest_validation_active = True
            self.trades_since_backtest: Dict[str, int] = {bot.name: 0 for bot in self.bots}
            self.parameter_snapshots: Dict[str, Dict] = {}  # Store known-good parameters
            self.last_backtest_results: Dict[str, Dict] = {}  # Store last validation results
            print(f"[Ensemble] Backtest Validation ENABLED - Validate after every {self.config.backtest_every_n_trades} trades")
        else:
            self.backtest_validation_active = False
            self.trades_since_backtest = {}
            self.parameter_snapshots = {}
            self.last_backtest_results = {}

        # FIXED: Initialize trade counter for periodic parameter optimization
        self.trades_since_optimization = 0

        # Initialize deep learning engine
        self.deep_learning = DeepLearningEngine(str(self.memory.data_dir))

        # Initialize regime detector
        self.regime_detector = RegimeDetector()

        # Initialize ML feature extractor for future ML model training
        self.ml_extractor = MLFeatureExtractor(str(self.memory.data_dir))

        # State tracking (persisted)
        self.state_file = self.memory.data_dir / "ensemble_state.json"
        self._load_state()

        # Daily tracking
        self.daily_trades: List[BotDecision] = []
        self.daily_pnl: float = 0
        self.active_positions: List[Dict] = []

        # Current regime
        self.current_regime: Optional[MarketRegime] = None

        # Save initial parameter snapshots after state loaded
        if self.backtest_validation_active:
            for bot in self.bots:
                self._save_parameter_snapshot(bot.name)
        self.regime_weights: Dict[str, float] = {}

    def _load_state(self):
        """Load persisted state from disk"""
        self.ensemble_performance = {
            "total_decisions": 0,
            "trades_taken": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "patterns_learned": 0,
        }

        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    self.ensemble_performance = data.get("performance", self.ensemble_performance)
            except (json.JSONDecodeError, TypeError):
                pass

    def _sanitize_runtime_override(self, name: str, value: Any) -> Optional[Any]:
        specs = {
            "min_signal_strength": ("float", 30.0, 70.0),
            "min_confidence": ("float", 45.0, 85.0),
            "min_bots_required": ("int", 1, 4),
            "high_conviction_threshold": ("float", 60.0, 85.0),
            "capital_preservation_mode": ("bool", None, None),
            "mtf_mode": ("choice", {"strict", "balanced", "permissive"}, None),
        }
        if name not in specs:
            return None

        kind, lower, upper = specs[name]
        if kind == "choice":
            normalized = str(value).strip().lower()
            return normalized if normalized in lower else None
        if kind == "bool":
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
            return None

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None

        numeric = max(lower, min(upper, numeric))
        if kind == "int":
            return int(round(numeric))
        return round(numeric, 2)

    def _apply_runtime_overrides(self) -> None:
        self.runtime_overrides = {}
        if not self.runtime_overrides_file.exists():
            return
        try:
            with open(self.runtime_overrides_file, "r") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError, TypeError):
            return

        if not isinstance(raw, dict):
            return

        for name, value in raw.items():
            normalized = self._sanitize_runtime_override(name, value)
            if normalized is None:
                continue
            if name == "capital_preservation_mode":
                self.capital_preservation_mode = normalized
            elif name == "mtf_mode":
                self.config.mtf_mode = normalized
            elif hasattr(self.config, name):
                setattr(self.config, name, normalized)
            self.runtime_overrides[name] = normalized

    def reload_runtime_overrides(self) -> Dict[str, Any]:
        self._apply_runtime_overrides()
        if getattr(self, "mtf_active", False) and self.mtf_engine:
            self.mtf_engine.set_mode(self.config.mtf_mode)
        return self.get_effective_runtime_parameters()

    def get_effective_runtime_parameters(self) -> Dict[str, Any]:
        return {
            "min_signal_strength": self.config.min_signal_strength,
            "min_confidence": self.config.min_confidence,
            "min_bots_required": self.config.min_bots_required,
            "high_conviction_threshold": self.config.high_conviction_threshold,
            "mtf_mode": self.config.mtf_mode,
            "capital_preservation_mode": self.capital_preservation_mode,
            "runtime_overrides": dict(self.runtime_overrides),
        }

    def _save_state(self):
        """Save state to disk"""
        data = {
            "performance": self.ensemble_performance,
            "last_updated": datetime.now().isoformat(),
        }
        with open(self.state_file, "w") as f:
            json.dump(data, f, indent=2)

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        option_chain: Optional[List[Dict]] = None
    ) -> Optional[BotDecision]:
        """
        Coordinate all bots and make institutional-grade decision

        Returns:
            BotDecision if there's a valid trading opportunity
        """
        rejection_reasons = []

        # Store institutional analysis for later use
        self._current_inst_analysis = None
        self._inst_size_multiplier = 1.0

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 0: INSTITUTIONAL RISK LAYER (HEDGE FUND GRADE - PRIMARY GATEKEEPER)
        # This runs FIRST and can block trading entirely based on market regime
        # Philosophy: "The best trade is the one you don't take in bad conditions"
        # ═══════════════════════════════════════════════════════════════════════
        if self.institutional_active and self.institutional_layer:
            # Detect market regime FIRST
            inst_regime = self.institutional_layer.detect_regime(index, market_data)

            # Store for other steps
            self._current_inst_analysis = inst_regime

            # Check if market is tradeable AT ALL
            if inst_regime.trading_condition == TradingCondition.NO_TRADE:
                print(f"[INSTITUTIONAL] ❌ BLOCKED - Market untradeable: {inst_regime.regime.value}")
                print(f"[INSTITUTIONAL] Choppiness: {inst_regime.choppiness_index:.1f} | Volatility: {inst_regime.volatility_percentile:.0f}%")
                return None

            if inst_regime.trading_condition == TradingCondition.POOR:
                print(f"[INSTITUTIONAL] ⚠️ POOR conditions - Size reduced to 30%")
                self._inst_size_multiplier = 0.3

            elif inst_regime.trading_condition == TradingCondition.CAUTION:
                print(
                    f"[INSTITUTIONAL] ⚠️ CAUTION - Size reduced to 60% | "
                    f"regime={inst_regime.regime.value} choppiness={inst_regime.choppiness_index:.1f} "
                    f"[STEP0 PASSED]"
                )
                self._inst_size_multiplier = 0.6

            # Log regime status
            if inst_regime.trading_condition in [TradingCondition.GOOD, TradingCondition.EXCELLENT]:
                print(
                    f"[INSTITUTIONAL] ✅ {inst_regime.regime.value} | "
                    f"Condition: {inst_regime.trading_condition.value} | "
                    f"choppiness={inst_regime.choppiness_index:.1f} [STEP0 PASSED]"
                )
                print(f"[INSTITUTIONAL] Suitable: {inst_regime.suitable_strategies} | Avoid: {inst_regime.avoid_strategies}")

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 1: INSTITUTIONAL GATE - Time and Risk Filters
        # ═══════════════════════════════════════════════════════════════════════
        if self.config.enforce_time_filters:
            gate_result = self._check_institutional_gate(market_data)
            if not gate_result["can_trade"]:
                print(
                    f"[STEP1-GATE] {index}: BLOCKED | "
                    f"session={gate_result.get('session','?')} "
                    f"reason={gate_result.get('reason','?')}"
                )
                return None
            print(f"[STEP1-GATE] {index}: PASSED | session={gate_result.get('session','?')}")

        # Check daily limits
        if len(self.daily_trades) >= self.config.max_daily_trades:
            return None

        if len(self.active_positions) >= self.config.max_concurrent_positions:
            return None

        if self.daily_pnl <= -self.config.max_daily_loss:
            return None  # Daily loss limit hit

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 2: REGIME DETECTION - Adapt to Market Conditions
        # ═══════════════════════════════════════════════════════════════════════
        if self.config.use_regime_detection:
            regime_analysis = self.regime_detector.detect_regime(index, market_data)
            self.current_regime = regime_analysis.regime

            # Get regime-specific strategy
            regime_strategy = self.regime_detector.get_regime_for_strategy(regime_analysis.regime)
            self.regime_weights = self._calculate_regime_weights(regime_strategy)

            # Add regime to market data for bots
            market_data["market_regime"] = regime_analysis.regime.value
            market_data["regime_confidence"] = regime_analysis.confidence
            market_data["regime_bias"] = regime_analysis.bias

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 3: DEEP LEARNING CHECK - Historical Pattern Analysis
        # ═══════════════════════════════════════════════════════════════════════
        confidence_adjustment = 0
        pattern_recommendation = "NEUTRAL"

        if self.config.use_deep_learning:
            should_trade, pattern_reason, conf_adj = self.deep_learning.should_trade(market_data)
            confidence_adjustment = conf_adj
            pattern_recommendation = pattern_reason

            if not should_trade:
                rejection_reasons.append(f"Pattern analysis: {pattern_reason}")
                # Don't immediately reject, but significantly reduce confidence
                confidence_adjustment = -30

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 4: COLLECT SIGNALS FROM ALL BOTS
        # ═══════════════════════════════════════════════════════════════════════
        signals = self._collect_signals(index, market_data, option_chain)

        if not signals:
            print(f"[Ensemble] {index}: No signals collected from bots")
            return None

        # Filter low-quality signals
        quality_signals = [s for s in signals if s.confidence >= self.config.min_signal_strength]

        print(f"[Ensemble] {index}: {len(signals)} signals, {len(quality_signals)} quality (>={self.config.min_signal_strength}%)")
        for sig in signals:
            quality_mark = "✓" if sig.confidence >= self.config.min_signal_strength else "✗"
            print(f"  {quality_mark} {sig.bot_name}: {sig.signal_type.value} @ {sig.confidence:.0f}%")

        if len(quality_signals) < self.config.min_bots_required:
            print(f"[Ensemble] {index}: Need {self.config.min_bots_required} bots, only have {len(quality_signals)}")
            return None

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 4.5: MULTI-TIMEFRAME FILTER (LOSS REDUCTION)
        # Block signals that go against 15m trend - "Never fight the boss"
        # ═══════════════════════════════════════════════════════════════════════
        if self.mtf_active and self.mtf_engine:
            # FIXED: Enforce forced_mtf_mode from AdaptiveRiskController
            if self.risk_controller_active and self.risk_controller:
                forced_mode = self.risk_controller.adaptive_params.get("forced_mtf_mode")
                if forced_mode:
                    self.mtf_engine.set_mode(forced_mode)
                    print(f"[AdaptiveRisk] Enforcing MTF mode: {forced_mode}")
                else:
                    # Auto-adjust MTF mode based on market volatility
                    self.mtf_engine.auto_adjust_mode(index, market_data)
            else:
                # Auto-adjust MTF mode based on market volatility
                self.mtf_engine.auto_adjust_mode(index, market_data)

            mtf_analysis = self.mtf_engine.analyze(index, market_data.get("ltp", 0))

            # Filter signals based on MTF alignment
            # INSTITUTIONAL RULE: High-conviction signals (80%+) are PRESERVED - trust the expert
            mtf_filtered = []
            for signal in quality_signals:
                # Skip disabled bots
                if signal.bot_name in getattr(self, 'disabled_bots', set()):
                    print(f"[Ensemble] SKIPPED {signal.bot_name} - bot disabled due to poor performance")
                    continue

                option_type_str = signal.option_type.value if hasattr(signal.option_type, 'value') else str(signal.option_type)

                # Use MTF filter with signal confidence (preserves 80%+ signals)
                allowed, reason, conf_adj = self.mtf_engine.should_allow_signal(
                    index, option_type_str, market_data.get("ltp", 0),
                    signal_confidence=signal.confidence
                )

                if not allowed:
                    print(f"[MTF] BLOCKED {signal.bot_name} {option_type_str} signal ({signal.confidence:.0f}%) - {reason}")
                    continue

                # Apply confidence adjustment based on alignment
                signal.confidence = min(95, signal.confidence + conf_adj)
                mtf_filtered.append(signal)

            # Log MTF filtering
            blocked_count = len(quality_signals) - len(mtf_filtered)
            if blocked_count > 0:
                print(f"[MTF] Blocked {blocked_count}/{len(quality_signals)} signals (confidence-gated)")

            quality_signals = mtf_filtered

            if len(quality_signals) < self.config.min_bots_required:
                print(f"[MTF] Insufficient signals after MTF filter ({mtf_analysis.alignment.value})")
                return None

        # ═══════════════════════════════════════════════════════════════════════
        # CAPITAL PRESERVATION MODE - Institutional Grade Safety Checks
        # "Quality over quantity - 2 experts agreeing with conviction beats 3 uncertain"
        # ═══════════════════════════════════════════════════════════════════════
        if getattr(self, 'capital_preservation_mode', True):
            threshold = self.config.high_conviction_threshold
            has_strong_signal = any(s.confidence >= threshold for s in quality_signals)

            if not has_strong_signal:
                confs = [(s.bot_name, round(s.confidence, 1)) for s in quality_signals]
                print(
                    f"[CapPres] {index}: BLOCKED — need >={threshold:.0f}% | "
                    f"actual={confs}"
                )
                return None

            # Institutional standard: require at least 2 agreeing quality bots
            if len(quality_signals) < 2:
                print(f"[Capital Preservation] Need 2 bots, only have {len(quality_signals)}")
                return None
            print(f"[Ensemble] {index}: Capital preservation OK - {len(quality_signals)} quality signals, high-conviction present")

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 5: LLM VETO LAYER - Review and Filter Signals (CAPITAL PROTECTION)
        # ═══════════════════════════════════════════════════════════════════════
        if self.veto_active and self.veto_layer:
            # Get recent trades for context
            recent_trades = [
                {
                    "index": pos.get("index"),
                    "action": pos.get("action"),
                    "entry": pos.get("entry"),
                    "bots": pos.get("bots"),
                }
                for pos in self.active_positions[-5:]
            ]

            # Review all signals through veto layer
            approved_signals, veto_decisions = self.veto_layer.review_signals(
                quality_signals,
                {index: market_data},  # Market data as dict
                recent_trades
            )

            # Log veto activity
            rejected_count = len(quality_signals) - len(approved_signals)
            if rejected_count > 0:
                print(f"[Veto] Rejected {rejected_count}/{len(quality_signals)} signals for capital protection")

            # Use approved signals only
            quality_signals = approved_signals

            if len(quality_signals) < self.config.min_bots_required:
                print(f"[Veto] Insufficient approved signals after veto review ({len(quality_signals)} < {self.config.min_bots_required})")
                return None
            print(f"[Ensemble] {index}: STEP 5 Veto OK - {len(quality_signals)} approved signals")

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 6: CALCULATE WEIGHTED CONSENSUS
        # ═══════════════════════════════════════════════════════════════════════
        print(f"[Ensemble] {index}: STEP 6 - Calculating consensus from {len(quality_signals)} signals")
        decision = self._calculate_consensus(
            quality_signals, index, market_data, confidence_adjustment
        )

        if not decision:
            print(f"[Ensemble] {index}: STEP 6 FAILED - No consensus decision")
            return None
        print(f"[Ensemble] {index}: STEP 6 OK - Decision: {decision.action} @ {decision.confidence:.0f}%")

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 7: FINAL VALIDATION
        # ═══════════════════════════════════════════════════════════════════════
        if not self._validate_decision(decision, market_data):
            print(f"[Ensemble] {index}: STEP 7 FAILED - Validation failed")
            return None
        print(f"[Ensemble] {index}: STEP 7 OK - Validation passed")

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 8: ENHANCE DECISION WITH CONTEXT
        # ═══════════════════════════════════════════════════════════════════════
        decision = self._enhance_decision(decision, market_data, signals, pattern_recommendation)

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 9: ADAPTIVE RISK CONTROLLER - FINAL CHECK (Independent Learning)
        # This is the LAST defense - can override everything
        # ═══════════════════════════════════════════════════════════════════════
        if self.risk_controller_active and self.risk_controller:
            # Derive option_type from action (BUY_CE -> CE, BUY_PE -> PE)
            option_type = "CE" if "CE" in decision.action else "PE" if "PE" in decision.action else "NONE"
            allowed, reason, modifications = self.risk_controller.should_allow_trade(
                index=index,
                option_type=option_type,
                bots=decision.contributing_bots,
                confidence=decision.confidence,
                market_conditions={
                    "change_pct": market_data.get("change_pct", 0),
                    "ltp": market_data.get("ltp", 0),
                    "pcr": market_data.get("pcr", 1),
                }
            )

            if not allowed:
                print(f"[AdaptiveRisk] BLOCKED: {reason}")
                return None

            # Apply modifications from risk controller
            if modifications.get("position_size_multiplier"):
                # Store for use in order sizing
                decision.risk_modifications = modifications

            print(f"[AdaptiveRisk] APPROVED | Risk: {self.risk_controller.current_risk_level.value}")

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 10: INSTITUTIONAL FINAL CHECK (Hedge Fund Grade Pre-Trade Intelligence)
        # Comprehensive check: Regime, Exposure, Expectancy, Decision Quality
        # ═══════════════════════════════════════════════════════════════════════
        if self.institutional_active and self.institutional_layer:
            # Prepare proposed trade
            proposed_trade = {
                "index": index,
                "action": decision.action,
                "entry": decision.entry,
                "target": decision.target,
                "stop_loss": decision.stop_loss,
                "contributing_bots": decision.contributing_bots,
                "confidence": decision.confidence,
                "risk_amount": abs((decision.entry or 0) - (decision.stop_loss or 0)) * 50,  # Assume 50 qty
            }

            # Get signals for quality scoring
            signals_for_quality = [
                {
                    "signal_type": s.signal_type.value if hasattr(s.signal_type, 'value') else str(s.signal_type),
                    "confidence": s.confidence,
                    "bot_name": s.bot_name,
                }
                for s in quality_signals
            ]

            # Get current capital (simplified)
            capital = self.config.total_capital

            # Run comprehensive pre-trade check
            allowed, reason, modifications = self.institutional_layer.pre_trade_check(
                index=index,
                proposed_trade=proposed_trade,
                current_positions=self.active_positions,
                capital=capital,
                market_data=market_data,
                signals=signals_for_quality,
            )

            if not allowed:
                print(f"[INSTITUTIONAL] ❌ STEP 10 FINAL CHECK FAILED: {reason}")
                return None
            print(f"[Ensemble] {index}: STEP 10 Institutional OK")

            # Apply institutional modifications
            if not hasattr(decision, 'institutional_modifications'):
                decision.institutional_modifications = {}
            decision.institutional_modifications = modifications

            # Apply size multiplier from institutional layer
            inst_size_mult = modifications.get("position_size_mult", 1.0) * self._inst_size_multiplier
            if hasattr(decision, 'risk_modifications'):
                decision.risk_modifications["institutional_size_mult"] = inst_size_mult
            else:
                decision.risk_modifications = {"institutional_size_mult": inst_size_mult}

            quality_score = modifications.get("quality_score", 0)
            print(f"[INSTITUTIONAL] ✅ APPROVED | Quality: {quality_score:.0f}/100 | Size: {inst_size_mult:.1f}x")

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 11: MODEL DRIFT CHECK - Ensure bot models are healthy
        # Block signals from quarantined or drifting bots
        # ═══════════════════════════════════════════════════════════════════════
        if self.drift_active and self.drift_detector:
            # Check health of all contributing bots
            for bot_name in decision.contributing_bots:
                allowed, reason = self.drift_detector.should_allow_trade(bot_name)
                if not allowed:
                    print(f"[DRIFT] ❌ BLOCKED: {bot_name} - {reason}")
                    # Remove this bot from contributors
                    decision.contributing_bots = [
                        b for b in decision.contributing_bots if b != bot_name
                    ]

            # Check if we still have minimum bots
            if len(decision.contributing_bots) < self.config.min_bots_required:
                print(f"[DRIFT] Insufficient healthy bots after drift filter")
                return None

        # ═══════════════════════════════════════════════════════════════════════
        # STEP 12: CAPITAL ALLOCATION - Request capital from institutional allocator
        # Strategy sleeves, Kelly sizing, drawdown protection
        # ═══════════════════════════════════════════════════════════════════════
        if self.allocator_active and self.capital_allocator:
            # Determine primary bot for sleeve assignment
            primary_bot = decision.contributing_bots[0] if decision.contributing_bots else "TrendFollower"

            # Get regime from institutional layer
            regime_str = "UNKNOWN"
            if hasattr(self, '_current_inst_analysis') and self._current_inst_analysis:
                regime_str = self._current_inst_analysis.regime.value

            # Request capital allocation
            capital_decision = self.capital_allocator.request_capital(
                bot_name=primary_bot,
                proposed_trade={
                    "index": index,
                    "action": decision.action,
                    "entry": decision.entry,
                    "stop_loss": decision.stop_loss,
                    "position_value": self.config.total_capital * 0.05,  # Base 5% position
                },
                market_regime=regime_str,
                signals={"implied_volatility": market_data.get("iv", 20) / 100},
            )

            if not capital_decision.approved:
                print(f"[CAPITAL] ❌ BLOCKED: {capital_decision.reason}")
                return None

            # Apply capital allocation to decision
            if not hasattr(decision, 'risk_modifications'):
                decision.risk_modifications = {}
            decision.risk_modifications["allocated_capital"] = capital_decision.allocated_capital
            decision.risk_modifications["capital_size_factor"] = capital_decision.position_size_factor
            decision.risk_modifications["strategy_sleeve"] = capital_decision.sleeve.value

            # Warnings
            for warning in capital_decision.warnings:
                print(f"[CAPITAL] ⚠️ {warning}")

            print(f"[CAPITAL] ✅ APPROVED: {capital_decision.allocated_capital:,.0f} from {capital_decision.sleeve.value} sleeve")

        # Record decision
        self.ensemble_performance["total_decisions"] += 1
        self._save_state()

        return decision

    def _check_institutional_gate(self, market_data: Dict) -> Dict:
        """Check institutional trading rules"""
        try:
            # Import from institutional module
            import sys
            sys.path.insert(0, str(self.memory.data_dir.parent / "trading"))
            from trading.institutional import get_market_session, get_trading_day_type, get_expiry_day_rules

            time_filter = get_market_session()
            day_type = get_trading_day_type()
            day_rules = get_expiry_day_rules(day_type)

            return {
                "can_trade": time_filter.can_trade,
                "session": time_filter.session.value,
                "warning": time_filter.warning,
                "reason": time_filter.reason,
                "day_type": day_type.value,
                "day_rules": day_rules,
            }
        except ImportError:
            # If institutional module not available, allow trading
            return {"can_trade": True, "session": "UNKNOWN", "warning": None}

    def _calculate_regime_weights(self, regime_strategy: Dict) -> Dict[str, float]:
        """Calculate bot weights based on current regime"""
        weights = {}
        preferred = regime_strategy.get("preferred_bots", [])
        avoid = regime_strategy.get("avoid_bots", [])
        risk_mult = regime_strategy.get("risk_multiplier", 1.0)

        for bot in self.bots:
            base_weight = bot.get_weight()

            if bot.name in preferred:
                weights[bot.name] = base_weight * 1.3 * risk_mult
            elif bot.name in avoid:
                weights[bot.name] = base_weight * 0.5 * risk_mult
            else:
                weights[bot.name] = base_weight * risk_mult

        return weights

    def _collect_signals(
        self,
        index: str,
        market_data: Dict[str, Any],
        option_chain: Optional[List[Dict]]
    ) -> List[BotSignal]:
        """Collect signals from all bots"""
        signals = []
        bot_signals_detail = {}

        # FIXED: Get blocked bots from AdaptiveRiskController to enforce overrides
        blocked_bots = set()
        if self.risk_controller_active and self.risk_controller:
            blocked_bots = self.risk_controller.adaptive_params.get("blocked_bots", set())
            if blocked_bots:
                print(f"[AdaptiveRisk] Skipping blocked bots: {blocked_bots}")

        for bot in self.bots:
            # FIXED: Skip blocked bots (AdaptiveRiskController override enforcement)
            if bot.name in blocked_bots:
                print(f"[AdaptiveRisk] Skipping {bot.name} - currently blocked")
                continue

            # FIXED: Skip explicitly disabled bots (e.g., ReversalHunter with poor performance)
            if bot.name in getattr(self, 'disabled_bots', set()):
                continue

            try:
                signal = bot.analyze(index, market_data, option_chain)
                if signal:
                    signals.append(signal)
                    bot_signals_detail[bot.name] = {
                        "signal_type": signal.signal_type.value,
                        "confidence": signal.confidence,
                        "reasoning": signal.reasoning,
                    }
            except Exception as e:
                print(f"Error in {bot.name}: {e}")

        # Collect ML bot signal (6th bot)
        try:
            ml_signal = self.ml_bot.analyze(
                index, market_data, option_chain, signals
            )
            if ml_signal and ml_signal.confidence > 0:
                signals.append(ml_signal)
                bot_signals_detail[self.ml_bot.name] = {
                    "signal_type": ml_signal.signal_type.value,
                    "confidence": ml_signal.confidence,
                    "reasoning": ml_signal.rationale,
                    "is_ml": True,
                    "trained": self.ml_bot.is_trained,
                }
        except Exception as e:
            print(f"Error in ML bot: {e}")

        # Collect LLM bot signal (7th bot - TRUE AI reasoning)
        if self.llm_bot_active and self.llm_bot:
            try:
                llm_signal = self.llm_bot.analyze(index, market_data, option_chain)
                if llm_signal and llm_signal.confidence >= 70:  # Only high-confidence LLM signals
                    signals.append(llm_signal)
                    bot_signals_detail[self.llm_bot.name] = {
                        "signal_type": llm_signal.signal_type.value,
                        "confidence": llm_signal.confidence,
                        "reasoning": llm_signal.reasoning,
                        "is_llm": True,
                        "key_factors": llm_signal.factors.get("key_factors", []),
                    }
                    print(f"[LLM Bot] Signal: {llm_signal.signal_type.value} @ {llm_signal.confidence}%")
            except Exception as e:
                print(f"Error in LLM bot: {e}")

        # Store for deep learning context
        market_data["_bot_signals"] = bot_signals_detail

        return signals

    def _calculate_consensus(
        self,
        signals: List[BotSignal],
        index: str,
        market_data: Dict[str, Any],
        confidence_adjustment: float
    ) -> Optional[BotDecision]:
        """Calculate weighted consensus from signals"""
        if not signals:
            return None

        # Group signals by direction
        bullish_signals = [
            s for s in signals
            if s.signal_type in [SignalType.STRONG_BUY, SignalType.BUY]
        ]
        bearish_signals = [
            s for s in signals
            if s.signal_type in [SignalType.STRONG_SELL, SignalType.SELL]
        ]

        # Calculate weighted scores with regime adjustments
        bullish_score = self._calculate_weighted_score(bullish_signals)
        bearish_score = self._calculate_weighted_score(bearish_signals)

        # Apply confidence adjustment from pattern analysis
        bullish_score += confidence_adjustment
        bearish_score += confidence_adjustment

        total_bots = max(1, len(self.bots) - len(getattr(self, 'disabled_bots', set())))
        bullish_consensus = len(bullish_signals) / total_bots
        bearish_consensus = len(bearish_signals) / total_bots
        print(
            f"[STEP6] {index}: total_bots={total_bots} bullish={len(bullish_signals)}/{total_bots}={bullish_consensus:.2f} "
            f"bearish={len(bearish_signals)}/{total_bots}={bearish_consensus:.2f} "
            f"need>={self.config.min_consensus} bull_score={bullish_score:.1f} bear_score={bearish_score:.1f}"
        )

        # Determine direction
        if bullish_score > bearish_score and bullish_consensus >= self.config.min_consensus:
            return self._create_decision(
                "BUY_CE", bullish_signals, bullish_consensus, bullish_score, index, market_data
            )
        elif bearish_score > bullish_score and bearish_consensus >= self.config.min_consensus:
            return self._create_decision(
                "BUY_PE", bearish_signals, bearish_consensus, bearish_score, index, market_data
            )

        # Check for strong individual signals (high conviction override)
        strongest = max(signals, key=lambda s: s.confidence)
        if strongest.confidence >= 80 and strongest.signal_type in [
            SignalType.STRONG_BUY, SignalType.STRONG_SELL
        ]:
            action = "BUY_CE" if strongest.signal_type == SignalType.STRONG_BUY else "BUY_PE"
            relevant_signals = bullish_signals if action == "BUY_CE" else bearish_signals
            consensus = bullish_consensus if action == "BUY_CE" else bearish_consensus

            return self._create_decision(
                action, relevant_signals or [strongest], consensus,
                strongest.confidence + confidence_adjustment, index, market_data, override=True
            )

        return None

    def _calculate_weighted_score(self, signals: List[BotSignal]) -> float:
        """Calculate weighted score for a group of signals"""
        if not signals:
            return 0

        total_weight = 0
        weighted_confidence = 0

        for signal in signals:
            # Get base weight
            if self.config.weight_by_performance:
                bot = self.bot_map.get(signal.bot_name)
                base_weight = bot.get_weight() if bot else 1.0
            else:
                base_weight = 1.0

            # Apply regime weight if available
            if self.config.weight_by_regime and signal.bot_name in self.regime_weights:
                weight = self.regime_weights[signal.bot_name]
            else:
                weight = base_weight

            # Strong signals get extra weight
            if signal.signal_type in [SignalType.STRONG_BUY, SignalType.STRONG_SELL]:
                weight *= 1.2

            total_weight += weight
            weighted_confidence += signal.confidence * weight

        return weighted_confidence / total_weight if total_weight > 0 else 0

    def _create_decision(
        self,
        action: str,
        signals: List[BotSignal],
        consensus: float,
        confidence: float,
        index: str,
        market_data: Dict,
        override: bool = False
    ) -> BotDecision:
        """Create final trading decision"""
        # Strike step sizes for proper ATM rounding (from shared config)
        step = get_strike_gap(index)

        # Aggregate strike, entry, target, stop_loss from signals
        strikes = [s.strike for s in signals if s.strike]
        entries = [s.entry for s in signals if s.entry]
        targets = [s.target for s in signals if s.target]
        stop_losses = [s.stop_loss for s in signals if s.stop_loss]

        # Calculate strike - use LTP if no strikes from bots, always round to proper step
        if strikes:
            avg_strike = sum(strikes) / len(strikes)
            strike = int(round(avg_strike / step) * step)
        else:
            # Fallback to ATM based on LTP
            ltp = market_data.get("ltp", 0)
            strike = int(round(ltp / step) * step) if ltp > 0 else None

        entry = sum(entries) / len(entries) if entries else None
        target = sum(targets) / len(targets) if targets else None
        stop_loss = sum(stop_losses) / len(stop_losses) if stop_losses else None

        # Build comprehensive reasoning
        reasoning = self._build_reasoning(signals, consensus, override, market_data)

        return BotDecision(
            action=action,
            index=index,
            strike=strike,
            entry=round(entry, 2) if entry else None,
            target=round(target, 2) if target else None,
            stop_loss=round(stop_loss, 2) if stop_loss else None,
            confidence=round(max(0, min(100, confidence)), 1),
            contributing_bots=[s.bot_name for s in signals],
            consensus_level=round(consensus * 100, 1),
            reasoning=reasoning,
            individual_signals=signals,
        )

    def _build_reasoning(
        self,
        signals: List[BotSignal],
        consensus: float,
        override: bool,
        market_data: Dict
    ) -> str:
        """Build comprehensive reasoning"""
        parts = []

        # Regime info
        if self.current_regime:
            parts.append(f"Regime: {self.current_regime.value}")

        # Override flag
        if override:
            parts.append("HIGH CONVICTION")

        # Consensus
        parts.append(f"Consensus: {consensus * 100:.0f}% ({len(signals)}/{len(self.bots)} bots)")

        # Bot contributions
        for signal in signals[:3]:  # Top 3 bots
            parts.append(f"{signal.bot_name}: {signal.reasoning[:50]}...")

        return " | ".join(parts)

    def _validate_decision(self, decision: BotDecision, market_data: Dict) -> bool:
        """Validate decision against all rules"""
        # Minimum confidence
        if decision.confidence < self.config.min_confidence:
            print(f"[Validation] FAILED: Confidence {decision.confidence:.0f}% < {self.config.min_confidence}%")
            return False

        # Existing position check
        existing = [p for p in self.active_positions if p.get("index") == decision.index]
        if existing:
            print(f"[Validation] FAILED: Already have position in {decision.index}")
            return False

        # Risk per trade check
        if decision.entry and decision.stop_loss:
            risk = abs(decision.entry - decision.stop_loss)
            if risk > self.config.max_per_trade_risk:
                print(f"[Validation] FAILED: Risk {risk:.2f} > max {self.config.max_per_trade_risk}")
                return False

        return True

    def _enhance_decision(
        self,
        decision: BotDecision,
        market_data: Dict,
        signals: List[BotSignal],
        pattern_recommendation: str
    ) -> BotDecision:
        """Enhance decision with additional context"""
        # Add pattern recommendation to reasoning
        if pattern_recommendation != "NEUTRAL":
            decision.reasoning = f"[{pattern_recommendation}] " + decision.reasoning

        return decision

    def execute_trade(self, decision: BotDecision, market_data: Dict) -> Dict[str, Any]:
        """Execute trade and record context for learning"""
        trade_id = self._generate_trade_id(decision)

        # ═══════════════════════════════════════════════════════════════════════
        # EXECUTION ENGINE - Smart order execution with slippage/impact awareness
        # ═══════════════════════════════════════════════════════════════════════
        execution_plan = None
        if self.execution_active and self.execution_engine:
            # Update order book if available
            if "order_book" in market_data:
                ob = market_data["order_book"]
                self.execution_engine.update_order_book(
                    decision.index,
                    bids=ob.get("bids", []),
                    asks=ob.get("asks", []),
                )

            # Update volatility
            if "iv" in market_data:
                self.execution_engine.update_volatility(decision.index, market_data["iv"] / 100)

            # Get execution approval and plan
            side = "BUY"  # Always buying options
            quantity = 50  # Default lot size
            if hasattr(decision, 'risk_modifications'):
                quantity = int(50 * decision.risk_modifications.get("capital_size_factor", 1.0))

            approved, reason, execution_plan = self.execution_engine.should_execute(
                symbol=decision.index,
                side=side,
                quantity=quantity,
            )

            if not approved:
                print(f"[EXECUTION] ❌ BLOCKED: {reason}")
                return {"status": "BLOCKED", "reason": reason}

            if execution_plan and execution_plan.warnings:
                for warning in execution_plan.warnings:
                    print(f"[EXECUTION] ⚠️ {warning}")

            print(f"[EXECUTION] Strategy: {execution_plan.strategy.value if execution_plan else 'MARKET'}")

        # ═══════════════════════════════════════════════════════════════════════
        # CAPITAL ALLOCATOR - Deploy capital to strategy sleeve
        # ═══════════════════════════════════════════════════════════════════════
        sleeve_deployed = None
        if self.allocator_active and self.capital_allocator:
            if hasattr(decision, 'risk_modifications'):
                allocated = decision.risk_modifications.get("allocated_capital", 0)
                sleeve_name = decision.risk_modifications.get("strategy_sleeve", "trend")

                # Map sleeve name to enum
                sleeve_map = {
                    "trend": StrategySleeve.TREND,
                    "mean_rev": StrategySleeve.MEAN_REVERSION,
                    "momentum": StrategySleeve.MOMENTUM,
                    "event": StrategySleeve.EVENT,
                    "defensive": StrategySleeve.DEFENSIVE,
                }
                sleeve_deployed = sleeve_map.get(sleeve_name, StrategySleeve.TREND)

                self.capital_allocator.deploy_capital(
                    position_id=trade_id,
                    capital=allocated,
                    sleeve=sleeve_deployed,
                )

        # Create full trade context for deep learning
        trade_context = TradeContext(
            trade_id=trade_id,
            timestamp=datetime.now().isoformat(),
            index=decision.index,
            ltp=market_data.get("ltp", 0),
            change_pct=market_data.get("change_pct", 0),
            high=market_data.get("high", 0),
            low=market_data.get("low", 0),
            volume=market_data.get("volume", 0),
            pcr=market_data.get("pcr", 1.0),
            ce_oi=market_data.get("ce_oi", 0),
            pe_oi=market_data.get("pe_oi", 0),
            ce_oi_change=market_data.get("ce_oi_change", 0),
            pe_oi_change=market_data.get("pe_oi_change", 0),
            max_pain=market_data.get("max_pain", 0),
            iv=market_data.get("iv", 0),
            iv_percentile=market_data.get("iv_percentile", 50),
            market_session=market_data.get("market_session", "UNKNOWN"),
            day_type=market_data.get("day_type", "NORMAL"),
            day_of_week=datetime.now().weekday(),
            hour=datetime.now().hour,
            minute=datetime.now().minute,
            market_regime=self.current_regime.value if self.current_regime else "UNKNOWN",
            vix=market_data.get("vix", 15),
            action=decision.action,
            strike=decision.strike or 0,
            entry_price=decision.entry or 0,
            target_price=decision.target or 0,
            stop_loss=decision.stop_loss or 0,
            confidence=decision.confidence,
            consensus_level=decision.consensus_level,
            contributing_bots=decision.contributing_bots,
            bot_signals=market_data.get("_bot_signals", {}),
        )

        # Record in deep learning
        self.deep_learning.record_trade_entry(trade_context)

        # Record ML features for future model training
        market_data["index"] = decision.index
        ml_features = self.ml_extractor.extract_features(
            market_data=market_data,
            bot_signals=market_data.get("_bot_signals", {}),
            action=decision.action,
            trade_id=trade_id
        )
        self.ml_extractor.record_features(ml_features)

        # Track position
        self.daily_trades.append(decision)
        self.active_positions.append({
            "trade_id": trade_id,
            "index": decision.index,
            "action": decision.action,
            "entry": decision.entry,
            "target": decision.target,
            "stop_loss": decision.stop_loss,
            "strike": decision.strike or 0,  # FIXED: Store strike for learning
            "entry_time": datetime.now().isoformat(),  # FIXED: Store actual entry time
            "bots": decision.contributing_bots,
            "market_data": market_data,
            "signals": decision.individual_signals,  # Store for veto outcome tracking
        })

        self.ensemble_performance["trades_taken"] += 1
        self._save_state()

        return {
            "trade_id": trade_id,
            "decision": asdict(decision) if hasattr(decision, '__dataclass_fields__') else decision,
            "executed_at": datetime.now().isoformat(),
            "status": "OPEN",
        }

    def close_trade(
        self,
        index: str,
        exit_price: float,
        outcome: str,
        pnl: float,
        exit_reason: str = "MANUAL"
    ):
        """Close trade and trigger learning"""
        # Find position
        position = None
        for i, pos in enumerate(self.active_positions):
            if pos.get("index") == index:
                position = pos
                self.active_positions.pop(i)
                break

        if not position:
            return

        # Update performance
        if outcome == "WIN":
            self.ensemble_performance["wins"] += 1
        elif outcome == "LOSS":
            self.ensemble_performance["losses"] += 1
        self.ensemble_performance["total_pnl"] += pnl
        self.daily_pnl += pnl

        # Record exit in deep learning
        pnl_pct = (pnl / position.get("entry", 1)) * 100 if position.get("entry") else 0
        self.deep_learning.record_trade_exit(
            trade_id=position.get("trade_id", ""),
            exit_price=exit_price,
            outcome=outcome,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason
        )

        # Update ML features with outcome for training
        self.ml_extractor.update_outcome(
            trade_id=position.get("trade_id", ""),
            outcome=outcome,
            pnl_pct=pnl_pct
        )

        # Inform veto layer about outcome (for tracking saved losses)
        if self.veto_active and self.veto_layer:
            # Use stored signals from the position
            for signal in position.get("signals", []):
                self.veto_layer.record_outcome(signal, outcome, pnl_pct)

        # FIXED: Capture exit market conditions (fresh data for learning)
        exit_market_conditions = position.get("market_data", {}).copy()
        exit_market_conditions["exit_price"] = exit_price
        exit_market_conditions["exit_reason"] = exit_reason
        exit_market_conditions["exit_time"] = datetime.now().isoformat()

        # Route to individual bots for learning
        for bot_name in position.get("bots", []):
            bot = self.bot_map.get(bot_name)
            if bot:
                trade_record = TradeRecord(
                    trade_id=position.get("trade_id", ""),
                    bot_name=bot_name,
                    index=index,
                    option_type=position.get("action", "").replace("BUY_", ""),
                    strike=position.get("strike", 0),  # FIXED: Use actual strike from position
                    entry_price=position.get("entry", 0),
                    exit_price=exit_price,
                    entry_time=position.get("entry_time", datetime.now().isoformat()),  # FIXED: Use stored entry time
                    exit_time=datetime.now().isoformat(),  # FIXED: Use actual exit time
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    outcome=outcome,
                    market_conditions=exit_market_conditions,  # FIXED: Use fresh exit conditions
                    bot_reasoning="",
                )
                bot.learn(trade_record)

        # Also let LLM bot learn (TRUE AI learning)
        if self.llm_bot_active and self.llm_bot and self.llm_bot.name in position.get("bots", []):
            try:
                llm_trade_record = TradeRecord(
                    trade_id=position.get("trade_id", ""),
                    bot_name=self.llm_bot.name,
                    index=index,
                    option_type=position.get("action", "").replace("BUY_", ""),
                    strike=position.get("strike", 0),  # FIXED: Use actual strike
                    entry_price=position.get("entry", 0),
                    exit_price=exit_price,
                    entry_time=position.get("entry_time", datetime.now().isoformat()),  # FIXED: Use stored entry time
                    exit_time=datetime.now().isoformat(),  # FIXED: Use actual exit time
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    outcome=outcome,
                    market_conditions=exit_market_conditions,  # FIXED: Use fresh exit conditions
                    bot_reasoning="LLM reasoning trade",
                )
                self.llm_bot.learn(llm_trade_record)
            except Exception as e:
                print(f"Error in LLM bot learning: {e}")

        # ═══════════════════════════════════════════════════════════════════════
        # AUTOMATED ADAPTIVE LEARNING - Feed all learning systems
        # ═══════════════════════════════════════════════════════════════════════
        self.report_trade_outcome(
            index=index,
            option_type=position.get("action", "").replace("BUY_", ""),
            bots_involved=position.get("bots", []),
            entry_price=position.get("entry", 0),
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            outcome=outcome,
            market_conditions=position.get("market_data", {}),
            confidence=position.get("market_data", {}).get("confidence", 70),
            holding_time_minutes=0  # Could calculate from entry time
        )

        # Record to Institutional Layer for expectancy tracking
        if self.institutional_active and self.institutional_layer:
            for bot_name in position.get("bots", []):
                self.institutional_layer.record_trade_outcome(
                    bot_name=bot_name,
                    trade={
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "outcome": outcome,
                        "index": index,
                    }
                )

        # ═══════════════════════════════════════════════════════════════════════
        # CAPITAL ALLOCATOR - Release capital back to sleeve
        # ═══════════════════════════════════════════════════════════════════════
        if self.allocator_active and self.capital_allocator:
            sleeve_name = position.get("market_data", {}).get("strategy_sleeve", "trend")
            sleeve_map = {
                "trend": StrategySleeve.TREND,
                "mean_rev": StrategySleeve.MEAN_REVERSION,
                "momentum": StrategySleeve.MOMENTUM,
                "event": StrategySleeve.EVENT,
                "defensive": StrategySleeve.DEFENSIVE,
            }
            sleeve = sleeve_map.get(sleeve_name, StrategySleeve.TREND)

            self.capital_allocator.release_capital(
                position_id=position.get("trade_id", ""),
                pnl=pnl,
                sleeve=sleeve,
            )

        # ═══════════════════════════════════════════════════════════════════════
        # DRIFT DETECTOR - Record trade for model health tracking
        # ═══════════════════════════════════════════════════════════════════════
        if self.drift_active and self.drift_detector:
            regime = position.get("market_data", {}).get("market_regime", "UNKNOWN")
            for bot_name in position.get("bots", []):
                self.drift_detector.record_trade(
                    bot_name=bot_name,
                    trade={
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "holding_time": 0,
                        "regime": regime,
                        "entry_time": datetime.now(),
                        "exit_time": datetime.now(),
                        "direction": position.get("action", "LONG"),
                    }
                )

        # ═══════════════════════════════════════════════════════════════════════
        # BACKTEST VALIDATION - Validate learned parameters via backtest
        # ═══════════════════════════════════════════════════════════════════════
        if self.backtest_validation_active:
            bots_in_trade = position.get("bots", [])
            logger.debug(f"[Backtest Validation] Bots in trade: {bots_in_trade}")
            for bot_name in bots_in_trade:
                self.trades_since_backtest[bot_name] += 1
                logger.debug(f"[Backtest Validation] {bot_name} count now: {self.trades_since_backtest[bot_name]}/{self.config.backtest_every_n_trades}")
                if self.trades_since_backtest[bot_name] >= self.config.backtest_every_n_trades:
                    # Trigger validation (async-like, doesn't block)
                    logger.info(f"[Backtest Validation] Triggering for {bot_name}")
                    self._run_backtest_validation(bot_name, index)

        # ═══════════════════════════════════════════════════════════════════════
        # PARAMETER OPTIMIZATION - Periodic auto-optimization
        # FIXED: Actually apply optimized parameters after learning
        # ═══════════════════════════════════════════════════════════════════════
        if self.optimizer_active and self.parameter_optimizer:
            self.trades_since_optimization += 1
            if self.trades_since_optimization >= self.config.optimization_every_n_trades:
                logger.info(f"[ParameterOptimizer] Running periodic optimization after {self.trades_since_optimization} trades")
                results = self.run_optimization()
                if results:
                    logger.info(f"[ParameterOptimizer] Applied {len(results)} parameter changes")
                    for r in results:
                        logger.info(f"  - {r['parameter']}: {r['old_value']} → {r['new_value']}")
                self.trades_since_optimization = 0

        # ═══════════════════════════════════════════════════════════════════════
        # EXECUTION ENGINE - Record execution quality
        # ═══════════════════════════════════════════════════════════════════════
        if self.execution_active and self.execution_engine:
            self.execution_engine.record_execution(
                order_id=position.get("trade_id", ""),
                symbol=index,
                side="BUY",
                ordered_qty=50,
                filled_qty=50,
                avg_fill_price=exit_price,
                arrival_price=position.get("entry", exit_price),
                execution_time_sec=1,
                strategy=ExecutionStrategy.MARKET,
            )

        # ═══════════════════════════════════════════════════════════════════════
        # ML BOT AUTO-TRAINING - FIXED: Auto-transition from shadow to production
        # Automatically train ML model when enough data is collected
        # ═══════════════════════════════════════════════════════════════════════
        if self.ml_bot and not self.ml_bot.is_trained:
            ml_status = self.ml_bot.get_status()
            if ml_status.get("ready_to_train", False):
                logger.info("[MLBot] Ready to train - initiating auto-training")
                try:
                    from .ml_bot import train_model
                    success = train_model(str(self.memory.data_dir))
                    if success:
                        # Reload the model
                        self.ml_bot._load_model()
                        logger.info("[MLBot] Auto-training complete - ML bot now active!")
                        print("[MLBot] ═══════════════════════════════════════════════")
                        print("[MLBot] AUTO-TRAINING COMPLETE - ML Bot now ACTIVE!")
                        print("[MLBot] The ML bot will now contribute to trading decisions")
                        print("[MLBot] ═══════════════════════════════════════════════")
                except Exception as e:
                    logger.error(f"[MLBot] Auto-training failed: {e}")

        self._save_state()

    def _generate_trade_id(self, decision: BotDecision) -> str:
        """Generate unique trade ID"""
        data = f"{decision.index}_{decision.action}_{datetime.now().isoformat()}"
        return hashlib.md5(data.encode()).hexdigest()[:12]

    def _save_parameter_snapshot(self, bot_name: str):
        """Save current bot parameters as known-good snapshot"""
        bot = self.bot_map.get(bot_name)
        if not bot:
            return

        # Save bot-agnostic parameters (if bot has config/params attribute)
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "performance": {
                "win_rate": bot.performance.win_rate,
                "total_pnl": bot.performance.total_pnl,
                "weight": bot.performance.weight,
                "total_trades": bot.performance.total_trades,
            },
        }

        # Save bot-specific parameters (if they exist)
        if hasattr(bot, "config"):
            snapshot["config"] = asdict(bot.config) if hasattr(bot.config, "__dataclass_fields__") else vars(bot.config)
        elif hasattr(bot, "params"):
            snapshot["params"] = bot.params.copy() if isinstance(bot.params, dict) else vars(bot.params)

        self.parameter_snapshots[bot_name] = snapshot
        logger.info(f"Saved parameter snapshot for {bot_name}")

    def _restore_parameter_snapshot(self, bot_name: str):
        """Restore bot parameters from snapshot"""
        snapshot = self.parameter_snapshots.get(bot_name)
        if not snapshot:
            logger.warning(f"No snapshot found for {bot_name}")
            return

        bot = self.bot_map.get(bot_name)
        if not bot:
            return

        # Restore bot-specific parameters
        if "config" in snapshot and hasattr(bot, "config"):
            for key, value in snapshot["config"].items():
                if hasattr(bot.config, key):
                    setattr(bot.config, key, value)
        elif "params" in snapshot and hasattr(bot, "params"):
            if isinstance(bot.params, dict):
                bot.params.update(snapshot["params"])
            else:
                for key, value in snapshot["params"].items():
                    if hasattr(bot.params, key):
                        setattr(bot.params, key, value)

        logger.info(f"Restored parameter snapshot for {bot_name} from {snapshot['timestamp']}")

    def _run_backtest_validation(self, bot_name: str, index: str):
        """
        Validate learned parameters via quick backtest
        
        Process:
        1. Run backtest with current (learned) parameters
        2. Compare to baseline/last known-good
        3. If better: Update baseline and save snapshot
        4. If worse: Optionally revert to last known-good
        """
        try:
            try:
                from backtesting.backtest import Backtester
            except ImportError:
                from livebench.backtesting.backtest import Backtester

            logger.info(f"[Backtest Validation] Triggering for {bot_name} after {self.trades_since_backtest[bot_name]} trades")

            # Run quick backtest
            backtester = Backtester()
            result = backtester.run_backtest(
                index=index,
                days=self.config.backtest_validation_days,
                resolution="5"
            )

            # Extract bot-specific results from backtest
            bot_trades = backtester.bot_trades.get(bot_name, [])
            if not bot_trades:
                logger.warning(f"No trades for {bot_name} in backtest")
                self.trades_since_backtest[bot_name] = 0
                return

            # Calculate bot performance from backtest
            winning_trades = [t for t in bot_trades if t.get("pnl", 0) > 0]
            current_win_rate = len(winning_trades) / len(bot_trades) if bot_trades else 0
            current_avg_pnl = sum(t.get("pnl", 0) for t in bot_trades) / len(bot_trades) if bot_trades else 0
            current_total_pnl = sum(t.get("pnl", 0) for t in bot_trades)
            
            # Get baseline (from drift detector or last backtest)
            baseline = self.drift_detector.baselines.get(bot_name) if self.drift_active else None
            last_result = self.last_backtest_results.get(bot_name, {})
            
            baseline_win_rate = baseline.win_rate if baseline else last_result.get("win_rate", 0.5)
            baseline_avg_return = baseline.avg_return if baseline else last_result.get("avg_pnl", 0)

            # Compare performance
            win_rate_delta = current_win_rate - baseline_win_rate
            pnl_delta = current_avg_pnl - baseline_avg_return

            logger.info(f"[Backtest Validation] {bot_name}: "
                       f"WinRate {current_win_rate:.1%} (baseline {baseline_win_rate:.1%}, Δ{win_rate_delta:+.1%}), "
                       f"AvgPnL {current_avg_pnl:.0f} (baseline {baseline_avg_return:.0f}, Δ{pnl_delta:+.0f})")

            # Decision: Keep or revert?
            improvement = win_rate_delta + (pnl_delta / 100)  # Combine metrics
            
            if improvement >= self.config.backtest_min_improvement:
                # ✅ IMPROVED - Update baseline and save snapshot
                logger.info(f"[Backtest Validation] ✅ {bot_name} IMPROVED ({improvement:+.1%}) - Updating baseline")
                
                # Update drift detector baseline
                if self.drift_active and self.drift_detector:
                    self.drift_detector.register_baseline(
                        bot_name=bot_name,
                        backtest_results={
                            "win_rate": current_win_rate,
                            "avg_return": current_avg_pnl / 100,  # Convert to decimal
                            "sharpe_ratio": 1.0,  # Could calculate properly
                            "max_drawdown": 0.10,
                            "profit_factor": current_total_pnl / max(1, abs(sum(t.get("pnl", 0) for t in bot_trades if t.get("pnl", 0) < 0))),
                        }
                    )
                
                # Save new snapshot
                self._save_parameter_snapshot(bot_name)
                
                # Store result
                self.last_backtest_results[bot_name] = {
                    "win_rate": current_win_rate,
                    "avg_pnl": current_avg_pnl,
                    "total_pnl": current_total_pnl,
                    "trades": len(bot_trades),
                    "timestamp": datetime.now().isoformat(),
                }
                
            elif improvement < -self.config.backtest_min_improvement and self.config.backtest_revert_on_failure:
                # ❌ DEGRADED - Revert to last known-good
                logger.warning(f"[Backtest Validation] ❌ {bot_name} DEGRADED ({improvement:+.1%}) - REVERTING parameters")
                self._restore_parameter_snapshot(bot_name)
                
            else:
                # 🟡 NO SIGNIFICANT CHANGE - Keep current
                logger.info(f"[Backtest Validation] 🟡 {bot_name} NO CHANGE ({improvement:+.1%}) - Keeping current params")

            # Reset counter
            self.trades_since_backtest[bot_name] = 0

        except Exception as e:
            logger.error(f"[Backtest Validation] Error validating {bot_name}: {e}")
            # Reset counter anyway to avoid repeated failures
            self.trades_since_backtest[bot_name] = 0

    def get_bot_status(self) -> List[Dict[str, Any]]:
        """Get status of all bots"""
        status = [bot.to_dict() for bot in self.bots]

        # Add ML bot status
        ml_status = self.ml_bot.get_status()
        status.append({
            "name": self.ml_bot.name,
            "description": self.ml_bot.description,
            "is_ml_bot": True,
            **ml_status,
        })

        # Add LLM bot status (TRUE AI reasoning)
        if self.llm_bot:
            status.append({
                "name": self.llm_bot.name,
                "description": self.llm_bot.description,
                "is_llm_bot": True,
                "enabled": self.llm_bot_active,
                "model": getattr(self.llm_bot, 'model', 'unknown'),
                "performance": {
                    "total_signals": self.llm_bot.performance.total_signals,
                    "total_trades": self.llm_bot.performance.total_trades,
                    "wins": self.llm_bot.performance.wins,
                    "losses": self.llm_bot.performance.losses,
                    "win_rate": self.llm_bot.performance.win_rate,
                    "total_pnl": self.llm_bot.performance.total_pnl,
                    "weight": self.llm_bot.performance.weight,
                },
                "recent_signals": [
                    {
                        "index": s.index,
                        "signal_type": s.signal_type.value,
                        "confidence": s.confidence,
                        "reasoning": s.reasoning[:100] + "..." if len(s.reasoning) > 100 else s.reasoning,
                    }
                    for s in self.llm_bot.recent_signals[-5:]
                ] if hasattr(self.llm_bot, 'recent_signals') else [],
            })

        # Add Veto Layer status (capital protection)
        if self.veto_layer:
            veto_stats = self.veto_layer.get_stats()
            status.append({
                "name": "VetoLayer",
                "description": "LLM-powered signal filter for capital protection",
                "is_veto_layer": True,
                "enabled": self.veto_active,
                "model": getattr(self.veto_layer, 'model', 'gpt-4o-mini'),
                "stats": veto_stats,
            })

        return status

    def get_ensemble_stats(self) -> Dict[str, Any]:
        """Get ensemble-level statistics"""
        total = self.ensemble_performance["wins"] + self.ensemble_performance["losses"]
        win_rate = self.ensemble_performance["wins"] / total * 100 if total > 0 else 0

        # Get learning summary
        learning_summary = self.deep_learning.get_learning_summary()

        # Get veto layer stats
        veto_stats = self.veto_layer.get_stats() if self.veto_active and self.veto_layer else None

        return {
            **self.ensemble_performance,
            "win_rate": round(win_rate, 1),
            "active_positions": len(self.active_positions),
            "daily_trades": len(self.daily_trades),
            "daily_pnl": round(self.daily_pnl, 2),
            "current_regime": self.current_regime.value if self.current_regime else None,
            "bot_count": len(self.bots) + 1 + (1 if self.llm_bot_active else 0),  # +1 for ML, +1 for LLM
            "ml_bot_status": self.ml_bot.get_status(),
            "veto_layer": {
                "enabled": self.veto_active,
                "stats": veto_stats,
            },
            "learning": learning_summary,
            "bots": [
                {
                    "name": bot.name,
                    "weight": round(bot.get_weight(), 2),
                    "regime_weight": round(self.regime_weights.get(bot.name, 1.0), 2),
                    "win_rate": round(bot.performance.win_rate, 1),
                    "total_trades": bot.performance.total_trades,
                }
                for bot in self.bots
            ] + [{
                "name": self.ml_bot.name,
                "weight": 1.5 if self.ml_bot.is_trained else 0.0,
                "regime_weight": 1.0,
                "win_rate": self.ml_bot.metadata.accuracy if self.ml_bot.metadata else 0,
                "total_trades": self.ml_bot.metadata.training_samples if self.ml_bot.metadata else 0,
                "is_ml": True,
                "trained": self.ml_bot.is_trained,
            }]
        }

    def get_leaderboard(self) -> List[Dict[str, Any]]:
        """Get bot leaderboard sorted by performance"""
        bot_stats = []
        for bot in self.bots:
            perf = bot.performance
            score = (
                (perf.win_rate / 100) * perf.profit_factor * perf.weight
                if perf.total_trades >= 5 else 0
            )
            bot_stats.append({
                "name": bot.name,
                "description": bot.description,
                "win_rate": round(perf.win_rate, 1),
                "profit_factor": round(perf.profit_factor, 2),
                "total_trades": perf.total_trades,
                "total_pnl": round(perf.total_pnl, 2),
                "weight": round(perf.weight, 2),
                "regime_weight": round(self.regime_weights.get(bot.name, 1.0), 2),
                "score": round(score, 3),
            })

        return sorted(bot_stats, key=lambda x: x["score"], reverse=True)

    def get_learning_insights(self) -> Dict:
        """Get insights from deep learning"""
        return self.deep_learning.get_learning_summary()

    def get_ml_statistics(self) -> Dict:
        """Get ML training data statistics"""
        return self.ml_extractor.get_statistics()

    def export_ml_training_data(self, filepath: str = None):
        """Export ML training data to CSV"""
        self.ml_extractor.export_to_csv(filepath)

    def get_ml_training_data(self):
        """Get ML training data as X, y arrays"""
        return self.ml_extractor.get_training_data()

    def reset_daily(self):
        """Reset daily counters (call at market open)"""
        self.daily_trades = []
        self.daily_pnl = 0

        # Reset adaptive risk controller
        if self.risk_controller_active and self.risk_controller:
            self.risk_controller.reset_daily()

        # Reset capital allocator daily tracking
        if self.allocator_active and self.capital_allocator:
            self.capital_allocator.reset_daily()

        # Rebalance capital based on current regime
        if self.allocator_active and self.capital_allocator:
            regime = "UNKNOWN"
            if hasattr(self, '_current_inst_analysis') and self._current_inst_analysis:
                regime = self._current_inst_analysis.regime.value
            self.capital_allocator.rebalance_sleeves(regime)

        # Run automated parameter optimization (learns from previous days)
        if self.optimizer_active and self.parameter_optimizer:
            print("[Ensemble] Running daily parameter optimization...")
            results = self.run_optimization()
            if results:
                print(f"[Optimizer] Applied {len(results)} parameter improvements:")
                for r in results:
                    print(f"  - {r['parameter']}: {r['old_value']} → {r['new_value']}")
            else:
                print("[Optimizer] No optimizations needed (insufficient data or no improvements found)")

    def report_trade_outcome(
        self,
        index: str,
        option_type: str,
        bots_involved: List[str],
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        outcome: str,
        market_conditions: Dict[str, Any],
        confidence: float,
        holding_time_minutes: float = 0
    ):
        """
        Report a completed trade outcome for learning.

        Call this after every trade closes (win, loss, or breakeven).
        The Adaptive Risk Controller will learn from this outcome.
        """
        if self.risk_controller_active and self.risk_controller:
            from datetime import datetime

            trade_outcome = TradeOutcome(
                timestamp=datetime.now(),
                index=index,
                option_type=option_type,
                bots_involved=bots_involved,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl=pnl,
                pnl_pct=pnl_pct,
                outcome=outcome,
                market_conditions=market_conditions,
                mtf_mode=self.mtf_engine.config.get("mode", "balanced") if self.mtf_active else "disabled",
                confidence=confidence,
                holding_time_minutes=holding_time_minutes,
            )

            self.risk_controller.record_trade(trade_outcome)

        # Record trade with Parameter Optimizer for each bot involved
        if self.optimizer_active and self.parameter_optimizer:
            outcome_str = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"
            for bot_name in bots_involved:
                bot_params = self._get_bot_parameters(bot_name)
                if bot_params:
                    self.parameter_optimizer.record_trade_with_params(
                        bot_name=bot_name,
                        parameters=bot_params,
                        outcome=outcome_str,
                        pnl=pnl,
                        market_conditions=market_conditions
                    )

    def get_risk_status(self) -> Dict[str, Any]:
        """Get current adaptive risk controller status"""
        if self.risk_controller_active and self.risk_controller:
            return self.risk_controller.get_status()
        return {"enabled": False}

    def _apply_optimized_parameters(self):
        """Apply optimized parameters to all bots"""
        if not self.optimizer_active or not self.parameter_optimizer:
            return

        for bot in self.bots:
            self.parameter_optimizer.apply_to_bot(bot, bot.name)

    def _get_bot_parameters(self, bot_name: str) -> Dict[str, float]:
        """Get current parameters from a bot for tracking"""
        if bot_name not in self.bot_map:
            return {}

        bot = self.bot_map[bot_name]
        if hasattr(bot, 'parameters'):
            return dict(bot.parameters)
        return {}

    def run_optimization(self, bot_name: str = None) -> List[Dict]:
        """
        Run parameter optimization for one or all bots.

        Call periodically (e.g., daily) or after significant trade history builds up.
        Returns list of optimization changes made.
        """
        if not self.optimizer_active or not self.parameter_optimizer:
            return []

        results = self.parameter_optimizer.optimize(bot_name)

        # Apply new parameters to bots
        if results:
            self._apply_optimized_parameters()

        return [
            {
                "parameter": r.parameter_name,
                "old_value": r.old_value,
                "new_value": r.new_value,
                "reason": r.reason,
            }
            for r in results
        ]

    def get_optimization_report(self) -> Dict[str, Any]:
        """Get comprehensive parameter optimization report"""
        if not self.optimizer_active or not self.parameter_optimizer:
            return {"enabled": False}

        return self.parameter_optimizer.get_optimization_report()

    def resume_trading(self):
        """Resume trading after a halt"""
        if self.risk_controller_active and self.risk_controller:
            self.risk_controller.resume_trading()

    def feed_candles(self, index: str, candles_1m: List[Dict]):
        """
        Feed 1-minute candles to the Multi-Timeframe engine.

        The MTF engine will automatically build 5m and 15m candles from 1m data.
        Call this with historical 1m candles to enable MTF filtering.

        Args:
            index: Index name (e.g., "NIFTY50", "BANKNIFTY")
            candles_1m: List of 1m candle dicts with keys: open, high, low, close, volume, timestamp
        """
        if self.mtf_active and self.mtf_engine:
            self.mtf_engine.build_candles_from_1m(index, candles_1m)
            print(f"[MTF] Loaded {len(candles_1m)} 1m candles for {index}")

    def add_candle(self, index: str, timeframe: str, candle: Dict):
        """
        Add a single candle to the MTF engine.

        Use this for real-time candle updates.

        Args:
            index: Index name
            timeframe: "1m", "5m", or "15m"
            candle: Candle dict with keys: open, high, low, close, volume, timestamp
        """
        if self.mtf_active and self.mtf_engine:
            self.mtf_engine.add_candle(index, timeframe, candle)

    def get_mtf_status(self) -> Dict[str, Any]:
        """Get Multi-Timeframe engine status for all indices"""
        if self.mtf_active and self.mtf_engine:
            return self.mtf_engine.get_status()
        return {"enabled": False}

    def set_capital_preservation(self, enabled: bool):
        """
        Enable/disable capital preservation mode.

        When enabled:
        - Requires higher confidence (70% vs 50%)
        - Requires at least 2 agreeing bots
        - More conservative position sizing
        - Prioritizes not losing over making money

        Args:
            enabled: True to enable, False to disable
        """
        self.capital_preservation_mode = enabled
        mode_str = "ENABLED" if enabled else "DISABLED"
        print(f"[Ensemble] Capital Preservation Mode: {mode_str}")

        if enabled:
            print("[Ensemble] Higher confidence required, fewer but safer trades")
        else:
            print("[Ensemble] Standard mode, normal trading parameters")

    def enable_bot(self, bot_name: str):
        """Re-enable a disabled bot"""
        if bot_name in self.disabled_bots:
            self.disabled_bots.remove(bot_name)
            print(f"[Ensemble] {bot_name} RE-ENABLED")

            # Reset weight to minimum viable
            if bot_name in self.bot_map:
                self.bot_map[bot_name].performance.weight = 0.5
        else:
            print(f"[Ensemble] {bot_name} was not disabled")

    def disable_bot(self, bot_name: str, reason: str = "manual"):
        """Disable a bot from contributing signals"""
        self.disabled_bots.add(bot_name)
        print(f"[Ensemble] {bot_name} DISABLED (reason: {reason})")

        # Set weight to 0
        if bot_name in self.bot_map:
            self.bot_map[bot_name].performance.weight = 0.0

    def get_safety_status(self) -> Dict[str, Any]:
        """Get current safety/protection status"""
        return {
            "capital_preservation_mode": getattr(self, 'capital_preservation_mode', True),
            "mtf_active": self.mtf_active,
            "mtf_mode": self.mtf_engine.config.get("mode", "balanced") if self.mtf_active else None,
            "veto_active": self.veto_active,
            "disabled_bots": list(getattr(self, 'disabled_bots', set())),
            "active_bots": [
                {"name": bot.name, "weight": bot.performance.weight}
                for bot in self.bots
                if bot.name not in getattr(self, 'disabled_bots', set())
            ],
            "min_confidence_required": 70 if getattr(self, 'capital_preservation_mode', True) else 50,
            "min_bots_required": 2 if getattr(self, 'capital_preservation_mode', True) else 1,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # HEDGE FUND GRADE REPORTING - Capital, Drift, Execution Intelligence
    # ═══════════════════════════════════════════════════════════════════════════

    def get_capital_allocation_status(self) -> Dict[str, Any]:
        """Get current capital allocation status across strategy sleeves"""
        if not self.allocator_active or not self.capital_allocator:
            return {"enabled": False}

        return self.capital_allocator.get_allocation_report()

    def get_drift_report(self) -> Dict[str, Any]:
        """Get model drift detection report for all bots"""
        if not self.drift_active or not self.drift_detector:
            return {"enabled": False}

        return self.drift_detector.get_drift_report()

    def get_execution_report(self) -> Dict[str, Any]:
        """Get execution quality report"""
        if not self.execution_active or not self.execution_engine:
            return {"enabled": False}

        return self.execution_engine.get_execution_report()

    def get_hedge_fund_status(self) -> Dict[str, Any]:
        """Get comprehensive hedge fund grade system status"""
        return {
            "timestamp": datetime.now().isoformat(),
            "institutional_layer": {
                "enabled": self.institutional_active,
                "current_regime": self._current_inst_analysis.regime.value if hasattr(self, '_current_inst_analysis') and self._current_inst_analysis else None,
                "trading_condition": self._current_inst_analysis.trading_condition.value if hasattr(self, '_current_inst_analysis') and self._current_inst_analysis else None,
            } if self.institutional_active else {"enabled": False},
            "capital_allocator": self.get_capital_allocation_status(),
            "drift_detector": {
                "enabled": self.drift_active,
                "quarantined_bots": [
                    bot for bot in self.drift_detector.model_status
                    if self.drift_detector.model_status[bot] == ModelStatus.QUARANTINED
                ] if self.drift_active and self.drift_detector else [],
            } if self.drift_active else {"enabled": False},
            "execution_engine": {
                "enabled": self.execution_active,
                "summary": self.get_execution_report().get("summary", {}) if self.execution_active else {},
            } if self.execution_active else {"enabled": False},
        }

    def rebalance_capital(self, regime: str = None):
        """Trigger capital rebalancing across strategy sleeves"""
        if not self.allocator_active or not self.capital_allocator:
            return

        # Use current regime if not specified
        if not regime and hasattr(self, '_current_inst_analysis') and self._current_inst_analysis:
            regime = self._current_inst_analysis.regime.value

        self.capital_allocator.rebalance_sleeves(regime or "UNKNOWN")
        print(f"[CAPITAL] Rebalanced sleeves for {regime} regime")

    def rehabilitate_bot(self, bot_name: str) -> bool:
        """Attempt to rehabilitate a quarantined bot"""
        if not self.drift_active or not self.drift_detector:
            return False

        return self.drift_detector.check_rehabilitation(bot_name)

    def update_order_book(self, symbol: str, bids: list, asks: list):
        """Update order book for execution engine"""
        if self.execution_active and self.execution_engine:
            self.execution_engine.update_order_book(symbol, bids, asks)

    def analyze_all_indices(
        self,
        indices_data: Dict[str, Dict[str, Any]]
    ) -> List[BotDecision]:
        """Analyze all indices and return decisions"""
        decisions = []
        for index, market_data in indices_data.items():
            decision = self.analyze(index, market_data)
            if decision:
                decisions.append(decision)
        return decisions

    def to_dict(self) -> Dict[str, Any]:
        """Convert ensemble state to dictionary"""
        return {
            "config": asdict(self.config),
            "performance": self.ensemble_performance,
            "active_positions": self.active_positions,
            "daily_trades_count": len(self.daily_trades),
            "current_regime": self.current_regime.value if self.current_regime else None,
            "learning_summary": self.deep_learning.get_learning_summary(),
            "bots": self.get_bot_status(),
        }

    def save_state(self, filepath: str = None):
        """Save ensemble state to file"""
        self._save_state()

    def load_state(self, filepath: str = None):
        """Load ensemble state from file"""
        self._load_state()

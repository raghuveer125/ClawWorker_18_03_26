"""
Scalping Engine - Main orchestrator for the 23-agent system.

Coordinates all agents in a continuous loop:
0. Safety Check (Agent 0) - KillSwitch runs FIRST
1. Data Collection (Agents 1-4) - includes LatencyGuardian
2. Market Regime (Agent 5) - runs first in analysis
3. Analysis (Agents 6-9)
4. Quality Gate (Agent 10) - SignalQuality filters weak signals
5. Risk Layer (Agents 11-13) - LiquidityMonitor, RiskGuardian, CorrelationGuard
6. Execution (Agents 14-17) - MetaAllocator, Entry, Exit, PositionManager
7. Learning (Agents 20-22) - runs periodically (QuantLearner, StrategyOptimizer, ExitOptimizer)
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Use local base module with fallbacks
from .base import BotContext, BotResult, BotStatus, create_event_bus

from .config import ScalpingConfig, IndexType, get_index_config
from . import api as api_state  # Import API state for dashboard updates
from .context_guard import ContextIntegrityError, raise_for_critical_issues, summarize_issues, validate_phase_inputs
from .execution_microstructure import (
    compute_momentum_strength,
    detect_liquidity_vacuum,
    detect_volatility_burst,
    estimate_queue_risk,
    run_pre_entry_confirmation,
)
from .market_simulator import MarketSimulator
from .replay_data_adapter import ReplayDataAdapter
from .replay_reporting import flatten_signals
from .agents import (
    # Safety Layer (1) - runs FIRST
    KillSwitchAgent,
    # Data Layer (4)
    DataFeedAgent,
    OptionChainAgent,
    FuturesAgent,
    LatencyGuardianAgent,
    # Analysis Layer (5)
    MarketRegimeAgent,
    StructureAgent,
    MomentumAgent,
    TrapDetectorAgent,
    VolatilitySurfaceAgent,
    DealerPressureAgent,
    StrikeSelectorAgent,
    # Quality Gate (1) - filters weak signals
    SignalQualityAgent,
    # Risk Layer (3)
    LiquidityMonitorAgent,
    RiskGuardianAgent,
    CorrelationGuardAgent,
    # Execution Layer (4)
    MetaAllocatorAgent,
    EntryAgent,
    ExitAgent,
    PositionManagerAgent,
    # Learning Layer (3)
    QuantLearnerAgent,
    StrategyOptimizerAgent,
    ExitOptimizerAgent,
)
from .agents.kill_switch_agent import KillSwitchReason


class ScalpingEngine:
    LIVE_PAPER = "LIVE_PAPER"
    REPLAY = "REPLAY"
    LIVE_REAL = "LIVE_REAL"
    IDLE = "IDLE"
    """
    Main orchestrator for the 23-agent scalping system.

    Architecture:
    ┌─────────────────────────────────────────────────────────────────┐
    │                     SCALPING ENGINE (21 AGENTS)                  │
    │                                                                  │
    │  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐     │
    │  │ DATA     │ → │ ANALYSIS │ → │ RISK     │ → │ EXECUTION│     │
    │  │ (1-4)    │   │ (5-9)    │   │ (11-13)  │   │ (14-17)  │     │
    │  └──────────┘   └──────────┘   └──────────┘   └──────────┘     │
    │   Feed,Chain     Regime,Struct  Liquidity,    Meta,Entry       │
    │   Futures,       Momentum,Trap  Risk,Correl   Exit,Position    │
    │   Latency        StrikeSelect                                   │
    │                                                                  │
    │                     ┌─────────────────────────┐                 │
    │                     │ LEARNING (18-20)        │ (periodic)      │
    │                     │ Quant, Strategy, Exit   │                 │
    │                     └─────────────────────────┘                 │
    └─────────────────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        config: Optional[ScalpingConfig] = None,
        dry_run: bool = True,
        event_bus=None,
        replay_mode: bool = False,
        replay_csv_path: Optional[str] = None,
        replay_interval_ms: int = 200,
        live_real_enabled: bool = False,
    ):
        self.config = config or ScalpingConfig()
        self.dry_run = dry_run
        self.event_bus = event_bus or create_event_bus("memory")
        self.live_real_enabled = live_real_enabled
        self.live_real_requested = not dry_run
        self.replay_mode = replay_mode
        self.replay_csv_path = replay_csv_path
        self.replay_interval_ms = replay_interval_ms
        self.live_interval_seconds = 5.0
        self.replay_adapter = ReplayDataAdapter(replay_csv_path, interval_ms=replay_interval_ms) if replay_mode and replay_csv_path else None
        self.market_simulator = MarketSimulator() if self.replay_adapter else None
        self.mode = self.REPLAY if replay_mode else self.IDLE
        self.replay_job_active = replay_mode
        self.replay_direction = 1
        self.replay_job_name: Optional[str] = None
        self.last_replay_report: Dict[str, Any] = {}
        self._cycle_lock = asyncio.Lock()
        self._execution_state_lock = asyncio.Lock()
        self._ist = ZoneInfo("Asia/Kolkata")
        self._skip_next_execution_stage = False
        self._last_cycle_overrun = 0.0
        self.execution_interval_ms = getattr(self.config, "execution_loop_interval_ms", 300)
        self._execution_task: Optional[asyncio.Task] = None
        self._signal_snapshot_version = 0
        self._last_micro_execution_version = 0
        self._skip_next_execution_stage = False
        self._last_cycle_overrun = 0.0
        self._micro_quote_state: Dict[str, Dict[str, Any]] = {}
        self._micro_tick_history: Dict[str, List[Dict[str, float]]] = {}

        # Initialize all agents
        self._init_agents()

        # State
        self.running = False
        self.cycle_count = 0
        self.last_learning_run = None
        self.context = None

        # Performance tracking
        self.stats = {
            "cycles": 0,
            "signals_generated": 0,
            "entries_taken": 0,
            "partial_exits": 0,
            "full_exits": 0,
            "total_pnl": 0,
        }

        self.data_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.analysis_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.risk_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.execution_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.learning_queue: asyncio.Queue = asyncio.Queue(maxsize=1)

        # Initialize API state
        self._sync_api_state()

    def _init_agents(self):
        """Initialize all 21 agents (including Kill Switch)."""
        # ═══════════════════════════════════════════════════════════════
        # SAFETY LAYER (Agent 0) - runs FIRST, can halt entire pipeline
        # ═══════════════════════════════════════════════════════════════
        self.kill_switch = KillSwitchAgent(
            event_bus=self.event_bus,
        )

        # ═══════════════════════════════════════════════════════════════
        # DATA LAYER (Agents 1-4)
        # ═══════════════════════════════════════════════════════════════
        self.data_feed = DataFeedAgent(
            symbols=[idx.value for idx in self.config.indices],
            event_bus=self.event_bus,
        )
        self.option_chain = OptionChainAgent(
            symbols=[idx.value for idx in self.config.indices],
            event_bus=self.event_bus,
        )
        self.futures = FuturesAgent(
            event_bus=self.event_bus,
        )
        self.latency_guardian = LatencyGuardianAgent(
            event_bus=self.event_bus,
        )

        # ═══════════════════════════════════════════════════════════════
        # ANALYSIS LAYER (Agents 5-11)
        # ═══════════════════════════════════════════════════════════════
        self.market_regime = MarketRegimeAgent(
            event_bus=self.event_bus,
        )
        self.structure = StructureAgent(
            timeframes=self.config.structure_timeframes,
            event_bus=self.event_bus,
        )
        self.momentum = MomentumAgent(
            event_bus=self.event_bus,
        )
        self.trap_detector = TrapDetectorAgent(
            event_bus=self.event_bus,
        )
        self.volatility_surface = VolatilitySurfaceAgent(
            event_bus=self.event_bus,
        )
        self.dealer_pressure = DealerPressureAgent(
            event_bus=self.event_bus,
        )
        self.strike_selector = StrikeSelectorAgent(
            event_bus=self.event_bus,
        )

        # ═══════════════════════════════════════════════════════════════
        # QUALITY GATE (Agent 12) - Filters weak signals
        # ═══════════════════════════════════════════════════════════════
        self.signal_quality = SignalQualityAgent(
            event_bus=self.event_bus,
        )

        # ═══════════════════════════════════════════════════════════════
        # RISK LAYER (Agents 13-15)
        # ═══════════════════════════════════════════════════════════════
        self.liquidity_monitor = LiquidityMonitorAgent(
            event_bus=self.event_bus,
        )
        self.risk_guardian = RiskGuardianAgent(
            event_bus=self.event_bus,
        )
        self.correlation_guard = CorrelationGuardAgent(
            event_bus=self.event_bus,
        )

        # ═══════════════════════════════════════════════════════════════
        # EXECUTION LAYER (Agents 16-19)
        # ═══════════════════════════════════════════════════════════════
        self.meta_allocator = MetaAllocatorAgent(
            event_bus=self.event_bus,
        )
        self.entry = EntryAgent(
            dry_run=self.dry_run,
            event_bus=self.event_bus,
        )
        self.exit = ExitAgent(
            dry_run=self.dry_run,
            event_bus=self.event_bus,
        )
        self.position_manager = PositionManagerAgent(
            event_bus=self.event_bus,
        )

        # ═══════════════════════════════════════════════════════════════
        # LEARNING LAYER (Agents 20-22)
        # ═══════════════════════════════════════════════════════════════
        self.quant_learner = QuantLearnerAgent(
            min_trades=self.config.min_trades_for_learning,
            event_bus=self.event_bus,
        )
        self.strategy_optimizer = StrategyOptimizerAgent(
            lookback_days=self.config.backtest_lookback_days,
            event_bus=self.event_bus,
        )
        self.exit_optimizer = ExitOptimizerAgent(
            event_bus=self.event_bus,
        )

        # Map agent names to their ID for API updates
        self._agent_map = {
            "kill_switch": 0,  # Safety agent - runs FIRST
            "data_feed": 1, "option_chain": 2, "futures": 3, "latency_guardian": 4,
            "market_regime": 5, "structure": 6, "momentum": 7, "trap_detector": 8,
            "volatility_surface": 9, "dealer_pressure": 10, "strike_selector": 11,
            "signal_quality": 12,
            "liquidity_monitor": 13, "risk_guardian": 14, "correlation_guard": 15,
            "meta_allocator": 16, "entry": 17, "exit": 18, "position_manager": 19,
            "quant_learner": 20, "strategy_optimizer": 21, "exit_optimizer": 22,
        }

    def _sync_api_state(self):
        """Sync engine state with API state for dashboard."""
        state = api_state.get_state()
        state.running = self.mode != self.IDLE
        state.mode = self.mode
        state.cycle_count = self.cycle_count

    def _update_agent_api(self, agent_name: str, result, status: str = "idle"):
        """Update API state for a single agent."""
        agent_id = self._agent_map.get(agent_name)
        # NOTE: Use 'is not None' because agent_id=0 (KillSwitch) is falsy!
        if agent_id is not None:
            output = result.output if hasattr(result, 'output') else {}
            metrics = result.metrics if hasattr(result, 'metrics') else {}
            api_state.update_agent_status(agent_id, status, output, metrics)

    def _record_flow(self, from_agent: str, to_agent: str, data_type: str, data_size: int = 1):
        """Record data flow for visualization."""
        api_state.record_data_flow(from_agent, to_agent, data_type, data_size)

    async def _reset_stage_queues(self):
        for queue in (
            self.data_queue,
            self.analysis_queue,
            self.risk_queue,
            self.execution_queue,
            self.learning_queue,
        ):
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    break

    async def _publish_execution_snapshot(self, context: BotContext) -> None:
        quality_signals = flatten_signals(context.data.get("quality_filtered_signals", []))
        liquid_signals = flatten_signals(context.data.get("liquidity_filtered_selections", []))
        liquid_map = {
            self._signal_key(signal): signal
            for signal in liquid_signals
            if isinstance(signal, dict)
        }

        merged = []
        for signal in quality_signals:
            if not isinstance(signal, dict):
                continue
            key = self._signal_key(signal)
            liquid_signal = liquid_map.get(key)
            if liquid_signal is None:
                continue
            merged.append(
                {
                    **signal,
                    **liquid_signal,
                    "signal_key": key,
                    "snapshot_time": datetime.now().isoformat(),
                }
            )

        self._signal_snapshot_version += 1
        snapshot = {
            "version": self._signal_snapshot_version,
            "mode": self.mode,
            "created_at": datetime.now().isoformat(),
            "signals": merged,
        }
        async with self._execution_state_lock:
            context.data["signal_snapshot_version"] = self._signal_snapshot_version
            context.data["execution_candidates_snapshot"] = snapshot
            context.data["execution_candidates"] = []
            context.data["executed_snapshot_version"] = None
            context.data["entry_confirmation_state"] = {}
            context.data["liquidity_vacuum"] = {}
            context.data["momentum_strength"] = {}
            context.data["queue_risk"] = {}
            context.data["volatility_burst"] = {}
            context.data["micro_execution_state"] = {
                "version": self._signal_snapshot_version,
                "confirmed": 0,
                "cancelled": 0,
                "status": "awaiting_confirmation",
            }
            context.data["micro_rejections"] = []

    def _signal_key(self, signal: Dict[str, Any]) -> str:
        symbol = str(signal.get("symbol", ""))
        strike = int(float(signal.get("strike", 0) or 0))
        option_type = str(signal.get("option_type", signal.get("side", ""))).upper()
        return f"{symbol}|{strike}|{option_type}"

    def _get_live_option(self, signal: Dict[str, Any]) -> Optional[Any]:
        if not self.context:
            return None
        symbol = str(signal.get("symbol", ""))
        strike = int(float(signal.get("strike", 0) or 0))
        option_type = str(signal.get("option_type", signal.get("side", ""))).upper()
        option_chains = self.context.data.get("option_chains", {})
        chain = option_chains.get(symbol)
        if not chain:
            return None
        for candidate in getattr(chain, "options", []):
            if int(getattr(candidate, "strike", 0) or 0) == strike and str(getattr(candidate, "option_type", "")).upper() == option_type:
                return candidate
        return None

    def _depth_totals(self, option_obj: Any) -> Dict[str, float]:
        bid_levels = list(getattr(option_obj, "top_bid_levels", []) or [])
        ask_levels = list(getattr(option_obj, "top_ask_levels", []) or [])
        return {
            "bid_total": round(
                sum(float(level.get("qty", 0) or 0) for level in bid_levels if isinstance(level, dict)),
                2,
            ),
            "ask_total": round(
                sum(float(level.get("qty", 0) or 0) for level in ask_levels if isinstance(level, dict)),
                2,
            ),
        }

    def _mid_price(self, option_obj: Any, signal: Dict[str, Any]) -> float:
        bid = float(getattr(option_obj, "bid", 0) or 0)
        ask = float(getattr(option_obj, "ask", 0) or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        ltp = float(getattr(option_obj, "ltp", 0) or 0)
        if ltp > 0:
            return ltp
        return float(signal.get("premium", signal.get("entry", 0)) or 0)

    def _futures_strength(self, symbol: str) -> float:
        if not self.context:
            return 0.0
        momentum_signals = self.context.data.get("momentum_signals", [])
        strengths = [
            float(getattr(signal, "strength", 0) or 0)
            for signal in momentum_signals
            if getattr(signal, "symbol", None) == symbol and str(getattr(signal, "signal_type", "")).lower() == "futures_surge"
        ]
        return max(strengths, default=0.0)

    def _volume_spike_strength(self, symbol: str) -> float:
        if not self.context:
            return 0.0
        momentum_signals = self.context.data.get("momentum_signals", [])
        strengths = [
            float(getattr(signal, "strength", 0) or 0)
            for signal in momentum_signals
            if getattr(signal, "symbol", None) == symbol and str(getattr(signal, "signal_type", "")).lower() == "volume_spike"
        ]
        return max(strengths, default=0.0)

    def _adaptive_confirmation_window(self, momentum_score: float) -> int:
        moderate = float(self.config.adaptive_entry_moderate_momentum or 0.45)
        strong = max(float(self.config.adaptive_entry_strong_momentum or 0.75), moderate + 0.01)
        min_window = int(self.config.entry_confirmation_window_min_ms)
        max_window = int(self.config.entry_confirmation_window_max_ms)
        if momentum_score >= strong:
            return min_window
        if momentum_score <= moderate:
            return max_window
        ratio = (momentum_score - moderate) / max(strong - moderate, 1e-6)
        return int(round(max_window - ((max_window - min_window) * ratio)))

    def _risk_still_valid(self, signal: Dict[str, Any]) -> bool:
        if not self.context:
            return False
        if self.context.data.get("trade_disabled"):
            return False
        if self.kill_switch and getattr(self.kill_switch, "state", None) and self.kill_switch.state.active:
            return False
        # Per-index breach check: only block signals for breached indices
        blocked_indices = set(self.context.data.get("risk_blocked_indices", []))
        signal_symbol = signal.get("symbol", "")
        if signal_symbol in blocked_indices:
            return False
        # Global breaches (non-index-specific) still block everything
        global_breaches = [
            b for b in self.context.data.get("risk_breaches", [])
            if not any(idx in b for idx in blocked_indices)
        ]
        if global_breaches:
            return False
        positions = self.context.data.get("positions", [])
        open_positions = len([position for position in positions if getattr(position, "status", "") != "closed"])
        if open_positions >= self.config.max_positions:
            return False
        blocked = set(self.context.data.get("correlation_blocked_signal_keys", []))
        correlation_penalties = self.context.data.get("correlation_signal_penalties", {})
        signal_key = self._signal_key(signal)
        if signal_key in blocked and not (isinstance(correlation_penalties, dict) and correlation_penalties.get(signal_key)):
            return False
        return True

    def _micro_validate_signal(self, signal: Dict[str, Any]) -> tuple[bool, List[str], Dict[str, Any]]:
        if not self.context:
            return False, ["missing_context"], {}
        config = self.config
        symbol = str(signal.get("symbol", ""))
        option_type = str(signal.get("option_type", signal.get("side", ""))).upper()
        signal_key = self._signal_key(signal)
        now = datetime.now()
        reasons: List[str] = []

        opt = self._get_live_option(signal)
        if opt is None:
            return False, ["quote_missing"], {}

        imbalance = float(getattr(opt, "order_book_imbalance", 0) or 0)
        spread_pct = float(getattr(opt, "spread_pct", 0) or 0)
        baseline_spread_pct = float(signal.get("spread_pct", spread_pct) or spread_pct)
        baseline_spread_pct = baseline_spread_pct if baseline_spread_pct > 0 else max(spread_pct, 0.01)
        current_mid = self._mid_price(opt, signal)
        reference_price = float(signal.get("premium", signal.get("entry", current_mid)) or current_mid)
        price_floor = reference_price * (1.0 - (float(config.price_reversal_pct_threshold or 0.0) / 100.0))
        price_not_reversed = current_mid >= price_floor if reference_price > 0 else True
        direction_support = True
        if option_type == "CE" and imbalance < -config.micro_imbalance_threshold:
            direction_support = False
        if option_type == "PE" and imbalance > config.micro_imbalance_threshold:
            direction_support = False
        spread_stable = spread_pct <= baseline_spread_pct * config.micro_spread_cancel_ratio
        entry_conditions_hold = self._entry_conditions_still_hold(symbol)
        risk_valid = self._risk_still_valid(signal)

        previous_quote = self._micro_quote_state.get(signal_key, {})
        current_depth = self._depth_totals(opt)
        previous_depth = dict(previous_quote.get("depth_totals", current_depth) or current_depth)
        vacuum_state = detect_liquidity_vacuum(previous_depth, current_depth, config.liquidity_vacuum_drop_threshold)

        tick_history = list(self._micro_tick_history.get(signal_key, []))
        previous_mid = float(previous_quote.get("mid", current_mid) or current_mid)
        previous_spread = float(previous_quote.get("spread_pct", spread_pct) or spread_pct)
        tick_history.append(
            {
                "mid": current_mid,
                "spread_pct": spread_pct,
                "mid_return": ((current_mid - previous_mid) / previous_mid) if previous_mid > 0 else 0.0,
                "spread_change": max(0.0, spread_pct - previous_spread),
                "timestamp": now.timestamp(),
            }
        )
        tick_history = tick_history[-8:]
        self._micro_tick_history[signal_key] = tick_history
        self._micro_quote_state[signal_key] = {
            "mid": current_mid,
            "spread_pct": spread_pct,
            "depth_totals": current_depth,
            "timestamp": now.isoformat(),
        }

        momentum_state = compute_momentum_strength(
            futures_strength=self._futures_strength(symbol),
            imbalance=imbalance,
            volume_spike=self._volume_spike_strength(symbol),
            strong_threshold=config.adaptive_entry_strong_momentum,
            moderate_threshold=config.adaptive_entry_moderate_momentum,
        )
        burst_state = detect_volatility_burst(
            tick_returns=[float(item.get("mid_return", 0.0) or 0.0) for item in tick_history],
            spread_changes=[float(item.get("spread_change", 0.0) or 0.0) for item in tick_history],
            vol_threshold=config.volatility_burst_vol_threshold,
            spread_threshold=config.volatility_burst_spread_threshold,
        )
        if vacuum_state.get("active"):
            momentum_state["score"] = round(min(1.0, float(momentum_state.get("score", 0.0) or 0.0) + 0.08), 4)
            if momentum_state.get("timing") == "reject":
                momentum_state["timing"] = "confirm_window"

        idx_config = None
        for idx_type in IndexType:
            if idx_type.value == symbol:
                idx_config = get_index_config(idx_type)
                break
        lot_size = idx_config.lot_size if idx_config else 25
        queue_state = estimate_queue_risk(
            qty_ahead=float(current_depth.get("ask_total", 0.0) or 0.0),
            order_size=max(1.0, float(config.entry_lots * lot_size)),
            ratio_threshold=config.queue_risk_ratio_threshold,
            reduce_threshold=config.queue_risk_reduce_threshold,
        )

        confirmation_map = self.context.data.get("entry_confirmation_state", {})
        existing_confirmation = (
            dict(confirmation_map.get(signal_key, {}) or {}) if isinstance(confirmation_map, dict) else {}
        )
        if str(existing_confirmation.get("status", "")) == "cancelled":
            reasons.append("confirmation_already_cancelled")
        confirmation_window_ms = self._adaptive_confirmation_window(float(momentum_state.get("score", 0.0) or 0.0))
        confirmation_window_ms = min(
            int(config.entry_confirmation_window_max_ms),
            max(int(config.entry_confirmation_window_min_ms), confirmation_window_ms),
        )
        if burst_state.get("active") and config.volatility_burst_fast_track:
            confirmation_window_ms = int(config.entry_confirmation_window_min_ms)

        if not direction_support:
            reasons.append("direction_support_failed")
        if not spread_stable:
            reasons.append("spread_widened_in_micro_loop")
        if not price_not_reversed:
            reasons.append("price_reversed_in_confirmation_window")
        if not entry_conditions_hold:
            reasons.append("entry_conditions_not_holding")
        if not risk_valid:
            reasons.append("risk_invalid")

        timing = str(momentum_state.get("timing", "reject"))
        confirmation_state = dict(existing_confirmation)
        if timing == "reject":
            reasons.append("momentum_too_weak_for_entry")
            confirmation_state = {
                **confirmation_state,
                "status": "cancelled",
                "started_at": confirmation_state.get("started_at", now.isoformat()),
                "last_checked_at": now.isoformat(),
                "window_ms": confirmation_window_ms,
                "timing": timing,
                "reasons": ["momentum_too_weak_for_entry"],
            }
        elif timing == "immediate" and not reasons:
            confirmation_state = {
                **confirmation_state,
                "status": "confirmed",
                "started_at": confirmation_state.get("started_at", now.isoformat()),
                "last_checked_at": now.isoformat(),
                "window_ms": confirmation_window_ms,
                "timing": timing,
                "reasons": [],
            }
        elif not reasons:
            confirmed, confirmation_state, confirmation_reasons = run_pre_entry_confirmation(
                direction_support=direction_support,
                momentum_active=True,
                spread_stable=spread_stable,
                price_not_reversed=price_not_reversed,
                state=confirmation_state,
                now=now,
                window_ms=confirmation_window_ms,
            )
            confirmation_state = {
                **confirmation_state,
                "timing": timing,
            }
            if not confirmed:
                reasons.extend(confirmation_reasons)
        else:
            confirmation_state = {
                **confirmation_state,
                "status": "cancelled",
                "started_at": confirmation_state.get("started_at", now.isoformat()),
                "last_checked_at": now.isoformat(),
                "window_ms": confirmation_window_ms,
                "timing": timing,
                "reasons": list(dict.fromkeys(reasons)),
            }

        if queue_state.get("size_scale", 1.0) <= 0.0:
            reasons.append("queue_position_too_deep")
            confirmation_state = {
                **confirmation_state,
                "status": "cancelled",
                "started_at": confirmation_state.get("started_at", now.isoformat()),
                "last_checked_at": now.isoformat(),
                "window_ms": confirmation_window_ms,
                "timing": timing,
                "reasons": list(dict.fromkeys(reasons)),
            }
        if timing == "confirm_window" and confirmation_state.get("status") != "confirmed":
            reasons.append("confirmation_pending")

        updates = {
            "entry_confirmation_state": {
                signal_key: confirmation_state,
            },
            "liquidity_vacuum": {
                signal_key: {
                    **vacuum_state,
                    "timestamp": now.isoformat(),
                }
            },
            "momentum_strength": {
                signal_key: {
                    **momentum_state,
                    "timestamp": now.isoformat(),
                }
            },
            "queue_risk": {
                signal_key: {
                    **queue_state,
                    "timestamp": now.isoformat(),
                }
            },
            "volatility_burst": {
                signal_key: {
                    **burst_state,
                    "timestamp": now.isoformat(),
                }
            },
        }
        return len(reasons) == 0, list(dict.fromkeys(reasons)), updates

    def _tick_momentum_burst(self, symbol: str, option_type: str) -> bool:
        if not self.context:
            return False
        momentum_signals = self.context.data.get("momentum_signals", [])
        symbol_signals = [m for m in momentum_signals if getattr(m, "symbol", None) == symbol]
        if not symbol_signals:
            return False
        for signal in symbol_signals:
            signal_type = str(getattr(signal, "signal_type", "")).lower()
            strength = float(getattr(signal, "strength", 0) or 0)
            price_move = float(getattr(signal, "price_move", 0) or 0)
            if strength < 0.65:
                continue
            if option_type == "CE" and signal_type == "futures_surge" and price_move > 0:
                return True
            if option_type == "PE" and signal_type == "futures_surge" and price_move < 0:
                return True
            if signal_type in {"volume_spike", "gamma_expansion"}:
                return True
        return False

    def _entry_conditions_still_hold(self, symbol: str) -> bool:
        if not self.context:
            return False
        structure_breaks = self.context.data.get("structure_breaks", [])
        momentum_signals = self.context.data.get("momentum_signals", [])
        trap_signals = self.context.data.get("trap_signals", [])
        conditions = self.entry._check_entry_conditions(symbol, structure_breaks, momentum_signals, trap_signals, self.config)
        return len(conditions) >= 2

    async def _micro_execution_loop(self) -> None:
        while self.running:
            try:
                if not self.context or self.mode in {self.IDLE, self.REPLAY}:
                    await asyncio.sleep(self.execution_interval_ms / 1000.0)
                    continue

                async with self._execution_state_lock:
                    snapshot = dict(self.context.data.get("execution_candidates_snapshot", {}) or {})

                version = int(snapshot.get("version", 0) or 0)
                signals = snapshot.get("signals", [])
                if not version or not isinstance(signals, list) or not signals:
                    await asyncio.sleep(self.execution_interval_ms / 1000.0)
                    continue

                confirmed: List[Dict[str, Any]] = []
                cancelled: List[Dict[str, Any]] = []
                confirmation_states: Dict[str, Any] = {}
                liquidity_vacuum_map: Dict[str, Any] = {}
                momentum_strength_map: Dict[str, Any] = {}
                queue_risk_map: Dict[str, Any] = {}
                volatility_burst_map: Dict[str, Any] = {}
                now = datetime.now()
                for signal in signals:
                    if not isinstance(signal, dict):
                        continue
                    snapshot_time = signal.get("snapshot_time")
                    if isinstance(snapshot_time, str):
                        try:
                            age_seconds = (now - datetime.fromisoformat(snapshot_time)).total_seconds()
                            if age_seconds > self.config.micro_signal_confirmation_ttl_seconds:
                                cancelled.append({**signal, "rejection_reasons": ["snapshot_expired"]})
                                continue
                        except ValueError:
                            pass

                    ok, reasons, updates = self._micro_validate_signal(signal)
                    confirmation_states.update(dict(updates.get("entry_confirmation_state", {}) or {}))
                    liquidity_vacuum_map.update(dict(updates.get("liquidity_vacuum", {}) or {}))
                    momentum_strength_map.update(dict(updates.get("momentum_strength", {}) or {}))
                    queue_risk_map.update(dict(updates.get("queue_risk", {}) or {}))
                    volatility_burst_map.update(dict(updates.get("volatility_burst", {}) or {}))
                    if ok:
                        confirmed.append({**signal, "micro_confirmed_at": now.isoformat()})
                    else:
                        cancelled.append({**signal, "rejection_reasons": reasons})

                async with self._execution_state_lock:
                    latest = dict(self.context.data.get("execution_candidates_snapshot", {}) or {})
                    if int(latest.get("version", 0) or 0) == version:
                        self.context.data["execution_candidates"] = confirmed
                        self.context.data["micro_rejections"] = cancelled
                        self.context.data["entry_confirmation_state"] = confirmation_states
                        self.context.data["liquidity_vacuum"] = liquidity_vacuum_map
                        self.context.data["momentum_strength"] = momentum_strength_map
                        self.context.data["queue_risk"] = queue_risk_map
                        self.context.data["volatility_burst"] = volatility_burst_map
                        self.context.data["micro_execution_state"] = {
                            "version": version,
                            "confirmed": len(confirmed),
                            "cancelled": len(cancelled),
                            "status": "confirmed" if confirmed else "idle",
                            "updated_at": now.isoformat(),
                        }
                if confirmed and version > self._last_micro_execution_version and not self._cycle_lock.locked():
                    await self._run_micro_execution_pass(version, confirmed)
                await asyncio.sleep(self.execution_interval_ms / 1000.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[MicroExecution] Error: {exc}")
                await asyncio.sleep(self.execution_interval_ms / 1000.0)

    async def _run_micro_execution_pass(self, version: int, confirmed: List[Dict[str, Any]]) -> None:
        if not self.context or not confirmed:
            return
        async with self._cycle_lock:
            if not self.running or self.mode == self.IDLE or self.context.data.get("trade_disabled"):
                return
            snapshot = dict(self.context.data.get("execution_candidates_snapshot", {}) or {})
            if int(snapshot.get("version", 0) or 0) != version:
                return
            if version <= self._last_micro_execution_version:
                return

            self.context.data["execution_candidates"] = confirmed
            self.context.data["micro_execution_state"] = {
                **dict(self.context.data.get("micro_execution_state", {}) or {}),
                "status": "executing",
                "version": version,
                "confirmed": len(confirmed),
                "updated_at": datetime.now().isoformat(),
            }

            await self.meta_allocator.run(self.context)
            entry_result = await self.entry.run(self.context)
            await self.exit.run(self.context)
            await self.position_manager.run(self.context)
            api_state.sync_engine_state(self.context)
            self._last_micro_execution_version = version
            self.context.data["executed_snapshot_version"] = version
            self.context.data["micro_execution_state"] = {
                **dict(self.context.data.get("micro_execution_state", {}) or {}),
                "status": "executed" if entry_result.output.get("orders_created", 0) else "confirmed",
                "version": version,
                "orders_created": entry_result.output.get("orders_created", 0),
                "updated_at": datetime.now().isoformat(),
            }

    def _market_hours_active(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(self._ist)
        current = now.astimezone(self._ist).time()
        start = datetime.strptime("09:15", "%H:%M").time()
        end = datetime.strptime("15:30", "%H:%M").time()
        return start <= current <= end

    def resolve_mode(self) -> str:
        if self.replay_job_active:
            return self.REPLAY
        if self._market_hours_active():
            if self.live_real_requested and self.live_real_enabled:
                return self.LIVE_REAL
            return self.LIVE_PAPER
        return self.IDLE

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.replay_mode = mode == self.REPLAY
        if self.entry:
            self.entry.dry_run = mode != self.LIVE_REAL
        if self.exit:
            self.exit.dry_run = mode != self.LIVE_REAL
        self._sync_api_state()

    def _default_replay_dataset(self) -> str:
        return str(
            Path(__file__).resolve().parents[3]
            / "fyersN7"
            / "fyers-2026-03-05"
            / "postmortem"
            / "2026-03-16"
            / "SENSEX"
            / "decision_journal.csv"
        )

    def _reset_runtime_state(self) -> None:
        if not self.context:
            return
        preserved = {
            "config": self.config,
            "engine_mode": self.mode,
            "replay_mode": self.mode == self.REPLAY,
            "broker_calls_disabled": self.mode != self.LIVE_REAL,
        }
        self.context.data.clear()
        self.context.data.update(preserved)
        self._signal_snapshot_version = 0
        self._last_micro_execution_version = 0
        if hasattr(self.position_manager, "_positions"):
            self.position_manager._positions = {}
        if hasattr(self.position_manager, "_trade_log"):
            self.position_manager._trade_log = []
        if hasattr(self.position_manager, "_trade_records"):
            self.position_manager._trade_records = {}
        if hasattr(self.data_feed, "_replay_candles"):
            self.data_feed._replay_candles = {}
        self._micro_quote_state = {}
        self._micro_tick_history = {}
        api_state.sync_engine_state(self.context)

    def start_replay(self, csv_path: str) -> None:
        self.replay_csv_path = csv_path
        self.replay_adapter = ReplayDataAdapter(csv_path, interval_ms=self.replay_interval_ms)
        self.market_simulator = MarketSimulator()
        self.replay_job_active = True
        self.replay_job_name = Path(csv_path).name
        self.last_replay_report = {}
        self.set_mode(self.REPLAY)
        self._reset_runtime_state()

    def finish_replay(self, report: Optional[Dict[str, Any]] = None) -> None:
        self.last_replay_report = report or self.last_replay_report
        self.replay_job_active = False
        self.replay_job_name = None
        self.replay_mode = False
        self.replay_adapter = None
        self.market_simulator = None
        self.set_mode(self.resolve_mode())

    def has_replay_remaining(self) -> bool:
        if not self.replay_adapter:
            return False
        if self.replay_direction < 0:
            return self.replay_adapter.has_previous()
        return self.replay_adapter.has_next()

    def _cycle_now(self) -> datetime:
        cycle_timestamp = self.context.data.get("cycle_timestamp") if self.context else None
        if isinstance(cycle_timestamp, str):
            try:
                return datetime.fromisoformat(cycle_timestamp)
            except ValueError:
                pass
        return datetime.now()

    def _prepare_replay_payload(self) -> bool:
        if not self.replay_adapter or not self.market_simulator:
            return False
        snapshot = self.replay_adapter.step(self.replay_direction)
        if snapshot is None:
            return False
        replay_payload = self.market_simulator.simulate_snapshot(snapshot)
        replay_payload["events"] = {
            "market_tick": replay_payload.get("spot_data", {}),
            "option_update": replay_payload.get("option_rows", {}),
            "futures_update": replay_payload.get("futures_data", {}),
        }
        replay_payload["raw_events"] = list(getattr(snapshot, "events", []))
        self.context.data["replay_mode"] = True
        self.context.data["replay_payload"] = replay_payload
        self.context.data["cycle_timestamp"] = replay_payload.get("timestamp")
        self.context.data["broker_calls_disabled"] = True
        return True

    def _trigger_context_guard(self, phase: str, issues: List[Any]) -> None:
        details = {
            "phase": phase,
            "issues": [{"field": issue.field, "message": issue.message} for issue in issues],
        }
        self.kill_switch.trigger(KillSwitchReason.MANUAL, details)
        state = api_state.get_state()
        state.kill_switch_active = True
        state.kill_switch_reason = f"context_guard:{phase}"
        state.kill_switch_triggered_at = datetime.now().isoformat()

    def _validate_stage_inputs(self, phase: str) -> List[Any]:
        issues = validate_phase_inputs(phase, self.context)
        if issues:
            warning_text = summarize_issues(issues)
            print(f"[Cycle {self.cycle_count}] Context guard ({phase}): {warning_text}")
        try:
            raise_for_critical_issues(phase, self.context)
        except ContextIntegrityError as exc:
            self._trigger_context_guard(phase, exc.issues)
            raise
        return issues

    async def _emit_market_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        event = {
            "event_type": event_type,
            "mode": self.mode,
            "cycle": self.cycle_count,
            "timestamp": str(self.context.data.get("cycle_timestamp") or datetime.now().isoformat()),
            "data": api_state._serialize_item(payload),
        }
        await api_state.broadcast_update(event_type, event)

    async def _run_agent_with_timeout(self, agent, context, agent_name: str, timeout_secs: float = 30.0):
        """Run an agent with timeout to prevent hangs."""
        try:
            return await asyncio.wait_for(agent.run(context), timeout=timeout_secs)
        except asyncio.TimeoutError:
            print(f"[Engine] {agent_name} timed out after {timeout_secs}s")
            from .base import BotResult, BotStatus
            return BotResult(
                bot_id=agent_name,
                bot_type=agent_name,
                status=BotStatus.ERROR,
                output={"error": "timeout"},
                metrics={"timeout": True},
            )
        except Exception as e:
            print(f"[Engine] {agent_name} error: {e}")
            from .base import BotResult, BotStatus
            return BotResult(
                bot_id=agent_name,
                bot_type=agent_name,
                status=BotStatus.ERROR,
                output={"error": str(e)},
                metrics={"exception": True},
            )

    def _error_result(self, agent_name: str, exception: Exception):
        """Create error result for failed parallel agent."""
        from .base import BotResult, BotStatus
        print(f"[Engine] {agent_name} parallel error: {exception}")
        return BotResult(
            bot_id=agent_name,
            bot_type=agent_name,
            status=BotStatus.ERROR,
            output={"error": str(exception)},
            metrics={"parallel_exception": True},
        )

    def _should_run_exit_optimizer(self) -> bool:
        """Check if exit optimizer should run (end of day or every 200 trades)."""
        # Run at end of day
        if self._is_end_of_day():
            return True
        # Run every 200 trades
        if self.stats.get("entries_taken", 0) > 0 and self.stats["entries_taken"] % 200 == 0:
            return True
        return False

    async def start(self):
        """Start the scalping engine."""
        self.running = True
        self.set_mode(self.resolve_mode())
        state = api_state.get_state()
        learning_mode = state.learning_mode or getattr(self.config, "learning_mode_default", "hybrid")
        self.context = BotContext(
            pipeline_id="scalping_engine",
            trigger="start",
            data={
                "config": self.config,
                "engine_mode": self.mode,
                "replay_mode": self.mode == self.REPLAY,
                "broker_calls_disabled": self.mode != self.LIVE_REAL,
                "learning_mode": learning_mode,
                "learning_profile_id": state.learning_active_profile_id,
            },
        )

        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║         SCALPING ENGINE - 21 AGENT AUTONOMOUS SYSTEM             ║
╠══════════════════════════════════════════════════════════════════╣
║  Mode: {self.mode:<56}║
║  Indices: {', '.join([idx.name for idx in self.config.indices]):<45}║
║  Capital: ₹{self.config.total_capital:,.0f}                                          ║
║  Risk/Trade: {self.config.risk_per_trade_pct}%                                           ║
║  Daily Limit: {self.config.daily_loss_limit_pct}%                                          ║
╠══════════════════════════════════════════════════════════════════╣
║  21 Agents Initialized:                                           ║
║    Safety Layer:   KillSwitch (1) - runs FIRST every cycle       ║
║    Data Layer:     Feed, Chain, Futures, LatencyGuard (4)        ║
║    Analysis Layer: Regime, Structure, Momentum, Trap, Strike (5) ║
║    Quality Gate:   SignalQuality (1) - filters weak signals      ║
║    Risk Layer:     Liquidity, RiskGuard, CorrelationGuard (3)    ║
║    Execution Layer: Meta, Entry, Exit, Position (4)              ║
║    Learning Layer: QuantLearner, StrategyOptimizer, ExitOpt (3)  ║
╚══════════════════════════════════════════════════════════════════╝
        """)

        await self.event_bus.start()
        if self._execution_task is None or self._execution_task.done():
            self._execution_task = asyncio.create_task(self._micro_execution_loop())

    async def stop(self):
        """Stop the scalping engine."""
        self.running = False
        if self._execution_task:
            self._execution_task.cancel()
            try:
                await self._execution_task
            except asyncio.CancelledError:
                pass
            self._execution_task = None
        await self.event_bus.stop()
        try:
            from .debate_client import get_debate_client

            await get_debate_client().close()
        except Exception:
            pass

        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                    SCALPING ENGINE STOPPED                        ║
╠══════════════════════════════════════════════════════════════════╣
║  Cycles Run: {self.stats['cycles']:<50}║
║  Signals Generated: {self.stats['signals_generated']:<43}║
║  Entries Taken: {self.stats['entries_taken']:<47}║
║  Total P&L: ₹{self.stats['total_pnl']:,.2f}                                        ║
╚══════════════════════════════════════════════════════════════════╝
        """)

    async def run_cycle(self) -> Dict[str, Any]:
        """
        Run a single scalping cycle.

        Flow:
        0. Safety Check (agent 0) - KillSwitch runs FIRST every cycle
        1. Data Collection (agents 1-4) - includes latency check
        2. Market Regime (agent 5) - runs first in analysis
        3. Analysis (agents 6-9) - PARALLEL: Structure, Momentum, TrapDetector
        4. Quality Gate (agent 10) - SignalQuality filters weak signals
        5. Risk Layer (agents 11-13) - Liquidity, Risk, Correlation
        6. Execution (agents 14-17) - Meta, Entry, Exit, Position
        7. Learning (agents 18-20) - periodic (ExitOptimizer: EOD or 200 trades)
        """
        async with self._cycle_lock:
            self.cycle_count += 1
            cycle_start = datetime.now()
            results: Dict[str, Any] = {}

            try:
                self.set_mode(self.resolve_mode() if not self.replay_job_active else self.REPLAY)
                if self.mode == self.IDLE:
                    state = api_state.get_state()
                    state.running = False
                    state.mode = self.IDLE
                    state.last_cycle_time = datetime.now(self._ist).isoformat()
                    return {"status": "idle", "mode": self.IDLE, "cycle": self.cycle_count}

                await self._reset_stage_queues()

                state = api_state.get_state()
                state.running = True
                state.mode = self.mode
                state.start_time = state.start_time or cycle_start.isoformat()

                learning_mode = state.learning_mode or getattr(self.config, "learning_mode_default", "hybrid")
                self.replay_direction = 1 if state.replay_direction >= 0 else -1
                self.context.data["engine_mode"] = self.mode
                self.context.data["replay_mode"] = self.mode == self.REPLAY
                self.context.data["broker_calls_disabled"] = self.mode != self.LIVE_REAL
                self.context.data["learning_mode"] = learning_mode
                self.context.data["learning_profile_id"] = state.learning_active_profile_id
                self.context.data["trade_disabled"] = False
                self.context.data["trade_disabled_reason"] = ""
                self.context.data["synthetic_fallback_used"] = False
                self.context.data["data_sources"] = {}
                self.context.data["replay_speed"] = float(state.replay_speed or 1.0)

                if self.mode == self.REPLAY:
                    if not self._prepare_replay_payload():
                        return {"status": "replay_complete", "cycle": self.cycle_count}
                else:
                    self.context.data.pop("replay_payload", None)
                    self.context.data["cycle_timestamp"] = cycle_start.isoformat()

                self._update_agent_api("kill_switch", None, "running")
                results["kill_switch"] = await self.kill_switch.run(self.context)
                self._update_agent_api("kill_switch", results["kill_switch"], "idle")

                if results["kill_switch"].status == BotStatus.BLOCKED:
                    kill_switch_state = self.kill_switch.get_state() if hasattr(self.kill_switch, "get_state") else {}
                    kill_switch_reason = (
                        kill_switch_state.get("reason")
                        or results["kill_switch"].output.get("reason")
                        or "unknown"
                    )
                    print(f"\n[Cycle {self.cycle_count}] 🛑 KILL SWITCH ACTIVE - Trading halted, monitoring continues")
                    state.kill_switch_active = True
                    state.kill_switch_reason = kill_switch_reason
                    state.kill_switch_triggered_at = (
                        kill_switch_state.get("triggered_at")
                        or results["kill_switch"].output.get("triggered_at")
                        or datetime.now().isoformat()
                    )
                    self.context.data["trade_disabled"] = True
                    self.context.data["trade_disabled_reason"] = f"kill_switch:{kill_switch_reason}"
                else:
                    state.kill_switch_active = False
                    state.kill_switch_reason = None
                    state.kill_switch_triggered_at = None

                async def data_stage_worker():
                    _ = await self.data_queue.get()
                    print(f"\n[Cycle {self.cycle_count}] Phase 1: Data Collection...")

                    self._update_agent_api("data_feed", None, "running")
                    results["data_feed"] = await self.data_feed.run(self.context)
                    self._update_agent_api("data_feed", results["data_feed"], "idle")
                    self._record_flow("DataFeed", "LatencyGuard", "spot", results["data_feed"].metrics.get("symbols_fetched", 1))
                    await self._emit_market_event("market_tick", self.context.data.get("spot_data", {}))

                    if not self.context.data.get("spot_data"):
                        results["cycle_skip_reason"] = "missing_spot_data"
                        await self.analysis_queue.put(None)
                        return

                    self._update_agent_api("option_chain", None, "running")
                    results["option_chain"] = await self.option_chain.run(self.context)
                    self._update_agent_api("option_chain", results["option_chain"], "idle")
                    self._record_flow("OptionChain", "LatencyGuard", "chain", 1)
                    await self._emit_market_event("option_update", self.context.data.get("option_chains", {}))

                    self._update_agent_api("futures", None, "running")
                    results["futures"] = await self.futures.run(self.context)
                    self._update_agent_api("futures", results["futures"], "idle")
                    self._record_flow("Futures", "LatencyGuard", "futures", 1)
                    await self._emit_market_event("futures_update", self.context.data.get("futures_data", {}))

                    if self.mode == self.REPLAY:
                        results["latency_guardian"] = BotResult(
                            bot_id="latency_guardian",
                            bot_type="latency_guardian",
                            status=BotStatus.SKIPPED,
                            output={"message": "LatencyGuardian disabled in replay mode"},
                            metrics={"replay_mode": True},
                        )
                        self._update_agent_api("latency_guardian", results["latency_guardian"], "idle")
                    else:
                        self._update_agent_api("latency_guardian", None, "running")
                        results["latency_guardian"] = await self.latency_guardian.run(self.context)
                        self._update_agent_api("latency_guardian", results["latency_guardian"], "idle")
                        if results["latency_guardian"].status == BotStatus.BLOCKED:
                            results["cycle_skip_reason"] = "latency_blocked"
                            await self.analysis_queue.put(None)
                            return

                    self._record_flow("LatencyGuard", "Regime", "validated", 1)
                    await self.analysis_queue.put(self.context)

                async def analysis_stage_worker():
                    stage_context = await self.analysis_queue.get()
                    if stage_context is None:
                        await self.risk_queue.put(None)
                        return

                    print(f"[Cycle {self.cycle_count}] Phase 2-4: Analysis Pipeline...")
                    self._validate_stage_inputs("analysis")

                    self._update_agent_api("market_regime", None, "running")
                    results["market_regime"] = await self.market_regime.run(stage_context)
                    self._update_agent_api("market_regime", results["market_regime"], "idle")
                    self._record_flow("Regime", "Structure", "regime", 1)
                    self._record_flow("Regime", "Momentum", "regime", 1)

                    for agent_name in ["structure", "momentum", "trap_detector"]:
                        self._update_agent_api(agent_name, None, "running")

                    analysis_tasks = [
                        self.structure.run(stage_context),
                        self.momentum.run(stage_context),
                        self._run_agent_with_timeout(self.trap_detector, stage_context, "trap_detector", timeout_secs=15.0),
                    ]
                    parallel_results = await asyncio.gather(*analysis_tasks, return_exceptions=True)
                    results["structure"] = parallel_results[0] if not isinstance(parallel_results[0], Exception) else self._error_result("structure", parallel_results[0])
                    results["momentum"] = parallel_results[1] if not isinstance(parallel_results[1], Exception) else self._error_result("momentum", parallel_results[1])
                    results["trap_detector"] = parallel_results[2] if not isinstance(parallel_results[2], Exception) else self._error_result("trap_detector", parallel_results[2])
                    self._update_agent_api("structure", results["structure"], "idle")
                    self._update_agent_api("momentum", results["momentum"], "idle")
                    self._update_agent_api("trap_detector", results["trap_detector"], "idle")
                    self._record_flow("Structure", "TrapDetector", "levels", 1)
                    self._record_flow("Momentum", "StrikeSelector", "momentum", 1)

                    self._update_agent_api("volatility_surface", None, "running")
                    results["volatility_surface"] = await self.volatility_surface.run(stage_context)
                    self._update_agent_api("volatility_surface", results["volatility_surface"], "idle")
                    self._record_flow("Momentum", "VolSurface", "volatility", 1)

                    self._update_agent_api("dealer_pressure", None, "running")
                    results["dealer_pressure"] = await self.dealer_pressure.run(stage_context)
                    self._update_agent_api("dealer_pressure", results["dealer_pressure"], "idle")
                    self._record_flow("OptionChain", "DealerPressure", "gamma", 1)

                    self._update_agent_api("strike_selector", None, "running")
                    results["strike_selector"] = await self._run_agent_with_timeout(
                        self.strike_selector, stage_context, "strike_selector", timeout_secs=15.0
                    )
                    self._update_agent_api("strike_selector", results["strike_selector"], "idle")
                    self._record_flow("StrikeSelector", "SignalQuality", "strikes", 1)

                    self._update_agent_api("signal_quality", None, "running")
                    results["signal_quality"] = await self.signal_quality.run(stage_context)
                    self._update_agent_api("signal_quality", results["signal_quality"], "idle")
                    self._record_flow("SignalQuality", "Liquidity", "filtered", 1)

                    await self.risk_queue.put(stage_context)

                async def risk_stage_worker():
                    stage_context = await self.risk_queue.get()
                    if stage_context is None:
                        await self.execution_queue.put(None)
                        return

                    print(f"[Cycle {self.cycle_count}] Phase 5: Risk Layer...")
                    self._validate_stage_inputs("risk")

                    self._update_agent_api("liquidity_monitor", None, "running")
                    results["liquidity_monitor"] = await self.liquidity_monitor.run(stage_context)
                    self._update_agent_api("liquidity_monitor", results["liquidity_monitor"], "idle")
                    self._record_flow("Liquidity", "RiskGuard", "liquid", 1)

                    self._update_agent_api("risk_guardian", None, "running")
                    results["risk_guardian"] = await self.risk_guardian.run(stage_context)
                    self._update_agent_api("risk_guardian", results["risk_guardian"], "idle")
                    self._record_flow("RiskGuard", "Meta", "risk_clear", 1)

                    self._update_agent_api("correlation_guard", None, "running")
                    results["correlation_guard"] = await self.correlation_guard.run(stage_context)
                    self._update_agent_api("correlation_guard", results["correlation_guard"], "idle")

                    if results["risk_guardian"].status == BotStatus.BLOCKED:
                        results["cycle_skip_reason"] = "risk_blocked"
                        await self.execution_queue.put(None)
                        return

                    await self._publish_execution_snapshot(stage_context)

                    await self.execution_queue.put(stage_context)

                async def execution_stage_worker():
                    stage_context = await self.execution_queue.get()
                    if stage_context is None:
                        await self.learning_queue.put(None)
                        return

                    print(f"[Cycle {self.cycle_count}] Phase 6: Execution...")
                    self._validate_stage_inputs("execution")

                    if self.mode != self.REPLAY and self._skip_next_execution_stage:
                        results["cycle_skip_reason"] = "watchdog_execution_skip"
                        print(f"[Cycle {self.cycle_count}] Watchdog: skipping execution after prior overrun ({self._last_cycle_overrun:.2f}s)")
                        self._skip_next_execution_stage = False
                        await self.learning_queue.put(stage_context)
                        return

                    if stage_context.data.get("trade_disabled"):
                        results["cycle_skip_reason"] = stage_context.data.get("trade_disabled_reason", "trade_disabled")
                        print(f"[Cycle {self.cycle_count}] Execution disabled: {results['cycle_skip_reason']}")
                        await self.learning_queue.put(stage_context)
                        return

                    current_snapshot = stage_context.data.get("execution_candidates_snapshot", {})
                    current_version = int(current_snapshot.get("version", 0) or 0) if isinstance(current_snapshot, dict) else 0
                    if current_version and stage_context.data.get("executed_snapshot_version") == current_version:
                        results["cycle_skip_reason"] = "micro_execution_already_ran"
                        await self.learning_queue.put(stage_context)
                        return

                    self._update_agent_api("meta_allocator", None, "running")
                    results["meta_allocator"] = await self.meta_allocator.run(stage_context)
                    self._update_agent_api("meta_allocator", results["meta_allocator"], "idle")
                    self._record_flow("Meta", "Entry", "decision", 1)

                    self._update_agent_api("entry", None, "running")
                    results["entry"] = await self.entry.run(stage_context)
                    self._update_agent_api("entry", results["entry"], "idle")
                    self._record_flow("Entry", "Position", "order", 1)

                    self._update_agent_api("exit", None, "running")
                    results["exit"] = await self.exit.run(stage_context)
                    self._update_agent_api("exit", results["exit"], "idle")

                    self._update_agent_api("position_manager", None, "running")
                    results["position_manager"] = await self.position_manager.run(stage_context)
                    self._update_agent_api("position_manager", results["position_manager"], "idle")
                    self._record_flow("Position", "Exit", "monitor", 1)

                    api_state.sync_engine_state(stage_context)
                    await self.learning_queue.put(stage_context)

                async def learning_stage_worker():
                    stage_context = await self.learning_queue.get()
                    if stage_context is None:
                        return
                    if str(stage_context.data.get("trade_disabled_reason", "")).startswith("kill_switch:"):
                        return
                    if self.mode == self.REPLAY:
                        return
                    if not self._should_run_learning():
                        return

                    print(f"[Cycle {self.cycle_count}] Phase 7: Learning...")
                    self._update_agent_api("quant_learner", None, "running")
                    results["quant_learner"] = await self.quant_learner.run(stage_context)
                    self._update_agent_api("quant_learner", results["quant_learner"], "idle")
                    self._record_flow("Exit", "QuantLearner", "outcome", 1)

                    if self._is_end_of_day():
                        self._update_agent_api("strategy_optimizer", None, "running")
                        results["strategy_optimizer"] = await self.strategy_optimizer.run(stage_context)
                        self._update_agent_api("strategy_optimizer", results["strategy_optimizer"], "idle")
                        self._record_flow("QuantLearner", "StrategyOptimizer", "pattern", 1)

                    if self._should_run_exit_optimizer():
                        self._update_agent_api("exit_optimizer", None, "running")
                        results["exit_optimizer"] = await self.exit_optimizer.run(stage_context)
                        self._update_agent_api("exit_optimizer", results["exit_optimizer"], "idle")
                        self._record_flow("StrategyOptimizer", "ExitOptimizer", "config", 1)

                    self.last_learning_run = self._cycle_now()

                workers = [
                    asyncio.create_task(data_stage_worker()),
                    asyncio.create_task(analysis_stage_worker()),
                    asyncio.create_task(risk_stage_worker()),
                    asyncio.create_task(execution_stage_worker()),
                    asyncio.create_task(learning_stage_worker()),
                ]

                await self.data_queue.put(self.context)
                await asyncio.gather(*workers)

                self._update_stats(results)

                state.cycle_count = self.cycle_count
                state.last_cycle_time = str(self.context.data.get("cycle_timestamp") or datetime.now().isoformat())
                cycle_duration = (datetime.now() - cycle_start).total_seconds()
                state.last_cycle_duration = cycle_duration
                cadence = self.live_interval_seconds
                if self.mode != self.REPLAY and cycle_duration > cadence * self.config.engine_watchdog_factor:
                    self._skip_next_execution_stage = True
                    self._last_cycle_overrun = cycle_duration
                    print(f"[Cycle {self.cycle_count}] Watchdog warning: cycle {cycle_duration:.2f}s exceeded cadence {cadence:.2f}s")
                api_state.sync_engine_state(self.context)

                self._print_cycle_summary(results, cycle_start)
                return results

            except Exception as e:
                print(f"[Cycle {self.cycle_count}] ❌ Error: {e}")
                import traceback
                traceback.print_exc()
                return {"error": str(e)}

    async def run_continuous(self, interval_seconds: float = 5.0):
        """Run continuous scalping loop."""
        self.live_interval_seconds = interval_seconds
        effective_interval = self.replay_interval_ms / 1000.0 if self.mode == self.REPLAY else interval_seconds
        print(f"\nStarting continuous loop (interval: {effective_interval}s)...")

        while self.running:
            try:
                await self.run_cycle()
                if self.mode == self.REPLAY and not self.has_replay_remaining():
                    break
                effective_interval = self.replay_interval_ms / 1000.0 if self.mode == self.REPLAY else interval_seconds
                await asyncio.sleep(effective_interval)
            except KeyboardInterrupt:
                print("\nStopping...")
                break
            except Exception as e:
                print(f"Cycle error: {e}")
                await asyncio.sleep(effective_interval)

    def _should_run_learning(self) -> bool:
        """Check if learning agents should run."""
        if self.mode == self.REPLAY:
            return False
        mode = None
        if self.context and isinstance(self.context.data, dict):
            mode = self.context.data.get("learning_mode")
        if not mode:
            mode = api_state.get_state().learning_mode
        mode = str(mode or "hybrid").lower().strip()
        if mode == "off":
            return False
        if mode == "daily":
            return self._is_end_of_day()
        if mode == "immediate":
            return True

        if self.last_learning_run is None:
            return True

        # Run every 50 cycles or 30 minutes
        if self.cycle_count % 50 == 0:
            return True

        if (datetime.now() - self.last_learning_run) > timedelta(minutes=30):
            return True

        return False

    def _is_end_of_day(self) -> bool:
        """Check if it's end of trading day."""
        now = self._cycle_now().time()
        eod = datetime.strptime("15:20", "%H:%M").time()
        return now >= eod

    def _update_stats(self, results: Dict):
        """Update performance statistics."""
        self.stats["cycles"] += 1

        # Count signals
        if "entry" in results:
            signals = results["entry"].output.get("entry_signals", [])
            self.stats["signals_generated"] += len(signals)
            orders = results["entry"].output.get("orders_created", 0)
            self.stats["entries_taken"] += orders

        # Count exits
        if "exit" in results:
            exits = results["exit"].output.get("exit_orders", 0)
            self.stats["partial_exits"] += exits

        # Update P&L
        if "position_manager" in results:
            pnl = results["position_manager"].output.get("total_realized_pnl", 0)
            pnl += results["position_manager"].output.get("total_unrealized_pnl", 0)
            self.stats["total_pnl"] = pnl

    def _print_cycle_summary(self, results: Dict, start_time: datetime):
        """Print cycle summary."""
        duration = (datetime.now() - start_time).total_seconds()
        strike_count = len(flatten_signals(self.context.data.get("strike_selections", [])))
        quality_count = len(flatten_signals(self.context.data.get("quality_filtered_signals", [])))
        liquidity_count = len(flatten_signals(self.context.data.get("liquidity_filtered_selections", [])))
        trade_count = len(self.context.data.get("executed_trades", []))

        print(f"""
┌────────────────────────────────────────────────────────────────┐
│ Cycle {self.cycle_count} Complete ({duration:.2f}s)                              │
├────────────────────────────────────────────────────────────────┤""")

        # Data status
        if "data_feed" in results:
            symbols = results["data_feed"].metrics.get("symbols_fetched", 0)
            print(f"│  Data: {symbols} symbols fetched                              │")

        # Latency status
        if "latency_guardian" in results:
            health = results["latency_guardian"].output.get("health_pct", 100)
            blocked = results["latency_guardian"].output.get("trading_blocked", False)
            status = "⚠️ BLOCKED" if blocked else "✓ OK"
            print(f"│  Latency: {health:.0f}% healthy ({status})                       │")

        # Regime status
        if "market_regime" in results:
            regimes = results["market_regime"].output.get("regimes", {})
            regime_str = ", ".join([f"{s[:4]}:{r[:4]}" for s, r in list(regimes.items())[:2]])
            print(f"│  Regime: {regime_str:<50}│")

        # Analysis status
        if "momentum" in results:
            signals = results["momentum"].output.get("strong_signals", 0)
            ready = results["momentum"].output.get("entry_ready", False)
            status = "✓ READY" if ready else "○ waiting"
            print(f"│  Momentum: {signals} strong signals ({status})                │")

        # Structure status
        if "structure" in results:
            breaks = results["structure"].metrics.get("breaks_detected", 0)
            print(f"│  Structure: {breaks} breaks detected                          │")

        # Liquidity status
        if "liquidity_monitor" in results:
            health = results["liquidity_monitor"].output.get("liquidity_health_pct", 100)
            illiquid = results["liquidity_monitor"].output.get("illiquid_selected_count", 0)
            print(f"│  Liquidity: {health:.0f}% healthy, {illiquid} illiquid filtered    │")

        # Entry status
        if "entry" in results:
            entries = results["entry"].output.get("orders_created", 0)
            dry = "DRY RUN" if self.dry_run else "LIVE"
            print(f"│  Entries: {entries} ({dry})                                   │")

        # Position status
        if "position_manager" in results:
            open_pos = results["position_manager"].output.get("open_positions", 0)
            pnl = results["position_manager"].output.get("total_unrealized_pnl", 0)
            print(f"│  Positions: {open_pos} open, P&L: ₹{pnl:,.2f}                 │")

        # Risk status
        if "risk_guardian" in results:
            breaches = results["risk_guardian"].metrics.get("breach_count", 0)
            status = "✓ CLEAR" if breaches == 0 else f"⚠️ {breaches} breaches"
            print(f"│  Risk: {status}                                      │")

        print("└────────────────────────────────────────────────────────────────┘")
        print(
            "CYCLE SUMMARY\n"
            f"strikes={strike_count}\n"
            f"quality={quality_count}\n"
            f"liquidity={liquidity_count}\n"
            f"trades={trade_count}"
        )


async def run_scalping_engine(
    dry_run: bool = True,
    single_cycle: bool = False,
    interval: float = 5.0,
):
    """Convenience function to run the scalping engine."""
    engine = ScalpingEngine(dry_run=dry_run)

    await engine.start()

    try:
        if single_cycle:
            await engine.run_cycle()
        else:
            await engine.run_continuous(interval_seconds=interval)
    finally:
        await engine.stop()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scalping Engine")
    parser.add_argument("--live", action="store_true", help="Run in live mode")
    parser.add_argument("--single", action="store_true", help="Run single cycle")
    parser.add_argument("--interval", type=float, default=5.0, help="Cycle interval")

    args = parser.parse_args()

    asyncio.run(run_scalping_engine(
        dry_run=not args.live,
        single_cycle=args.single,
        interval=args.interval,
    ))

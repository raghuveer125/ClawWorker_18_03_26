"""
Scalping Dashboard API - Real-time monitoring for 21-agent system.

Provides:
- Live positions and P&L
- Trade history with entry/exit/SL details
- Agent status and decision pipeline
- Capital allocation view
- WebSocket for live updates

Run: uvicorn scalping.api:app --port 8002
"""

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict, is_dataclass

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Local imports
from .config import ScalpingConfig, get_index_config, IndexType
from .base import BotStatus
from .replay_reporting import ReplayDiagnosticsTracker

app = FastAPI(
    title="Scalping Dashboard API",
    description="21-Agent Autonomous Scalping System",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# In-Memory State (shared with engine via import)
# ============================================================================

@dataclass
class TradeRecord:
    """Single trade record."""
    trade_id: str
    symbol: str
    index: str
    strike: int
    option_type: str  # CE/PE
    direction: str  # long/short
    entry_time: str
    entry_price: float
    quantity: int
    lots: int
    status: str  # open, partial, closed
    # Exit info
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    # Stop loss
    initial_sl: float = 0
    current_sl: float = 0
    sl_moves: List[Dict] = field(default_factory=list)
    # Partial exits
    partial_exits: List[Dict] = field(default_factory=list)
    remaining_qty: int = 0
    # P&L
    realized_pnl: float = 0
    unrealized_pnl: float = 0
    pnl_pct: float = 0
    # Signals that triggered entry
    entry_signals: Dict = field(default_factory=dict)
    # Entry decision snapshot for postmortem
    decision_packet: Dict = field(default_factory=dict)
    # Agent decisions
    agent_decisions: List[Dict] = field(default_factory=list)


@dataclass
class AgentStatus:
    """Status of a single agent."""
    agent_id: int
    name: str
    layer: str  # data, analysis, execution, learning, meta
    status: str  # idle, running, blocked, error
    bot_type: str = ""
    debate_mode: str = "debate"  # debate | single | off
    last_run: Optional[str] = None
    last_output: Dict = field(default_factory=dict)
    metrics: Dict = field(default_factory=dict)
    run_count: int = 0


@dataclass
class DataFlowEvent:
    """Records data movement between agents."""
    timestamp: str
    from_agent: str
    to_agent: str
    data_type: str  # spot, chain, futures, signal, decision
    data_size: int  # Number of items
    latency_ms: float
    status: str  # success, partial, failed


@dataclass
class ScalpingState:
    """Global scalping system state."""
    running: bool = False
    mode: str = "DRY RUN"
    start_time: Optional[str] = None
    cycle_count: int = 0
    # Data flow tracking
    data_flows: List[DataFlowEvent] = field(default_factory=list)
    last_flow_update: Optional[str] = None
    # Capital
    initial_capital: float = 100000
    available_capital: float = 100000
    used_capital: float = 0
    # P&L
    realized_pnl: float = 0
    unrealized_pnl: float = 0
    total_pnl: float = 0
    daily_pnl: float = 0
    # Kill Switch
    kill_switch_active: bool = False
    kill_switch_reason: Optional[str] = None
    kill_switch_triggered_at: Optional[str] = None
    # Risk
    daily_loss_limit: float = 10000
    risk_per_trade: float = 5000
    risk_used_pct: float = 0
    # Positions
    open_positions: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0
    # Agents
    agents: List[AgentStatus] = field(default_factory=list)
    debate_modes: Dict[str, str] = field(default_factory=dict)
    debate_global_mode: str = "single"
    # Learning mode + tracking
    learning_mode: str = "hybrid"  # off | daily | hybrid | immediate
    learning_profiles: List[Dict[str, Any]] = field(default_factory=list)
    learning_active_profile_id: Optional[str] = None
    learning_metrics: Dict[str, Any] = field(default_factory=dict)
    learning_history: List[Dict[str, Any]] = field(default_factory=list)
    learning_last_update: Optional[str] = None
    # Trades
    trades: List[Dict[str, Any]] = field(default_factory=list)
    positions: List[Dict[str, Any]] = field(default_factory=list)
    capital: Dict[str, Any] = field(default_factory=dict)
    # Signals
    active_signals: List[Dict] = field(default_factory=list)
    # Last cycle
    last_cycle_time: Optional[str] = None
    last_cycle_duration: float = 0
    replay_active: bool = False
    replay_progress_pct: float = 0
    replay_dataset: Optional[str] = None
    replay_result: Dict[str, Any] = field(default_factory=dict)
    replay_paused: bool = False
    replay_speed: float = 1.0
    replay_direction: int = 1
    replay_position: int = 0
    replay_total_batches: int = 0
    # Analysis debug snapshot
    analysis_snapshot: Dict[str, Any] = field(default_factory=dict)
    analysis_snapshot_updated_at: Optional[str] = None


# Global state
_state = ScalpingState()

def _init_learning_state() -> None:
    state = get_state()
    if state.learning_profiles:
        return
    config = ScalpingConfig()
    mode = str(getattr(config, "learning_mode_default", "hybrid") or "hybrid").lower()
    state.learning_mode = mode
    profile_id = "default"
    state.learning_profiles = [
        {
            "id": profile_id,
            "label": f"Default ({mode})",
            "mode": mode,
            "created_at": datetime.now().isoformat(),
        }
    ]
    state.learning_active_profile_id = profile_id

def get_state() -> ScalpingState:
    """Get current scalping state."""
    return _state


def _serialize_item(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize_item(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_item(item) for key, item in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _normalize_position(position: Dict[str, Any]) -> Dict[str, Any]:
    symbol = str(position.get("symbol", ""))
    index = "BANKNIFTY" if "BANKNIFTY" in symbol else (
        "SENSEX" if "SENSEX" in symbol else (
            "FINNIFTY" if "FINNIFTY" in symbol else (
                "NIFTY50" if "NIFTY" in symbol else symbol
            )
        )
    )
    current_price = float(position.get("current_price", 0.0) or 0.0)
    entry_price = float(position.get("entry_price", 0.0) or 0.0)
    quantity = int(position.get("remaining_qty", position.get("quantity", 0)) or 0)
    unrealized = (current_price - entry_price) * quantity if current_price > 0 else float(position.get("unrealized_pnl", 0.0) or 0.0)
    return {
        "trade_id": position.get("trade_id", position.get("position_id", symbol)),
        "symbol": symbol,
        "index": position.get("index", index),
        "strike": position.get("strike", 0),
        "option_type": position.get("option_type", ""),
        "quantity": int(position.get("quantity", 0) or 0),
        "remaining_qty": quantity,
        "entry_price": entry_price,
        "current_price": current_price,
        "current_sl": float(position.get("current_sl") or position.get("trail_stop") or position.get("sl_price") or 0.0),
        "target_price": float(position.get("target_price", 0.0) or 0.0),
        "unrealized_pnl": round(unrealized, 2),
        "status": position.get("status", "open"),
    }


def _calculate_average_exit_price(trade: Dict[str, Any]) -> Optional[float]:
    partial_exits = trade.get("partial_exits", [])
    total_quantity = float(trade.get("quantity", 0) or 0)
    final_exit_price = trade.get("exit_price")
    weighted_notional = 0.0
    exited_quantity = 0.0

    if isinstance(partial_exits, list):
        for partial in partial_exits:
            if not isinstance(partial, dict):
                continue
            quantity = float(partial.get("quantity", 0) or 0)
            price = float(partial.get("price", 0) or 0)
            if quantity <= 0 or price <= 0:
                continue
            weighted_notional += quantity * price
            exited_quantity += quantity

    if final_exit_price is not None:
        try:
            final_price = float(final_exit_price)
        except (TypeError, ValueError):
            final_price = 0.0
        if final_price > 0:
            final_quantity = max(total_quantity - exited_quantity, 0.0)
            if final_quantity <= 0 and exited_quantity == 0:
                final_quantity = total_quantity or 1.0
            if final_quantity > 0:
                weighted_notional += final_quantity * final_price
                exited_quantity += final_quantity

    if exited_quantity <= 0:
        return None
    return round(weighted_notional / exited_quantity, 4)


def _normalize_trade(trade: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(trade)
    normalized.setdefault("index", _normalize_position(trade).get("index"))
    normalized.setdefault("sl_moves", [])
    normalized.setdefault("partial_exits", [])
    normalized.setdefault("entry_signals", {})
    normalized.setdefault("decision_packet", {})
    normalized.setdefault("agent_decisions", [])
    normalized.setdefault("remaining_qty", normalized.get("quantity", 0))
    normalized.setdefault("current_sl", normalized.get("initial_sl", 0.0))
    normalized.setdefault("realized_pnl", 0.0)
    normalized.setdefault("unrealized_pnl", 0.0)
    normalized.setdefault("pnl_pct", 0.0)
    normalized.setdefault("average_exit_price", _calculate_average_exit_price(normalized))
    return normalized


def sync_engine_state(context: Any) -> None:
    """Synchronize shared dashboard state from engine context."""
    if context is None or not hasattr(context, "data"):
        return

    state = get_state()
    positions = _serialize_item(context.data.get("positions", []))
    trades = _serialize_item(context.data.get("executed_trades", []))
    capital = _serialize_item(context.data.get("capital_state") or {})

    state.positions = [_normalize_position(position) for position in positions]
    # Preserve simulated/backtest trades — only replace engine-sourced trades
    simulated_trades = [t for t in state.trades if str(t.get("trade_id", "")).startswith(("BT-", "SIM-"))]
    engine_trades = [_normalize_trade(trade) for trade in trades]
    state.trades = engine_trades + simulated_trades
    state.capital = dict(capital)

    if capital:
        state.initial_capital = capital.get("initial_capital", state.initial_capital)
        state.available_capital = capital.get("available_capital", state.available_capital)
        state.used_capital = capital.get("used_capital", state.used_capital)
        state.realized_pnl = capital.get("realized_pnl", state.realized_pnl)
        state.unrealized_pnl = capital.get("unrealized_pnl", state.unrealized_pnl)
        state.total_pnl = capital.get("total_pnl", state.total_pnl)
        state.daily_pnl = capital.get("daily_pnl", state.daily_pnl)
        state.daily_loss_limit = capital.get("daily_loss_limit", state.daily_loss_limit)
        state.risk_per_trade = capital.get("risk_per_trade", state.risk_per_trade)
        state.risk_used_pct = capital.get("risk_used_pct", state.risk_used_pct)

    state.open_positions = len([position for position in state.positions if position.get("status") in ("open", "partial")])
    state.total_trades = len(state.trades)
    closed_trades = [trade for trade in state.trades if trade.get("status") == "closed"]
    state.winning_trades = len([trade for trade in closed_trades if trade.get("realized_pnl", 0) > 0])
    state.losing_trades = len([trade for trade in closed_trades if trade.get("realized_pnl", 0) <= 0])
    state.win_rate = (state.winning_trades / len(closed_trades) * 100) if closed_trades else 0
    active_signals = context.data.get("liquidity_filtered_selections") or context.data.get("quality_filtered_signals") or []
    state.active_signals = _serialize_item(active_signals)[-50:] if isinstance(active_signals, list) else []

    analysis_keys = [
        "spot_data",
        "futures_data",
        "futures_momentum",
        "option_chains",
        "market_regimes",
        "regime_changes",
        "market_structure",
        "structure_breaks",
        "vwap_signals",
        "momentum_signals",
        "trap_signals",
        "volatility_surface",
        "dealer_pressure",
        "strike_selections",
        "quality_filtered_signals",
        "rejected_signals",
        "signal_quality_stats",
        "adaptive_quality_weights",
    ]
    analysis_snapshot = {}
    for key in analysis_keys:
        if key in context.data:
            analysis_snapshot[key] = _serialize_item(context.data.get(key))
    if analysis_snapshot:
        state.analysis_snapshot = analysis_snapshot
        state.analysis_snapshot_updated_at = datetime.now().isoformat()

    feedback = context.data.get("learning_feedback")
    if isinstance(feedback, dict) and feedback:
        closed_trades = int(feedback.get("closed_trades", 0) or 0)
        wins = int(feedback.get("wins", 0) or 0)
        losses = int(feedback.get("losses", 0) or 0)
        win_rate = round((wins / closed_trades * 100) if closed_trades else 0.0, 2)
        summary = {
            "closed_trades": closed_trades,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": win_rate,
            "average_spread_pct": float(feedback.get("average_spread_pct", 0) or 0),
            "average_momentum_strength": float(feedback.get("average_momentum_strength", 0) or 0),
            "adaptive_weights": feedback.get("adaptive_weights", {}) if isinstance(feedback.get("adaptive_weights"), dict) else {},
        }
        state.learning_metrics = summary
        state.learning_last_update = datetime.now().isoformat()
        if not state.learning_history or state.learning_history[-1].get("closed_trades") != closed_trades:
            entry = dict(summary)
            entry["timestamp"] = state.learning_last_update
            state.learning_history.append(entry)
            if len(state.learning_history) > 200:
                state.learning_history = state.learning_history[-200:]

def init_agents():
    """Initialize agent status list."""
    agents = [
        # Safety Layer (runs FIRST)
        AgentStatus(0, "KillSwitch", "safety", "idle", bot_type="kill_switch"),
        # Data Layer
        AgentStatus(1, "DataFeed", "data", "idle", bot_type="data_feed"),
        AgentStatus(2, "OptionChain", "data", "idle", bot_type="option_chain"),
        AgentStatus(3, "Futures", "data", "idle", bot_type="futures"),
        AgentStatus(4, "LatencyGuardian", "data", "idle", bot_type="latency_guardian"),
        # Analysis Layer
        AgentStatus(5, "MarketRegime", "analysis", "idle", bot_type="market_regime"),
        AgentStatus(6, "Structure", "analysis", "idle", bot_type="structure"),
        AgentStatus(7, "Momentum", "analysis", "idle", bot_type="momentum"),
        AgentStatus(8, "TrapDetector", "analysis", "idle", bot_type="trap_detector"),
        AgentStatus(9, "VolatilitySurface", "analysis", "idle", bot_type="volatility_surface"),
        AgentStatus(10, "DealerPressure", "analysis", "idle", bot_type="dealer_pressure"),
        AgentStatus(11, "StrikeSelector", "analysis", "idle", bot_type="strike_selector"),
        # Quality Gate
        AgentStatus(12, "SignalQuality", "quality", "idle", bot_type="signal_quality"),
        # Risk Layer
        AgentStatus(13, "LiquidityMonitor", "risk", "idle", bot_type="liquidity_monitor"),
        AgentStatus(14, "RiskGuardian", "risk", "idle", bot_type="risk_guardian"),
        AgentStatus(15, "CorrelationGuard", "risk", "idle", bot_type="correlation_guard"),
        AgentStatus(16, "MetaAllocator", "risk", "idle", bot_type="meta_allocator"),
        # Execution Layer
        AgentStatus(17, "Entry", "execution", "idle", bot_type="entry"),
        AgentStatus(18, "Exit", "execution", "idle", bot_type="exit"),
        AgentStatus(19, "PositionManager", "execution", "idle", bot_type="position_manager"),
        # Learning Layer
        AgentStatus(20, "QuantLearner", "learning", "idle", bot_type="quant_learner"),
        AgentStatus(21, "StrategyOptimizer", "learning", "idle", bot_type="strategy_optimizer"),
        AgentStatus(22, "ExitOptimizer", "learning", "idle", bot_type="exit_optimizer"),
    ]
    _state.agents = agents
    try:
        from .debate_integration import set_global_debate_mode
        set_global_debate_mode(_state.debate_global_mode or "debate")
    except Exception:
        pass

init_agents()
_init_learning_state()

# ============================================================================
# WebSocket Manager
# ============================================================================

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/")
async def root():
    return {"service": "Scalping Dashboard API", "version": "1.0.0"}


@app.get("/api/scalping/status")
async def get_status():
    """Get overall system status."""
    state = get_state()
    return {
        "running": state.running,
        "mode": state.mode,
        "start_time": state.start_time,
        "cycle_count": state.cycle_count,
        "last_cycle_time": state.last_cycle_time,
        "last_cycle_duration": state.last_cycle_duration,
        "replay_active": state.replay_active,
        "replay_progress_pct": state.replay_progress_pct,
        "replay_dataset": state.replay_dataset,
    }


@app.get("/api/scalping/capital")
async def get_capital():
    """Get capital and P&L summary."""
    state = get_state()
    capital = state.capital or {}
    return {
        "initial_capital": capital.get("initial_capital", state.initial_capital),
        "available_capital": capital.get("available_capital", state.available_capital),
        "used_capital": capital.get("used_capital", state.used_capital),
        "realized_pnl": capital.get("realized_pnl", state.realized_pnl),
        "unrealized_pnl": capital.get("unrealized_pnl", state.unrealized_pnl),
        "total_pnl": capital.get("total_pnl", state.total_pnl),
        "daily_pnl": capital.get("daily_pnl", state.daily_pnl),
        "daily_loss_limit": capital.get("daily_loss_limit", state.daily_loss_limit),
        "risk_per_trade": capital.get("risk_per_trade", state.risk_per_trade),
        "risk_used_pct": capital.get("risk_used_pct", state.risk_used_pct),
    }


@app.get("/api/scalping/analysis-debug")
async def get_analysis_debug(include_option_chain: bool = False):
    """Get latest analysis-layer snapshot from engine context."""
    state = get_state()
    snapshot = dict(state.analysis_snapshot or {})
    if not include_option_chain:
        snapshot.pop("option_chains", None)
    return {
        "updated_at": state.analysis_snapshot_updated_at,
        "data": snapshot,
    }


@app.get("/api/scalping/positions")
async def get_positions():
    """Get open positions."""
    state = get_state()
    open_trades = [position for position in state.positions if position.get("status") in ("open", "partial")]
    return {
        "count": len(open_trades),
        "positions": open_trades,
    }


@app.get("/api/scalping/trades")
async def get_trades(limit: int = 50, status: Optional[str] = None):
    """Get trade history."""
    state = get_state()
    trades = list(state.trades)

    if status:
        trades = [t for t in trades if t.get("status") == status]

    # Sort by entry time descending
    trades = sorted(trades, key=lambda t: t.get("entry_time", ""), reverse=True)[:limit]

    return {
        "total": len(state.trades),
        "filtered": len(trades),
        "trades": trades,
    }


@app.get("/api/scalping/trades/{trade_id}")
async def get_trade_detail(trade_id: str):
    """Get detailed trade info including SL moves and partial exits."""
    state = get_state()
    for trade in state.trades:
        if trade.get("trade_id") == trade_id:
            return trade
    return JSONResponse(status_code=404, content={"error": "Trade not found"})


@app.get("/api/scalping/agents")
async def get_agents():
    """Get all agent statuses."""
    state = get_state()
    for agent in state.agents:
        agent.debate_mode = state.debate_global_mode or "debate"
    return {
        "agents": [asdict(a) for a in state.agents],
        "by_layer": {
            "safety": [asdict(a) for a in state.agents if a.layer == "safety"],
            "data": [asdict(a) for a in state.agents if a.layer == "data"],
            "analysis": [asdict(a) for a in state.agents if a.layer == "analysis"],
            "quality": [asdict(a) for a in state.agents if a.layer == "quality"],
            "risk": [asdict(a) for a in state.agents if a.layer == "risk"],
            "execution": [asdict(a) for a in state.agents if a.layer == "execution"],
            "learning": [asdict(a) for a in state.agents if a.layer == "learning"],
        }
    }


@app.get("/api/scalping/agents/{agent_id}")
async def get_agent_detail(agent_id: int):
    """Get detailed agent info."""
    state = get_state()
    for agent in state.agents:
        if agent.agent_id == agent_id:
            agent.debate_mode = state.debate_global_mode or "debate"
            return asdict(agent)
    return JSONResponse(status_code=404, content={"error": "Agent not found"})


@app.post("/api/scalping/agents/{agent_id}/debate")
async def set_agent_debate_mode(agent_id: int, payload: Dict[str, Any]):
    """Set per-agent debate mode: debate | single | off."""
    mode = str(payload.get("mode", "")).lower().strip()
    if mode not in {"debate", "single", "off"}:
        return JSONResponse(status_code=400, content={"error": "Invalid mode"})

    state = get_state()
    for agent in state.agents:
        if agent.agent_id == agent_id:
            agent.debate_mode = mode
            if agent.bot_type:
                state.debate_modes[agent.bot_type] = mode
                try:
                    from .debate_integration import set_agent_debate_mode as _set_mode
                    _set_mode(agent.bot_type, mode)
                except Exception:
                    pass
            return {
                "status": "ok",
                "agent_id": agent_id,
                "bot_type": agent.bot_type,
                "mode": mode,
            }
    return JSONResponse(status_code=404, content={"error": "Agent not found"})


@app.get("/api/scalping/debate")
async def get_debate_mode():
    """Get global debate mode."""
    state = get_state()
    return {"mode": state.debate_global_mode or "debate"}


@app.post("/api/scalping/debate")
async def set_debate_mode(payload: Dict[str, Any]):
    """Set global debate mode: debate | single | off."""
    mode = str(payload.get("mode", "")).lower().strip()
    if mode not in {"debate", "single", "off"}:
        return JSONResponse(status_code=400, content={"error": "Invalid mode"})
    state = get_state()
    state.debate_global_mode = mode
    try:
        from .debate_integration import set_global_debate_mode as _set_global
        _set_global(mode)
    except Exception:
        pass
    return {"status": "ok", "mode": mode}


@app.get("/api/scalping/learning")
async def get_learning_mode():
    """Get learning mode and tracking summary."""
    state = get_state()
    return {
        "mode": state.learning_mode,
        "active_profile_id": state.learning_active_profile_id,
        "profiles": state.learning_profiles,
        "metrics": state.learning_metrics,
        "last_update": state.learning_last_update,
        "history": state.learning_history[-50:],
    }


@app.post("/api/scalping/learning")
async def set_learning_mode(payload: Dict[str, Any]):
    """Set learning mode: off | daily | hybrid | immediate, or revert to profile."""
    state = get_state()
    action = str(payload.get("action", "")).lower().strip()
    profile_id = str(payload.get("profile_id", "")).strip()
    if action == "revert":
        default_profile = next((p for p in state.learning_profiles if p.get("id") == "default"), None)
        if default_profile:
            state.learning_mode = default_profile.get("mode", state.learning_mode)
            state.learning_active_profile_id = default_profile.get("id")
            return {"status": "ok", "mode": state.learning_mode, "active_profile_id": state.learning_active_profile_id}
        return JSONResponse(status_code=404, content={"error": "Default learning profile not found"})

    if profile_id:
        profile = next((p for p in state.learning_profiles if p.get("id") == profile_id), None)
        if not profile:
            return JSONResponse(status_code=404, content={"error": "Learning profile not found"})
        state.learning_mode = str(profile.get("mode", state.learning_mode)).lower()
        state.learning_active_profile_id = profile_id
        return {"status": "ok", "mode": state.learning_mode, "active_profile_id": state.learning_active_profile_id}

    mode = str(payload.get("mode", "")).lower().strip()
    if mode not in {"off", "daily", "hybrid", "immediate"}:
        return JSONResponse(status_code=400, content={"error": "Invalid learning mode"})

    state.learning_mode = mode
    profile_id = uuid.uuid4().hex
    state.learning_profiles.append(
        {
            "id": profile_id,
            "label": f"{mode} ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
            "mode": mode,
            "created_at": datetime.now().isoformat(),
        }
    )
    state.learning_active_profile_id = profile_id
    if len(state.learning_profiles) > 20:
        state.learning_profiles = [p for p in state.learning_profiles if p.get("id") == "default"] + state.learning_profiles[-19:]

    return {"status": "ok", "mode": mode, "active_profile_id": state.learning_active_profile_id}


@app.get("/api/scalping/signals")
async def get_signals():
    """Get active trading signals."""
    state = get_state()
    return {
        "count": len(state.active_signals),
        "signals": state.active_signals,
    }


@app.get("/api/scalping/pipeline")
async def get_pipeline():
    """Get pipeline flow visualization data."""
    state = get_state()

    # Build pipeline stages - 23 agents total
    # Agent IDs match engine._agent_map:
    # 0=KillSwitch, 1-4=Data, 5-11=Analysis(regime,structure,momentum,trap,volsurf,dealer,strike),
    # 12=Quality, 13-16=Risk(liquidity,risk,corr,meta), 17-19=Execution(entry,exit,posmgr), 20-22=Learning
    def get_agents_by_ids(ids):
        return [a for a in state.agents if a.agent_id in ids]

    pipeline = {
        "stages": [
            {
                "id": "safety",
                "name": "Safety Check",
                "agents": [0],
                "status": "running" if any(a.status == "running" for a in get_agents_by_ids([0])) else "idle",
                "critical": True,
            },
            {
                "id": "data",
                "name": "Data Collection",
                "agents": [1, 2, 3, 4],
                "status": "running" if any(a.status == "running" for a in get_agents_by_ids([1, 2, 3, 4])) else "idle",
            },
            {
                "id": "analysis",
                "name": "Market Analysis",
                "agents": [5, 6, 7, 8, 9, 10, 11],
                "status": "running" if any(a.status == "running" for a in get_agents_by_ids([5, 6, 7, 8, 9, 10, 11])) else "idle",
            },
            {
                "id": "quality",
                "name": "Quality Gate",
                "agents": [12],
                "status": "running" if any(a.status == "running" for a in get_agents_by_ids([12])) else "idle",
            },
            {
                "id": "risk",
                "name": "Risk Check",
                "agents": [13, 14, 15, 16],
                "status": "running" if any(a.status == "running" for a in get_agents_by_ids([13, 14, 15, 16])) else "idle",
            },
            {
                "id": "execution",
                "name": "Execution",
                "agents": [17, 18, 19],
                "status": "running" if any(a.status == "running" for a in get_agents_by_ids([17, 18, 19])) else "idle",
            },
            {
                "id": "learning",
                "name": "Learning",
                "agents": [20, 21, 22],
                "status": "running" if any(a.status == "running" for a in get_agents_by_ids([20, 21, 22])) else "idle",
                "periodic": True,
            },
        ],
        "cycle_count": state.cycle_count,
        "last_cycle": state.last_cycle_time,
    }

    return pipeline


@app.get("/api/scalping/dataflow")
async def get_dataflow():
    """Get real-time data flow between agents for visualization."""
    state = get_state()

    # Get last N flow events
    recent_flows = state.data_flows[-50:] if state.data_flows else []

    # Build flow connections with status
    connections = [
        # Data Layer flows
        {"from": "DataFeed", "to": "LatencyGuard", "type": "spot", "active": False},
        {"from": "OptionChain", "to": "LatencyGuard", "type": "chain", "active": False},
        {"from": "Futures", "to": "LatencyGuard", "type": "futures", "active": False},
        {"from": "LatencyGuard", "to": "Regime", "type": "validated", "active": False},
        # Analysis flows
        {"from": "Regime", "to": "Structure", "type": "regime", "active": False},
        {"from": "Regime", "to": "Momentum", "type": "regime", "active": False},
        {"from": "Structure", "to": "TrapDetector", "type": "levels", "active": False},
        {"from": "Momentum", "to": "StrikeSelector", "type": "momentum", "active": False},
        # Risk flows
        {"from": "StrikeSelector", "to": "Liquidity", "type": "strikes", "active": False},
        {"from": "Liquidity", "to": "RiskGuard", "type": "liquid", "active": False},
        {"from": "RiskGuard", "to": "Meta", "type": "risk_clear", "active": False},
        # Execution flows
        {"from": "Meta", "to": "Entry", "type": "decision", "active": False},
        {"from": "Entry", "to": "Position", "type": "order", "active": False},
        {"from": "Position", "to": "Exit", "type": "monitor", "active": False},
        # Learning flows
        {"from": "Exit", "to": "QuantLearner", "type": "outcome", "active": False},
        {"from": "QuantLearner", "to": "StrategyOptimizer", "type": "pattern", "active": False},
    ]

    # Mark active connections based on recent flows (last 10 seconds)
    now = datetime.now()
    for flow in recent_flows:
        try:
            flow_time = datetime.fromisoformat(flow.timestamp)
            if (now - flow_time).total_seconds() < 10:
                for conn in connections:
                    if conn["from"] == flow.from_agent and conn["to"] == flow.to_agent:
                        conn["active"] = True
                        conn["last_data"] = flow.data_type
                        conn["latency_ms"] = flow.latency_ms
                        break
        except (ValueError, TypeError, AttributeError):
            pass  # intentional: skip flows with unparseable timestamps

    return {
        "connections": connections,
        "recent_events": [asdict(f) for f in recent_flows[-20:]],
        "last_update": state.last_flow_update,
        "cycle": state.cycle_count,
    }


def record_data_flow(from_agent: str, to_agent: str, data_type: str,
                     data_size: int = 1, latency_ms: float = 0, status: str = "success"):
    """Record a data flow event (call from engine)."""
    state = get_state()
    event = DataFlowEvent(
        timestamp=datetime.now().isoformat(),
        from_agent=from_agent,
        to_agent=to_agent,
        data_type=data_type,
        data_size=data_size,
        latency_ms=latency_ms,
        status=status,
    )
    state.data_flows.append(event)
    state.last_flow_update = event.timestamp

    # Keep only last 200 events
    if len(state.data_flows) > 200:
        state.data_flows = state.data_flows[-200:]


@app.get("/api/scalping/stats")
async def get_stats():
    """Get trading statistics."""
    state = get_state()
    return {
        "total_trades": state.total_trades,
        "open_positions": state.open_positions,
        "winning_trades": state.winning_trades,
        "losing_trades": state.losing_trades,
        "win_rate": state.win_rate,
        "total_pnl": state.total_pnl,
        "daily_pnl": state.daily_pnl,
        "best_trade": max([float(t.get("realized_pnl", 0) or 0) for t in state.trades], default=0),
        "worst_trade": min(
            [float(t.get("realized_pnl", 0) or 0) for t in state.trades if t.get("status") == "closed"],
            default=0,
        ),
    }


@app.get("/api/scalping/config")
async def get_config():
    """Get current configuration."""
    config = ScalpingConfig()
    # Per-index configs (premium/delta are on IndexConfig, not ScalpingConfig)
    index_details = {}
    for idx_type in config.indices:
        idx_cfg = get_index_config(idx_type)
        if idx_cfg:
            index_details[idx_type.value] = {
                "lot_size": idx_cfg.lot_size,
                "strike_interval": idx_cfg.strike_interval,
                "otm_distance_min": idx_cfg.otm_distance_min,
                "otm_distance_max": idx_cfg.otm_distance_max,
                "premium_min": idx_cfg.premium_min,
                "premium_max": idx_cfg.premium_max,
                "delta_min": idx_cfg.delta_min,
                "delta_max": idx_cfg.delta_max,
            }
    return {
        "indices": [idx.value for idx in config.indices],
        "index_configs": index_details,
        "total_capital": config.total_capital,
        "risk_per_trade_pct": config.risk_per_trade_pct,
        "daily_loss_limit_pct": config.daily_loss_limit_pct,
        "max_positions": config.max_positions,
        "max_consecutive_losses": config.max_consecutive_losses,
        "entry_rules": {
            "require_structure_break": config.require_structure_break,
            "require_futures_confirm": config.require_futures_confirm,
            "require_volume_burst": config.require_volume_burst,
            "require_trap_confirm": config.require_trap_confirm,
            "late_entry_cutoff_time": config.late_entry_cutoff_time,
        },
        "exit_rules": {
            "partial_exit_pct": config.partial_exit_pct,
            "first_target_points": config.first_target_points,
            "move_sl_to_entry": config.move_sl_to_entry,
        },
        "risk_controls": {
            "max_bid_ask_spread_pct": config.max_bid_ask_spread_pct,
            "min_volume_threshold": config.min_volume_threshold,
            "min_oi_threshold": config.min_oi_threshold,
            "disable_high_spread": config.disable_high_spread,
            "trading_hours": config.trading_hours,
        },
    }


# ============================================================================
# Index & Expiry Info (fetched from exchange, not hardcoded)
# ============================================================================

@app.get("/api/scalping/indices")
async def get_indices_with_expiry():
    """Get active indices with live expiry dates fetched from exchange."""
    import sys
    from pathlib import Path
    _project_root = Path(__file__).resolve().parents[3]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

    config = ScalpingConfig()
    result = {"indices": [], "timestamp": datetime.now().isoformat()}

    try:
        from shared_project_engine.indices import (
            get_expiry_schedule,
            get_todays_expiring_indices,
            INDEX_CONFIG,
        )
        expiry_schedule = get_expiry_schedule(use_live=True)
        todays_expiry = get_todays_expiring_indices(use_live=True)

        for idx_type in config.indices:
            # Map enum value to INDEX_CONFIG key (e.g. "NSE:NIFTY50-INDEX" → "NIFTY50")
            idx_key = None
            for name, cfg in INDEX_CONFIG.items():
                if cfg.get("symbol") == idx_type.value or name in idx_type.value:
                    idx_key = name
                    break
            if not idx_key:
                idx_key = idx_type.name

            expiry_info = expiry_schedule.get(idx_key, {})
            idx_config = get_index_config(idx_type)

            result["indices"].append({
                "name": idx_key,
                "symbol": idx_type.value,
                "lot_size": idx_config.lot_size if idx_config else 0,
                "strike_interval": idx_config.strike_interval if idx_config else 0,
                "is_expiry_today": idx_key in todays_expiry,
                "upcoming_expiry": expiry_info.get("nextExpiry", expiry_info.get("date")),
                "expiry_weekday": expiry_info.get("weekday"),
                "days_to_expiry": expiry_info.get("daysUntil"),
                "source": expiry_info.get("source", "computed"),
            })
    except ImportError:
        # Fallback if shared_project_engine not available
        for idx_type in config.indices:
            idx_config = get_index_config(idx_type)
            result["indices"].append({
                "name": idx_type.name,
                "symbol": idx_type.value,
                "lot_size": idx_config.lot_size if idx_config else 0,
                "strike_interval": idx_config.strike_interval if idx_config else 0,
                "is_expiry_today": False,
                "upcoming_expiry": None,
                "source": "unavailable",
            })
    except Exception as e:
        result["error"] = str(e)

    return result


# ============================================================================
# WebSocket for Live Updates
# ============================================================================

@app.websocket("/ws/scalping")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial state
        state = get_state()
        await websocket.send_json({
            "type": "init",
            "data": {
                "status": {
                    "running": state.running,
                    "mode": state.mode,
                    "cycle_count": state.cycle_count,
                },
                "capital": {
                    "total": state.capital.get("initial_capital", state.initial_capital),
                    "available": state.capital.get("available_capital", state.available_capital),
                    "pnl": state.capital.get("total_pnl", state.total_pnl),
                },
                "positions": state.positions,
                "replay": {
                    "active": state.replay_active,
                    "progress_pct": state.replay_progress_pct,
                    "dataset": state.replay_dataset,
                    "result": state.replay_result,
                    "paused": state.replay_paused,
                    "speed": state.replay_speed,
                    "direction": state.replay_direction,
                    "position": state.replay_position,
                    "total_batches": state.replay_total_batches,
                },
            }
        })

        while True:
            # Wait for messages from client
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ============================================================================
# State Update Functions (called by engine)
# ============================================================================

async def broadcast_update(update_type: str, data: dict):
    """Broadcast update to all connected clients."""
    message = {
        "type": update_type,
        "timestamp": datetime.now().isoformat(),
        "data": data,
    }
    if update_type == "update" and "cycle" in data:
        message["cycle"] = data["cycle"]
    await manager.broadcast(message)


def update_agent_status(agent_id: int, status: str, output: dict = None, metrics: dict = None):
    """Update agent status."""
    state = get_state()
    for agent in state.agents:
        if agent.agent_id == agent_id:
            agent.status = status
            agent.last_run = datetime.now().isoformat()
            agent.run_count += 1
            if output:
                agent.last_output = output
            if metrics:
                agent.metrics = metrics
            break


def add_trade(trade: TradeRecord):
    """Add new trade to state."""
    state = get_state()
    state.trades.append(_normalize_trade(_serialize_item(trade)))
    state.total_trades += 1
    state.open_positions += 1
    state.used_capital += trade.entry_price * trade.quantity


def update_trade(trade_id: str, updates: dict):
    """Update existing trade."""
    state = get_state()
    for trade in state.trades:
        if trade.get("trade_id") == trade_id:
            for key, value in updates.items():
                trade[key] = value
            break


def close_trade(trade_id: str, exit_price: float, exit_time: str):
    """Close a trade."""
    state = get_state()
    for trade in state.trades:
        if trade.get("trade_id") == trade_id:
            trade["status"] = "closed"
            trade["exit_price"] = exit_price
            trade["exit_time"] = exit_time

            # Calculate P&L
            if trade.get("direction") == "long":
                trade["realized_pnl"] = (exit_price - trade.get("entry_price", 0)) * trade.get("quantity", 0)
            else:
                trade["realized_pnl"] = (trade.get("entry_price", 0) - exit_price) * trade.get("quantity", 0)

            trade["pnl_pct"] = (trade["realized_pnl"] / max(trade.get("entry_price", 0) * max(trade.get("quantity", 0), 1), 1e-9)) * 100

            # Update state
            state.open_positions -= 1
            state.realized_pnl += trade["realized_pnl"]
            state.total_pnl = state.realized_pnl + state.unrealized_pnl

            if trade["realized_pnl"] > 0:
                state.winning_trades += 1
            else:
                state.losing_trades += 1

            if state.total_trades > 0:
                state.win_rate = (state.winning_trades / state.total_trades) * 100

            break


def add_sl_move(trade_id: str, old_sl: float, new_sl: float, reason: str):
    """Record SL move."""
    state = get_state()
    for trade in state.trades:
        if trade.get("trade_id") == trade_id:
            trade.setdefault("sl_moves", []).append({
                "time": datetime.now().isoformat(),
                "old_sl": old_sl,
                "new_sl": new_sl,
                "reason": reason,
            })
            trade["current_sl"] = new_sl
            break


def add_partial_exit(trade_id: str, qty: int, price: float, pnl: float):
    """Record partial exit."""
    state = get_state()
    for trade in state.trades:
        if trade.get("trade_id") == trade_id:
            trade.setdefault("partial_exits", []).append({
                "time": datetime.now().isoformat(),
                "quantity": qty,
                "price": price,
                "pnl": pnl,
            })
            trade["remaining_qty"] = trade.get("remaining_qty", trade.get("quantity", 0)) - qty
            trade["status"] = "partial"
            trade["realized_pnl"] = trade.get("realized_pnl", 0) + pnl
            state.realized_pnl += pnl
            break


# ============================================================================
# Backtest/Simulation Endpoints (for historical replay)
# ============================================================================

@app.post("/api/scalping/backtest/signal")
async def add_backtest_signal(signal: dict):
    """Add a signal from backtest simulation."""
    state = get_state()
    signal["source"] = "backtest"
    signal["time"] = signal.get("timestamp", datetime.now().isoformat())
    state.active_signals.append(signal)
    # Keep only last 50 signals
    if len(state.active_signals) > 50:
        state.active_signals = state.active_signals[-50:]
    return {"status": "ok", "count": len(state.active_signals)}


@app.post("/api/scalping/backtest/trade")
async def add_backtest_trade(trade: dict):
    """Add a simulated trade from backtest."""
    state = get_state()

    trade_record = TradeRecord(
        trade_id=trade.get("id", f"BT-{len(state.trades)+1:04d}"),
        symbol=trade.get("symbol", f"{trade.get('index', 'SENSEX')}-SIM"),
        index=trade.get("index", "SENSEX"),
        strike=trade.get("strike", 0),
        option_type=trade.get("option_type", "CE"),
        direction="long" if trade.get("type") == "LONG" else "short",
        entry_time=trade.get("timestamp", datetime.now().isoformat()),
        entry_price=trade.get("entry", 0),
        quantity=trade.get("quantity", 1),
        lots=trade.get("lots", 1),
        status=trade.get("status", "simulated"),
        entry_signals=trade.get("signals", {}),
    )

    state.trades.append(_normalize_trade(asdict(trade_record)))
    state.total_trades += 1

    return {"status": "ok", "trade_id": trade_record.trade_id, "total": len(state.trades)}


@app.post("/api/scalping/backtest/clear")
async def clear_backtest_data():
    """Clear backtest simulation data."""
    state = get_state()
    state.trades = [t for t in state.trades if not str(t.get("trade_id", "")).startswith("BT-")]
    state.active_signals = [s for s in state.active_signals if s.get("source") != "backtest"]
    return {"status": "cleared"}


def _replay_cycles_per_chunk(speed: float) -> int:
    """Map replay speed presets to batches processed per scheduling slice."""
    try:
        numeric_speed = float(speed or 1.0)
    except Exception:
        numeric_speed = 1.0
    if numeric_speed >= 8.0:
        return 8
    if numeric_speed >= 4.0:
        return 4
    if numeric_speed >= 2.0:
        return 2
    return 1


async def _run_replay_job(csv_path: str, dataset_name: str) -> Dict[str, Any]:
    global _engine_instance

    if _engine_instance is None:
        raise RuntimeError("Engine not initialized")

    state = get_state()
    state.replay_active = True
    state.replay_paused = False
    state.replay_direction = 1 if state.replay_direction >= 0 else -1
    state.replay_speed = float(state.replay_speed or 1.0)
    state.replay_progress_pct = 0
    state.replay_dataset = dataset_name
    state.replay_result = {}
    state.mode = "REPLAY"

    diagnostics = ReplayDiagnosticsTracker()
    start_cycle = _engine_instance.cycle_count
    _engine_instance.start_replay(csv_path)
    total_batches = _engine_instance.replay_adapter.total_batches() if _engine_instance.replay_adapter else 0
    state.replay_total_batches = total_batches
    state.replay_position = 0

    try:
        while state.replay_active:
            if state.replay_paused:
                await asyncio.sleep(0.1)
                continue

            adapter = _engine_instance.replay_adapter
            if not adapter:
                break

            direction = 1 if state.replay_direction >= 0 else -1
            _engine_instance.replay_direction = direction
            cycles_per_chunk = _replay_cycles_per_chunk(state.replay_speed)
            chunk_started = perf_counter()
            chunk_processed = 0
            last_progress_payload: Optional[Dict[str, Any]] = None

            for _ in range(cycles_per_chunk):
                if not state.replay_active or state.replay_paused:
                    break

                if direction < 0 and not adapter.has_previous():
                    state.replay_paused = True
                    state.replay_progress_pct = 0.0
                    break
                if direction > 0 and not adapter.has_next():
                    state.replay_position = total_batches
                    state.replay_progress_pct = 100.0
                    state.replay_active = False
                    break

                results = await _engine_instance.run_cycle()
                chunk_processed += 1
                if results.get("status") == "replay_complete":
                    state.replay_position = total_batches
                    state.replay_progress_pct = 100.0
                    state.replay_active = False
                    break

                diagnostics.observe_cycle(_engine_instance.context, results)
                processed = adapter.current_index()
                state.replay_position = processed
                progress_pct = round((processed / total_batches) * 100, 2) if total_batches else 100.0
                state.replay_progress_pct = progress_pct
                last_progress_payload = {
                    "dataset": dataset_name,
                    "processed_cycles": processed,
                    "total_cycles": total_batches,
                    "progress_pct": progress_pct,
                }

            if last_progress_payload is not None:
                await broadcast_update("replay_progress", last_progress_payload)
                await broadcast_update(
                    "update",
                    {
                        "cycle": state.cycle_count,
                        "cycle_count": state.cycle_count,
                        "last_cycle_time": state.last_cycle_time,
                    },
                )

            if not state.replay_active:
                break
            if state.replay_paused:
                continue

            base_interval = max(0.0, _engine_instance.replay_interval_ms / 1000.0)
            if chunk_processed <= 0:
                await asyncio.sleep(min(0.05, max(base_interval, 0.01)))
                continue

            remaining_delay = base_interval - (perf_counter() - chunk_started)
            if remaining_delay > 0:
                await asyncio.sleep(remaining_delay)

        if (
            getattr(_engine_instance, "context", None) is not None
            and getattr(_engine_instance, "position_manager", None) is not None
            and hasattr(_engine_instance.position_manager, "flatten_open_positions")
        ):
            _engine_instance.position_manager.flatten_open_positions(
                _engine_instance.context,
                reason="Replay completed",
            )

        capital_state = _engine_instance.context.data.get("capital_state", {})
        trades = list(_engine_instance.context.data.get("executed_trades", []))
        report = diagnostics.build_report(trades, float(capital_state.get("total_pnl", 0) or 0))
        report.update(
            {
                "dataset": dataset_name,
                "dataset_path": csv_path,
                "total_cycles": total_batches,
                "engine_cycles_delta": _engine_instance.cycle_count - start_cycle,
                "signals_detected": report["stage_totals"]["total_strike_selections"],
                "signals_after_quality": report["stage_totals"]["total_quality_pass"],
                "signals_after_liquidity": report["stage_totals"]["total_liquidity_pass"],
                "trades_executed": report["stage_totals"]["total_trades"],
            }
        )
        state.replay_result = report
        state.replay_progress_pct = state.replay_progress_pct or 0.0
        await broadcast_update("replay_complete", report)
        return report
    finally:
        state.replay_active = False
        state.replay_paused = False
        _engine_instance.finish_replay(state.replay_result)


@app.get("/api/scalping/replay/status")
async def get_replay_status():
    state = get_state()
    return {
        "active": state.replay_active,
        "progress_pct": state.replay_progress_pct,
        "dataset": state.replay_dataset,
        "result": state.replay_result,
        "paused": state.replay_paused,
        "speed": state.replay_speed,
        "direction": state.replay_direction,
        "position": state.replay_position,
        "total_batches": state.replay_total_batches,
    }


@app.post("/api/scalping/replay/run")
@app.post("/replay/run")
async def run_replay(request: Request):
    global _engine_instance, _replay_task

    if _engine_instance is None:
        return JSONResponse(status_code=503, content={"error": "Engine not running"})
    if get_state().replay_active:
        return JSONResponse(status_code=409, content={"error": "Replay already active"})

    body = await request.body()
    if not body:
        return JSONResponse(status_code=400, content={"error": "Replay CSV body is empty"})

    filename = request.headers.get("x-replay-filename", "replay.csv")
    suffix = Path(filename).suffix or ".csv"
    temp_path = Path(tempfile.gettempdir()) / f"scalping-replay-{uuid.uuid4().hex}{suffix}"
    with temp_path.open("wb") as handle:
        handle.write(body)

    async def _run_and_cleanup() -> None:
        global _replay_task
        try:
            await _run_replay_job(str(temp_path), filename or temp_path.name)
        finally:
            _replay_task = None
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    state = get_state()
    state.replay_active = True
    state.replay_paused = False
    state.replay_progress_pct = 0.0
    state.replay_dataset = filename or temp_path.name
    _replay_task = asyncio.create_task(_run_and_cleanup())
    return {"status": "started", "dataset": filename or temp_path.name, "progress_pct": 0}


@app.post("/api/scalping/replay/control")
async def control_replay(payload: Dict[str, Any]):
    """Control replay: play, pause, speed, direction, fast_forward, fast_rewind, stop, seek."""
    global _engine_instance
    state = get_state()
    if _engine_instance is None or not state.replay_active:
        return JSONResponse(status_code=409, content={"error": "Replay not active"})

    action = str(payload.get("action", "")).lower().strip()
    if action == "play":
        state.replay_paused = False
    elif action == "pause":
        state.replay_paused = True
    elif action == "stop":
        state.replay_active = False
        state.replay_paused = True
    elif action == "direction":
        direction = str(payload.get("direction", "forward")).lower().strip()
        state.replay_direction = -1 if direction in {"backward", "rewind", "reverse"} else 1
    elif action == "fast_forward":
        state.replay_direction = 1
        state.replay_speed = 8.0
        state.replay_paused = False
    elif action == "fast_rewind":
        state.replay_direction = -1
        state.replay_speed = 8.0
        state.replay_paused = False
    elif action == "speed":
        speed = float(payload.get("speed", 1.0) or 1.0)
        if speed not in {1.0, 2.0, 4.0, 8.0}:
            return JSONResponse(status_code=400, content={"error": "Invalid speed"})
        state.replay_speed = speed
    elif action == "seek":
        if not _engine_instance.replay_adapter:
            return JSONResponse(status_code=409, content={"error": "Replay adapter not ready"})
        index = payload.get("index")
        pct = payload.get("pct")
        total = _engine_instance.replay_adapter.total_batches()
        if index is None and pct is None:
            return JSONResponse(status_code=400, content={"error": "Missing index or pct"})
        if pct is not None:
            index = int(max(0, min(total - 1, round((float(pct) / 100.0) * max(total - 1, 0)))))
        try:
            _engine_instance.replay_adapter.seek(int(index))
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid seek index"})
        state.replay_position = _engine_instance.replay_adapter.current_index()
        state.replay_progress_pct = round((state.replay_position / total) * 100, 2) if total else 0.0
    else:
        return JSONResponse(status_code=400, content={"error": "Invalid action"})

    return {
        "status": "ok",
        "active": state.replay_active,
        "paused": state.replay_paused,
        "speed": state.replay_speed,
        "direction": state.replay_direction,
        "position": state.replay_position,
        "progress_pct": state.replay_progress_pct,
    }


# ============================================================================
# Background Engine Runner (runs engine in same process as API)
# ============================================================================

_engine_task = None
_engine_instance = None
_replay_task = None

async def _run_engine_loop():
    """Background task that runs the scalping engine."""
    global _engine_instance
    from .engine import ScalpingEngine
    from .config import ScalpingConfig
    # Check if engine should be enabled
    if os.environ.get("SCALPING_ENGINE_ENABLED", "1") != "1":
        print("[API] Engine disabled via SCALPING_ENGINE_ENABLED=0")
        return

    dry_run = os.environ.get("SCALPING_LIVE", "0") != "1"
    live_real_enabled = os.environ.get("SCALPING_ENABLE_LIVE_REAL", "0") == "1"
    interval = float(os.environ.get("SCALPING_INTERVAL", "5"))
    replay_interval_ms = int(os.environ.get("SCALPING_REPLAY_INTERVAL_MS", "200"))

    print(
        f"[API] Starting embedded engine (dry_run={dry_run}, interval={interval}s, "
        f"live_real_enabled={live_real_enabled})"
    )

    config = ScalpingConfig()
    _engine_instance = ScalpingEngine(
        config=config,
        dry_run=dry_run,
        replay_interval_ms=replay_interval_ms,
        live_real_enabled=live_real_enabled,
    )

    # Update API state
    state = get_state()
    state.running = False
    state.mode = _engine_instance.resolve_mode()
    state.start_time = datetime.now().isoformat()

    await _engine_instance.start()

    try:
        while True:
            try:
                if _engine_instance.replay_job_active:
                    await asyncio.sleep(_engine_instance.replay_interval_ms / 1000.0)
                    continue

                mode = _engine_instance.resolve_mode()
                _engine_instance.set_mode(mode)
                state.mode = mode
                state.running = mode != _engine_instance.IDLE

                if mode == _engine_instance.IDLE:
                    await asyncio.sleep(1.0)
                    continue

                await _engine_instance.run_cycle()
                await broadcast_update(
                    "update",
                    {
                        "cycle": state.cycle_count,
                        "cycle_count": state.cycle_count,
                        "last_cycle_time": state.last_cycle_time,
                        "mode": state.mode,
                    },
                )
            except Exception as e:
                print(f"[API] Engine cycle error: {e}")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        print("[API] Engine task cancelled")
    finally:
        state.running = False
        if _engine_instance:
            await _engine_instance.stop()


_price_ticker_task = None


async def _price_ticker_loop():
    """Background task: fetch live position prices and broadcast via WebSocket every 1.5s."""
    from engines.scalping.scalping.agents.execution_agents import _batch_fetch_position_ltp, _fetch_live_option_ltp

    while True:
        try:
            await asyncio.sleep(1.5)
            if not manager.active_connections:
                continue

            state = get_state()
            open_positions = [p for p in state.positions if p.get("status") in ("open", "partial")]
            if not open_positions:
                continue

            # Build Position-like objects for batch fetch
            class _PosStub:
                def __init__(self, d):
                    self.position_id = d.get("trade_id", "")
                    self.symbol = d.get("symbol", "")
                    self.strike = int(d.get("strike", 0) or 0)
                    self.option_type = d.get("option_type", "")
                    self.status = d.get("status", "open")
                    self.entry_price = float(d.get("entry_price", 0) or 0)

            stubs = [_PosStub(p) for p in open_positions]
            ctx_data: dict = {}
            ltp_map = _batch_fetch_position_ltp(stubs, ctx_data)

            # Fallback: individual fetch for any missed
            for stub in stubs:
                if stub.position_id not in ltp_map:
                    ltp = _fetch_live_option_ltp(stub.symbol, stub.strike, stub.option_type)
                    if ltp > 0:
                        ltp_map[stub.position_id] = ltp

            if not ltp_map:
                continue

            # Build price update payload
            updates = []
            for p in open_positions:
                tid = p.get("trade_id", "")
                ltp = ltp_map.get(tid, 0)
                if ltp <= 0:
                    continue
                entry = float(p.get("entry_price", 0) or 0)
                qty = int(p.get("remaining_qty", p.get("quantity", 0)) or 0)
                pnl = round((ltp - entry) * qty, 2)
                updates.append({
                    "trade_id": tid,
                    "current_price": round(ltp, 2),
                    "unrealized_pnl": pnl,
                })

            if updates:
                await manager.broadcast({
                    "type": "price_tick",
                    "timestamp": datetime.now().isoformat(),
                    "data": updates,
                })
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(3)


@app.on_event("startup")
async def startup_event():
    """Start background engine and price ticker when API starts."""
    global _engine_task, _price_ticker_task

    # ── Daily expiry classification check (fresh on every startup) ──
    _run_expiry_classification_check()

    _engine_task = asyncio.create_task(_run_engine_loop())
    _price_ticker_task = asyncio.create_task(_price_ticker_loop())
    print("[API] Background engine task scheduled")
    print("[API] Price ticker WebSocket task started (1.5s interval)")


def _run_expiry_classification_check() -> None:
    """Check expiry status per-index using exchange data. Runs every startup."""
    import sys
    from datetime import date
    from pathlib import Path

    today = date.today()
    config = ScalpingConfig()

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║            EXPIRY CLASSIFICATION CHECK                       ║")
    print("╠══════════════════════════════════════════════════════════════╣")

    # Try exchange-sourced schedule
    schedule_data = {}
    todays_expiry = []
    source = "unknown"

    _project_root = Path(__file__).resolve().parents[2]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

    try:
        from shared_project_engine.indices import get_expiry_schedule, get_todays_expiring_indices
        schedule_data = get_expiry_schedule(use_live=True)
        todays_expiry = get_todays_expiring_indices(use_live=True)
        source = "exchange_live"
    except ImportError:
        source = "cache_file"
    except Exception as e:
        print(f"║  ⚠ Schedule fetch error: {e}")
        source = "error"

    # Fallback: read cache file directly
    if not schedule_data:
        try:
            import json as _json
            for parents_up in (2, 3, 4):
                cache_path = Path(__file__).resolve().parents[parents_up] / "shared_project_engine" / "indices" / ".cache" / "expiry_schedule.json"
                if cache_path.exists():
                    raw = _json.loads(cache_path.read_text())
                    data = raw.get("data", {})
                    today_str = today.isoformat()
                    schedule_data = {k: {"nextExpiry": v.get("next_expiry"), "source": v.get("source")}
                                     for k, v in data.items() if isinstance(v, dict)}
                    todays_expiry = [k for k, v in data.items()
                                     if isinstance(v, dict) and v.get("next_expiry") == today_str]
                    fetched_at = raw.get("fetched_at", "unknown")
                    age_warning = ""
                    if fetched_at != "unknown":
                        try:
                            from datetime import datetime as dt
                            fetch_dt = dt.fromisoformat(str(fetched_at).replace("+05:30", ""))
                            age_hours = (dt.now() - fetch_dt).total_seconds() / 3600
                            if age_hours > 24:
                                age_warning = f" ⚠ STALE ({age_hours:.0f}h old)"
                        except Exception:
                            pass
                    source = f"cache_file{age_warning}"
                    break
        except Exception:
            pass

    # Map IndexType to names
    _idx_names = {
        "NSE:NIFTY50-INDEX": "NIFTY50",
        "NSE:NIFTYBANK-INDEX": "BANKNIFTY",
        "BSE:SENSEX-INDEX": "SENSEX",
        "NSE:FINNIFTY-INDEX": "FINNIFTY",
        "NSE:MIDCPNIFTY-INDEX": "MIDCPNIFTY",
    }

    print(f"║  Date:   {today}                                            ║")
    print(f"║  Source: {source:<50}  ║")
    print("║                                                              ║")

    for idx_type in config.indices:
        symbol = idx_type.value
        name = _idx_names.get(symbol, idx_type.name)
        is_expiry = name in todays_expiry
        next_exp = schedule_data.get(name, {}).get("nextExpiry", schedule_data.get(name, {}).get("next_expiry", "?"))
        marker = "★ EXPIRY" if is_expiry else "  ─"
        print(f"║  {name:<12} next_expiry={str(next_exp):<12} is_expiry={str(is_expiry):<6} {marker:<8} ║")

    if not todays_expiry:
        print("║                                                              ║")
        print("║  No index expiring today — non-expiry filters for all        ║")
    else:
        print("║                                                              ║")
        print(f"║  Today's expiring: {', '.join(todays_expiry):<40} ║")

    if not schedule_data:
        print("║                                                              ║")
        print("║  ⚠ WARNING: No expiry schedule available!                    ║")
        print("║  Using weekday fallback (may be incorrect for holidays)      ║")

    print("╚══════════════════════════════════════════════════════════════╝")
    print()


@app.on_event("shutdown")
async def shutdown_event():
    """Stop background engine and price ticker when API stops."""
    global _engine_task, _price_ticker_task
    for task in (_engine_task, _price_ticker_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    print("[API] Background engine stopped")


@app.get("/api/scalping/engine/status")
async def get_engine_status():
    """Get embedded engine status."""
    global _engine_instance, _engine_task
    return {
        "embedded_engine": _engine_instance is not None,
        "task_running": _engine_task is not None and not _engine_task.done(),
        "engine_cycle_count": _engine_instance.cycle_count if _engine_instance else 0,
        "engine_stats": _engine_instance.stats if _engine_instance else {},
        "mode": _engine_instance.mode if _engine_instance else "IDLE",
        "replay_active": _engine_instance.replay_job_active if _engine_instance else False,
        "last_replay_report": _engine_instance.last_replay_report if _engine_instance else {},
    }


# ============================================================================
# Kill Switch API
# ============================================================================

@app.get("/api/scalping/killswitch")
async def get_killswitch_status():
    """Get kill switch status and thresholds."""
    global _engine_instance
    state = get_state()

    response = {
        "active": state.kill_switch_active,
        "reason": state.kill_switch_reason,
        "triggered_at": state.kill_switch_triggered_at,
    }

    # Add detailed state from engine if available
    if _engine_instance and hasattr(_engine_instance, 'kill_switch'):
        response.update(_engine_instance.kill_switch.get_state())

    return response


@app.post("/api/scalping/killswitch/reset")
async def reset_killswitch():
    """Manually reset the kill switch."""
    global _engine_instance
    state = get_state()

    if not _engine_instance or not hasattr(_engine_instance, 'kill_switch'):
        return {"success": False, "error": "Engine not running"}

    result = _engine_instance.kill_switch.manual_reset()

    if result:
        state.kill_switch_active = False
        state.kill_switch_reason = None
        state.kill_switch_triggered_at = None
        return {"success": True, "message": "Kill switch reset successfully"}
    else:
        return {"success": False, "message": "Kill switch was not active"}


@app.post("/api/scalping/killswitch/trigger")
async def manual_trigger_killswitch(reason: str = "manual"):
    """Manually trigger the kill switch (emergency stop)."""
    global _engine_instance
    state = get_state()

    if not _engine_instance or not hasattr(_engine_instance, 'kill_switch'):
        return {"success": False, "error": "Engine not running"}

    from .agents.kill_switch_agent import KillSwitchReason
    from datetime import datetime

    # Trigger manual kill switch
    _engine_instance.kill_switch.trigger(
        KillSwitchReason.MANUAL,
        {"triggered_by": "api", "reason": reason}
    )

    state.kill_switch_active = True
    state.kill_switch_reason = "manual"
    state.kill_switch_triggered_at = datetime.now().isoformat()

    return {"success": True, "message": "Kill switch triggered manually"}


# ============================================================================
# Run API Server
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)

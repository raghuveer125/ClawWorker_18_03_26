"""
Shared dependencies for LiveBench API routers.

Contains path constants, singleton getters, helper functions,
Pydantic models, and WebSocket managers used across multiple routers.
"""

import os
import json
import asyncio
import logging
import re
from contextlib import suppress
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, WebSocket
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

DATA_PATH = Path(__file__).parent.parent / "data" / "agent_data"
HIDDEN_AGENTS_PATH = Path(__file__).parent.parent / "data" / "hidden_agents.json"
DISPLAYING_NAMES_PATH = Path(__file__).parent.parent / "data" / "displaying_names.json"
FYERS_DATA_PATH = Path(__file__).parent.parent / "data" / "fyers"
FYERS_SCREENER_LOG_PATH = Path(__file__).parent.parent.parent / "logs" / "fyers_screener_loop.log"
FYERSN7_DATA_PATH = (
    Path(__file__).parent.parent.parent.parent / "fyersN7" / "fyers-2026-03-05" / "postmortem"
)
_TASK_VALUES_PATH = (
    Path(__file__).parent.parent.parent / "scripts" / "task_value_estimates" / "task_values.jsonl"
)

# ---------------------------------------------------------------------------
# Task value lookup
# ---------------------------------------------------------------------------


def _load_task_values() -> dict:
    values = {}
    if not _TASK_VALUES_PATH.exists():
        return values
    with open(_TASK_VALUES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                tid = entry.get("task_id")
                val = entry.get("task_value_usd")
                if tid and val is not None:
                    values[tid] = val
            except json.JSONDecodeError:
                pass
    return values


TASK_VALUES = _load_task_values()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AgentStatus(BaseModel):
    """Agent status model"""
    signature: str
    balance: float
    net_worth: float
    survival_status: str
    current_activity: Optional[str] = None
    current_date: Optional[str] = None


class WorkTask(BaseModel):
    """Work task model"""
    task_id: str
    sector: str
    occupation: str
    prompt: str
    date: str
    status: str = "assigned"


class LearningEntry(BaseModel):
    """Learning memory entry"""
    topic: str
    content: str
    timestamp: str


class EconomicMetrics(BaseModel):
    """Economic metrics model"""
    balance: float
    total_token_cost: float
    total_work_income: float
    net_worth: float
    dates: List[str]
    balance_history: List[float]


# ---------------------------------------------------------------------------
# Agent directory helpers (used by agents router + artifacts + settings)
# ---------------------------------------------------------------------------


def _has_nonempty_jsonl(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return any(line.strip() for line in handle)
    except OSError:
        return False


def _subtree_has_files(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    try:
        return any(child.is_file() for child in path.rglob("*"))
    except OSError:
        return False


def _is_real_agent_dir(agent_dir: Path) -> bool:
    if not agent_dir.exists() or not agent_dir.is_dir():
        return False
    if not _has_nonempty_jsonl(agent_dir / "economic" / "balance.jsonl"):
        return False
    runtime_evidence = (
        _subtree_has_files(agent_dir / "activity_logs"),
        _subtree_has_files(agent_dir / "terminal_logs"),
        _has_nonempty_jsonl(agent_dir / "work" / "tasks.jsonl"),
        _has_nonempty_jsonl(agent_dir / "work" / "evaluations.jsonl"),
        _has_nonempty_jsonl(agent_dir / "decisions" / "decisions.jsonl"),
        _has_nonempty_jsonl(agent_dir / "memory" / "memory.jsonl"),
        _subtree_has_files(agent_dir / "learning"),
    )
    return any(runtime_evidence)


def _iter_real_agent_dirs():
    if not DATA_PATH.exists():
        return
    for agent_dir in DATA_PATH.iterdir():
        if agent_dir.is_dir() and _is_real_agent_dir(agent_dir):
            yield agent_dir


def _require_real_agent_dir(signature: str) -> Path:
    agent_dir = DATA_PATH / signature
    if not _is_real_agent_dir(agent_dir):
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent_dir


# ---------------------------------------------------------------------------
# Activity-log recovery helpers (used by agents router)
# ---------------------------------------------------------------------------


def _extract_sim_date_from_system(messages: list) -> Optional[str]:
    for message in messages or []:
        if message.get("role") != "system":
            continue
        content = message.get("content") or ""
        match = re.search(r"CURRENT ECONOMIC STATUS\s*-\s*(\d{4}-\d{2}-\d{2})", content)
        if match:
            return match.group(1)
    return None


def _infer_activity_from_messages(messages: list) -> Optional[str]:
    combined = "\n".join((msg.get("content") or "") for msg in (messages or []))
    lowered = combined.lower()
    if any(token in lowered for token in [
        "submit_work", "work task", "lending recommendation", "curriculum", "credit risk",
        "decide_activity", "activity completed",
    ]):
        return "work"
    if any(token in lowered for token in [
        "learn(", "learn topic", "research and learn", "save_to_memory"
    ]):
        return "learn"
    # Infer from user prompt ("Analyze your situation and decide your activity")
    if "decide your activity" in lowered:
        return "work"
    return None


def _extract_reasoning_from_messages(messages: list) -> str:
    # First try: look for assistant messages with actual content
    for message in reversed(messages or []):
        if message.get("role") != "assistant":
            continue
        content = (message.get("content") or "").strip()
        if content and len(content) > 10:
            compact = " ".join(content.split())
            return compact[:200]
    # Second try: look for tool results with reasoning
    for message in reversed(messages or []):
        content = (message.get("content") or "").strip()
        if "reasoning" in content.lower() and len(content) > 20:
            compact = " ".join(content.split())
            return compact[:200]
    # Third try: extract from user prompt date
    for message in messages or []:
        if message.get("role") == "user":
            content = (message.get("content") or "").strip()
            if "Today is" in content:
                return content[:200]
    return "Recovered from activity logs"


def _load_decisions_from_activity_logs(agent_dir: Path) -> List[dict]:
    activity_root = agent_dir / "activity_logs"
    if not activity_root.exists():
        return []
    recovered = []
    for log_path in sorted(activity_root.glob("**/log.jsonl")):
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                messages = entry.get("messages", [])
                activity = _infer_activity_from_messages(messages)
                if not activity:
                    continue
                sim_date = _extract_sim_date_from_system(messages)
                if not sim_date:
                    timestamp = (entry.get("timestamp") or "")
                    sim_date = timestamp[:10] if len(timestamp) >= 10 else ""
                recovered.append({
                    "activity": activity,
                    "date": sim_date,
                    "reasoning": _extract_reasoning_from_messages(messages),
                    "source": "activity_logs",
                })
    return recovered


# ---------------------------------------------------------------------------
# FYERS screener failure helper (used by fyersn7 router)
# ---------------------------------------------------------------------------


def _latest_fyers_screener_failure() -> Optional[Dict[str, str]]:
    if not FYERS_SCREENER_LOG_PATH.exists():
        return None
    try:
        lines = FYERS_SCREENER_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None

    message = None
    hint = None
    exited = None

    for line in reversed(lines[-80:]):
        stripped = line.strip()
        if not stripped:
            continue
        if message is None and stripped.startswith("\u274c"):
            message = stripped.lstrip("\u274c").strip()
            continue
        if hint is None and stripped.startswith("FYERS_WATCHLIST="):
            hint = stripped
            continue
        if exited is None and "FYERS screener exited with status" in stripped:
            exited = stripped
            continue
        if message and hint and exited:
            break

    if message is None and exited is None:
        return None

    payload = {
        "message": message or "Latest FYERS screener run failed",
        "updated_at": datetime.fromtimestamp(FYERS_SCREENER_LOG_PATH.stat().st_mtime).isoformat(),
    }
    if hint:
        payload["hint"] = hint
    if exited:
        payload["detail"] = exited
    return payload


# ---------------------------------------------------------------------------
# WebSocket connection managers
# ---------------------------------------------------------------------------


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients"""
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass  # intentional: skip disconnected WebSocket clients


manager = ConnectionManager()


def _market_stream_settings() -> Tuple[float, float, float]:
    interval_seconds = max(1.0, float(os.getenv("LIVE_MARKET_STREAM_INTERVAL_SEC", "15")))
    ttl_seconds = max(1.0, float(os.getenv("LIVE_MARKET_STREAM_TTL_SEC", str(interval_seconds))))
    read_timeout_seconds = max(20.0, interval_seconds + 10.0)
    return interval_seconds, ttl_seconds, read_timeout_seconds


class MarketBroadcastManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._latest_update: Optional[Dict[str, Any]] = None

    async def connect(self, websocket: WebSocket) -> Optional[Dict[str, Any]]:
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
            latest_update = dict(self._latest_update) if isinstance(self._latest_update, dict) else None
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run(), name="market-websocket-broadcast")
        return latest_update

    async def disconnect(self, websocket: WebSocket) -> None:
        task_to_cancel: Optional[asyncio.Task] = None
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
            if not self.active_connections and self._task is not None:
                task_to_cancel = self._task
                self._task = None
        if task_to_cancel is not None:
            task_to_cancel.cancel()
            with suppress(asyncio.CancelledError):
                await task_to_cancel

    async def broadcast(self, message: Dict[str, Any]) -> None:
        async with self._lock:
            connections = list(self.active_connections)
        dead_connections: List[WebSocket] = []
        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        if dead_connections:
            async with self._lock:
                for connection in dead_connections:
                    if connection in self.active_connections:
                        self.active_connections.remove(connection)

    async def _run(self) -> None:
        stream = None
        try:
            symbols, index_symbol_map, expected_indices = _load_market_index_symbols()
            if not symbols:
                await self.broadcast({
                    "type": "market_live_error",
                    "error": "No index symbols configured",
                    "timestamp": datetime.now().isoformat(),
                })
                return

            interval_seconds, ttl_seconds, read_timeout_seconds = _market_stream_settings()
            client = _build_market_client()
            stream = client.stream_quotes(
                symbols=",".join(symbols),
                interval_seconds=interval_seconds,
                ttl_seconds=ttl_seconds,
                read_timeout_seconds=read_timeout_seconds,
            )

            while True:
                event = await asyncio.to_thread(_next_market_stream_event, stream)
                if event is None:
                    break

                event_type = str(event.get("event", "") or "")
                if event_type == "quotes":
                    payload = _build_live_market_response(
                        event.get("payload", {}),
                        index_symbol_map=index_symbol_map,
                        expected_indices=expected_indices,
                    )
                    payload["type"] = "market_live_update"
                    payload["stream_source"] = event.get("_source", "service_stream")
                    async with self._lock:
                        self._latest_update = dict(payload)
                    await self.broadcast(payload)
                    continue

                if event_type == "error":
                    await self.broadcast({
                        "type": "market_live_error",
                        "error": event.get("error", "Unknown stream error"),
                        "timestamp": datetime.now().isoformat(),
                    })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Market broadcast error: {e}")
            await self.broadcast({
                "type": "market_live_error",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            })
        finally:
            if stream is not None:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            async with self._lock:
                if self._task is asyncio.current_task():
                    self._task = None


market_manager = MarketBroadcastManager()

# ---------------------------------------------------------------------------
# Singleton getters (lazy-loaded instances)
# ---------------------------------------------------------------------------

_ensemble_instance = None


def get_ensemble():
    """Get or create ensemble coordinator instance"""
    global _ensemble_instance
    if _ensemble_instance is None:
        from bots import EnsembleCoordinator, SharedMemory
        shared_memory = SharedMemory()
        _ensemble_instance = EnsembleCoordinator(shared_memory)
    return _ensemble_instance


_rh_pipeline_instance = None


def get_rh_pipeline():
    """Get or create the RegimeHunter independent pipeline instance."""
    global _rh_pipeline_instance
    if _rh_pipeline_instance is None:
        from trading.regime_hunter_pipeline import RegimeHunterPipeline

        mode = "paper"
        if os.getenv("FYERS_DRY_RUN", "true").lower() == "false" and \
           os.getenv("FYERS_ALLOW_LIVE_ORDERS", "false").lower() == "true":
            mode = "live"

        _rh_pipeline_instance = RegimeHunterPipeline(mode=mode)
    return _rh_pipeline_instance


_hybrid_pipeline_instance = None


def get_hybrid_pipeline():
    """Get or create hybrid regime hunter pipeline instance."""
    global _hybrid_pipeline_instance
    if _hybrid_pipeline_instance is None:
        try:
            from bots.regime_modules import RegimeHunterPipeline
            _hybrid_pipeline_instance = RegimeHunterPipeline()
            logger.info("Hybrid RegimeHunterPipeline initialized")
        except ImportError as e:
            logger.warning(f"Could not import hybrid pipeline: {e}")
            return None
    return _hybrid_pipeline_instance


_hybrid_bridge_instance = None


def get_hybrid_bridge():
    """Get or create the HybridExecutionBridge singleton."""
    global _hybrid_bridge_instance
    if _hybrid_bridge_instance is None:
        try:
            from trading.hybrid_execution_bridge import HybridExecutionBridge
            pipeline = get_hybrid_pipeline()
            if pipeline is None:
                logger.warning("[HybridBridge] Pipeline unavailable -- bridge not created")
                return None
            market_client = _build_market_client()
            _hybrid_bridge_instance = HybridExecutionBridge(
                pipeline=pipeline,
                market_client=market_client,
                mode=os.getenv("HYBRID_BRIDGE_MODE", "paper"),
                min_confidence=float(os.getenv("HYBRID_BRIDGE_MIN_CONF", "55")),
                max_concurrent=int(os.getenv("HYBRID_BRIDGE_MAX_POS", "2")),
                target_multiplier=float(os.getenv("HYBRID_BRIDGE_TARGET_MULT", "10.0")),
                stop_loss_pct=float(os.getenv("HYBRID_BRIDGE_SL_PCT", "70.0")),
                loop_interval=int(os.getenv("HYBRID_BRIDGE_INTERVAL", "60")),
            )
        except Exception:
            logger.exception("Failed to create HybridExecutionBridge")
    return _hybrid_bridge_instance


_auto_trader_instance = None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def get_auto_trader():
    """Get or create auto-trader instance"""
    global _auto_trader_instance
    if _auto_trader_instance is None:
        from trading.auto_trader import (
            AutoTrader,
            resolve_auto_trader_data_dir,
            resolve_auto_trader_strategy_id,
            get_trading_mode_from_env,
        )
        ensemble = get_ensemble()
        trading_mode = get_trading_mode_from_env()
        strategy_id = resolve_auto_trader_strategy_id()
        data_dir = resolve_auto_trader_data_dir(strategy_id)
        _auto_trader_instance = AutoTrader(
            ensemble=ensemble,
            mode=trading_mode,
            data_dir=str(data_dir),
            strategy_id=strategy_id,
        )
    return _auto_trader_instance


# ---------------------------------------------------------------------------
# Market data helpers (used by market router, websocket router, ensemble, etc.)
# ---------------------------------------------------------------------------


def _market_env_file() -> Optional[str]:
    server_dir = Path(__file__).parent
    project_root = server_dir.parent.parent.parent  # livebench/api -> ClawWork_FyersN7
    env_file = project_root / ".env"
    return str(env_file) if env_file.exists() else None


def _build_market_client():
    from trading.fyers_client import build_market_data_client
    return build_market_data_client(_market_env_file())


def _load_market_index_symbols() -> Tuple[List[str], Dict[str, str], List[str]]:
    from shared_project_engine.indices import INDEX_CONFIG

    symbols: List[str] = []
    index_symbol_map: Dict[str, str] = {}
    ordered_indices: List[str] = []

    for name, cfg in INDEX_CONFIG.items():
        ordered_indices.append(name)
        sym = cfg.get("symbol")
        if sym:
            symbols.append(sym)
            index_symbol_map[sym] = name

    return symbols, index_symbol_map, ordered_indices


def _to_float(val, default=0.0):
    """Safe float conversion."""
    try:
        return float(val) if val not in (None, "", "None") else default
    except (ValueError, TypeError):
        return default


def _to_int(val, default=0):
    """Safe int conversion."""
    try:
        return int(float(val)) if val not in (None, "", "None") else default
    except (ValueError, TypeError):
        return default


def _get_fyersn7_fallback_data():
    """
    Fallback: Read latest spot prices from fyersN7 decision journals.
    Used when FYERS API is rate-limited.
    """
    indices_data = {}
    today = datetime.now().strftime("%Y-%m-%d")
    fyersn7_base = FYERSN7_DATA_PATH / today

    if not fyersn7_base.exists():
        return indices_data

    index_map = {
        "NIFTY50": "NIFTY50",
        "BANKNIFTY": "BANKNIFTY",
        "FINNIFTY": "FINNIFTY",
        "MIDCPNIFTY": "MIDCPNIFTY",
        "SENSEX": "SENSEX",
    }

    for folder_name, index_name in index_map.items():
        journal_path = fyersn7_base / folder_name / "decision_journal.csv"
        if not journal_path.exists():
            continue
        try:
            with open(journal_path, "r") as f:
                lines = f.readlines()
                if len(lines) < 2:
                    continue
                header = lines[0].strip().split(",")
                last_line = lines[-1].strip().split(",")
                spot_idx = header.index("spot") if "spot" in header else -1
                time_idx = header.index("time") if "time" in header else -1
                if spot_idx >= 0 and len(last_line) > spot_idx:
                    spot = float(last_line[spot_idx])
                    time_str = last_line[time_idx] if time_idx >= 0 and len(last_line) > time_idx else ""
                    indices_data[index_name] = {
                        "ltp": spot,
                        "open": 0,
                        "high": 0,
                        "low": 0,
                        "prev_close": 0,
                        "change": 0,
                        "change_pct": 0,
                        "volume": 0,
                        "symbol": index_name,
                        "source": "fyersN7",
                        "time": time_str,
                    }
        except Exception as e:
            logger.warning(f"Error reading fyersN7 data for {index_name}: {e}")
            continue

    return indices_data


def _parse_index_quotes(quotes_result: Dict[str, Any], index_symbol_map: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    data = quotes_result.get("data", {})
    quotes_list = data.get("d", []) if isinstance(data, dict) else []
    indices_data: Dict[str, Dict[str, Any]] = {}

    for quote in quotes_list:
        v = quote.get("v", {})
        sym = v.get("symbol") or v.get("n") or quote.get("n")
        if not sym or sym not in index_symbol_map:
            continue

        quote_state = str(v.get("s", "")).strip().lower()
        quote_code = v.get("code")
        has_quote_error = bool(v.get("errmsg")) or quote_state == "error" or (
            isinstance(quote_code, (int, float)) and quote_code < 0
        )
        if has_quote_error:
            logger.warning(f"Skipping errored quote for {sym}: {v.get('errmsg') or quote_code}")
            continue

        index_name = index_symbol_map[sym]
        ltp = v.get("lp", 0) or v.get("ltp", 0) or v.get("last_price", 0)
        prev_close = v.get("prev_close_price", 0) or v.get("pc", 0)
        change = ltp - prev_close if prev_close else 0
        change_pct = (change / prev_close * 100) if prev_close else 0

        indices_data[index_name] = {
            "ltp": ltp,
            "open": v.get("open_price", 0) or v.get("o", 0),
            "high": v.get("high_price", 0) or v.get("h", 0),
            "low": v.get("low_price", 0) or v.get("l", 0),
            "prev_close": prev_close,
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "volume": v.get("volume", 0) or v.get("v", 0),
            "symbol": sym,
            "source": "fyers_api",
        }

    return indices_data


def _fill_market_fallback(
    indices_data: Dict[str, Dict[str, Any]],
    expected_indices: List[str],
) -> List[str]:
    fallback_data = _get_fyersn7_fallback_data()
    fallback_filled = []

    for index_name in expected_indices:
        current = indices_data.get(index_name)
        current_ltp = _to_float(current.get("ltp")) if isinstance(current, dict) else 0
        if current and current_ltp > 0:
            continue
        fallback = fallback_data.get(index_name)
        fallback_ltp = _to_float(fallback.get("ltp")) if isinstance(fallback, dict) else 0
        if fallback and fallback_ltp > 0:
            enriched = dict(fallback)
            enriched["source"] = "fyersN7_fallback"
            indices_data[index_name] = enriched
            fallback_filled.append(index_name)

    return fallback_filled


def _build_live_market_response(
    quotes_result: Dict[str, Any],
    index_symbol_map: Dict[str, str],
    expected_indices: List[str],
) -> Dict[str, Any]:
    success = quotes_result.get("success")
    has_data = isinstance(quotes_result.get("data"), dict)
    if success is False or (success is None and not has_data):
        error_msg = quotes_result.get("error", "Unknown error")
        logger.warning(f"FYERS quotes failed: {error_msg}, trying fyersN7 fallback")

        fallback_data = _get_fyersn7_fallback_data()
        if fallback_data:
            return {
                "indices": fallback_data,
                "market_open": True,
                "timestamp": datetime.now().isoformat(),
                "source": "fyersN7_fallback",
                "api_error": error_msg,
            }

        return {
            "error": error_msg,
            "indices": {},
            "market_open": False,
            "timestamp": datetime.now().isoformat(),
        }

    indices_data = _parse_index_quotes(quotes_result, index_symbol_map)
    fallback_filled = _fill_market_fallback(indices_data, expected_indices)

    return {
        "indices": indices_data,
        "market_open": len(indices_data) > 0 and any(
            _to_float(d.get("ltp")) > 0 for d in indices_data.values()
        ),
        "timestamp": datetime.now().isoformat(),
        "source": "fyers_api+fyersN7_fallback" if fallback_filled else "fyers_api",
        "fallback_filled": fallback_filled,
    }


def _next_market_stream_event(stream) -> Optional[Dict[str, Any]]:
    try:
        return next(stream)
    except StopIteration:
        return None

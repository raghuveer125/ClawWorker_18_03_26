"""
LiveBench API Server - Real-time updates and data access for frontend

This FastAPI server provides:
- WebSocket endpoint for live agent activity streaming
- REST endpoints for agent data, tasks, and economic metrics
- Real-time updates as agents work and learn
"""

import os
import json
import asyncio
import random
import re
import csv
import logging
from contextlib import suppress
from datetime import date, datetime
from functools import lru_cache

logger = logging.getLogger(__name__)
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import glob

app = FastAPI(title="LiveBench API", version="1.0.0")

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data path
DATA_PATH = Path(__file__).parent.parent / "data" / "agent_data"
HIDDEN_AGENTS_PATH = Path(__file__).parent.parent / "data" / "hidden_agents.json"
FYERS_DATA_PATH = Path(__file__).parent.parent / "data" / "fyers"
# fyersN7 signal data path (relative to ClawWork_FyersN7 project root)
FYERSN7_DATA_PATH = Path(__file__).parent.parent.parent.parent / "fyersN7" / "fyers-2026-03-05" / "postmortem"

# Task value lookup (task_id -> task_value_usd)
_TASK_VALUES_PATH = Path(__file__).parent.parent.parent / "scripts" / "task_value_estimates" / "task_values.jsonl"


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
    if any(token in lowered for token in ["submit_work", "work task", "lending recommendation", "curriculum", "credit risk"]):
        return "work"
    if any(token in lowered for token in ["learn(", "learn topic", "research and learn", "save_to_memory"]):
        return "learn"
    return None


def _extract_reasoning_from_messages(messages: list) -> str:
    for message in messages or []:
        if message.get("role") != "assistant":
            continue
        content = (message.get("content") or "").strip()
        if content:
            compact = " ".join(content.split())
            return compact[:200]
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

# Active WebSocket connections
active_connections: List[WebSocket] = []


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


# WebSocket Connection Manager
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
            except:
                pass


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


@app.get("/")
async def root():
    """API root endpoint"""
    return {
        "message": "LiveBench API",
        "version": "1.0.0",
        "endpoints": {
            "agents": "/api/agents",
            "agent_detail": "/api/agents/{signature}",
            "tasks": "/api/agents/{signature}/tasks",
            "learning": "/api/agents/{signature}/learning",
            "economic": "/api/agents/{signature}/economic",
            "websocket": "/ws"
        }
    }


@app.get("/api/")
async def api_root():
    """API health check endpoint"""
    return {"status": "ok", "message": "LiveBench API is running"}


@app.get("/api/agents")
async def get_agents():
    """Get list of all agents with their current status"""
    agents = []

    if not DATA_PATH.exists():
        return {"agents": []}

    for agent_dir in DATA_PATH.iterdir():
        if agent_dir.is_dir():
            signature = agent_dir.name

            # Get latest balance
            balance_file = agent_dir / "economic" / "balance.jsonl"
            balance_data = None
            if balance_file.exists():
                with open(balance_file, 'r') as f:
                    lines = f.readlines()
                    if lines:
                        balance_data = json.loads(lines[-1])

            # Get latest decision
            decision_file = agent_dir / "decisions" / "decisions.jsonl"
            current_activity = None
            current_date = None
            if decision_file.exists():
                with open(decision_file, 'r') as f:
                    lines = f.readlines()
                    if lines:
                        decision = json.loads(lines[-1])
                        current_activity = decision.get("activity")
                        current_date = decision.get("date")

            if balance_data:
                agents.append({
                    "signature": signature,
                    "balance": balance_data.get("balance", 0),
                    "net_worth": balance_data.get("net_worth", 0),
                    "survival_status": balance_data.get("survival_status", "unknown"),
                    "current_activity": current_activity,
                    "current_date": current_date,
                    "total_token_cost": balance_data.get("total_token_cost", 0)
                })

    return {"agents": agents}


@app.get("/api/agents/{signature}")
async def get_agent_details(signature: str):
    """Get detailed information about a specific agent"""
    agent_dir = DATA_PATH / signature

    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get balance history
    balance_file = agent_dir / "economic" / "balance.jsonl"
    balance_history = []
    if balance_file.exists():
        with open(balance_file, 'r') as f:
            for line in f:
                balance_history.append(json.loads(line))

    # Get decisions
    decision_file = agent_dir / "decisions" / "decisions.jsonl"
    decisions = []
    if decision_file.exists():
        with open(decision_file, 'r') as f:
            for line in f:
                decisions.append(json.loads(line))
    else:
        decisions = _load_decisions_from_activity_logs(agent_dir)

    # Get evaluation statistics
    evaluations_file = agent_dir / "work" / "evaluations.jsonl"
    avg_evaluation_score = None
    evaluation_scores = []
    
    if evaluations_file.exists():
        with open(evaluations_file, 'r') as f:
            for line in f:
                eval_data = json.loads(line)
                score = eval_data.get("evaluation_score")
                if score is not None:
                    evaluation_scores.append(score)
        
        if evaluation_scores:
            avg_evaluation_score = sum(evaluation_scores) / len(evaluation_scores)
    
    # Get latest status
    latest_balance = balance_history[-1] if balance_history else {}
    latest_decision = decisions[-1] if decisions else {}

    return {
        "signature": signature,
        "current_status": {
            "balance": latest_balance.get("balance", 0),
            "net_worth": latest_balance.get("net_worth", 0),
            "survival_status": latest_balance.get("survival_status", "unknown"),
            "total_token_cost": latest_balance.get("total_token_cost", 0),
            "total_work_income": latest_balance.get("total_work_income", 0),
            "current_activity": latest_decision.get("activity"),
            "current_date": latest_decision.get("date"),
            "avg_evaluation_score": avg_evaluation_score,  # Average 0.0-1.0 score
            "num_evaluations": len(evaluation_scores)
        },
        "balance_history": balance_history,
        "decisions": decisions,
        "evaluation_scores": evaluation_scores  # List of all scores
    }


@app.get("/api/agents/{signature}/tasks")
async def get_agent_tasks(signature: str):
    """Get all tasks assigned to an agent"""
    agent_dir = DATA_PATH / signature

    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    tasks_file = agent_dir / "work" / "tasks.jsonl"
    evaluations_file = agent_dir / "work" / "evaluations.jsonl"

    tasks = []
    if tasks_file.exists():
        with open(tasks_file, 'r') as f:
            for line in f:
                tasks.append(json.loads(line))

    # Load evaluations grouped by task_id (preserve order)
    evaluations = {}
    if evaluations_file.exists():
        with open(evaluations_file, 'r') as f:
            for line in f:
                eval_data = json.loads(line)
                task_id = eval_data.get("task_id")
                if task_id:
                    if task_id not in evaluations:
                        evaluations[task_id] = []
                    evaluations[task_id].append(eval_data)

    # Merge tasks with evaluations
    for task in tasks:
        task_id = task.get("task_id")
        # Inject task market value if available
        if task_id and task_id in TASK_VALUES:
            task["task_value_usd"] = TASK_VALUES[task_id]
        evaluation_list = evaluations.get(task_id, [])
        evaluation = evaluation_list.pop(0) if evaluation_list else None
        if evaluation is not None:
            task["evaluation"] = evaluation
            task["completed"] = True
            task["payment"] = evaluation.get("payment", 0)
            task["feedback"] = evaluation.get("feedback", "")
            task["evaluation_score"] = evaluation.get("evaluation_score", None)  # 0.0-1.0 scale
            task["evaluation_method"] = evaluation.get("evaluation_method", "heuristic")
        else:
            task["completed"] = False
            task["payment"] = 0
            task["evaluation_score"] = None

    return {"tasks": tasks}


@app.get("/api/agents/{signature}/terminal-log/{date}")
async def get_terminal_log(signature: str, date: str):
    """Get terminal log for an agent on a specific date"""
    agent_dir = DATA_PATH / signature
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")
    log_file = agent_dir / "terminal_logs" / f"{date}.log"
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    content = log_file.read_text(encoding="utf-8", errors="replace")
    return {"date": date, "content": content}


@app.get("/api/agents/{signature}/learning")
async def get_agent_learning(signature: str):
    """Get agent's learning memory"""
    agent_dir = DATA_PATH / signature

    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    memory_file = agent_dir / "memory" / "memory.jsonl"

    if not memory_file.exists():
        return {"memory": "", "entries": []}

    # Parse JSONL format
    entries = []
    with open(memory_file, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                entries.append({
                    "topic": entry.get("topic", "Unknown"),
                    "timestamp": entry.get("timestamp", ""),
                    "date": entry.get("date", ""),
                    "content": entry.get("knowledge", "")
                })

    # Create a summary memory content
    memory_content = "\n\n".join([
        f"## {entry['topic']} ({entry['date']})\n{entry['content']}"
        for entry in entries
    ])

    return {
        "memory": memory_content,
        "entries": entries
    }


@app.get("/api/agents/{signature}/economic")
async def get_agent_economic(signature: str):
    """Get economic metrics for an agent"""
    agent_dir = DATA_PATH / signature

    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    balance_file = agent_dir / "economic" / "balance.jsonl"

    if not balance_file.exists():
        raise HTTPException(status_code=404, detail="No economic data found")

    dates = []
    balance_history = []
    token_costs = []
    work_income = []

    with open(balance_file, 'r') as f:
        for line in f:
            data = json.loads(line)
            dates.append(data.get("date", ""))
            balance_history.append(data.get("balance", 0))
            token_costs.append(data.get("daily_token_cost", 0))
            work_income.append(data.get("work_income_delta", 0))

    latest = json.loads(line) if line else {}

    return {
        "balance": latest.get("balance", 0),
        "total_token_cost": latest.get("total_token_cost", 0),
        "total_work_income": latest.get("total_work_income", 0),
        "net_worth": latest.get("net_worth", 0),
        "survival_status": latest.get("survival_status", "unknown"),
        "dates": dates,
        "balance_history": balance_history,
        "token_costs": token_costs,
        "work_income": work_income
    }


@app.get("/api/agents/{signature}/learning/roi")
async def get_learning_roi(signature: str):
    """Get learning ROI (Return on Investment) metrics for an agent.
    
    Shows which knowledge topics contribute most to task success and earnings.
    """
    agent_dir = DATA_PATH / signature

    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    # Load effectiveness index
    effectiveness_dir = agent_dir / "knowledge_effectiveness"
    index_file = effectiveness_dir / "knowledge_index.json"

    if not index_file.exists():
        return {
            "total_knowledge_items": 0,
            "total_knowledge_uses": 0,
            "total_earnings_from_knowledge": 0,
            "avg_earnings_per_use": 0,
            "high_roi_topics": [],
            "message": "No knowledge effectiveness data yet"
        }

    try:
        with open(index_file, 'r', encoding='utf-8') as f:
            index = json.load(f)

        # Calculate metrics
        total_items = len(index)
        total_uses = sum(data["total_uses"] for data in index.values())
        total_earnings = sum(data["total_earnings"] for data in index.values())
        avg_earnings_per_use = total_earnings / max(1, total_uses)

        # Identify high-ROI topics
        high_roi_topics = []
        for topic, data in index.items():
            if data["total_uses"] >= 2:  # Min uses threshold
                success_rate = data["successful_uses"] / max(1, data["total_uses"])
                avg_earnings = data["total_earnings"] / max(1, data["total_uses"])

                if success_rate >= 0.6 or avg_earnings >= 10.0:
                    high_roi_topics.append({
                        "topic": topic,
                        "total_uses": data["total_uses"],
                        "success_rate": round(success_rate, 2),
                        "total_earnings": round(data["total_earnings"], 2),
                        "avg_earnings": round(avg_earnings, 2),
                        "last_used": data["last_used"]
                    })

        # Sort by earnings
        high_roi_topics = sorted(high_roi_topics, key=lambda x: x["total_earnings"], reverse=True)

        return {
            "total_knowledge_items": total_items,
            "total_knowledge_uses": total_uses,
            "total_earnings_from_knowledge": round(total_earnings, 2),
            "avg_earnings_per_use": round(avg_earnings_per_use, 2),
            "high_roi_topics": high_roi_topics,
            "all_topics": [
                {
                    "topic": topic,
                    "total_uses": data["total_uses"],
                    "success_rate": round(data["successful_uses"] / max(1, data["total_uses"]), 2),
                    "total_earnings": round(data["total_earnings"], 2)
                }
                for topic, data in sorted(index.items(), key=lambda x: x[1]["total_earnings"], reverse=True)
            ]
        }
    except Exception as e:
        return {
            "error": str(e),
            "total_knowledge_items": 0,
            "high_roi_topics": []
        }


@app.get("/api/leaderboard")
async def get_leaderboard():
    """Get leaderboard data for all agents with summary metrics and balance histories"""
    if not DATA_PATH.exists():
        return {"agents": []}

    agents = []

    for agent_dir in DATA_PATH.iterdir():
        if not agent_dir.is_dir():
            continue

        signature = agent_dir.name

        # Load balance history
        balance_file = agent_dir / "economic" / "balance.jsonl"
        balance_history = []
        if balance_file.exists():
            with open(balance_file, 'r') as f:
                for line in f:
                    if line.strip():
                        balance_history.append(json.loads(line))

        if not balance_history:
            continue

        latest = balance_history[-1]
        initial_balance = balance_history[0].get("balance", 0)
        current_balance = latest.get("balance", 0)
        pct_change = ((current_balance - initial_balance) / initial_balance * 100) if initial_balance else 0

        # Load evaluation scores
        evaluations_file = agent_dir / "work" / "evaluations.jsonl"
        evaluation_scores = []
        if evaluations_file.exists():
            with open(evaluations_file, 'r') as f:
                for line in f:
                    if line.strip():
                        eval_data = json.loads(line)
                        score = eval_data.get("evaluation_score")
                        if score is not None:
                            evaluation_scores.append(score)

        avg_eval_score = (sum(evaluation_scores) / len(evaluation_scores)) if evaluation_scores else None

        # Strip balance history to essential fields, exclude initialization
        stripped_history = [
            {
                "date": entry.get("date"),
                "balance": entry.get("balance", 0),
                "task_completion_time_seconds": entry.get("task_completion_time_seconds"),
            }
            for entry in balance_history
            if entry.get("date") != "initialization"
        ]

        agents.append({
            "signature": signature,
            "initial_balance": initial_balance,
            "current_balance": current_balance,
            "pct_change": round(pct_change, 1),
            "total_token_cost": latest.get("total_token_cost", 0),
            "total_work_income": latest.get("total_work_income", 0),
            "net_worth": latest.get("net_worth", 0),
            "survival_status": latest.get("survival_status", "unknown"),
            "num_tasks": len(evaluation_scores),
            "avg_eval_score": avg_eval_score,
            "balance_history": stripped_history,
        })

    # Sort by current_balance descending
    agents.sort(key=lambda a: a["current_balance"], reverse=True)

    return {"agents": agents}


@app.get("/api/fyers/screener/latest")
async def get_latest_fyers_screener():
    """Get the most recent FYERS screener output JSON."""
    if not FYERS_DATA_PATH.exists():
        return {"available": False, "message": "No FYERS screener data directory found"}

    screener_files = sorted(
        FYERS_DATA_PATH.glob("screener_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not screener_files:
        return {"available": False, "message": "No screener runs found"}

    latest_file = screener_files[0]
    try:
        payload = json.loads(latest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=f"Invalid JSON in {latest_file.name}")

    return {
        "available": True,
        "file": latest_file.name,
        "updated_at": datetime.fromtimestamp(latest_file.stat().st_mtime).isoformat(),
        "data": payload,
    }


# =============================================================================
# FYERSN7 SIGNAL VIEW ENDPOINTS
# =============================================================================

def _read_csv_as_dicts(file_path: Path) -> List[Dict]:
    """Read CSV file and return list of dicts."""
    if not file_path.exists():
        return []
    return list(_read_csv_as_dicts_cached(str(file_path), file_path.stat().st_mtime_ns))


@lru_cache(maxsize=64)
def _read_csv_as_dicts_cached(path_str: str, mtime_ns: int) -> tuple[Dict, ...]:
    """Read and cache CSV rows by file path + mtime."""
    rows = []
    with open(path_str, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return tuple(rows)


def _dedupe_csv_rows(rows: List[Dict], key_fields: List[str]) -> List[Dict]:
    deduped: List[Dict] = []
    seen = set()

    for row in rows:
        key = tuple((row.get(field, "") or "") for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    return deduped


def _dedupe_trade_rows(rows: List[Dict]) -> List[Dict]:
    return _dedupe_csv_rows(
        rows,
        [
            "trade_id",
            "symbol",
            "side",
            "strike",
            "qty",
            "entry_date",
            "entry_time",
            "entry_price",
            "exit_date",
            "exit_time",
            "exit_price",
            "exit_reason",
        ],
    )


def _dedupe_event_rows(rows: List[Dict]) -> List[Dict]:
    return _dedupe_csv_rows(
        rows,
        [
            "event_date",
            "event_time",
            "event_type",
            "side",
            "strike",
            "entry",
            "exit",
            "reason",
        ],
    )


def _transform_signal_row(row: Dict) -> Dict:
    """Transform a decision_journal row to the frontend-friendly signal shape."""
    return {
        "date": row.get("date", ""),
        "time": row.get("time", ""),
        "side": row.get("side", ""),
        "strike": _to_int(row.get("strike")),
        "strike_pcr": _to_float(row.get("strike_pcr")),
        "entry": _to_float(row.get("entry")),
        "sl": _to_float(row.get("sl")),
        "t1": _to_float(row.get("t1")),
        "t2": _to_float(row.get("t2")),
        "confidence": _to_int(row.get("confidence")),
        "status": row.get("status", ""),
        "score": row.get("score", ""),
        "action": row.get("action", ""),
        "stable": row.get("stable", ""),
        "cooldown_sec": _to_int(row.get("cooldown_sec")),
        "entry_ready": row.get("entry_ready", "N") == "Y",
        "selected": row.get("selected", "N") == "Y",
        "bid": _to_float(row.get("bid")),
        "ask": _to_float(row.get("ask")),
        "spread_pct": _to_float(row.get("spread_pct")),
        "iv": _to_float(row.get("iv")),
        "delta": _to_float(row.get("delta")),
        "gamma": _to_float(row.get("gamma")),
        "theta_day": _to_float(row.get("theta_day")),
        "decay_pct": _to_float(row.get("decay_pct")),
        "vote_ce": _to_int(row.get("vote_ce")),
        "vote_pe": _to_int(row.get("vote_pe")),
        "vote_side": row.get("vote_side", ""),
        "vote_diff": _to_int(row.get("vote_diff")),
        "vol_dom": row.get("vol_dom", ""),
        "vol_switch": row.get("vol_switch", "N") == "Y",
        "flow_match": row.get("flow_match", ""),
        "fvg_side": row.get("fvg_side", ""),
        "fvg_active": row.get("fvg_active", ""),
        "fvg_gap": _to_float(row.get("fvg_gap")),
        "fvg_distance": _to_float(row.get("fvg_distance")),
        "fvg_distance_atr": _to_float(row.get("fvg_distance_atr")),
        "fvg_plus": row.get("fvg_plus", "N") == "Y",
        "learn_prob": _to_float(row.get("learn_prob")),
        "learn_gate": row.get("learn_gate", ""),
        "outside_window": row.get("outside_window", ""),
        "reason": row.get("reason", ""),
        "outcome": row.get("outcome", ""),
        "spot": _to_float(row.get("spot")),
        "vix": _to_float(row.get("vix")),
        "net_pcr": _to_float(row.get("net_pcr")),
        "max_pain": _to_float(row.get("max_pain")),
        "max_pain_dist": _to_float(row.get("max_pain_dist")),
        "fut_symbol": row.get("fut_symbol", ""),
        "contract_symbol": row.get("contract_symbol", "") or row.get("option_symbol", ""),
        "option_expiry": row.get("option_expiry", "") or row.get("expiry", "") or row.get("expiry_date", ""),
        "option_expiry_code": row.get("option_expiry_code", ""),
        "fut_basis": _to_float(row.get("fut_basis")),
        "fut_basis_pct": _to_float(row.get("fut_basis_pct")),
    }


def _is_valid_trade_signal(row: Dict) -> bool:
    side = str(row.get("side", "")).upper()
    strike = row.get("strike")
    return side in ("CE", "PE") and strike not in (None, "", "None")


def _signal_row_key(row: Dict) -> tuple[str, str]:
    return (str(row.get("date", "")), str(row.get("time", "")))


def _build_latest_signal_payload(rows: List[Dict]) -> Dict:
    """
    Return only the latest decision_journal batch plus metadata needed by SignalView.

    This avoids sending the full day's journal to the browser when the page only
    renders the most recent timestamp.
    """
    total_signals = len(rows)
    selected_count = 0
    latest_signal_row = None
    latest_valid_key = None
    latest_valid_batch_rows: List[Dict] = []

    for row in rows:
        latest_signal_row = row
        if row.get("selected") == "Y":
            selected_count += 1

        if not _is_valid_trade_signal(row):
            continue

        row_key = _signal_row_key(row)
        if latest_valid_key != row_key:
            latest_valid_key = row_key
            latest_valid_batch_rows = [row]
        else:
            latest_valid_batch_rows.append(row)

    deduped_batch: Dict[str, Dict] = {}
    for row in latest_valid_batch_rows:
        deduped_batch[f"{row.get('strike')}-{row.get('side')}"] = _transform_signal_row(row)

    return {
        "rows": list(deduped_batch.values()),
        "total_signals": total_signals,
        "selected_count": selected_count,
        "latest_signal": _transform_signal_row(latest_signal_row) if latest_signal_row else {},
    }


def _empty_latest_signal_payload() -> Dict:
    return {
        "rows": [],
        "total_signals": 0,
        "selected_count": 0,
        "latest_signal": {},
    }


def _build_latest_signal_payload_from_csv(file_path: Path) -> Dict:
    """Stream the CSV once and keep only the latest batch plus summary metadata."""
    if not file_path.exists():
        return _empty_latest_signal_payload()
    return _build_latest_signal_payload_from_csv_cached(str(file_path), file_path.stat().st_mtime_ns)


@lru_cache(maxsize=64)
def _build_latest_signal_payload_from_csv_cached(path_str: str, mtime_ns: int) -> Dict:
    total_signals = 0
    selected_count = 0
    latest_signal_row = None
    latest_valid_key = None
    latest_valid_batch_rows: List[Dict] = []

    with open(path_str, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw_row in reader:
            row = dict(raw_row)
            total_signals += 1
            latest_signal_row = row

            if row.get("selected") == "Y":
                selected_count += 1

            if not _is_valid_trade_signal(row):
                continue

            row_key = _signal_row_key(row)
            if latest_valid_key != row_key:
                latest_valid_key = row_key
                latest_valid_batch_rows = [row]
            else:
                latest_valid_batch_rows.append(row)

    if latest_signal_row is None:
        return _empty_latest_signal_payload()

    deduped_batch: Dict[str, Dict] = {}
    for row in latest_valid_batch_rows:
        deduped_batch[f"{row.get('strike')}-{row.get('side')}"] = _transform_signal_row(row)

    return {
        "rows": list(deduped_batch.values()),
        "total_signals": total_signals,
        "selected_count": selected_count,
        "latest_signal": _transform_signal_row(latest_signal_row),
    }


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


# =============================================================================
# INDICES CONFIGURATION ENDPOINT
# =============================================================================

@lru_cache(maxsize=1)
def _get_cached_indices_config() -> Dict:
    """Load centralized indices configuration once per API process."""
    try:
        import sys
        # Add shared_project_engine to path if not already
        shared_path = Path(__file__).parent.parent.parent.parent / "shared_project_engine"
        if str(shared_path.parent) not in sys.path:
            sys.path.insert(0, str(shared_path.parent))

        from shared_project_engine.indices import INDEX_CONFIG, ACTIVE_INDICES
        from shared_project_engine.indices.config import MONTHLY_EXPIRY_DATES

        return {
            "indices": {
                name: {
                    "name": cfg["name"],
                    "displayName": cfg["display_name"],
                    "exchange": cfg["exchange"],
                    "lotSize": cfg["lot_size"],
                    "strikeGap": cfg["strike_gap"],
                    "expiryWeekday": cfg["expiry_weekday"],
                    "enabled": cfg.get("enabled", True),
                }
                for name, cfg in INDEX_CONFIG.items()
            },
            "activeIndices": ACTIVE_INDICES,
            "monthlyExpiry": MONTHLY_EXPIRY_DATES,
        }
    except ImportError as e:
        # Fallback if shared_project_engine not available
        return {
            "indices": {
                "SENSEX": {"name": "SENSEX", "displayName": "BSE SENSEX", "enabled": True},
                "NIFTY50": {"name": "NIFTY50", "displayName": "NIFTY 50", "enabled": True},
                "BANKNIFTY": {"name": "BANKNIFTY", "displayName": "BANK NIFTY", "enabled": True},
                "FINNIFTY": {"name": "FINNIFTY", "displayName": "NIFTY FIN SERVICE", "enabled": True},
                "MIDCPNIFTY": {"name": "MIDCPNIFTY", "displayName": "NIFTY MIDCAP SELECT", "enabled": False},
            },
            "activeIndices": ["SENSEX", "NIFTY50", "BANKNIFTY", "FINNIFTY"],
            "error": str(e),
        }


@app.get("/api/indices/config")
async def get_indices_config():
    """Get centralized indices configuration."""
    return _get_cached_indices_config()


@app.get("/api/fyersn7/dates")
async def get_fyersn7_dates():
    """Get list of available dates with signal data."""
    if not FYERSN7_DATA_PATH.exists():
        return {"dates": [], "message": "No fyersN7 data directory found"}

    dates = _list_fyersn7_dates()

    return {"dates": dates, "latest": dates[0] if dates else None}


def _list_fyersn7_dates() -> List[str]:
    if not FYERSN7_DATA_PATH.exists():
        return []
    return sorted([
        d.name for d in FYERSN7_DATA_PATH.iterdir()
        if d.is_dir() and d.name.startswith("202")
    ], reverse=True)


def _get_fyersn7_date_dir(date: str) -> Path:
    date_dir = FYERSN7_DATA_PATH / date
    if not date_dir.exists():
        raise HTTPException(status_code=404, detail=f"No data for date {date}")
    return date_dir


def _get_fyersn7_indices(date_dir: Path, index: Optional[str] = None) -> List[str]:
    return [index] if index else [d.name for d in date_dir.iterdir() if d.is_dir()]


def _load_fyersn7_signals(date_dir: Path, indices: List[str], latest_only: bool = False) -> Dict[str, object]:
    result: Dict[str, object] = {}

    for idx in indices:
        csv_path = date_dir / idx / "decision_journal.csv"

        if latest_only:
            result[idx] = _build_latest_signal_payload_from_csv(csv_path)
        else:
            rows = _read_csv_as_dicts(csv_path)
            result[idx] = [_transform_signal_row(row) for row in rows]

    return result


def _load_fyersn7_trades(date_dir: Path, indices: List[str]) -> Dict[str, List[Dict]]:
    result: Dict[str, List[Dict]] = {}

    for idx in indices:
        csv_path = date_dir / idx / "paper_trades.csv"
        rows = _dedupe_trade_rows(_read_csv_as_dicts(csv_path))

        trades = []
        for row in rows:
            trade_id = row.get("trade_id", "")
            trades.append({
                "id": trade_id,
                "trade_id": trade_id,
                "side": row.get("side", ""),
                "strike": _to_int(row.get("strike")),
                "qty": _to_int(row.get("qty")),
                "entry_time": row.get("entry_time", ""),
                "entry_price": _to_float(row.get("entry_price")),
                "sl": _to_float(row.get("sl")),
                "t1": _to_float(row.get("t1")),
                "t2": _to_float(row.get("t2")),
                "exit_time": row.get("exit_time", ""),
                "exit_price": _to_float(row.get("exit_price")),
                "exit_reason": row.get("exit_reason", ""),
                "net_pnl": _to_float(row.get("net_pnl")),
                "hold_sec": _to_int(row.get("hold_sec")),
                "result": row.get("result", ""),
            })
        result[idx] = trades

    return result


def _load_fyersn7_events(date_dir: Path, indices: List[str]) -> Dict[str, List[Dict]]:
    result: Dict[str, List[Dict]] = {}

    for idx in indices:
        csv_path = date_dir / idx / "opportunity_events.csv"
        rows = _dedupe_event_rows(_read_csv_as_dicts(csv_path))

        events = []
        for row in rows:
            events.append({
                "event_date": row.get("event_date", ""),
                "event_time": row.get("event_time", ""),
                "event_type": row.get("event_type", ""),
                "side": row.get("side", ""),
                "strike": _to_int(row.get("strike")),
                "entry": _to_float(row.get("entry")),
                "exit": _to_float(row.get("exit")),
                "score": _to_int(row.get("score")),
                "confidence": _to_int(row.get("confidence")),
                "vote_diff": _to_int(row.get("vote_diff")),
                "reason": row.get("reason", ""),
            })
        result[idx] = events

    return result


@app.get("/api/fyersn7/signals/{date}")
async def get_fyersn7_signals(date: str, index: Optional[str] = None, latest_only: bool = Query(False)):
    """Get decision journal signals for a date (all indices or specific)."""
    date_dir = _get_fyersn7_date_dir(date)
    indices = _get_fyersn7_indices(date_dir, index)
    return {"date": date, "indices": _load_fyersn7_signals(date_dir, indices, latest_only=latest_only)}


@app.get("/api/fyersn7/trades/{date}")
async def get_fyersn7_trades(date: str, index: Optional[str] = None):
    """Get paper trades for a date."""
    date_dir = _get_fyersn7_date_dir(date)
    indices = _get_fyersn7_indices(date_dir, index)
    return {"date": date, "indices": _load_fyersn7_trades(date_dir, indices)}


@app.get("/api/fyersn7/events/{date}")
async def get_fyersn7_events(date: str, index: Optional[str] = None):
    """Get opportunity events (entries/exits) for a date."""
    date_dir = _get_fyersn7_date_dir(date)
    indices = _get_fyersn7_indices(date_dir, index)
    return {"date": date, "indices": _load_fyersn7_events(date_dir, indices)}


@app.get("/api/fyersn7/snapshot/{date}")
async def get_fyersn7_snapshot(date: str, index: Optional[str] = None, latest_only: bool = Query(False)):
    """Get a combined SignalView payload in a single request."""
    date_dir = _get_fyersn7_date_dir(date)
    indices = _get_fyersn7_indices(date_dir, index)
    return {
        "date": date,
        "signals": _load_fyersn7_signals(date_dir, indices, latest_only=latest_only),
        "trades": _load_fyersn7_trades(date_dir, indices),
        "events": _load_fyersn7_events(date_dir, indices),
    }


@app.get("/api/fyersn7/live-signals/{index}")
async def get_fyersn7_live_signals(index: str):
    """Get the latest available date plus full signal rows for one index."""
    dates = _list_fyersn7_dates()
    if not dates:
        raise HTTPException(status_code=404, detail="No fyersN7 dates found")

    latest_date = dates[0]
    date_dir = _get_fyersn7_date_dir(latest_date)
    signals = _load_fyersn7_signals(date_dir, [index], latest_only=False)

    return {
        "date": latest_date,
        "index": index,
        "rows": signals.get(index, []),
    }


@app.get("/api/fyersn7/summary/{date}")
async def get_fyersn7_summary(date: str):
    """Get summary statistics for all indices on a date."""
    date_dir = FYERSN7_DATA_PATH / date
    if not date_dir.exists():
        raise HTTPException(status_code=404, detail=f"No data for date {date}")

    indices = [d.name for d in date_dir.iterdir() if d.is_dir()]
    summaries = {}

    for idx in indices:
        # Read paper trades for P&L
        trades_path = date_dir / idx / "paper_trades.csv"
        trades = _dedupe_trade_rows(_read_csv_as_dicts(trades_path))

        total_pnl = sum(_to_float(t.get("net_pnl")) for t in trades)
        wins = sum(1 for t in trades if t.get("result") == "Win")
        losses = sum(1 for t in trades if t.get("result") == "Loss")

        # Read signals for entry count
        signals_path = date_dir / idx / "decision_journal.csv"
        signals_meta = _build_latest_signal_payload_from_csv(signals_path)
        latest_signal = signals_meta.get("latest_signal", {})

        summaries[idx] = {
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(trades) * 100, 1) if trades else 0,
            "total_pnl": round(total_pnl, 2),
            "total_entries": signals_meta.get("selected_count", 0),
            "total_signals": signals_meta.get("total_signals", 0),
            "spot": _to_float(latest_signal.get("spot")),
            "vix": _to_float(latest_signal.get("vix")),
            "net_pcr": _to_float(latest_signal.get("net_pcr")),
        }

    return {"date": date, "summaries": summaries}


# =============================================================================
# INSTITUTIONAL TRADING ENDPOINTS
# =============================================================================

@app.get("/api/institutional/market-session")
async def get_market_session():
    """Get current market session and trading recommendation"""
    from trading.institutional import get_market_session, get_trading_day_type, get_expiry_day_rules
    from dataclasses import asdict

    time_filter = get_market_session()
    day_type = get_trading_day_type()
    day_rules = get_expiry_day_rules(day_type)

    return {
        "session": time_filter.session.value,
        "can_trade": time_filter.can_trade,
        "warning": time_filter.warning,
        "reason": time_filter.reason,
        "recommended_action": time_filter.recommended_action,
        "day_type": day_type.value,
        "day_rules": day_rules,
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/institutional/validate-trade")
async def validate_trade_endpoint(
    index: str,
    direction: str,
    entry: float,
    target: float,
    stop_loss: float,
    realized_pnl_today: float = 0
):
    """Validate a trade against all institutional rules"""
    from trading.institutional import validate_trade

    result = validate_trade(
        index=index,
        direction=direction,
        entry=entry,
        target=target,
        stop_loss=stop_loss,
        realized_pnl_today=realized_pnl_today
    )

    return result


@app.get("/api/institutional/position-size")
async def calculate_position_size_endpoint(
    index: str,
    entry: float,
    stop_loss: float
):
    """Calculate position size based on risk management rules"""
    from trading.institutional import calculate_position_size
    from dataclasses import asdict

    position = calculate_position_size(index, entry, stop_loss)
    return asdict(position)


@app.get("/api/institutional/risk-config")
async def get_risk_config():
    """Get current risk management configuration"""
    from trading.institutional import load_risk_config
    from dataclasses import asdict

    config = load_risk_config()
    return asdict(config)


# =============================================================================
# MULTI-BOT ENSEMBLE TRADING ENDPOINTS
# =============================================================================

# Global ensemble instance (lazy loaded)
_ensemble_instance = None

def get_ensemble():
    """Get or create ensemble coordinator instance"""
    global _ensemble_instance
    if _ensemble_instance is None:
        from bots import EnsembleCoordinator, SharedMemory
        shared_memory = SharedMemory()
        _ensemble_instance = EnsembleCoordinator(shared_memory)
    return _ensemble_instance


@app.get("/api/bots/status")
async def get_bots_status():
    """Get status of all trading bots in the ensemble"""
    ensemble = get_ensemble()
    return {
        "bots": ensemble.get_bot_status(),
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/bots/leaderboard")
async def get_bots_leaderboard():
    """Get bot leaderboard sorted by performance"""
    ensemble = get_ensemble()
    return {
        "leaderboard": ensemble.get_leaderboard(),
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/bots/ensemble-stats")
async def get_ensemble_stats():
    """Get ensemble-level statistics"""
    ensemble = get_ensemble()
    return {
        "stats": ensemble.get_ensemble_stats(),
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/bots/ict-sniper/status")
async def get_ict_sniper_status():
    """Get ICT Sniper bot detailed status"""
    ensemble = get_ensemble()
    ict_bot = ensemble.bot_map.get("ICTSniper")
    
    if not ict_bot:
        return {
            "error": "ICT Sniper bot not found",
            "timestamp": datetime.now().isoformat()
        }
    
    tf_state = ict_bot.get_multi_timeframe_state() if hasattr(ict_bot, "get_multi_timeframe_state") else {}
    warmup_state = ict_bot.get_warmup_status() if hasattr(ict_bot, "get_warmup_status") else {}

    def _any_flag(flag_name: str) -> bool:
        return any(bool((state or {}).get(flag_name)) for state in tf_state.values())

    recent_signals = list(reversed(getattr(ict_bot, "signal_history", [])[-5:]))

    return {
        "name": ict_bot.name,
        "description": ict_bot.description,
        "active_index": getattr(ict_bot, "_current_index", "") or None,
        "performance": {
            "total_signals": ict_bot.performance.total_signals,
            "total_trades": ict_bot.performance.total_trades,
            "wins": ict_bot.performance.wins,
            "losses": ict_bot.performance.losses,
            "win_rate": ict_bot.performance.win_rate,
            "total_pnl": ict_bot.performance.total_pnl,
            "weight": ict_bot.performance.weight,
        },
        "configuration": {
            "swing_lookback": ict_bot.config.swing_lookback,
            "mss_swing_len": ict_bot.config.mss_swing_len,
            "max_bars_after_sweep": ict_bot.config.max_bars_after_sweep,
            "vol_multiplier": ict_bot.config.vol_multiplier,
            "displacement_multiplier": ict_bot.config.displacement_multiplier,
            "rr_ratio": ict_bot.config.rr_ratio,
            "atr_sl_buffer": ict_bot.config.atr_sl_buffer,
            "max_fvg_size": ict_bot.config.max_fvg_size,
            "entry_type": ict_bot.config.entry_type,
            "require_displacement": ict_bot.config.require_displacement,
            "require_volume_spike": ict_bot.config.require_volume_spike,
        },
        "setup_state": {
            "bullish_setup_active": _any_flag("bullish_setup_active"),
            "bearish_setup_active": _any_flag("bearish_setup_active"),
            "bullish_mss_confirmed": _any_flag("bullish_mss_confirmed"),
            "bearish_mss_confirmed": _any_flag("bearish_mss_confirmed"),
            "bullish_fvg_active": _any_flag("bullish_fvg_active"),
            "bearish_fvg_active": _any_flag("bearish_fvg_active"),
            "bullish_ifvg_active": _any_flag("bullish_ifvg_active"),
            "bearish_ifvg_active": _any_flag("bearish_ifvg_active"),
            "bullish_order_block_active": _any_flag("bullish_order_block_active"),
            "bearish_order_block_active": _any_flag("bearish_order_block_active"),
        },
        "multi_timeframe_state": tf_state,
        "warmup_state": warmup_state,
        "recent_signals": recent_signals,
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/bots/ict-sniper/analyze")
async def analyze_ict_sniper(body: dict):
    """Analyze one market candle directly through the ICT Sniper bot."""
    ensemble = get_ensemble()
    ict_bot = ensemble.bot_map.get("ICTSniper")
    if not ict_bot:
        raise HTTPException(status_code=404, detail="ICT Sniper bot not found")

    index = body.get("index", "SENSEX")
    market_data = body.get("market_data", {})
    await asyncio.to_thread(_warm_ict_sniper_from_history, ict_bot, index, market_data)
    signal = ict_bot.analyze(index, market_data)

    if signal:
        action = "BUY_CE" if signal.option_type.value == "CE" else "BUY_PE" if signal.option_type.value == "PE" else "NO_TRADE"
        return {
            "has_decision": True,
            "decision": {
                "action": action,
                "index": signal.index,
                "strike": signal.strike,
                "entry": signal.entry,
                "target": signal.target,
                "stop_loss": signal.stop_loss,
                "confidence": signal.confidence,
                "consensus_level": 1.0,
                "contributing_bots": [signal.bot_name],
                "reasoning": signal.reasoning,
                "analysis": dict(signal.factors),
                "timestamp": signal.timestamp,
            },
            "timestamp": datetime.now().isoformat(),
        }

    return {
        "has_decision": False,
        "message": "No ICT Sniper opportunity found",
        "timestamp": datetime.now().isoformat(),
    }


def _parse_history_candles(payload: Dict[str, Any]) -> List[List[float]]:
    data = payload.get("data", {})
    if isinstance(data, dict) and isinstance(data.get("candles"), list):
        return data["candles"]
    if isinstance(payload.get("candles"), list):
        return payload["candles"]
    return []


def _extract_session_key(timestamp_value: Any) -> str:
    if isinstance(timestamp_value, str) and len(timestamp_value) >= 10:
        try:
            return datetime.fromisoformat(timestamp_value).date().isoformat()
        except ValueError:
            return timestamp_value[:10]
    return datetime.now().date().isoformat()


def _build_ict_warmup_candles(candles: List[List[float]], current_market_data: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    current_bar_index = int(float(current_market_data.get("bar_index") or 0) or 0)
    prepared: List[Dict[str, Any]] = []

    for candle in candles or []:
        if not isinstance(candle, list) or len(candle) < 6:
            continue
        epoch_value = float(candle[0])
        if epoch_value > 1_000_000_000_000:
            epoch_value /= 1000.0
        bar_index = int(epoch_value) // 60
        if current_bar_index and bar_index >= current_bar_index:
            continue

        prepared.append({
            "open": float(candle[1] or 0.0),
            "high": float(candle[2] or 0.0),
            "low": float(candle[3] or 0.0),
            "close": float(candle[4] or 0.0),
            "volume": float(candle[5] or 0.0),
            "bar_index": bar_index,
            "timestamp": datetime.fromtimestamp(int(epoch_value)).isoformat(),
        })

    if limit > 0:
        return prepared[-limit:]
    return prepared


def _warm_ict_sniper_from_history(ict_bot: Any, index: str, market_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not hasattr(ict_bot, "warmup") or not hasattr(ict_bot, "warmup_session_matches"):
        return None

    session_key = _extract_session_key(market_data.get("timestamp"))
    if ict_bot.warmup_session_matches(index, session_key):
        return None

    try:
        from shared_project_engine.indices import canonicalize_index_name, get_market_index_config

        canonical_index = canonicalize_index_name(index)
        config = get_market_index_config(canonical_index)
        symbol = str(config.get("spot_symbol") or config.get("symbol") or "").strip()
        if not symbol:
            return None

        client = _build_market_client()
        history = client.get_history_snapshot(
            symbol=symbol,
            resolution="1",
            lookback_days=1,
        )
        candles = _parse_history_candles(history)
        warmup_bars = max(20, int(os.getenv("ICT_WARMUP_BARS", "300")))
        warmup_candles = _build_ict_warmup_candles(candles, market_data, warmup_bars)
        if not warmup_candles:
            return None

        status = ict_bot.warmup(canonical_index, warmup_candles, session_key=session_key)
        logger.info("[ICT] Warmed %s with %s bars for session %s", canonical_index, status.get("bars_loaded", 0), session_key)
        return status
    except Exception as exc:
        logger.warning("[ICT] Warmup skipped for %s: %s", index, exc)
        return None


@app.post("/api/bots/ict-sniper/record-trade")
async def record_ict_sniper_trade(body: dict):
    """Record a direct ICT Sniper trade outcome for learning/performance."""
    ensemble = get_ensemble()
    ict_bot = ensemble.bot_map.get("ICTSniper")
    if not ict_bot:
        raise HTTPException(status_code=404, detail="ICT Sniper bot not found")

    from bots.base import TradeRecord

    outcome = str(body.get("outcome", "BREAKEVEN")).upper()
    entry_price = float(body.get("entry_price", 0.0) or 0.0)
    exit_price = float(body.get("exit_price", 0.0) or 0.0)
    pnl = float(body.get("pnl", 0.0) or 0.0)
    pnl_pct = float(body.get("pnl_pct", 0.0) or 0.0)
    if pnl_pct == 0.0 and entry_price:
        direction = str(body.get("action", "BUY_CE")).upper()
        signed_move = (exit_price - entry_price) if "CE" in direction else (entry_price - exit_price)
        pnl_pct = signed_move / entry_price * 100.0

    trade_record = TradeRecord(
        trade_id=str(body.get("trade_id", f"ICTSniper_{datetime.now().timestamp()}")),
        bot_name="ICTSniper",
        index=str(body.get("index", "SENSEX")),
        option_type=str(body.get("option_type", "CE")),
        strike=int(float(body.get("strike", 0) or 0)),
        entry_price=entry_price,
        exit_price=exit_price,
        entry_time=str(body.get("entry_time", datetime.now().isoformat())),
        exit_time=str(body.get("exit_time", datetime.now().isoformat())),
        pnl=pnl,
        pnl_pct=pnl_pct,
        outcome=outcome,
        market_conditions=dict(body.get("market_data", {}) or {}),
        bot_reasoning=str(body.get("reasoning", "")),
    )
    ict_bot.learn(trade_record)

    return {
        "status": "recorded",
        "message": "ICT Sniper trade outcome recorded",
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/bots/regime-hunter/status")
async def get_regime_hunter_status():
    """Get Regime Hunter bot detailed status"""
    ensemble = get_ensemble()
    rh_bot = ensemble.bot_map.get("RegimeHunter")

    if not rh_bot:
        return {
            "error": "Regime Hunter bot not found",
            "timestamp": datetime.now().isoformat()
        }

    return {
        "name": rh_bot.name,
        "description": rh_bot.description,
        "performance": {
            "total_signals": rh_bot.performance.total_signals,
            "total_trades": rh_bot.performance.total_trades,
            "wins": rh_bot.performance.wins,
            "losses": rh_bot.performance.losses,
            "win_rate": rh_bot.performance.win_rate,
            "total_pnl": rh_bot.performance.total_pnl,
            "weight": rh_bot.performance.weight,
        },
        "regime_state": {
            "current_regime": rh_bot._current_regime,
            "entries_this_regime": rh_bot._entries_this_regime,
        },
        "parameters": {
            k: v for k, v in rh_bot.parameters.items()
            if not k.startswith("_")
        },
        "recent_signals": [
            {
                "index": s.index,
                "type": s.signal_type.value if hasattr(s.signal_type, "value") else str(s.signal_type),
                "option": s.option_type.value if hasattr(s.option_type, "value") else str(s.option_type),
                "confidence": s.confidence,
                "regime": s.factors.get("regime", ""),
            }
            for s in (rh_bot.recent_signals or [])[-5:]
        ],
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/bots/analyze")
async def analyze_market(body: dict):
    """
    Analyze market data and get ensemble decision

    Body: {
        "index": "NIFTY50",
        "market_data": {
            "ltp": 24500,
            "change_pct": 0.5,
            "high": 24600,
            "low": 24400,
            "ce_oi": 1000000,
            "pe_oi": 800000,
            "pcr": 0.8,
            "iv": 15,
            ...
        }
    }
    """
    ensemble = get_ensemble()
    index = body.get("index", "NIFTY50")
    market_data = body.get("market_data", {})

    decision = ensemble.analyze(index, market_data)

    if decision:
        return {
            "has_decision": True,
            "decision": {
                "action": decision.action,
                "index": decision.index,
                "strike": decision.strike,
                "entry": decision.entry,
                "target": decision.target,
                "stop_loss": decision.stop_loss,
                "confidence": decision.confidence,
                "consensus_level": decision.consensus_level,
                "contributing_bots": decision.contributing_bots,
                "reasoning": decision.reasoning,
            },
            "timestamp": datetime.now().isoformat()
        }

    return {
        "has_decision": False,
        "message": "No trading opportunity found",
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/bots/analyze-all")
async def analyze_all_indices(body: dict):
    """
    Analyze multiple indices and get all decisions

    Body: {
        "indices_data": {
            "NIFTY50": {"ltp": 24500, "change_pct": 0.5, ...},
            "BANKNIFTY": {"ltp": 52000, "change_pct": -0.3, ...},
            ...
        }
    }
    """
    ensemble = get_ensemble()
    indices_data = body.get("indices_data", {})

    decisions = ensemble.analyze_all_indices(indices_data)

    return {
        "decisions": [
            {
                "action": d.action,
                "index": d.index,
                "strike": d.strike,
                "entry": d.entry,
                "target": d.target,
                "stop_loss": d.stop_loss,
                "confidence": d.confidence,
                "consensus_level": d.consensus_level,
                "contributing_bots": d.contributing_bots,
                "reasoning": d.reasoning,
            }
            for d in decisions
        ],
        "count": len(decisions),
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/bots/record-trade")
async def record_trade_outcome(body: dict):
    """
    Record a trade outcome for learning

    Body: {
        "index": "NIFTY50",
        "exit_price": 250.0,
        "outcome": "WIN",  // WIN, LOSS, BREAKEVEN
        "pnl": 500.0
    }
    """
    ensemble = get_ensemble()

    ensemble.close_trade(
        index=body.get("index"),
        exit_price=body.get("exit_price", 0),
        outcome=body.get("outcome", "BREAKEVEN"),
        pnl=body.get("pnl", 0)
    )

    return {
        "status": "recorded",
        "message": f"Trade outcome recorded and routed to bots for learning",
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/bots/reset-daily")
async def reset_daily_counters():
    """Reset daily trading counters (call at market open)"""
    ensemble = get_ensemble()
    ensemble.reset_daily()
    return {
        "status": "reset",
        "message": "Daily counters reset",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/bots/{bot_name}/details")
async def get_bot_details(bot_name: str):
    """Get detailed information about a specific bot"""
    ensemble = get_ensemble()

    bot = ensemble.bot_map.get(bot_name)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_name}' not found")

    return {
        "bot": bot.to_dict(),
        "learnings": bot.memory.get_knowledge(topic=bot_name, limit=20),
        "timestamp": datetime.now().isoformat()
    }


# =============================================================================
# ML TRAINING DATA ENDPOINTS
# =============================================================================

@app.get("/api/ml/statistics")
async def get_ml_statistics():
    """Get ML training data statistics - shows readiness for model training"""
    ensemble = get_ensemble()
    stats = ensemble.get_ml_statistics()
    return {
        "statistics": stats,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/ml/learning-insights")
async def get_ml_learning_insights():
    """Get learning insights from pattern discovery"""
    ensemble = get_ensemble()
    insights = ensemble.get_learning_insights()
    return {
        "insights": insights,
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/ml/export-csv")
async def export_ml_training_csv():
    """Export ML training data to CSV file"""
    ensemble = get_ensemble()
    csv_path = ensemble.memory.data_dir / "ml_training_data.csv"
    ensemble.export_ml_training_data(str(csv_path))
    return {
        "status": "exported",
        "file_path": str(csv_path),
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/ml/training-data")
async def get_ml_training_data():
    """Get ML training data as feature vectors and labels"""
    ensemble = get_ensemble()
    X, y = ensemble.get_ml_training_data()
    return {
        "feature_count": len(X[0]) if X else 0,
        "sample_count": len(X),
        "features": X[:100],  # Return first 100 samples
        "labels": y[:100],
        "label_distribution": {
            "loss": y.count(0) if y else 0,
            "breakeven": y.count(1) if y else 0,
            "win": y.count(2) if y else 0,
        },
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/ml/bot-status")
async def get_ml_bot_status():
    """Get ML bot status - check if trained and ready"""
    ensemble = get_ensemble()
    return {
        "ml_bot": ensemble.ml_bot.get_status(),
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/ml/train")
async def train_ml_model(model_type: str = "random_forest"):
    """
    Train the ML model on collected data.

    Requires at least 100 samples with outcomes.
    After training, the 6th bot will auto-activate.

    Args:
        model_type: "random_forest", "xgboost", or "gradient_boost"
    """
    from livebench.bots.ml_bot import train_model

    ensemble = get_ensemble()
    stats = ensemble.get_ml_statistics()

    if stats.get("total_samples", 0) < 100:
        return {
            "status": "error",
            "message": f"Not enough training samples. Have {stats.get('total_samples', 0)}, need at least 100.",
            "samples_needed": 100 - stats.get("total_samples", 0),
        }

    try:
        data_dir = str(ensemble.memory.data_dir)
        success = train_model(data_dir, model_type)

        if success:
            # Reload the ML bot to pick up the new model
            ensemble.ml_bot._load_model()

            return {
                "status": "success",
                "message": "ML model trained successfully! The 6th bot is now active.",
                "model_type": model_type,
                "ml_bot_status": ensemble.ml_bot.get_status(),
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {
                "status": "error",
                "message": "Training failed. Check server logs for details.",
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Training error: {str(e)}",
        }


@app.get("/api/agents/{signature}/institutional-shadow/latest")
async def get_latest_institutional_shadow(signature: str):
    """Get latest institutional shadow summary from agent trading screener audit log."""
    agent_dir = DATA_PATH / signature
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    screener_log = agent_dir / "trading" / "fyers_screener.jsonl"
    if not screener_log.exists():
        return {
            "available": False,
            "message": "No agent screener audit log found",
            "signature": signature,
        }

    latest_payload = None
    with open(screener_log, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                latest_payload = json.loads(line)
            except json.JSONDecodeError:
                continue

    if not isinstance(latest_payload, dict):
        return {
            "available": False,
            "message": "No valid screener audit entries found",
            "signature": signature,
        }

    shadow = latest_payload.get("institutional_shadow", {})
    return {
        "available": True,
        "signature": signature,
        "timestamp": latest_payload.get("timestamp"),
        "date": latest_payload.get("date"),
        "success": latest_payload.get("success"),
        "institutional_shadow": shadow if isinstance(shadow, dict) else {},
    }


@app.get("/api/agents/{signature}/dashboard-supplemental")
async def get_agent_dashboard_supplemental(signature: str):
    """Get the supplemental dashboard payload in a single request."""
    screener = await get_latest_fyers_screener()
    shadow = await get_latest_institutional_shadow(signature)
    market_session = await get_market_session()

    return {
        "signature": signature,
        "fyers_screener": screener,
        "institutional_shadow": shadow,
        "market_session": market_session,
        "updated_at": datetime.now().isoformat(),
    }


# =============================================================================
# REGIME HUNTER INDEPENDENT PIPELINE
# =============================================================================

_rh_pipeline_instance = None


def get_rh_pipeline():
    """Get or create the RegimeHunter independent pipeline instance."""
    global _rh_pipeline_instance
    if _rh_pipeline_instance is None:
        import os
        from trading.regime_hunter_pipeline import RegimeHunterPipeline

        mode = "paper"
        if os.getenv("FYERS_DRY_RUN", "true").lower() == "false" and \
           os.getenv("FYERS_ALLOW_LIVE_ORDERS", "false").lower() == "true":
            mode = "live"

        _rh_pipeline_instance = RegimeHunterPipeline(mode=mode)
    return _rh_pipeline_instance


@app.get("/api/regime-hunter-pipeline/status")
async def rh_pipeline_status():
    """Get RegimeHunter pipeline status."""
    pipeline = get_rh_pipeline()
    return {
        **pipeline.get_status(),
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/regime-hunter-pipeline/start")
async def rh_pipeline_start():
    """Start the independent RegimeHunter pipeline."""
    pipeline = get_rh_pipeline()
    pipeline.start()
    return {"status": "started", "mode": pipeline.mode, "timestamp": datetime.now().isoformat()}


@app.post("/api/regime-hunter-pipeline/stop")
async def rh_pipeline_stop():
    """Stop the RegimeHunter pipeline."""
    pipeline = get_rh_pipeline()
    pipeline.stop()
    return {"status": "stopped", "timestamp": datetime.now().isoformat()}


@app.post("/api/regime-hunter-pipeline/pause")
async def rh_pipeline_pause():
    pipeline = get_rh_pipeline()
    pipeline.pause()
    return {"status": "paused", "timestamp": datetime.now().isoformat()}


@app.post("/api/regime-hunter-pipeline/resume")
async def rh_pipeline_resume():
    pipeline = get_rh_pipeline()
    pipeline.resume()
    return {"status": "resumed", "timestamp": datetime.now().isoformat()}


@app.post("/api/regime-hunter-pipeline/reset-daily")
async def rh_pipeline_reset():
    pipeline = get_rh_pipeline()
    pipeline.reset_daily()
    return {"status": "reset", "timestamp": datetime.now().isoformat()}


@app.post("/api/regime-hunter-pipeline/process")
async def rh_pipeline_process(body: dict):
    """
    Feed market data directly into the RegimeHunter pipeline.

    Body: {"index": "SENSEX", "market_data": {"ltp": 79500, ...}}
    """
    pipeline = get_rh_pipeline()
    index = body.get("index", "")
    market_data = body.get("market_data", {})
    if not index or not market_data:
        return {"action": "ERROR", "reason": "Missing index or market_data"}

    result = pipeline.process(index, market_data)
    result["timestamp"] = datetime.now().isoformat()
    return result


# =============================================================================
# HYBRID REGIME HUNTER PIPELINE (Modular Architecture)
# =============================================================================
# New hybrid module system with separate Volatility, Sentiment, Trend modules

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


@app.get("/api/indices/expiry-schedule")
async def get_index_expiry_schedule():
    """
    Get expiry schedule for all indices from shared_project_engine.

    Returns: {indices: {...}, expirySchedule: {...}, todaysExpiry: [...]}
    """
    try:
        from shared_project_engine.indices import (
            get_expiry_schedule,
            get_todays_expiring_indices,
            INDEX_CONFIG,
            ACTIVE_INDICES,
        )

        schedule = get_expiry_schedule()
        todays_expiry = get_todays_expiring_indices()

        return {
            "expirySchedule": schedule,
            "todaysExpiry": todays_expiry,
            "activeIndices": ACTIVE_INDICES,
            "indices": list(INDEX_CONFIG.keys()),
            "timestamp": datetime.now().isoformat(),
        }
    except ImportError as e:
        logger.warning(f"Could not import shared_project_engine: {e}")
        # Fallback to hardcoded values
        from datetime import date
        weekday = date.today().weekday()
        fallback_schedule = {
            "NIFTY50": {"weekday": 3, "weekday_name": "Thursday", "weekday_short": "Thu", "is_expiry_today": weekday == 3},
            "BANKNIFTY": {"weekday": 2, "weekday_name": "Wednesday", "weekday_short": "Wed", "is_expiry_today": weekday == 2},
            "FINNIFTY": {"weekday": 1, "weekday_name": "Tuesday", "weekday_short": "Tue", "is_expiry_today": weekday == 1},
            "MIDCPNIFTY": {"weekday": 0, "weekday_name": "Monday", "weekday_short": "Mon", "is_expiry_today": weekday == 0},
            "SENSEX": {"weekday": 4, "weekday_name": "Friday", "weekday_short": "Fri", "is_expiry_today": weekday == 4},
        }
        return {
            "expirySchedule": fallback_schedule,
            "todaysExpiry": [k for k, v in fallback_schedule.items() if v["is_expiry_today"]],
            "activeIndices": ["SENSEX", "NIFTY50", "BANKNIFTY", "FINNIFTY"],
            "indices": list(fallback_schedule.keys()),
            "timestamp": datetime.now().isoformat(),
        }


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

    # Map index folder names to INDEX_CONFIG names
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
            # Read last line of CSV for latest data
            with open(journal_path, "r") as f:
                lines = f.readlines()
                if len(lines) < 2:
                    continue
                header = lines[0].strip().split(",")
                last_line = lines[-1].strip().split(",")

                # Find column indices
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


def _market_env_file() -> Optional[str]:
    server_dir = Path(__file__).parent
    project_root = server_dir.parent.parent.parent  # livebench/api -> ClawWork_FyersN7
    env_file = project_root / ".env"
    return str(env_file) if env_file.exists() else None


def _build_market_client():
    from trading.fyers_client import MarketDataClient

    return MarketDataClient(
        env_file=_market_env_file(),
        fallback_to_local=bool(os.getenv("FYERS_ACCESS_TOKEN")),
    )


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
        "market_open": len(indices_data) > 0 and any(_to_float(d.get("ltp")) > 0 for d in indices_data.values()),
        "timestamp": datetime.now().isoformat(),
        "source": "fyers_api+fyersN7_fallback" if fallback_filled else "fyers_api",
        "fallback_filled": fallback_filled,
    }


def _next_market_stream_event(stream) -> Optional[Dict[str, Any]]:
    try:
        return next(stream)
    except StopIteration:
        return None


@app.get("/api/market/live")
async def get_live_market_data():
    """
    Fetch live market data for all indices through the shared market adapter.
    Falls back to fyersN7 decision journals if upstream data is unavailable.

    Returns: {indices: {NIFTY50: {ltp, open, high, low, change_pct, ...}, ...}}
    """
    try:
        client = _build_market_client()
        symbols, index_symbol_map, expected_indices = _load_market_index_symbols()

        if not symbols:
            return {"error": "No index symbols configured", "indices": {}}

        result = client.quotes(",".join(symbols))
        return _build_live_market_response(result, index_symbol_map, expected_indices)

    except ImportError as e:
        logger.warning(f"Could not import shared_project_engine: {e}")
        return {
            "error": f"Missing dependency: {e}",
            "indices": {},
            "market_open": False,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Error fetching live market data: {e}")
        return {
            "error": str(e),
            "indices": {},
            "market_open": False,
            "timestamp": datetime.now().isoformat(),
        }


@app.websocket("/ws/market")
async def market_websocket_endpoint(websocket: WebSocket):
    """Push live index snapshots to dashboard consumers through a shared websocket stream."""
    try:
        symbols, _, _ = _load_market_index_symbols()
        if not symbols:
            await websocket.accept()
            await websocket.send_json({
                "type": "market_live_error",
                "error": "No index symbols configured",
                "timestamp": datetime.now().isoformat(),
            })
            return

        interval_seconds, ttl_seconds, _ = _market_stream_settings()
        latest_update = await market_manager.connect(websocket)

        await websocket.send_json({
            "type": "market_live_connected",
            "symbols": symbols,
            "interval_seconds": interval_seconds,
            "ttl_seconds": ttl_seconds,
            "timestamp": datetime.now().isoformat(),
        })

        if latest_update:
            await websocket.send_json(latest_update)

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Market websocket error: {e}")
        try:
            await websocket.send_json({
                "type": "market_live_error",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            })
        except Exception:
            pass
    finally:
        with suppress(Exception):
            await market_manager.disconnect(websocket)


@app.get("/api/hybrid-pipeline/status")
async def hybrid_pipeline_status():
    """Get hybrid pipeline status with all module info."""
    pipeline = get_hybrid_pipeline()
    if not pipeline:
        return {"error": "Hybrid pipeline not available", "available": False}

    return {
        "available": True,
        "modules": pipeline.get_module_status(),
        "config": {
            "volatility_weight": pipeline.config.volatility_weight,
            "sentiment_weight": pipeline.config.sentiment_weight,
            "trend_weight": pipeline.config.trend_weight,
            "min_confidence": pipeline.config.min_confidence,
            "consensus_required": pipeline.config.consensus_required,
        },
        "stats": pipeline.stats,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/hybrid-pipeline/analyze")
async def hybrid_pipeline_analyze(body: dict):
    """
    Analyze market data using hybrid pipeline.

    Body: {"index": "NIFTY50", "market_data": {"ltp": 22500, "is_expiry": true, ...}}

    If is_expiry flag is true, automatically applies expiry mode settings.
    """
    pipeline = get_hybrid_pipeline()
    if not pipeline:
        return {"error": "Hybrid pipeline not available"}

    index = body.get("index", "")
    market_data = body.get("market_data", {})
    historical_data = body.get("historical_data")

    if not index or not market_data:
        return {"error": "Missing index or market_data"}

    # Check for expiry flag and auto-configure
    is_expiry = market_data.pop("is_expiry", False)
    if is_expiry:
        pipeline.configure_for_expiry()
    else:
        pipeline.configure_for_normal()

    decision = pipeline.analyze(index, market_data, historical_data)

    return {
        "regime": decision.regime.value,
        "action": decision.action,
        "confidence": decision.confidence,
        "position_bias": decision.position_bias,
        "risk_multiplier": decision.risk_multiplier,
        "entry_side": decision.entry_side,
        "stop_distance_pct": decision.stop_distance_pct,
        "target_distance_pct": decision.target_distance_pct,
        "consensus": {
            "agreeing": decision.modules_agreeing,
            "total": decision.total_modules,
            "level": decision.consensus_level,
        },
        "modules": {
            "volatility": {
                "level": decision.volatility.level.value,
                "vix": decision.volatility.vix,
                "range_pct": decision.volatility.range_pct,
                "risk_multiplier": decision.volatility.risk_multiplier,
                "confidence": decision.volatility.confidence,
                "warning": decision.volatility.warning,
            },
            "sentiment": {
                "bias": decision.sentiment.bias.value,
                "pcr": decision.sentiment.pcr,
                "oi_pattern": decision.sentiment.oi_pattern.value,
                "institutional_signal": decision.sentiment.institutional_signal,
                "position_bias": decision.sentiment.position_bias,
                "confidence": decision.sentiment.confidence,
            },
            "trend": {
                "direction": decision.trend.direction.value,
                "strength": decision.trend.strength,
                "phase": decision.trend.phase.value,
                "momentum": decision.trend.momentum,
                "support": decision.trend.support,
                "resistance": decision.trend.resistance,
                "quality": decision.trend.trend_quality,
                "confidence": decision.trend.confidence,
            },
        },
        "reasoning": decision.reasoning,
        "warnings": decision.warnings,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/hybrid-pipeline/module/toggle")
async def hybrid_pipeline_toggle_module(body: dict):
    """Enable or disable a module. Body: {"module": "sentiment", "enabled": false}"""
    pipeline = get_hybrid_pipeline()
    if not pipeline:
        return {"error": "Hybrid pipeline not available"}

    module = body.get("module", "")
    enabled = body.get("enabled", True)

    if module not in ["volatility", "sentiment", "trend"]:
        return {"error": f"Unknown module: {module}"}

    if enabled:
        pipeline.enable_module(module)
    else:
        pipeline.disable_module(module)

    return {
        "module": module,
        "enabled": enabled,
        "status": pipeline.get_module_status(),
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/hybrid-pipeline/module/weight")
async def hybrid_pipeline_set_weight(body: dict):
    """Set module weight. Body: {"module": "volatility", "weight": 1.5}"""
    pipeline = get_hybrid_pipeline()
    if not pipeline:
        return {"error": "Hybrid pipeline not available"}

    module = body.get("module", "")
    weight = body.get("weight", 1.0)

    if module not in ["volatility", "sentiment", "trend"]:
        return {"error": f"Unknown module: {module}"}

    pipeline.set_module_weight(module, weight)

    return {
        "module": module,
        "weight": weight,
        "status": pipeline.get_module_status(),
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/hybrid-pipeline/configure-expiry")
async def hybrid_pipeline_expiry_mode():
    """Configure pipeline for expiry day trading."""
    pipeline = get_hybrid_pipeline()
    if not pipeline:
        return {"error": "Hybrid pipeline not available"}

    pipeline.configure_for_expiry()
    return {
        "mode": "expiry",
        "config": {
            "volatility_weight": pipeline.config.volatility_weight,
            "sentiment_weight": pipeline.config.sentiment_weight,
            "trend_weight": pipeline.config.trend_weight,
        },
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/hybrid-pipeline/configure-normal")
async def hybrid_pipeline_normal_mode():
    """Configure pipeline for normal trading."""
    pipeline = get_hybrid_pipeline()
    if not pipeline:
        return {"error": "Hybrid pipeline not available"}

    pipeline.configure_for_normal()
    return {
        "mode": "normal",
        "config": {
            "volatility_weight": pipeline.config.volatility_weight,
            "sentiment_weight": pipeline.config.sentiment_weight,
            "trend_weight": pipeline.config.trend_weight,
        },
        "timestamp": datetime.now().isoformat(),
    }


# =============================================================================
# AUTO-TRADER ENDPOINTS (Autonomous Trading System)
# =============================================================================

# Global auto-trader instance (lazy loaded)
_auto_trader_instance = None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def get_auto_trader():
    """Get or create auto-trader instance

    Trading mode is determined by .env variables:
    - FYERS_DRY_RUN=true → Paper mode (default)
    - FYERS_ALLOW_LIVE_ORDERS=false → Paper mode (safety override)
    - Both false/true respectively → Live mode (real money)
    """
    global _auto_trader_instance
    if _auto_trader_instance is None:
        from trading.auto_trader import (
            AutoTrader,
            resolve_auto_trader_data_dir,
            resolve_auto_trader_strategy_id,
            get_trading_mode_from_env,
        )
        ensemble = get_ensemble()

        # Get trading mode from environment variables
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


def autostart_auto_trader_on_startup() -> Dict[str, Any]:
    """Auto-start the paper auto-trader when the API comes online."""
    from trading.auto_trader import TradingMode, get_trading_mode_from_env

    enabled = _env_flag("AUTO_TRADER_AUTOSTART", True)
    mode = get_trading_mode_from_env()

    if not enabled:
        message = "Auto-trader autostart disabled by AUTO_TRADER_AUTOSTART"
        logger.info(message)
        return {
            "attempted": False,
            "started": False,
            "mode": mode.value,
            "message": message,
        }

    if mode != TradingMode.PAPER:
        message = f"Auto-trader autostart skipped for {mode.value} mode"
        logger.info(message)
        return {
            "attempted": False,
            "started": False,
            "mode": mode.value,
            "message": message,
        }

    trader = get_auto_trader()
    trader.mode = mode
    try:
        trader.start()
    except RuntimeError as exc:
        message = f"Auto-trader autostart blocked: {exc}"
        logger.warning(message)
        return {
            "attempted": True,
            "started": False,
            "mode": mode.value,
            "message": message,
        }

    message = f"Auto-trader auto-started in paper mode for strategy '{trader.strategy_id}'"
    logger.info(message)
    return {
        "attempted": True,
        "started": True,
        "mode": mode.value,
        "strategy_id": trader.strategy_id,
        "message": message,
    }


@app.get("/api/auto-trader/status")
async def get_auto_trader_status():
    """Get auto-trader status"""
    trader = get_auto_trader()
    return {
        "status": trader.get_status(),
        "timestamp": datetime.now().isoformat()
    }


@app.api_route("/api/auto-trader/start", methods=["GET", "POST"])
async def start_auto_trader():
    """
    Start auto-trading with mode determined by .env configuration.

    Environment Variables (from .env):
    - FYERS_DRY_RUN=true → Paper mode (simulated)
    - FYERS_ALLOW_LIVE_ORDERS=false → Paper mode (safety override)
    - FYERS_DRY_RUN=false AND FYERS_ALLOW_LIVE_ORDERS=true → LIVE mode

    The trading mode is automatically determined on startup based on .env.
    To switch between paper and live, modify .env and restart the server.
    """
    import os
    from trading.auto_trader import TradingMode, get_trading_mode_from_env

    trader = get_auto_trader()

    # Get current mode from environment
    env_mode = get_trading_mode_from_env()

    # Check if trying to go live but env prevents it
    dry_run = os.getenv("FYERS_DRY_RUN", "true").lower()
    allow_live = os.getenv("FYERS_ALLOW_LIVE_ORDERS", "false").lower()

    trader.mode = env_mode
    try:
        trader.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    mode_display = "LIVE" if env_mode == TradingMode.LIVE else "PAPER"
    message = (
        f"Auto-trader started in {mode_display} mode. "
        f"(DRY_RUN={dry_run}, ALLOW_LIVE_ORDERS={allow_live})"
    )

    if env_mode == TradingMode.LIVE:
        message += " ⚠️ REAL MONEY AT RISK!"

    return {
        "status": "started",
        "strategy_id": trader.strategy_id,
        "mode": mode_display.lower(),
        "dry_run": dry_run,
        "allow_live_orders": allow_live,
        "message": message,
        "timestamp": datetime.now().isoformat()
    }


@app.api_route("/api/auto-trader/stop", methods=["GET", "POST"])
async def stop_auto_trader():
    """Stop auto-trading"""
    trader = get_auto_trader()
    trader.stop()
    return {
        "status": "stopped",
        "timestamp": datetime.now().isoformat()
    }


@app.api_route("/api/auto-trader/pause", methods=["GET", "POST"])
async def pause_auto_trader():
    """Pause auto-trading"""
    trader = get_auto_trader()
    trader.pause()
    return {
        "status": "paused",
        "timestamp": datetime.now().isoformat()
    }


@app.api_route("/api/auto-trader/resume", methods=["GET", "POST"])
async def resume_auto_trader():
    """Resume auto-trading"""
    trader = get_auto_trader()
    trader.resume()
    return {
        "status": "resumed",
        "timestamp": datetime.now().isoformat()
    }


@app.api_route("/api/auto-trader/emergency-stop", methods=["GET", "POST"])
async def emergency_stop_auto_trader():
    """Emergency stop - closes all positions"""
    trader = get_auto_trader()
    trader.emergency_stop()
    return {
        "status": "emergency_stopped",
        "message": "All positions closed. Trading disabled.",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/auto-trader/performance")
async def get_auto_trader_performance():
    """Get auto-trader performance summary"""
    trader = get_auto_trader()
    return {
        "performance": trader.get_performance_summary(),
        "status": trader.get_status(),
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/auto-trader/positions")
async def get_auto_trader_positions():
    """Get current open positions"""
    trader = get_auto_trader()
    status = trader.get_status()
    return {
        "strategy_id": status.get("strategy_id"),
        "positions": status.get("positions", []),
        "open_count": status.get("open_positions", 0),
        "daily_pnl": status.get("daily_pnl", 0),
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/auto-trader/trades")
async def get_auto_trader_trades(
    limit: int = Query(default=100, ge=1, le=1000),
    mode: Optional[str] = Query(default=None),
):
    """Get closed auto-trader trades, newest first."""
    normalized_mode = str(mode or "").strip().lower() or None
    if normalized_mode not in {None, "paper", "live"}:
        raise HTTPException(status_code=400, detail="mode must be 'paper' or 'live'")

    trader = get_auto_trader()
    trades = trader.get_recent_trades(limit=limit, mode=normalized_mode)
    return {
        "strategy_id": trader.strategy_id,
        "trades": trades,
        "count": len(trades),
        "timestamp": datetime.now().isoformat(),
    }


@app.api_route("/api/auto-trader/reset-daily", methods=["GET", "POST"])
async def reset_auto_trader_daily():
    """Reset daily counters (call at market open)"""
    trader = get_auto_trader()
    trader.reset_daily()
    return {
        "status": "reset",
        "timestamp": datetime.now().isoformat()
    }


@app.api_route("/api/auto-trader/optimize", methods=["GET", "POST"])
async def run_ai_optimizer():
    """Run AI-powered log analysis and get optimization suggestions"""
    try:
        from bots.ai_optimizer import get_optimizer
        optimizer = get_optimizer(auto_apply=True)
        result = optimizer.run_optimization_cycle()
        ensemble = get_ensemble()
        runtime_parameters = ensemble.reload_runtime_overrides()
        return {
            "status": "success",
            "analysis": result,
            "runtime_parameters": runtime_parameters,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.get("/api/auto-trader/trading-mode")
async def get_trading_mode():
    """
    Get current trading mode based on .env configuration.

    This endpoint shows the ACTUAL trading mode that will be used,
    based on the environment variables in .env file.

    To change mode:
    1. Edit .env file
    2. Set FYERS_DRY_RUN=true for paper trading
    3. Set FYERS_DRY_RUN=false AND FYERS_ALLOW_LIVE_ORDERS=true for live
    4. Restart the server
    """
    import os
    from trading.auto_trader import TradingMode, get_trading_mode_from_env

    dry_run = os.getenv("FYERS_DRY_RUN", "true")
    allow_live = os.getenv("FYERS_ALLOW_LIVE_ORDERS", "false")
    mode = get_trading_mode_from_env()

    return {
        "mode": mode.value,
        "is_paper": mode == TradingMode.PAPER,
        "is_live": mode == TradingMode.LIVE,
        "environment": {
            "FYERS_DRY_RUN": dry_run,
            "FYERS_ALLOW_LIVE_ORDERS": allow_live,
        },
        "explanation": (
            "PAPER mode: No real money trades. Safe for testing."
            if mode == TradingMode.PAPER
            else "⚠️ LIVE mode: Real money trades will be executed!"
        ),
        "how_to_change": (
            "To enable LIVE trading: Set FYERS_DRY_RUN=false AND FYERS_ALLOW_LIVE_ORDERS=true in .env, then restart server."
            if mode == TradingMode.PAPER
            else "To switch to PAPER: Set FYERS_DRY_RUN=true in .env and restart server."
        ),
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/auto-trader/toggle-mode")
async def toggle_trading_mode(body: dict = None):
    """
    Toggle between PAPER and LIVE trading modes.

    This endpoint modifies the .env file and updates the current auto-trader instance.
    The change takes effect immediately without requiring a server restart.

    Body (optional): {
        "mode": "live" | "paper"  // If not provided, toggles current mode
    }
    """
    import os
    from pathlib import Path
    from trading.auto_trader import TradingMode, get_trading_mode_from_env

    try:
        # Get current mode
        current_mode = get_trading_mode_from_env()

        # Determine target mode
        if body and body.get("mode"):
            target_mode = body["mode"].lower()
            if target_mode not in ["live", "paper"]:
                return {"success": False, "error": f"Invalid mode: {target_mode}. Use 'live' or 'paper'."}
        else:
            # Toggle
            target_mode = "paper" if current_mode == TradingMode.LIVE else "live"

        # Update .env file
        env_path = Path(__file__).parent.parent.parent / ".env"

        if not env_path.exists():
            return {"success": False, "error": ".env file not found"}

        # Read current .env content
        with open(env_path, "r") as f:
            content = f.read()

        # Update the values
        if target_mode == "live":
            new_dry_run = "false"
            new_allow_live = "true"
        else:
            new_dry_run = "true"
            new_allow_live = "false"

        # Replace values in content
        import re
        content = re.sub(r'FYERS_DRY_RUN=\w+', f'FYERS_DRY_RUN={new_dry_run}', content)
        content = re.sub(r'FYERS_ALLOW_LIVE_ORDERS=\w+', f'FYERS_ALLOW_LIVE_ORDERS={new_allow_live}', content)

        # Write back
        with open(env_path, "w") as f:
            f.write(content)

        # Update environment variables in current process
        os.environ["FYERS_DRY_RUN"] = new_dry_run
        os.environ["FYERS_ALLOW_LIVE_ORDERS"] = new_allow_live

        # Update the auto-trader instance mode
        trader = get_auto_trader()
        new_mode_enum = TradingMode.LIVE if target_mode == "live" else TradingMode.PAPER
        trader.mode = new_mode_enum

        return {
            "success": True,
            "previous_mode": current_mode.value,
            "new_mode": target_mode,
            "is_live": target_mode == "live",
            "message": f"Switched to {target_mode.upper()} mode. {'⚠️ REAL MONEY AT RISK!' if target_mode == 'live' else 'Safe paper trading enabled.'}",
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.api_route("/api/auto-trader/feed", methods=["GET", "POST"])
async def feed_auto_trader(body: dict = None):
    """
    Feed market data directly to the auto-trader.

    This endpoint allows external systems (like the screener) to push
    market data to the auto-trader for immediate processing.

    Body (POST): {
        "index": "NIFTY50",
        "market_data": {
            "ltp": 24500,
            "change_pct": 0.5,
            "high": 24600,
            "low": 24400,
            ...
        }
    }

    GET: Returns current auto-trader state and last processed signal
    """
    trader = get_auto_trader()

    if body:
        index = body.get("index", "NIFTY50")
        market_data = body.get("market_data", {})

        result = trader.feed_market_data(index, market_data)
        return {
            "result": result,
            "is_running": trader.is_running,
            "mode": trader.mode.value,
            "timestamp": datetime.now().isoformat()
        }

    # GET request - return current state
    return {
        "is_running": trader.is_running,
        "is_paused": trader.is_paused,
        "mode": trader.mode.value,
        "open_positions": len([p for p in trader.positions.values() if p.status == "open"]),
        "daily_pnl": trader.daily_pnl,
        "daily_trades": trader.daily_trades,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/auto-trader/loop-status")
async def get_auto_trader_loop_status():
    """
    Get detailed status of the background trading loop.

    Shows whether the autonomous trading loop is running,
    when it last processed data, and current positions.
    """
    trader = get_auto_trader()

    loop_active = hasattr(trader, '_trading_thread') and trader._trading_thread.is_alive()

    return {
        "loop_active": loop_active,
        "is_running": trader.is_running,
        "is_paused": trader.is_paused,
        "mode": trader.mode.value,
        "open_positions": trader.get_status().get("open_positions", 0),
        "daily_pnl": trader.daily_pnl,
        "daily_trades": trader.daily_trades,
        "can_trade": trader.check_can_trade(),
        "last_trade_time": trader.last_trade_time.isoformat() if trader.last_trade_time else None,
        "message": (
            "Trading loop is actively monitoring and executing trades"
            if loop_active and trader.is_running and not trader.is_paused
            else "Trading loop is paused" if trader.is_paused
            else "Trading loop is stopped" if not trader.is_running
            else "Trading loop thread not started"
        ),
        "timestamp": datetime.now().isoformat()
    }


# =============================================================================
# EXECUTION QUALITY & GO-LIVE VALIDATION
# =============================================================================

@app.get("/api/auto-trader/execution-quality")
async def get_execution_quality(days: int = Query(default=10, ge=1, le=90)):
    """
    Get execution quality report for go-live validation.

    Returns slippage, latency, fill rates, and gate validation status.
    This data is critical for deciding when to transition from paper to live trading.
    """
    trader = get_auto_trader()

    if not hasattr(trader, 'execution_tracker') or trader.execution_tracker is None:
        return {
            "error": "Execution quality tracking not available",
            "message": "ExecutionQualityTracker module not loaded"
        }

    try:
        report = trader.execution_tracker.get_execution_quality_report(days=days)
        return report
    except Exception as e:
        logger.error(f"Failed to get execution quality report: {e}")
        return {"error": str(e)}


@app.get("/api/auto-trader/gates")
async def get_gate_status():
    """
    Get status of all validation gates for staged rollout.

    Gates:
    - paper_validation: 10 days paper trading
    - micro_live: 20 days with 20% capital
    - scale_up: 30 days with 60% capital
    - full_capital: Ongoing with 100% capital
    """
    trader = get_auto_trader()

    if not hasattr(trader, 'execution_tracker') or trader.execution_tracker is None:
        return {
            "error": "Execution quality tracking not available",
            "gates": {}
        }

    try:
        gates = trader.execution_tracker.get_all_gate_status()
        current_gate = trader.execution_tracker.get_current_gate()

        return {
            "current_gate": current_gate,
            "gates": {name: {
                "status": result.status,
                "days_completed": result.days_completed,
                "days_required": result.days_required,
                "trades_completed": result.trades_completed,
                "trades_required": result.trades_required,
                "all_criteria_passed": result.all_criteria_passed,
                "failure_reasons": result.failure_reasons,
                "metrics": {
                    "slippage": {"value": result.avg_slippage_pct, "passed": result.slippage_passed},
                    "latency": {"value": result.avg_latency_ms, "passed": result.latency_passed},
                    "fill_rate": {"value": result.fill_rate_pct, "passed": result.fill_rate_passed},
                    "rejection_rate": {"value": result.rejection_rate_pct, "passed": result.rejection_passed},
                    "win_rate": {"value": result.win_rate_pct, "passed": result.win_rate_passed},
                    "drawdown": {"value": result.max_drawdown_pct, "passed": result.drawdown_passed},
                }
            } for name, result in gates.items()},
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get gate status: {e}")
        return {"error": str(e)}


@app.get("/api/auto-trader/gate/{gate_name}")
async def validate_specific_gate(gate_name: str):
    """
    Validate a specific gate and get detailed results.

    Valid gate names: paper_validation, micro_live, scale_up, full_capital
    """
    trader = get_auto_trader()

    if not hasattr(trader, 'execution_tracker') or trader.execution_tracker is None:
        return {"error": "Execution quality tracking not available"}

    try:
        result = trader.execution_tracker.validate_gate(gate_name)
        return {
            "gate_name": result.gate_name,
            "status": result.status,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "progress": {
                "days": f"{result.days_completed}/{result.days_required}",
                "trades": f"{result.trades_completed}/{result.trades_required}"
            },
            "metrics": {
                "avg_slippage_pct": round(result.avg_slippage_pct, 3),
                "avg_latency_ms": round(result.avg_latency_ms, 1),
                "fill_rate_pct": round(result.fill_rate_pct, 1),
                "rejection_rate_pct": round(result.rejection_rate_pct, 1),
                "win_rate_pct": round(result.win_rate_pct, 1),
                "max_drawdown_pct": round(result.max_drawdown_pct, 1),
            },
            "checks": {
                "slippage": "PASS" if result.slippage_passed else "FAIL",
                "latency": "PASS" if result.latency_passed else "FAIL",
                "fill_rate": "PASS" if result.fill_rate_passed else "FAIL",
                "rejection_rate": "PASS" if result.rejection_passed else "FAIL",
                "win_rate": "PASS" if result.win_rate_passed else "FAIL",
                "drawdown": "PASS" if result.drawdown_passed else "FAIL",
            },
            "all_criteria_passed": result.all_criteria_passed,
            "failure_reasons": result.failure_reasons,
            "recommendation": (
                "Ready to proceed to next gate" if result.all_criteria_passed
                else f"Not ready: {', '.join(result.failure_reasons[:3])}"
            ),
            "timestamp": datetime.now().isoformat()
        }
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Failed to validate gate {gate_name}: {e}")
        return {"error": str(e)}


@app.post("/api/auto-trader/generate-daily-summary")
async def generate_daily_summary(target_date: str = None):
    """
    Generate execution quality summary for a specific date.

    If no date provided, generates for today.
    """
    trader = get_auto_trader()

    if not hasattr(trader, 'execution_tracker') or trader.execution_tracker is None:
        return {"error": "Execution quality tracking not available"}

    try:
        summary = trader.execution_tracker.generate_daily_summary(target_date)
        if summary:
            return {
                "success": True,
                "summary": {
                    "date": summary.date,
                    "mode": summary.mode,
                    "total_orders": summary.total_orders,
                    "filled_orders": summary.filled_orders,
                    "rejected_orders": summary.rejected_orders,
                    "fill_rate_pct": round(summary.overall_fill_rate_pct, 1),
                    "avg_slippage_pct": round(summary.avg_slippage_pct, 3),
                    "total_slippage_cost": round(summary.total_slippage_cost, 2),
                    "avg_latency_ms": round(summary.avg_latency_ms, 1),
                    "total_pnl": round(summary.total_pnl, 2),
                    "win_rate_pct": round(summary.win_rate_pct, 1),
                }
            }
        else:
            return {"success": False, "message": f"No trade data found for {target_date or 'today'}"}
    except Exception as e:
        logger.error(f"Failed to generate daily summary: {e}")
        return {"error": str(e)}


@app.post("/api/auto-trader/reset-gates")
async def reset_gate_status():
    """
    Reset gate validation status (for testing or starting fresh).
    Only resets gate status - trade data is preserved.
    """
    trader = get_auto_trader()

    if not hasattr(trader, 'execution_tracker') or trader.execution_tracker is None:
        return {"error": "Execution quality tracking not available"}

    try:
        from pathlib import Path
        import json
        from datetime import date

        gates_dir = trader.execution_tracker.gates_dir
        status_file = gates_dir / "current_status.json"

        # Reset to just paper_validation start
        reset_status = {
            "paper_validation_start": date.today().isoformat()
        }

        with open(status_file, 'w') as f:
            json.dump(reset_status, f, indent=2)

        return {
            "success": True,
            "message": "Gate status reset. Starting fresh from Paper Validation.",
            "new_status": reset_status
        }
    except Exception as e:
        logger.error(f"Failed to reset gate status: {e}")
        return {"error": str(e)}


# =============================================================================
# ARTIFACT HANDLING
# =============================================================================

ARTIFACT_EXTENSIONS = {'.pdf', '.docx', '.xlsx', '.pptx'}
ARTIFACT_MIME_TYPES = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}


def _parse_artifact_work_date(raw_value: str) -> Optional[date]:
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _list_artifact_entries() -> List[Tuple[float, dict]]:
    if not DATA_PATH.exists():
        return []

    artifacts: List[Tuple[float, dict]] = []
    today = datetime.now().date()
    for agent_dir in DATA_PATH.iterdir():
        if not agent_dir.is_dir():
            continue
        sandbox_dir = agent_dir / "sandbox"
        if not sandbox_dir.exists():
            continue
        signature = agent_dir.name
        for date_dir in sandbox_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for file_path in date_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                # Skip code_exec, videos, and reference_files directories
                rel_parts = file_path.relative_to(date_dir).parts
                if any(p in ('code_exec', 'videos', 'reference_files') for p in rel_parts):
                    continue
                ext = file_path.suffix.lower()
                if ext not in ARTIFACT_EXTENSIONS:
                    continue
                stat = file_path.stat()
                work_date = date_dir.name
                parsed_work_date = _parse_artifact_work_date(work_date)
                rel_path = str(file_path.relative_to(DATA_PATH))
                artifacts.append((
                    stat.st_mtime,
                    {
                        "agent": signature,
                        "work_date": work_date,
                        "work_date_is_future": bool(parsed_work_date and parsed_work_date > today),
                        "filename": file_path.name,
                        "extension": ext,
                        "size_bytes": stat.st_size,
                        "path": rel_path,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    },
                ))

    return artifacts


@app.get("/api/artifacts")
async def get_artifacts(
    count: int = Query(default=30, ge=1, le=100),
    sort: str = Query(default="recent"),
):
    """Get agent-produced artifacts ordered by recency or sampled randomly."""
    sort_key = (sort or "recent").strip().lower()
    if sort_key not in {"recent", "random"}:
        raise HTTPException(status_code=400, detail="sort must be one of: recent, random")

    artifacts = _list_artifact_entries()
    total = len(artifacts)

    if sort_key == "recent":
        artifacts.sort(key=lambda item: (item[0], item[1]["path"]), reverse=True)
        selected = [entry for _, entry in artifacts[:count]]
    else:
        if len(artifacts) > count:
            artifacts = random.sample(artifacts, count)
        selected = [entry for _, entry in artifacts]

    return {
        "artifacts": selected,
        "count": len(selected),
        "total": total,
        "sort": sort_key,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/artifacts/random")
async def get_random_artifacts(count: int = Query(default=30, ge=1, le=100)):
    """Backwards-compatible random artifact sample endpoint."""
    return await get_artifacts(count=count, sort="random")


@app.get("/api/artifacts/file")
async def get_artifact_file(path: str = Query(...)):
    """Serve an artifact file for preview/download"""
    if ".." in path:
        raise HTTPException(status_code=400, detail="Invalid path")

    file_path = (DATA_PATH / path).resolve()
    # Ensure resolved path is within DATA_PATH
    if not str(file_path).startswith(str(DATA_PATH.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    ext = file_path.suffix.lower()
    media_type = ARTIFACT_MIME_TYPES.get(ext, 'application/octet-stream')
    return FileResponse(file_path, media_type=media_type)


@app.get("/api/settings/hidden-agents")
async def get_hidden_agents():
    """Get list of hidden agent signatures"""
    if HIDDEN_AGENTS_PATH.exists():
        with open(HIDDEN_AGENTS_PATH, 'r') as f:
            hidden = json.load(f)
        return {"hidden": hidden}
    return {"hidden": []}


@app.put("/api/settings/hidden-agents")
async def set_hidden_agents(body: dict):
    """Set list of hidden agent signatures"""
    hidden = body.get("hidden", [])
    HIDDEN_AGENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HIDDEN_AGENTS_PATH, 'w') as f:
        json.dump(hidden, f)
    return {"status": "ok"}


DISPLAYING_NAMES_PATH = Path(__file__).parent.parent / "data" / "displaying_names.json"

@app.get("/api/settings/displaying-names")
async def get_displaying_names():
    """Get display name mapping {signature: display_name}"""
    if DISPLAYING_NAMES_PATH.exists():
        with open(DISPLAYING_NAMES_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await manager.connect(websocket)
    try:
        # Send initial connection message
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to LiveBench real-time updates"
        })

        # Keep connection alive and listen for messages
        while True:
            data = await websocket.receive_text()
            # Echo back for now, in production this would handle commands
            await websocket.send_json({
                "type": "echo",
                "data": data
            })
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/api/broadcast")
async def broadcast_message(message: dict):
    """
    Endpoint for LiveBench to broadcast updates to connected clients
    This should be called by the LiveAgent during execution
    """
    await manager.broadcast(message)
    return {"status": "broadcast sent"}


# File watcher for live updates (optional, for when agents are running)
async def watch_agent_files():
    """
    Watch agent data files for changes and broadcast updates
    This runs as a background task
    """
    import time
    last_modified = {}

    while True:
        try:
            if DATA_PATH.exists():
                for agent_dir in DATA_PATH.iterdir():
                    if agent_dir.is_dir():
                        signature = agent_dir.name

                        # Check balance file
                        balance_file = agent_dir / "economic" / "balance.jsonl"
                        if balance_file.exists():
                            mtime = balance_file.stat().st_mtime
                            key = f"{signature}_balance"

                            if key not in last_modified or mtime > last_modified[key]:
                                last_modified[key] = mtime

                                # Read latest balance
                                with open(balance_file, 'r') as f:
                                    lines = f.readlines()
                                    if lines:
                                        data = json.loads(lines[-1])
                                        await manager.broadcast({
                                            "type": "balance_update",
                                            "signature": signature,
                                            "data": data
                                        })

                        # Check decisions file
                        decision_file = agent_dir / "decisions" / "decisions.jsonl"
                        if decision_file.exists():
                            mtime = decision_file.stat().st_mtime
                            key = f"{signature}_decision"

                            if key not in last_modified or mtime > last_modified[key]:
                                last_modified[key] = mtime

                                # Read latest decision
                                with open(decision_file, 'r') as f:
                                    lines = f.readlines()
                                    if lines:
                                        data = json.loads(lines[-1])
                                        await manager.broadcast({
                                            "type": "activity_update",
                                            "signature": signature,
                                            "data": data
                                        })

                        # Check learning memory file
                        memory_file = agent_dir / "memory" / "memory.jsonl"
                        if memory_file.exists():
                            mtime = memory_file.stat().st_mtime
                            key = f"{signature}_learning"

                            if key not in last_modified or mtime > last_modified[key]:
                                last_modified[key] = mtime

                                last_entry = None
                                with open(memory_file, 'r', encoding='utf-8') as f:
                                    for line in f:
                                        if line.strip():
                                            last_entry = json.loads(line)

                                await manager.broadcast({
                                    "type": "learning_update",
                                    "signature": signature,
                                    "data": {
                                        "topic": (last_entry or {}).get("topic", ""),
                                        "timestamp": (last_entry or {}).get("timestamp", ""),
                                        "date": (last_entry or {}).get("date", ""),
                                    }
                                })
        except Exception as e:
            print(f"Error watching files: {e}")

        await asyncio.sleep(1)  # Check every second


@app.on_event("startup")
async def startup_event():
    """Start background tasks on startup"""
    asyncio.create_task(watch_agent_files())
    try:
        autostart_auto_trader_on_startup()
    except Exception:
        logger.exception("Auto-trader autostart failed during API startup")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

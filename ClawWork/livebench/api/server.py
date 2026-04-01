"""
LiveBench API Server - Real-time updates and data access for frontend

This FastAPI server provides:
- WebSocket endpoint for live agent activity streaming
- REST endpoints for agent data, tasks, and economic metrics
- Real-time updates as agents work and learn
"""

import asyncio
import json
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .deps import DATA_PATH, manager

logger = logging.getLogger(__name__)

app = FastAPI(title="LiveBench API", version="1.0.0")

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Import and include all routers
# ---------------------------------------------------------------------------

from .routers import (  # noqa: E402
    agents,
    artifacts,
    auto_trader,
    ensemble,
    fyersn7,
    market,
    pipelines,
    settings,
    websocket,
)

app.include_router(agents.router)
app.include_router(fyersn7.router)
app.include_router(ensemble.router)
app.include_router(pipelines.router)
app.include_router(auto_trader.router)
app.include_router(market.router)
app.include_router(artifacts.router)
app.include_router(settings.router)
app.include_router(websocket.router)


# ---------------------------------------------------------------------------
# Health-check / root endpoints
# ---------------------------------------------------------------------------


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
            "websocket": "/ws",
        },
    }


@app.get("/api/")
async def api_root():
    """API health check endpoint"""
    return {"status": "ok", "message": "LiveBench API is running"}


# ---------------------------------------------------------------------------
# Background file watcher (broadcasts live agent updates over WebSocket)
# ---------------------------------------------------------------------------


async def watch_agent_files():
    """
    Watch agent data files for changes and broadcast updates.
    This runs as a background task.
    """
    last_modified: dict = {}

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
                                with open(balance_file, "r") as f:
                                    lines = f.readlines()
                                    if lines:
                                        data = json.loads(lines[-1])
                                        await manager.broadcast({
                                            "type": "balance_update",
                                            "signature": signature,
                                            "data": data,
                                        })

                        # Check decisions file
                        decision_file = agent_dir / "decisions" / "decisions.jsonl"
                        if decision_file.exists():
                            mtime = decision_file.stat().st_mtime
                            key = f"{signature}_decision"

                            if key not in last_modified or mtime > last_modified[key]:
                                last_modified[key] = mtime
                                with open(decision_file, "r") as f:
                                    lines = f.readlines()
                                    if lines:
                                        data = json.loads(lines[-1])
                                        await manager.broadcast({
                                            "type": "activity_update",
                                            "signature": signature,
                                            "data": data,
                                        })

                        # Check learning memory file
                        memory_file = agent_dir / "memory" / "memory.jsonl"
                        if memory_file.exists():
                            mtime = memory_file.stat().st_mtime
                            key = f"{signature}_learning"

                            if key not in last_modified or mtime > last_modified[key]:
                                last_modified[key] = mtime
                                last_entry = None
                                with open(memory_file, "r", encoding="utf-8") as f:
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
                                    },
                                })
        except Exception as e:
            logger.warning(f"Error watching files: {e}")

        await asyncio.sleep(1)  # Check every second


# ---------------------------------------------------------------------------
# Startup / shutdown events
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup_event():
    """Start background tasks on startup"""
    asyncio.create_task(watch_agent_files())
    try:
        from .routers.auto_trader import autostart_auto_trader_on_startup
        autostart_auto_trader_on_startup()
    except Exception:
        logger.exception("Auto-trader autostart failed during API startup")
    try:
        from .routers.pipelines import autostart_hybrid_bridge_on_startup
        autostart_hybrid_bridge_on_startup()
    except Exception:
        logger.exception("Hybrid bridge autostart failed during API startup")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

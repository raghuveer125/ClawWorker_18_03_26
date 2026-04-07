"""WebSocket endpoints and broadcast route."""

import json
import logging
from contextlib import suppress
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..deps import (
    DATA_PATH,
    _load_market_index_symbols,
    _market_stream_settings,
    manager,
    market_manager,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


# ---------------------------------------------------------------------------
# General WebSocket
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await manager.connect(websocket)
    try:
        # Send initial connection message
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to LiveBench real-time updates",
        })

        # Keep connection alive and listen for messages
        while True:
            data = await websocket.receive_text()
            # Echo back for now, in production this would handle commands
            await websocket.send_json({
                "type": "echo",
                "data": data,
            })
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Market data WebSocket
# ---------------------------------------------------------------------------


@router.websocket("/ws/market")
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


# ---------------------------------------------------------------------------
# Broadcast (REST endpoint used by LiveAgent to push to WS clients)
# ---------------------------------------------------------------------------


@router.post("/api/broadcast")
async def broadcast_message(message: dict):
    """
    Endpoint for LiveBench to broadcast updates to connected clients.
    This should be called by the LiveAgent during execution.
    """
    await manager.broadcast(message)
    return {"status": "broadcast sent"}

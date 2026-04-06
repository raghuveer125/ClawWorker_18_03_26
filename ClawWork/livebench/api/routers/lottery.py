"""Lottery pipeline API — REST + WebSocket endpoints for the Lottery dashboard.

Provides:
- GET endpoints for status, tables, trades, config
- WebSocket for live 1s streaming updates
- Isolated from other pipelines

All data comes from the lottery engine's runtime state and DB.
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query

# Ensure lottery engine is importable
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lottery", tags=["lottery"])

# ── Lazy-loaded singleton state ────────────────────────────────────────────
# The lottery engine is heavy — only initialize on first request.

_engine_state: Dict[str, Any] = {}


_pipeline_threads: Dict[str, Any] = {}


def _get_engine(symbol: str = "NIFTY"):
    """Get lottery engine — starts pipeline in background thread on first access."""
    if symbol in _engine_state:
        return _engine_state[symbol]

    try:
        from engines.lottery.config import load_config
        from engines.lottery.main import LotteryPipeline, register_pipeline
        import threading

        cfg = load_config()

        # Resolve exchange from symbol
        exchange = "BSE" if symbol.upper() == "SENSEX" else "NSE"

        # Create and start pipeline in background
        pipeline = LotteryPipeline(config=cfg, symbol=symbol, exchange=exchange)
        register_pipeline(pipeline)

        thread = threading.Thread(
            target=pipeline.run,
            daemon=True,
            name=f"lottery-{symbol}",
        )
        thread.start()
        _pipeline_threads[symbol] = thread

        state = {
            "config": pipeline.config,
            "db": pipeline.db,
            "rsm": pipeline.rsm,
            "symbol": symbol,
            "initialized": True,
            "source": "pipeline",
        }
        _engine_state[symbol] = state
        logger.info("Lottery pipeline started in background for %s", symbol)
        return state

    except Exception as e:
        logger.error("Failed to start lottery pipeline for %s: %s", symbol, e)
        return None


# ── REST Endpoints ─────────────────────────────────────────────────────────


@router.get("/status")
async def get_status(symbol: str = Query(default="NIFTY")):
    """Get current lottery pipeline status."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail=f"Lottery engine not initialized for {symbol}")

    rsm = engine["rsm"]
    return {
        "success": True,
        "data": rsm.get_status_summary(),
    }


@router.get("/config")
async def get_config(symbol: str = Query(default="NIFTY")):
    """Get current lottery config."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail="Lottery engine not initialized")

    cfg = engine["config"]
    return {
        "success": True,
        "data": {
            "version_hash": cfg.version_hash,
            "config": cfg.to_dict(),
        },
    }


@router.get("/raw-data")
async def get_raw_data(symbol: str = Query(default="NIFTY")):
    """Get raw option chain table from latest snapshot."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail="Lottery engine not initialized")

    rsm = engine["rsm"]
    snapshot = rsm.state.last_chain_snapshot

    if not snapshot:
        return {"success": True, "data": {"rows": [], "spot_ltp": None, "message": "No snapshot available"}}

    from engines.lottery.reporting import raw_data_table
    rows = raw_data_table(snapshot)

    return {
        "success": True,
        "data": {
            "spot_ltp": snapshot.spot_ltp,
            "symbol": snapshot.symbol,
            "expiry": snapshot.expiry,
            "snapshot_id": snapshot.snapshot_id,
            "timestamp": snapshot.snapshot_timestamp.isoformat(),
            "row_count": len(rows),
            "rows": rows,
        },
    }


@router.get("/formula-audit")
async def get_formula_audit(symbol: str = Query(default="NIFTY")):
    """Get formula audit table from latest calculation."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail="Lottery engine not initialized")

    rsm = engine["rsm"]
    calc = rsm.state.last_calculated

    if not calc:
        return {"success": True, "data": {"rows": [], "message": "No calculations available"}}

    from engines.lottery.reporting import formula_audit_table
    rows = formula_audit_table(list(calc.rows))

    return {
        "success": True,
        "data": {
            "spot_ltp": calc.spot_ltp,
            "config_version": calc.config_version,
            "row_count": len(rows),
            "rows": rows,
        },
    }


@router.get("/quality")
async def get_quality(symbol: str = Query(default="NIFTY")):
    """Get snapshot quality check results."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail="Lottery engine not initialized")

    rsm = engine["rsm"]
    report = rsm.state.last_quality_report

    if not report:
        return {"success": True, "data": {"checks": [], "message": "No quality report available"}}

    from engines.lottery.reporting import quality_table
    rows = quality_table(report)

    return {
        "success": True,
        "data": {
            "overall_status": report.overall_status.value,
            "quality_score": report.quality_score,
            "checks": rows,
        },
    }


@router.get("/signals")
async def get_signals(
    symbol: str = Query(default="NIFTY"),
    limit: int = Query(default=50, le=500),
):
    """Get recent signal history."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail="Lottery engine not initialized")

    # Try runtime state first, fall back to DB
    rsm = engine["rsm"]
    signals = list(rsm.state.recent_signals)[-limit:]

    if signals:
        from engines.lottery.reporting import signal_table
        rows = signal_table(signals)
    else:
        # Fall back to DB
        db = engine["db"]
        rows = db.get_recent_signals(limit)

    return {
        "success": True,
        "data": {
            "count": len(rows),
            "signals": rows,
        },
    }


@router.get("/trades")
async def get_trades(
    symbol: str = Query(default="NIFTY"),
    limit: int = Query(default=50, le=500),
):
    """Get paper trade history."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail="Lottery engine not initialized")

    db = engine["db"]
    rows = db.get_trades(limit)

    # Add active trade if exists
    rsm = engine["rsm"]
    active = rsm.state.active_trade

    return {
        "success": True,
        "data": {
            "active_trade": {
                "trade_id": active.trade_id,
                "strike": active.strike,
                "side": active.side.value,
                "option_type": active.option_type.value,
                "entry_price": active.entry_price,
                "sl": active.sl,
                "t1": active.t1,
                "t2": active.t2,
                "t3": active.t3,
                "qty": active.qty,
                "status": active.status.value,
            } if active else None,
            "count": len(rows),
            "trades": rows,
        },
    }


@router.get("/capital")
async def get_capital(symbol: str = Query(default="NIFTY")):
    """Get capital ledger and summary."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail="Lottery engine not initialized")

    db = engine["db"]
    ledger = db.get_capital_ledger(100)

    return {
        "success": True,
        "data": {
            "count": len(ledger),
            "ledger": ledger,
        },
    }


@router.get("/candidates")
async def get_candidates(symbol: str = Query(default="NIFTY")):
    """Get scored strike candidates from latest cycle."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail="Lottery engine not initialized")

    rsm = engine["rsm"]
    # Candidates are stored in the last signal's debug data
    # For now return from recent signals
    last_signal = rsm.state.last_signal

    return {
        "success": True,
        "data": {
            "last_signal": {
                "validity": last_signal.validity.value,
                "selected_strike": last_signal.selected_strike,
                "selected_premium": last_signal.selected_premium,
                "machine_state": last_signal.machine_state.value,
                "zone": last_signal.zone,
            } if last_signal else None,
        },
    }


@router.get("/rejections")
async def get_rejections(
    symbol: str = Query(default="NIFTY"),
    limit: int = Query(default=20, le=100),
):
    """Get recent rejection reasons for debugging."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail="Lottery engine not initialized")

    rsm = engine["rsm"]
    rejections = rsm.get_recent_rejections(limit)

    return {
        "success": True,
        "data": {
            "count": len(rejections),
            "rejections": rejections,
        },
    }


@router.get("/spot-history")
async def get_spot_history(
    symbol: str = Query(default="NIFTY"),
    limit: int = Query(default=60, le=300),
):
    """Get recent spot price history."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail="Lottery engine not initialized")

    rsm = engine["rsm"]
    history = rsm.get_spot_history(limit)

    return {
        "success": True,
        "data": {
            "count": len(history),
            "history": history,
        },
    }


# ── WebSocket ──────────────────────────────────────────────────────────────


@router.websocket("/ws")
async def lottery_websocket(websocket: WebSocket, symbol: str = "NIFTY"):
    """Live streaming updates for the Lottery dashboard.

    Sends JSON updates every 1 second with:
    - status summary
    - last signal
    - active trade
    - quality status
    """
    await websocket.accept()
    engine = _get_engine(symbol)

    if not engine:
        await websocket.send_json({"error": f"Lottery engine not initialized for {symbol}"})
        await websocket.close()
        return

    rsm = engine["rsm"]

    try:
        while True:
            status = rsm.get_status_summary()

            # Add last signal summary
            last_sig = rsm.state.last_signal
            signal_data = None
            if last_sig:
                signal_data = {
                    "validity": last_sig.validity.value,
                    "strike": last_sig.selected_strike,
                    "premium": last_sig.selected_premium,
                    "state": last_sig.machine_state.value,
                    "zone": last_sig.zone,
                    "rejection": last_sig.rejection_reason.value if last_sig.rejection_reason else None,
                }

            # Active trade
            active = rsm.state.active_trade
            trade_data = None
            if active:
                trade_data = {
                    "trade_id": active.trade_id,
                    "strike": active.strike,
                    "side": active.side.value,
                    "entry": active.entry_price,
                    "sl": active.sl,
                    "t1": active.t1,
                    "peak_ltp": rsm.state.active_trade_peak_ltp,
                }

            payload = {
                "type": "lottery_update",
                "symbol": symbol,
                "timestamp": time.time(),
                "status": status,
                "last_signal": signal_data,
                "active_trade": trade_data,
            }

            await websocket.send_json(payload)
            await asyncio.sleep(1)

    except WebSocketDisconnect:
        logger.info("Lottery WebSocket disconnected for %s", symbol)
    except Exception as e:
        logger.error("Lottery WebSocket error: %s", e)

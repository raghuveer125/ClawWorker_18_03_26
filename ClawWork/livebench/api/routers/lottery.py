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

# ── Per-index configuration ────────────────────────────────────────────────

_INDEX_CONFIG = {
    "NIFTY":      {"strike_step": 50,  "lot_size": 75, "exchange": "NSE"},
    "BANKNIFTY":  {"strike_step": 100, "lot_size": 30, "exchange": "NSE"},
    "SENSEX":     {"strike_step": 100, "lot_size": 10, "exchange": "BSE"},
    "FINNIFTY":   {"strike_step": 50,  "lot_size": 40, "exchange": "NSE"},
    "MIDCPNIFTY": {"strike_step": 25,  "lot_size": 50, "exchange": "NSE"},
}

_AUTO_START_INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"]

_engine_state: Dict[str, Any] = {}
_pipeline_threads: Dict[str, Any] = {}


def _start_pipeline(symbol: str):
    """Start a lottery pipeline for the given index symbol."""
    if symbol in _engine_state:
        return _engine_state[symbol]

    try:
        from engines.lottery.config import load_config
        from engines.lottery.main import LotteryPipeline, register_pipeline
        from dataclasses import replace as dc_replace
        import threading

        cfg = load_config()
        idx = _INDEX_CONFIG.get(symbol.upper(), _INDEX_CONFIG["NIFTY"])

        # Override per-index config values
        cfg = dc_replace(cfg,
            instrument=dc_replace(cfg.instrument,
                symbol=symbol.upper(),
                strike_step=idx["strike_step"],
            ),
            paper_trading=dc_replace(cfg.paper_trading,
                lot_size=idx["lot_size"],
            ),
        )

        exchange = idx["exchange"]
        pipeline = LotteryPipeline(config=cfg, symbol=symbol.upper(), exchange=exchange)
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
            "symbol": symbol.upper(),
            "initialized": True,
            "source": "pipeline",
        }
        _engine_state[symbol.upper()] = state
        logger.info(
            "Lottery pipeline started for %s (step=%d, lot=%d, exchange=%s)",
            symbol, idx["strike_step"], idx["lot_size"], exchange,
        )
        return state

    except Exception as e:
        logger.error("Failed to start lottery pipeline for %s: %s", symbol, e)
        return None


def _get_engine(symbol: str = "NIFTY"):
    """Get lottery engine for a symbol. Auto-starts if not running."""
    sym = symbol.upper()
    if sym in _engine_state:
        return _engine_state[sym]
    return _start_pipeline(sym)


def _auto_start_all():
    """Auto-start pipelines for all configured indices."""
    for sym in _AUTO_START_INDICES:
        if sym not in _engine_state:
            logger.info("Auto-starting lottery pipeline for %s", sym)
            _start_pipeline(sym)


# Auto-start all 3 indices when the router module is loaded
try:
    import threading
    threading.Thread(target=_auto_start_all, daemon=True, name="lottery-autostart").start()
except Exception as e:
    logger.warning("Auto-start failed: %s", e)


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

    # Fallback to DB if RSM hasn't been populated yet (first 30s after restart)
    if not calc:
        try:
            db = engine["db"]
            row = db._conn.execute(
                "SELECT spot_ltp, config_version, rows_json FROM calculated_rows "
                "WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                (symbol.upper(),)
            ).fetchone()
            if row:
                import json as _json
                from engines.lottery.reporting import formula_audit_table
                db_rows = _json.loads(row[2])
                # Convert dicts to minimal format for the table
                return {
                    "success": True,
                    "data": {
                        "spot_ltp": row[0],
                        "config_version": row[1],
                        "row_count": len(db_rows),
                        "rows": db_rows,
                        "source": "db_fallback",
                    },
                }
        except Exception:
            pass
        return {"success": True, "data": {"rows": [], "message": "No calculations available — waiting for first analysis cycle"}}

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


@router.get("/band-candidates")
async def get_band_candidates(symbol: str = Query(default="NIFTY")):
    """Get band-eligible candidates with trigger zone info for live dashboard."""
    engine = _get_engine(symbol)
    if not engine:
        raise HTTPException(status_code=503, detail="Lottery engine not initialized")

    rsm = engine["rsm"]
    cfg = engine["config"]
    snapshot = rsm.state.last_chain_snapshot
    calculated = rsm.state.last_calculated

    if not snapshot:
        return {"success": True, "data": {"rows": [], "spot": None, "triggers": None}}

    spot = snapshot.spot_ltp
    step = cfg.instrument.strike_step
    band_min = cfg.premium_band.min
    band_max = cfg.premium_band.max

    # Compute dynamic triggers
    strikes = sorted(set(r.strike for r in snapshot.rows))
    below = [s for s in strikes if s <= spot]
    above = [s for s in strikes if s > spot]
    lower_trigger = max(below) if below else spot - step
    upper_trigger = min(above) if above else spot + step
    buffer = cfg.hysteresis.buffer_points

    # Build band-eligible rows from raw chain
    rows = []
    strike_map = {}
    for r in snapshot.rows:
        if r.strike not in strike_map:
            strike_map[r.strike] = {}
        strike_map[r.strike][r.option_type.value] = r

    max_spread = cfg.tradability.max_spread_pct

    for strike in sorted(strike_map.keys()):
        ce = strike_map[strike].get("CE")
        pe = strike_map[strike].get("PE")

        ce_ltp = ce.ltp if ce else None
        pe_ltp = pe.ltp if pe else None

        # Tradability filter: OTM + has bid/ask + spread < max
        def _is_tradable(r, ltp, is_ce):
            if not r or not ltp or ltp <= 0:
                return False, None
            if (is_ce and r.strike <= spot) or (not is_ce and r.strike >= spot):
                return False, None  # ITM
            bid = r.bid if r.bid and r.bid > 0 else None
            ask = r.ask if r.ask and r.ask > 0 else None
            if not bid or not ask:
                return False, None
            mid = (bid + ask) / 2
            sp = round(((ask - bid) / mid) * 100, 1) if mid > 0 else None
            if sp is not None and sp <= max_spread:
                return True, sp
            return False, sp

        ce_ok, ce_spread = _is_tradable(ce, ce_ltp, True)
        pe_ok, pe_spread = _is_tradable(pe, pe_ltp, False)

        if not ce_ok and not pe_ok:
            continue

        dist = abs(strike - spot)
        side = "OTM-CE" if strike > spot else "OTM-PE" if strike < spot else "ATM"

        row = {
            "strike": strike,
            "distance": round(dist, 0),
            "side": side,
        }

        if ce_ok:
            row["CE_LTP"] = ce_ltp
            row["CE_bid"] = ce.bid
            row["CE_ask"] = ce.ask
            row["CE_spread_pct"] = ce_spread
            row["CE_volume"] = ce.volume
            row["CE_OI"] = ce.oi

        if pe_ok:
            row["PE_LTP"] = pe_ltp
            row["PE_bid"] = pe.bid
            row["PE_ask"] = pe.ask
            row["PE_spread_pct"] = pe_spread
            row["PE_volume"] = pe.volume
            row["PE_OI"] = pe.oi

        rows.append(row)

    # Build BEST LOTTERY STRIKES — near-ATM OTM strikes ranked by volume
    # Highest volume = most participation = highest probability of premium moving on breakout
    best_entries = []
    sl_ratio = cfg.exit_rules.sl_ratio
    t1_ratio = cfg.exit_rules.t1_ratio
    t2_ratio = cfg.exit_rules.t2_ratio
    t3_ratio = cfg.exit_rules.t3_ratio

    def _make_entry(opt_row, opt_type, label, emoji, direction, trigger_cond):
        ask = opt_row.ask if opt_row.ask and opt_row.ask > 0 else opt_row.ltp
        ep = round(ask, 2)
        return {
            "emoji": emoji,
            "label": label,
            "strike": opt_row.strike,
            "option_type": opt_type,
            "entry_price": ep,
            "sl": round(ep * sl_ratio, 2),
            "t1": round(ep * t1_ratio, 2),
            "t2": round(ep * t2_ratio, 2),
            "t3": round(ep * t3_ratio, 2),
            "trigger_condition": trigger_cond,
            "direction": direction,
        }

    # PE: OTM strikes below spot, sorted by volume (highest first)
    pe_otm = []
    for k in sorted(strike_map.keys(), reverse=True):
        if k >= spot:
            continue
        r = strike_map[k].get("PE")
        if r and r.ltp and r.ltp > 0 and (r.volume or 0) > 0:
            pe_otm.append(r)
        if len(pe_otm) >= 5:
            break
    pe_otm.sort(key=lambda r: r.volume or 0, reverse=True)

    if len(pe_otm) >= 1:
        best_entries.append(_make_entry(
            pe_otm[0], "PE", "BEST LOTTERY PE", "\U0001f4a5",
            "breakdown below", f"spot < {lower_trigger - buffer}",
        ))
    if len(pe_otm) >= 2:
        best_entries.append(_make_entry(
            pe_otm[1], "PE", "ALT LOTTERY PE", "\U0001f4a5",
            "breakdown below", f"spot < {lower_trigger - buffer}",
        ))

    # CE: OTM strikes above spot, sorted by volume (highest first)
    ce_otm = []
    for k in sorted(strike_map.keys()):
        if k <= spot:
            continue
        r = strike_map[k].get("CE")
        if r and r.ltp and r.ltp > 0 and (r.volume or 0) > 0:
            ce_otm.append(r)
        if len(ce_otm) >= 5:
            break
    ce_otm.sort(key=lambda r: r.volume or 0, reverse=True)

    if len(ce_otm) >= 1:
        best_entries.append(_make_entry(
            ce_otm[0], "CE", "BEST LOTTERY CE", "\U0001f680",
            "breakout above", f"spot > {upper_trigger + buffer}",
        ))
    if len(ce_otm) >= 2:
        best_entries.append(_make_entry(
            ce_otm[1], "CE", "ALT LOTTERY CE", "\U0001f680",
            "breakout above", f"spot > {upper_trigger + buffer}",
        ))

    status = rsm.get_status_summary()

    return {
        "success": True,
        "data": {
            "spot": spot,
            "rows": rows,
            "best_entries": best_entries,
            "triggers": {
                "upper": upper_trigger,
                "lower": lower_trigger,
                "buffer": buffer,
                "ce_activation": upper_trigger + buffer,
                "pe_activation": lower_trigger - buffer,
                "spot_to_upper": round(upper_trigger + buffer - spot, 1),
                "spot_to_lower": round(spot - (lower_trigger - buffer), 1),
            },
            "band": {"min": band_min, "max": band_max},
            "selected_strike": status.get("selected_strike"),
            "side_bias": status.get("side_bias"),
            "state": status.get("state"),
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

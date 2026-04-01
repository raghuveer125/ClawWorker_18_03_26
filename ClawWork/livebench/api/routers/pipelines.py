"""Regime/Hybrid pipeline control endpoints (~13 routes)."""

import logging
import os
from datetime import datetime
from functools import lru_cache
from typing import Dict

from fastapi import APIRouter

from ..deps import (
    _build_market_client,
    get_hybrid_bridge,
    get_hybrid_pipeline,
    get_rh_pipeline,
    _env_flag,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["pipelines"])

# ---------------------------------------------------------------------------
# Indices config (used by pipelines + market)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_cached_indices_config() -> Dict:
    """Load centralized indices configuration once per API process."""
    try:
        import sys
        from pathlib import Path

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


# ---------------------------------------------------------------------------
# Regime-Hunter pipeline
# ---------------------------------------------------------------------------


@router.get("/regime-hunter-pipeline/status")
async def rh_pipeline_status():
    """Get RegimeHunter pipeline status."""
    pipeline = get_rh_pipeline()
    return {
        **pipeline.get_status(),
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/regime-hunter-pipeline/start")
async def rh_pipeline_start():
    """Start the independent RegimeHunter pipeline."""
    pipeline = get_rh_pipeline()
    pipeline.start()
    return {"status": "started", "mode": pipeline.mode, "timestamp": datetime.now().isoformat()}


@router.post("/regime-hunter-pipeline/stop")
async def rh_pipeline_stop():
    """Stop the RegimeHunter pipeline."""
    pipeline = get_rh_pipeline()
    pipeline.stop()
    return {"status": "stopped", "timestamp": datetime.now().isoformat()}


@router.post("/regime-hunter-pipeline/pause")
async def rh_pipeline_pause():
    pipeline = get_rh_pipeline()
    pipeline.pause()
    return {"status": "paused", "timestamp": datetime.now().isoformat()}


@router.post("/regime-hunter-pipeline/resume")
async def rh_pipeline_resume():
    pipeline = get_rh_pipeline()
    pipeline.resume()
    return {"status": "resumed", "timestamp": datetime.now().isoformat()}


@router.post("/regime-hunter-pipeline/reset-daily")
async def rh_pipeline_reset():
    pipeline = get_rh_pipeline()
    pipeline.reset_daily()
    return {"status": "reset", "timestamp": datetime.now().isoformat()}


@router.post("/regime-hunter-pipeline/process")
async def rh_pipeline_process(body: dict):
    """Feed market data directly into the RegimeHunter pipeline."""
    pipeline = get_rh_pipeline()
    index = body.get("index", "")
    market_data = body.get("market_data", {})
    if not index or not market_data:
        return {"action": "ERROR", "reason": "Missing index or market_data"}

    result = pipeline.process(index, market_data)
    result["timestamp"] = datetime.now().isoformat()
    return result


# ---------------------------------------------------------------------------
# Hybrid pipeline
# ---------------------------------------------------------------------------


@router.get("/hybrid-pipeline/status")
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


@router.post("/hybrid-pipeline/analyze")
async def hybrid_pipeline_analyze(body: dict):
    """Analyze market data using hybrid pipeline."""
    pipeline = get_hybrid_pipeline()
    if not pipeline:
        return {"error": "Hybrid pipeline not available"}

    index = body.get("index", "")
    market_data = body.get("market_data", {})
    historical_data = body.get("historical_data")

    if not index or not market_data:
        return {"error": "Missing index or market_data"}

    is_expiry = market_data.pop("is_expiry", False)
    if is_expiry:
        pipeline.configure_for_expiry()
    else:
        pipeline.configure_for_normal()

    # Enrich with futures high/low
    try:
        _fut_client = _build_market_client()
        _fut_symbol, _fut_ltp = _fut_client.resolve_future_quote(index)
        if _fut_symbol and _fut_ltp > 0:
            _fut_quote = _fut_client.get_quote(_fut_symbol)
            if _fut_quote.get("high", 0) > 0:
                market_data["futures_high"] = _fut_quote["high"]
                market_data["futures_low"] = _fut_quote["low"]
    except Exception:
        pass

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


@router.post("/hybrid-pipeline/module/toggle")
async def hybrid_pipeline_toggle_module(body: dict):
    """Enable or disable a module."""
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


@router.post("/hybrid-pipeline/module/weight")
async def hybrid_pipeline_set_weight(body: dict):
    """Set module weight."""
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


@router.post("/hybrid-pipeline/configure-expiry")
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


@router.post("/hybrid-pipeline/configure-normal")
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


# ---------------------------------------------------------------------------
# Hybrid Execution Bridge
# ---------------------------------------------------------------------------


def autostart_hybrid_bridge_on_startup() -> None:
    """Auto-start the hybrid execution bridge when the API comes online."""
    enabled = _env_flag("HYBRID_BRIDGE_AUTOSTART", True)
    if not enabled:
        logger.info("[HybridBridge] Autostart disabled by HYBRID_BRIDGE_AUTOSTART=false")
        return
    bridge = get_hybrid_bridge()
    if bridge is None:
        logger.warning("[HybridBridge] Autostart skipped -- bridge unavailable")
        return
    bridge.start()
    logger.info("[HybridBridge] Autostarted on server startup | mode=%s", bridge.mode)


@router.get("/hybrid-bridge/status")
async def hybrid_bridge_status():
    """Get execution bridge status, open positions, and today's performance."""
    bridge = get_hybrid_bridge()
    if not bridge:
        return {"error": "Hybrid execution bridge not available"}
    return bridge.get_status()


@router.post("/hybrid-bridge/start")
async def hybrid_bridge_start():
    """Start the execution bridge background loop."""
    bridge = get_hybrid_bridge()
    if not bridge:
        return {"error": "Hybrid execution bridge not available"}
    bridge.start()
    return {"started": True, "mode": bridge.mode, "timestamp": datetime.now().isoformat()}


@router.post("/hybrid-bridge/stop")
async def hybrid_bridge_stop():
    """Stop the execution bridge background loop."""
    bridge = get_hybrid_bridge()
    if not bridge:
        return {"error": "Hybrid execution bridge not available"}
    bridge.stop()
    return {"stopped": True, "timestamp": datetime.now().isoformat()}


@router.get("/hybrid-bridge/positions")
async def hybrid_bridge_positions():
    """List all open lottery positions managed by the bridge."""
    bridge = get_hybrid_bridge()
    if not bridge:
        return {"error": "Hybrid execution bridge not available"}
    return {"positions": bridge.get_open_positions(), "timestamp": datetime.now().isoformat()}


@router.get("/hybrid-bridge/trades")
async def hybrid_bridge_trades(limit: int = 100):
    """List recently closed trades (newest first)."""
    bridge = get_hybrid_bridge()
    if not bridge:
        return {"error": "Hybrid execution bridge not available"}
    return {"trades": bridge.get_trades(limit=limit), "timestamp": datetime.now().isoformat()}


@router.post("/hybrid-bridge/close/{position_id}")
async def hybrid_bridge_close_position(position_id: str):
    """Manually close a specific open position."""
    bridge = get_hybrid_bridge()
    if not bridge:
        return {"error": "Hybrid execution bridge not available"}
    closed = bridge.close_position_manual(position_id)
    if not closed:
        return {"error": f"Position {position_id} not found or already closed"}
    return {"closed": True, "position_id": position_id, "timestamp": datetime.now().isoformat()}

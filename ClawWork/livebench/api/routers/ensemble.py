"""Bot ensemble status/analysis endpoints (~13 routes)."""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from ..deps import (
    _build_market_client,
    _to_float,
    get_ensemble,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ensemble"])


# ---------------------------------------------------------------------------
# ICT Sniper warmup helpers (only used by ensemble routes)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Institutional trading helpers (only used by ensemble routes)
# ---------------------------------------------------------------------------


@router.get("/institutional/market-session")
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


@router.post("/institutional/validate-trade")
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


@router.get("/institutional/position-size")
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


@router.get("/institutional/risk-config")
async def get_risk_config():
    """Get current risk management configuration"""
    from trading.institutional import load_risk_config
    from dataclasses import asdict

    config = load_risk_config()
    return asdict(config)


# ---------------------------------------------------------------------------
# Multi-bot ensemble endpoints
# ---------------------------------------------------------------------------


@router.get("/bots/status")
async def get_bots_status():
    """Get status of all trading bots in the ensemble"""
    ensemble = get_ensemble()
    return {
        "bots": ensemble.get_bot_status(),
        "timestamp": datetime.now().isoformat()
    }


@router.get("/bots/leaderboard")
async def get_bots_leaderboard():
    """Get bot leaderboard sorted by performance"""
    ensemble = get_ensemble()
    return {
        "leaderboard": ensemble.get_leaderboard(),
        "timestamp": datetime.now().isoformat()
    }


@router.get("/bots/ensemble-stats")
async def get_ensemble_stats():
    """Get ensemble-level statistics"""
    ensemble = get_ensemble()
    return {
        "stats": ensemble.get_ensemble_stats(),
        "timestamp": datetime.now().isoformat()
    }


@router.get("/bots/ict-sniper/status")
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


@router.post("/bots/ict-sniper/analyze")
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


@router.post("/bots/ict-sniper/record-trade")
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


@router.get("/bots/regime-hunter/status")
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


@router.post("/bots/analyze")
async def analyze_market(body: dict):
    """Analyze market data and get ensemble decision"""
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


@router.post("/bots/analyze-all")
async def analyze_all_indices(body: dict):
    """Analyze multiple indices and get all decisions"""
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


@router.post("/bots/record-trade")
async def record_trade_outcome(body: dict):
    """Record a trade outcome for learning"""
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


@router.post("/bots/reset-daily")
async def reset_daily_counters():
    """Reset daily trading counters (call at market open)"""
    ensemble = get_ensemble()
    ensemble.reset_daily()
    return {
        "status": "reset",
        "message": "Daily counters reset",
        "timestamp": datetime.now().isoformat()
    }


@router.get("/bots/{bot_name}/details")
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


# ---------------------------------------------------------------------------
# ML training data endpoints
# ---------------------------------------------------------------------------


@router.get("/ml/statistics")
async def get_ml_statistics():
    """Get ML training data statistics"""
    ensemble = get_ensemble()
    stats = ensemble.get_ml_statistics()
    return {
        "statistics": stats,
        "timestamp": datetime.now().isoformat()
    }


@router.get("/ml/learning-insights")
async def get_ml_learning_insights():
    """Get learning insights from pattern discovery"""
    ensemble = get_ensemble()
    insights = ensemble.get_learning_insights()
    return {
        "insights": insights,
        "timestamp": datetime.now().isoformat()
    }


@router.post("/ml/export-csv")
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


@router.get("/ml/training-data")
async def get_ml_training_data():
    """Get ML training data as feature vectors and labels"""
    ensemble = get_ensemble()
    X, y = ensemble.get_ml_training_data()
    return {
        "feature_count": len(X[0]) if X else 0,
        "sample_count": len(X),
        "features": X[:100],
        "labels": y[:100],
        "label_distribution": {
            "loss": y.count(0) if y else 0,
            "breakeven": y.count(1) if y else 0,
            "win": y.count(2) if y else 0,
        },
        "timestamp": datetime.now().isoformat()
    }


@router.get("/ml/bot-status")
async def get_ml_bot_status():
    """Get ML bot status"""
    ensemble = get_ensemble()
    return {
        "ml_bot": ensemble.ml_bot.get_status(),
        "timestamp": datetime.now().isoformat()
    }


@router.post("/ml/train")
async def train_ml_model(model_type: str = "random_forest"):
    """Train the ML model on collected data."""
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

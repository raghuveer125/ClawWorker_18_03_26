"""AutoTrader status/control endpoints (~12 routes)."""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from ..deps import (
    _env_flag,
    get_auto_trader,
    get_ensemble,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auto_trader"])


# ---------------------------------------------------------------------------
# Autostart helper (called from server.py startup)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.get("/auto-trader/status")
async def get_auto_trader_status():
    """Get auto-trader status"""
    trader = get_auto_trader()
    return {
        "status": trader.get_status(),
        "timestamp": datetime.now().isoformat()
    }


@router.api_route("/auto-trader/start", methods=["GET", "POST"])
async def start_auto_trader():
    """Start auto-trading with mode determined by .env configuration."""
    from trading.auto_trader import TradingMode, get_trading_mode_from_env

    trader = get_auto_trader()
    env_mode = get_trading_mode_from_env()

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
        message += " \u26a0\ufe0f REAL MONEY AT RISK!"

    return {
        "status": "started",
        "strategy_id": trader.strategy_id,
        "mode": mode_display.lower(),
        "dry_run": dry_run,
        "allow_live_orders": allow_live,
        "message": message,
        "timestamp": datetime.now().isoformat()
    }


@router.api_route("/auto-trader/stop", methods=["GET", "POST"])
async def stop_auto_trader():
    """Stop auto-trading"""
    trader = get_auto_trader()
    trader.stop()
    return {
        "status": "stopped",
        "timestamp": datetime.now().isoformat()
    }


@router.api_route("/auto-trader/pause", methods=["GET", "POST"])
async def pause_auto_trader():
    """Pause auto-trading"""
    trader = get_auto_trader()
    trader.pause()
    return {
        "status": "paused",
        "timestamp": datetime.now().isoformat()
    }


@router.api_route("/auto-trader/resume", methods=["GET", "POST"])
async def resume_auto_trader():
    """Resume auto-trading"""
    trader = get_auto_trader()
    trader.resume()
    return {
        "status": "resumed",
        "timestamp": datetime.now().isoformat()
    }


@router.api_route("/auto-trader/emergency-stop", methods=["GET", "POST"])
async def emergency_stop_auto_trader():
    """Emergency stop - closes all positions"""
    trader = get_auto_trader()
    trader.emergency_stop()
    return {
        "status": "emergency_stopped",
        "message": "All positions closed. Trading disabled.",
        "timestamp": datetime.now().isoformat()
    }


@router.get("/auto-trader/performance")
async def get_auto_trader_performance():
    """Get auto-trader performance summary"""
    trader = get_auto_trader()
    return {
        "performance": trader.get_performance_summary(),
        "status": trader.get_status(),
        "timestamp": datetime.now().isoformat()
    }


@router.get("/auto-trader/positions")
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


@router.get("/auto-trader/trades")
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


@router.api_route("/auto-trader/reset-daily", methods=["GET", "POST"])
async def reset_auto_trader_daily():
    """Reset daily counters (call at market open)"""
    trader = get_auto_trader()
    trader.reset_daily()
    return {
        "status": "reset",
        "timestamp": datetime.now().isoformat()
    }


@router.api_route("/auto-trader/optimize", methods=["GET", "POST"])
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


@router.get("/auto-trader/trading-mode")
async def get_trading_mode():
    """Get current trading mode based on .env configuration."""
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
            else "\u26a0\ufe0f LIVE mode: Real money trades will be executed!"
        ),
        "how_to_change": (
            "To enable LIVE trading: Set FYERS_DRY_RUN=false AND FYERS_ALLOW_LIVE_ORDERS=true in .env, then restart server."
            if mode == TradingMode.PAPER
            else "To switch to PAPER: Set FYERS_DRY_RUN=true in .env and restart server."
        ),
        "timestamp": datetime.now().isoformat()
    }


@router.post("/auto-trader/toggle-mode")
async def toggle_trading_mode(body: dict = None):
    """Toggle between PAPER and LIVE trading modes."""
    from trading.auto_trader import TradingMode, get_trading_mode_from_env

    try:
        current_mode = get_trading_mode_from_env()

        if body and body.get("mode"):
            target_mode = body["mode"].lower()
            if target_mode not in ["live", "paper"]:
                return {"success": False, "error": f"Invalid mode: {target_mode}. Use 'live' or 'paper'."}
        else:
            target_mode = "paper" if current_mode == TradingMode.LIVE else "live"

        env_path = Path(__file__).parent.parent.parent.parent / ".env"

        if not env_path.exists():
            return {"success": False, "error": ".env file not found"}

        with open(env_path, "r") as f:
            content = f.read()

        if target_mode == "live":
            new_dry_run = "false"
            new_allow_live = "true"
        else:
            new_dry_run = "true"
            new_allow_live = "false"

        content = re.sub(r'FYERS_DRY_RUN=\w+', f'FYERS_DRY_RUN={new_dry_run}', content)
        content = re.sub(r'FYERS_ALLOW_LIVE_ORDERS=\w+', f'FYERS_ALLOW_LIVE_ORDERS={new_allow_live}', content)

        with open(env_path, "w") as f:
            f.write(content)

        os.environ["FYERS_DRY_RUN"] = new_dry_run
        os.environ["FYERS_ALLOW_LIVE_ORDERS"] = new_allow_live

        trader = get_auto_trader()
        new_mode_enum = TradingMode.LIVE if target_mode == "live" else TradingMode.PAPER
        trader.mode = new_mode_enum

        return {
            "success": True,
            "previous_mode": current_mode.value,
            "new_mode": target_mode,
            "is_live": target_mode == "live",
            "message": f"Switched to {target_mode.upper()} mode. {'\u26a0\ufe0f REAL MONEY AT RISK!' if target_mode == 'live' else 'Safe paper trading enabled.'}",
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.api_route("/auto-trader/feed", methods=["GET", "POST"])
async def feed_auto_trader(body: dict = None):
    """Feed market data directly to the auto-trader."""
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

    return {
        "is_running": trader.is_running,
        "is_paused": trader.is_paused,
        "mode": trader.mode.value,
        "open_positions": len([p for p in trader.positions.values() if p.status == "open"]),
        "daily_pnl": trader.daily_pnl,
        "daily_trades": trader.daily_trades,
        "timestamp": datetime.now().isoformat()
    }


@router.get("/auto-trader/loop-status")
async def get_auto_trader_loop_status():
    """Get detailed status of the background trading loop."""
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


# ---------------------------------------------------------------------------
# Execution quality & go-live validation
# ---------------------------------------------------------------------------


@router.get("/auto-trader/execution-quality")
async def get_execution_quality(days: int = Query(default=10, ge=1, le=90)):
    """Get execution quality report for go-live validation."""
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


@router.get("/auto-trader/gates")
async def get_gate_status():
    """Get status of all validation gates for staged rollout."""
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


@router.get("/auto-trader/gate/{gate_name}")
async def validate_specific_gate(gate_name: str):
    """Validate a specific gate and get detailed results."""
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


@router.post("/auto-trader/generate-daily-summary")
async def generate_daily_summary(target_date: str = None):
    """Generate execution quality summary for a specific date."""
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


@router.post("/auto-trader/reset-gates")
async def reset_gate_status():
    """Reset gate validation status."""
    trader = get_auto_trader()

    if not hasattr(trader, 'execution_tracker') or trader.execution_tracker is None:
        return {"error": "Execution quality tracking not available"}

    try:
        from datetime import date

        gates_dir = trader.execution_tracker.gates_dir
        status_file = gates_dir / "current_status.json"

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

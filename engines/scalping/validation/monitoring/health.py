"""
FastAPI health and monitoring endpoints for the scalping validator.

Provides:
  GET /health          - basic liveness check
  GET /metrics         - real-time rolling metrics
  GET /pipeline-status - per-component pipeline health
  GET /alerts          - recent alert history
  GET /score           - production readiness score
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Dict

from fastapi import FastAPI
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from monitoring.alert_manager import AlertManager
    from monitoring.metrics import MetricsCollector
    from scoring.scoring_engine import ScoringEngine


def create_health_app(
    metrics: "MetricsCollector",
    alert_manager: "AlertManager",
    pipeline_validator: Any,
    scoring_engine: "ScoringEngine",
) -> FastAPI:
    """Build and return a configured FastAPI application.

    Parameters
    ----------
    metrics:
        ``MetricsCollector`` instance for the ``/metrics`` endpoint.
    alert_manager:
        ``AlertManager`` instance for the ``/alerts`` endpoint.
    pipeline_validator:
        Any object exposing ``check_pipeline_health() -> dict``.
        May be ``None`` if no pipeline validator is available.
    scoring_engine:
        ``ScoringEngine`` instance for the ``/score`` endpoint.
    """
    app = FastAPI(
        title="Scalping Validator",
        version="1.0.0",
        description="Health and monitoring API for the scalping validation pipeline.",
    )

    # -- /health -------------------------------------------------------------

    @app.get("/health", tags=["Health"])
    async def health() -> Dict[str, Any]:
        """Basic liveness check."""
        return {
            "status": "ok",
            "pipeline": "scalping",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "uptime_sec": round(time.time() - _start_time, 2),
        }

    # -- /metrics ------------------------------------------------------------

    @app.get("/metrics", tags=["Monitoring"])
    async def metrics_endpoint() -> Dict[str, Any]:
        """Return real-time rolling-window metrics."""
        return metrics.get_metrics()

    # -- /pipeline-status ----------------------------------------------------

    @app.get("/pipeline-status", tags=["Monitoring"])
    async def pipeline_status() -> JSONResponse:
        """Full pipeline health with per-component status."""
        if pipeline_validator is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unavailable",
                    "message": "Pipeline validator not configured",
                },
            )

        try:
            report = pipeline_validator.check_pipeline_health()
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": str(exc),
                },
            )

        return JSONResponse(content=report)

    # -- /alerts -------------------------------------------------------------

    @app.get("/alerts", tags=["Monitoring"])
    async def alerts() -> Dict[str, Any]:
        """Recent alerts and summary counts."""
        return {
            "alerts": alert_manager.get_active_alerts(),
            "summary": alert_manager.get_alert_summary(),
        }

    # -- /score --------------------------------------------------------------

    @app.get("/score", tags=["Scoring"])
    async def score() -> Dict[str, Any]:
        """Calculate and return the production readiness score."""
        return scoring_engine.calculate_score()

    # Track startup time for uptime calculation
    _start_time = time.time()

    return app

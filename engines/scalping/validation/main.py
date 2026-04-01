"""
Scalping Pipeline Validation System

Wires together all validators, metrics collection, alerting, scoring,
and the health API into a single async runtime.

Usage:
    # Run with live Kafka
    python main.py

    # Run in simulation mode (generates fake data)
    python main.py --simulate

    # Run scoring only (one-shot, no server)
    python main.py --score-only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from typing import Any, Dict, List

import uvicorn

from config.settings import SETTINGS, SCALPING_TOPICS
from monitoring.alert_manager import AlertLevel, AlertManager
from monitoring.health import create_health_app
from monitoring.metrics import MetricsCollector
from scoring.scoring_engine import ScoringEngine
from validator.data_validator import DataValidator
from validator.kafka_consumer import ScalpingKafkaConsumer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Validation message handler
# ---------------------------------------------------------------------------


async def _make_message_handler(
    data_validator: DataValidator,
    metrics: MetricsCollector,
    alert_manager: AlertManager,
):
    """Return an async callback suitable for ``ScalpingKafkaConsumer.register_handler``."""

    async def handler(topic: str, message: Dict[str, Any]) -> None:
        size_bytes = len(json.dumps(message, default=str).encode("utf-8"))
        start = time.time()

        # Record metrics
        metrics.record_message(topic, size_bytes)

        # Estimate Kafka lag from message timestamp
        msg_ts_raw = message.get("timestamp")
        if msg_ts_raw:
            try:
                from validator.data_validator import DataValidator as _DV

                msg_ts = _DV._parse_ts(msg_ts_raw)
                if msg_ts is not None:
                    lag_ms = (time.time() * 1000) - msg_ts
                    metrics.record_kafka_lag(topic, max(0.0, lag_ms))
            except Exception:
                pass

        # Run data validation
        issues = await data_validator.validate(topic, message)

        # Record stage latency
        elapsed_ms = (time.time() - start) * 1000
        metrics.record_latency("data_validation", elapsed_ms)

        # Fire alerts for issues
        for issue in issues:
            level = {
                "CRITICAL": AlertLevel.CRITICAL,
                "WARNING": AlertLevel.WARNING,
            }.get(issue.severity, AlertLevel.INFO)

            await alert_manager.fire(
                level=level,
                category=issue.category,
                message=issue.message,
                component=topic,
                details=issue.details if hasattr(issue, "details") else {},
            )

    return handler


# ---------------------------------------------------------------------------
# Periodic health & scoring checks
# ---------------------------------------------------------------------------


async def _periodic_checks(
    scoring_engine: ScoringEngine,
    alert_manager: AlertManager,
    metrics: MetricsCollector,
    interval_sec: float = 5.0,
) -> None:
    """Run pipeline health and scoring checks on a fixed interval."""
    while True:
        await asyncio.sleep(interval_sec)
        try:
            score_report = scoring_engine.calculate_score()
            total = score_report.get("total_score", 0)
            status = score_report.get("status", "UNKNOWN")

            logger.info(
                "Pipeline score: %.1f / 100 (%s)", total, status
            )

            if status == "NOT_READY":
                await alert_manager.fire(
                    level=AlertLevel.WARNING,
                    category="pipeline_score",
                    message=f"Production readiness score {total:.1f}/100 ({status})",
                    component="scoring_engine",
                    details={"total_score": total, "status": status},
                )

            # Check for stale topics
            topic_metrics = metrics.get_metrics().get("topics", {})
            now = time.time()
            for topic_name, tm in topic_metrics.items():
                last_time = tm.get("last_message_time")
                if last_time is not None:
                    gap = now - last_time
                    if gap > SETTINGS.stale_data_threshold_sec:
                        await alert_manager.fire(
                            level=AlertLevel.WARNING,
                            category="stale_data",
                            message=f"No data on {topic_name} for {gap:.1f}s",
                            component=topic_name,
                        )

        except Exception:
            logger.exception("Error during periodic checks")


# ---------------------------------------------------------------------------
# Score-only mode
# ---------------------------------------------------------------------------


def _run_score_only() -> None:
    """One-shot: instantiate validators, compute score, print, and exit."""
    data_validator = DataValidator(SETTINGS)
    metrics = MetricsCollector(SETTINGS)
    scoring_engine = ScoringEngine(
        settings=SETTINGS,
        data_validator=data_validator,
        metrics=metrics,
    )
    report = scoring_engine.calculate_score()
    print(json.dumps(report, indent=2, default=str))


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def run_validation_system(simulate: bool = False) -> None:
    """Start the full validation system.

    1. Create all validators and support objects.
    2. Create metrics collector and alert manager.
    3. Create scoring engine.
    4. Create Kafka consumer and register handlers.
    5. Optionally start mock data generator.
    6. Start Kafka consumer.
    7. Start health API via uvicorn.
    8. Run periodic checks every ``health_check_interval_sec``.
    """
    # 1. Validators
    data_validator = DataValidator(SETTINGS)

    # 2. Monitoring
    metrics = MetricsCollector(SETTINGS)
    alert_manager = AlertManager(SETTINGS)

    # 3. Scoring
    scoring_engine = ScoringEngine(
        settings=SETTINGS,
        data_validator=data_validator,
        metrics=metrics,
    )

    # 4. Kafka consumer + handler registration
    consumer = ScalpingKafkaConsumer(SETTINGS)
    handler = await _make_message_handler(data_validator, metrics, alert_manager)
    for topic in SCALPING_TOPICS.values():
        consumer.register_handler(topic, handler)

    # 5. Optional mock data generator
    simulator_task = None
    if simulate:
        from simulator.mock_data_generator import MockDataGenerator

        generator = MockDataGenerator(SETTINGS.kafka_bootstrap_servers)
        simulator_task = asyncio.create_task(
            generator.generate_and_publish(duration_seconds=300, tick_interval_ms=500),
            name="mock-data-generator",
        )
        logger.info("Simulation mode enabled — generating mock data for 300s")

    # 6. Start Kafka consumer
    try:
        await consumer.start()
    except ConnectionError:
        logger.warning(
            "Could not connect to Kafka at %s. "
            "The health API will still start, but no messages will be consumed.",
            SETTINGS.kafka_bootstrap_servers,
        )

    # 7. Health API
    health_app = create_health_app(
        metrics=metrics,
        alert_manager=alert_manager,
        pipeline_validator=None,  # extend when pipeline validator is available
        scoring_engine=scoring_engine,
    )

    api_config = uvicorn.Config(
        health_app,
        host=SETTINGS.api_host,
        port=SETTINGS.api_port,
        log_level="info",
        access_log=False,
    )
    api_server = uvicorn.Server(api_config)

    # 8. Periodic checks
    checks_task = asyncio.create_task(
        _periodic_checks(
            scoring_engine,
            alert_manager,
            metrics,
            interval_sec=SETTINGS.health_check_interval_sec,
        ),
        name="periodic-checks",
    )

    # Graceful shutdown on SIGINT / SIGTERM
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows does not support add_signal_handler
            pass

    logger.info(
        "Scalping validation system running on http://%s:%d",
        SETTINGS.api_host,
        SETTINGS.api_port,
    )

    # Run API server and wait for shutdown
    api_task = asyncio.create_task(api_server.serve(), name="health-api")

    await shutdown_event.wait()

    # Cleanup
    logger.info("Shutting down...")
    checks_task.cancel()
    if simulator_task is not None:
        simulator_task.cancel()

    api_server.should_exit = True
    await consumer.stop()

    # Wait briefly for API to finish
    try:
        await asyncio.wait_for(api_task, timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass

    logger.info("Scalping validation system stopped")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _configure_logging()

    parser = argparse.ArgumentParser(
        description="Scalping Pipeline Validation System"
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Generate fake data via MockDataGenerator instead of reading live Kafka",
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="Calculate and print the production readiness score, then exit",
    )
    args = parser.parse_args()

    if args.score_only:
        _run_score_only()
        sys.exit(0)

    asyncio.run(run_validation_system(simulate=args.simulate))

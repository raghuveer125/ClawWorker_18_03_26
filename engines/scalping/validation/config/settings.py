"""
Validation system configuration — single source of truth.

Designed to be pipeline-agnostic: swap PIPELINE_NAME and TOPICS
to validate any pipeline.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List


# ── Kafka topic definitions ──────────────────────────────────────────────────
# Each topic carries JSON messages from the corresponding pipeline stage.

SCALPING_TOPICS = {
    "market_data":   "scalping.market_data",
    "option_chain":  "scalping.option_chain",
    "futures":       "scalping.futures",
    "analysis":      "scalping.analysis",
    "signals":       "scalping.signals",
    "trades":        "scalping.trades",
    "heartbeat":     "scalping.heartbeat",
}


# ── Pipeline component registry ─────────────────────────────────────────────
# Maps each component to its pipeline stage and expected Kafka topic.

SCALPING_COMPONENTS = {
    # DATA stage
    "DataFeed":          {"stage": "DATA",      "input_topic": None,              "output_topic": "scalping.market_data"},
    "OptionChain":       {"stage": "DATA",      "input_topic": None,              "output_topic": "scalping.option_chain"},
    "Futures":           {"stage": "DATA",      "input_topic": None,              "output_topic": "scalping.futures"},
    "LatencyGuardian":   {"stage": "DATA",      "input_topic": "scalping.market_data", "output_topic": "scalping.heartbeat"},
    # ANALYSIS stage
    "MarketRegime":      {"stage": "ANALYSIS",  "input_topic": "scalping.market_data", "output_topic": "scalping.analysis"},
    "Structure":         {"stage": "ANALYSIS",  "input_topic": "scalping.market_data", "output_topic": "scalping.analysis"},
    "Momentum":          {"stage": "ANALYSIS",  "input_topic": "scalping.futures",     "output_topic": "scalping.analysis"},
    "TrapDetector":      {"stage": "ANALYSIS",  "input_topic": "scalping.option_chain","output_topic": "scalping.analysis"},
    "StrikeDetector":    {"stage": "ANALYSIS",  "input_topic": "scalping.option_chain","output_topic": "scalping.analysis"},
    # QUALITY stage
    "SignalQuality":     {"stage": "QUALITY",   "input_topic": "scalping.analysis",    "output_topic": "scalping.signals"},
    # RISK stage
    "LiquidityMonitor":  {"stage": "RISK",      "input_topic": "scalping.signals",     "output_topic": "scalping.signals"},
    "RiskGuardian":      {"stage": "RISK",       "input_topic": "scalping.signals",     "output_topic": "scalping.signals"},
    "CorrelationGuard":  {"stage": "RISK",       "input_topic": "scalping.signals",     "output_topic": "scalping.signals"},
    # EXECUTION stage
    "Entry":             {"stage": "EXECUTION", "input_topic": "scalping.signals",     "output_topic": "scalping.trades"},
    "Exit":              {"stage": "EXECUTION", "input_topic": "scalping.trades",      "output_topic": "scalping.trades"},
    "PositionManager":   {"stage": "EXECUTION", "input_topic": "scalping.trades",      "output_topic": "scalping.trades"},
}

# ── Kafka topic schemas ─────────────────────────────────────────────────────
# Required fields per topic for schema validation.

TOPIC_SCHEMAS: Dict[str, List[str]] = {
    "scalping.market_data": [
        "symbol", "ltp", "bid", "ask", "volume", "timestamp", "source",
    ],
    "scalping.option_chain": [
        "symbol", "strike", "option_type", "ltp", "delta", "gamma",
        "theta", "vega", "oi", "volume", "bid", "ask", "timestamp",
    ],
    "scalping.futures": [
        "symbol", "ltp", "basis", "volume", "oi", "timestamp",
    ],
    "scalping.analysis": [
        "component", "signal_type", "confidence", "index", "timestamp",
    ],
    "scalping.signals": [
        "signal_id", "index", "side", "strike", "entry_price",
        "stop_loss", "target", "confidence", "timestamp",
    ],
    "scalping.trades": [
        "trade_id", "signal_id", "index", "side", "strike",
        "entry_price", "quantity", "status", "timestamp",
    ],
    "scalping.heartbeat": [
        "component", "status", "latency_ms", "timestamp",
    ],
}


@dataclass(frozen=True)
class Settings:
    """Immutable validation system settings."""

    # ── Identity ─────────────────────────────────────────────────────────
    pipeline_name: str = "scalping"

    # ── Kafka ────────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_consumer_group: str = "scalping-validator"
    kafka_consumer_timeout_ms: int = 1000

    # ── Validation thresholds ────────────────────────────────────────────
    max_timestamp_drift_ms: float = 2000.0      # max acceptable clock drift (2s for local Kafka)
    max_tick_gap_ms: float = 5000.0             # gap before "missing tick"
    max_execution_delay_ms: float = 500.0       # trade execution latency limit
    stale_data_threshold_sec: float = 15.0      # no data = stale
    pipeline_block_timeout_sec: float = 30.0    # component silent = blocked

    # ── Monitoring ───────────────────────────────────────────────────────
    metrics_window_sec: float = 60.0            # rolling window for rates
    health_check_interval_sec: float = 5.0
    alert_cooldown_sec: float = 60.0            # don't repeat same alert

    # ── Scoring weights (must sum to 100) ────────────────────────────────
    score_data_integrity: int = 25
    score_kafka_health: int = 20
    score_indicator_coverage: int = 15
    score_strategy_accuracy: int = 20
    score_trade_reliability: int = 10
    score_latency: int = 10

    # ── API ──────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8003

    # ── Alerts ───────────────────────────────────────────────────────────
    webhook_url: str = ""                       # optional external webhook
    log_file: str = "logs/scalping_validator.log"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            kafka_bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            kafka_consumer_group=os.getenv("VALIDATOR_CONSUMER_GROUP", "scalping-validator"),
            api_port=int(os.getenv("VALIDATOR_API_PORT", "8003")),
            webhook_url=os.getenv("VALIDATOR_WEBHOOK_URL", ""),
            log_file=os.getenv("VALIDATOR_LOG_FILE", "logs/scalping_validator.log"),
        )


# Singleton
SETTINGS = Settings.from_env()

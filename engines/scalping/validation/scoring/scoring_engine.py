"""
Production readiness scoring engine for the scalping pipeline.

Computes a 0--100 composite score across six weighted categories:

  1. Data Integrity  (default 25)
  2. Kafka Health     (default 20)
  3. Indicator Coverage (default 15)
  4. Strategy Accuracy  (default 20)
  5. Trade Reliability  (default 10)
  6. Latency            (default 10)

Each category is scored independently and combined into a single
readiness verdict: READY (>90), WARNING (75--90), NOT_READY (<75).
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from config.settings import SCALPING_COMPONENTS, SCALPING_TOPICS, Settings

# Maximum acceptable average latency (ms) before the latency score
# reaches zero.  Derived from settings.max_execution_delay_ms if
# available; otherwise defaults to 500 ms.
_DEFAULT_MAX_ACCEPTABLE_LATENCY_MS = 500.0


class ScoringEngine:
    """Calculate a production readiness score from validator reports and
    live metrics.

    All validator arguments are duck-typed: any object that exposes the
    expected ``get_report() -> dict`` (or equivalent) method works.
    Pass ``None`` for validators that are not yet available -- the
    engine will degrade gracefully and assign a score of 0 for that
    category.
    """

    def __init__(
        self,
        settings: Settings,
        data_validator: Any = None,
        indicator_validator: Any = None,
        strategy_validator: Any = None,
        trade_validator: Any = None,
        pipeline_validator: Any = None,
        metrics: Any = None,
    ) -> None:
        self._settings = settings
        self._data_validator = data_validator
        self._indicator_validator = indicator_validator
        self._strategy_validator = strategy_validator
        self._trade_validator = trade_validator
        self._pipeline_validator = pipeline_validator
        self._metrics = metrics

        self._max_latency_ms = getattr(
            settings,
            "max_execution_delay_ms",
            _DEFAULT_MAX_ACCEPTABLE_LATENCY_MS,
        )

    # -- Public API ---------------------------------------------------------

    def calculate_score(self) -> Dict[str, Any]:
        """Return the composite production readiness score."""
        categories: Dict[str, Dict[str, Any]] = {}

        categories["data_integrity"] = self._score_data_integrity()
        categories["kafka_health"] = self._score_kafka_health()
        categories["indicator_coverage"] = self._score_indicator_coverage()
        categories["strategy_accuracy"] = self._score_strategy_accuracy()
        categories["trade_reliability"] = self._score_trade_reliability()
        categories["latency"] = self._score_latency()

        total = sum(cat["score"] for cat in categories.values())
        total = round(total, 1)

        if total > 90:
            status = "READY"
        elif total >= 75:
            status = "WARNING"
        else:
            status = "NOT_READY"

        return {
            "total_score": total,
            "status": status,
            "categories": categories,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "pipeline": self._settings.pipeline_name,
        }

    # -- Category scorers ---------------------------------------------------

    def _score_data_integrity(self) -> Dict[str, Any]:
        max_score = self._settings.score_data_integrity
        details: Dict[str, Any] = {}

        if self._data_validator is None:
            return {"score": 0.0, "max": max_score, "details": {"reason": "validator_unavailable"}}

        report = self._safe_get_report(self._data_validator)
        total = report.get("total_messages", 0)
        issues_by_cat = report.get("issues_by_category", {})

        total_issues = sum(issues_by_cat.values())
        issue_rate = (total_issues / total) if total > 0 else 0.0
        score = round(max_score * max(0.0, 1.0 - issue_rate), 1)

        details.update({
            "total_messages": total,
            "total_issues": total_issues,
            "issue_rate": round(issue_rate, 4),
            "issues_by_category": issues_by_cat,
        })

        return {"score": score, "max": max_score, "details": details}

    def _score_kafka_health(self) -> Dict[str, Any]:
        max_score = self._settings.score_kafka_health
        details: Dict[str, Any] = {}
        total_topics = len(SCALPING_TOPICS)

        if self._metrics is None:
            return {"score": 0.0, "max": max_score, "details": {"reason": "metrics_unavailable"}}

        snapshot = self._metrics.get_metrics()
        topic_metrics = snapshot.get("topics", {})

        active_topics = 0
        total_lag_penalty = 0.0
        lag_count = 0

        for topic_name in SCALPING_TOPICS.values():
            tm = topic_metrics.get(topic_name, {})
            if tm.get("message_count", 0) > 0:
                active_topics += 1

        kafka_section = snapshot.get("kafka", {})
        for topic_name in SCALPING_TOPICS.values():
            lag_info = kafka_section.get(topic_name, {})
            lag_ms = lag_info.get("estimated_lag_ms")
            if lag_ms is not None:
                lag_count += 1
                # Penalise linearly: lag of 5000 ms => penalty of 1.0
                penalty = min(1.0, lag_ms / 5000.0)
                total_lag_penalty += penalty

        avg_lag_penalty = (total_lag_penalty / lag_count) if lag_count > 0 else 0.0
        topic_ratio = active_topics / total_topics if total_topics > 0 else 0.0
        score = round(max_score * topic_ratio * max(0.0, 1.0 - avg_lag_penalty), 1)

        details.update({
            "active_topics": active_topics,
            "total_topics": total_topics,
            "avg_lag_penalty": round(avg_lag_penalty, 4),
        })

        return {"score": score, "max": max_score, "details": details}

    def _score_indicator_coverage(self) -> Dict[str, Any]:
        max_score = self._settings.score_indicator_coverage
        total_components = len(SCALPING_COMPONENTS)

        if self._indicator_validator is None:
            return {
                "score": 0.0,
                "max": max_score,
                "details": {"reason": "validator_unavailable"},
            }

        report = self._safe_get_report(self._indicator_validator)
        components_seen = report.get("components_seen", 0)
        if isinstance(components_seen, (set, list)):
            components_seen = len(components_seen)

        ratio = (components_seen / total_components) if total_components > 0 else 0.0
        score = round(max_score * ratio, 1)

        return {
            "score": score,
            "max": max_score,
            "details": {
                "components_seen": components_seen,
                "total_components": total_components,
                "coverage_pct": round(ratio * 100, 1),
            },
        }

    def _score_strategy_accuracy(self) -> Dict[str, Any]:
        max_score = self._settings.score_strategy_accuracy

        if self._strategy_validator is None:
            return {
                "score": 0.0,
                "max": max_score,
                "details": {"reason": "validator_unavailable"},
            }

        report = self._safe_get_report(self._strategy_validator)
        accuracy_pct = report.get("accuracy_pct", 0.0)
        score = round(max_score * accuracy_pct / 100.0, 1)

        return {
            "score": score,
            "max": max_score,
            "details": {
                "accuracy_pct": round(accuracy_pct, 2),
            },
        }

    def _score_trade_reliability(self) -> Dict[str, Any]:
        max_score = self._settings.score_trade_reliability

        if self._trade_validator is None:
            return {
                "score": 0.0,
                "max": max_score,
                "details": {"reason": "validator_unavailable"},
            }

        report = self._safe_get_report(self._trade_validator)
        total_trades = report.get("total_trades", 0)
        total_issues = report.get("total_issues", 0)

        issue_rate = (total_issues / total_trades) if total_trades > 0 else 0.0
        score = round(max_score * max(0.0, 1.0 - issue_rate), 1)

        return {
            "score": score,
            "max": max_score,
            "details": {
                "total_trades": total_trades,
                "total_issues": total_issues,
                "issue_rate": round(issue_rate, 4),
            },
        }

    def _score_latency(self) -> Dict[str, Any]:
        max_score = self._settings.score_latency

        if self._metrics is None:
            return {
                "score": 0.0,
                "max": max_score,
                "details": {"reason": "metrics_unavailable"},
            }

        snapshot = self._metrics.get_metrics()
        stage_metrics = snapshot.get("stages", {})

        if not stage_metrics:
            return {
                "score": max_score,
                "max": max_score,
                "details": {"reason": "no_latency_data_yet", "assumed_healthy": True},
            }

        latencies = [
            s["avg_latency_ms"]
            for s in stage_metrics.values()
            if s.get("avg_latency_ms") is not None
        ]

        if not latencies:
            return {
                "score": max_score,
                "max": max_score,
                "details": {"reason": "no_latency_samples"},
            }

        avg_latency = sum(latencies) / len(latencies)
        score = round(
            max_score * max(0.0, 1.0 - avg_latency / self._max_latency_ms), 1
        )

        return {
            "score": score,
            "max": max_score,
            "details": {
                "avg_latency_ms": round(avg_latency, 2),
                "max_acceptable_ms": self._max_latency_ms,
                "stages_measured": len(latencies),
            },
        }

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _safe_get_report(validator: Any) -> Dict[str, Any]:
        """Call ``get_report()`` (or ``get_coverage_report()``) on *validator*."""
        for method_name in ("get_report", "get_coverage_report"):
            fn = getattr(validator, method_name, None)
            if fn is not None:
                try:
                    return fn()
                except Exception:
                    pass
        return {}

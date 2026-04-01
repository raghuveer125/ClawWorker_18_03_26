"""
Pipeline validator — monitors end-to-end pipeline health.

Tracks input/output flow through every component defined in
SCALPING_COMPONENTS, detects blocked stages, diagnoses root causes,
and reports throughput per stage.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from config.settings import SETTINGS, SCALPING_TOPICS, SCALPING_COMPONENTS, Settings
from validator.models import ValidationIssue

# ── Component health states ─────────────────────────────────────────────────

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
BLOCKED = "BLOCKED"

# ── Root-cause labels ───────────────────────────────────────────────────────

CAUSE_MISSING_KAFKA = "missing_kafka_data"
CAUSE_DEPENDENCY = "dependency_failure"
CAUSE_PROCESSING_DELAY = "processing_delay"
CAUSE_LOGIC_FAILURE = "logic_failure"

# ── Processing delay threshold (seconds) ────────────────────────────────────

_PROCESSING_DELAY_THRESHOLD_SEC: float = 30.0


@dataclass
class ComponentState:
    """Mutable tracking state for a single pipeline component."""

    name: str
    stage: str
    input_topic: Optional[str]
    output_topic: Optional[str]
    last_input_time: float = 0.0
    last_output_time: float = 0.0
    input_count: int = 0
    output_count: int = 0
    status: str = HEALTHY
    root_cause: Optional[str] = None


class PipelineValidator:
    """Validates pipeline connectivity and detects blocked components."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._block_timeout = settings.pipeline_block_timeout_sec

        # component_name → ComponentState
        self._components: Dict[str, ComponentState] = {}
        for name, meta in SCALPING_COMPONENTS.items():
            self._components[name] = ComponentState(
                name=name,
                stage=meta["stage"],
                input_topic=meta.get("input_topic"),
                output_topic=meta.get("output_topic"),
            )

        # topic → set of component names that use it as input
        self._input_topic_map: Dict[str, Set[str]] = defaultdict(set)
        # topic → set of component names that use it as output
        self._output_topic_map: Dict[str, Set[str]] = defaultdict(set)

        for name, meta in SCALPING_COMPONENTS.items():
            if meta.get("input_topic"):
                self._input_topic_map[meta["input_topic"]].add(name)
            if meta.get("output_topic"):
                self._output_topic_map[meta["output_topic"]].add(name)

        # Throughput counters: topic → list of monotonic timestamps
        self._topic_timestamps: Dict[str, List[float]] = defaultdict(list)

        # Accumulated issues
        self._issues: List[ValidationIssue] = []

    # ── Public API ──────────────────────────────────────────────────────────

    async def on_message(self, topic: str, message: dict) -> None:
        """Track input/output per component based on topic."""

        now = time.monotonic()

        # Record for throughput calculation
        ts_list = self._topic_timestamps[topic]
        ts_list.append(now)
        # Keep only last 120 s of timestamps (cap list size)
        cutoff = now - 120.0
        if len(ts_list) > 5000:
            self._topic_timestamps[topic] = [
                t for t in ts_list if t >= cutoff
            ]

        # Update components whose input_topic matches
        for comp_name in self._input_topic_map.get(topic, set()):
            state = self._components[comp_name]
            state.last_input_time = now
            state.input_count += 1

        # Update components whose output_topic matches
        for comp_name in self._output_topic_map.get(topic, set()):
            state = self._components[comp_name]
            state.last_output_time = now
            state.output_count += 1

    def check_pipeline_health(self) -> Dict[str, Any]:
        """Evaluate each component for blocking and return a summary."""

        now = time.monotonic()
        blocked_components: List[str] = []
        degraded_components: List[str] = []

        for name, state in self._components.items():
            status, cause = self._evaluate_component(state, now)
            state.status = status
            state.root_cause = cause

            if status == BLOCKED:
                blocked_components.append(name)
                self._issues.append(
                    ValidationIssue(
                        severity="CRITICAL",
                        category="pipeline",
                        topic=state.input_topic or "",
                        message=f"Component {name} is BLOCKED "
                                f"(root cause: {cause})",
                        timestamp=_iso_now(),
                        details={
                            "component": name,
                            "stage": state.stage,
                            "root_cause": cause,
                            "input_count": state.input_count,
                            "output_count": state.output_count,
                        },
                    )
                )
            elif status == DEGRADED:
                degraded_components.append(name)
                self._issues.append(
                    ValidationIssue(
                        severity="WARNING",
                        category="pipeline",
                        topic=state.output_topic or "",
                        message=f"Component {name} is DEGRADED "
                                f"(root cause: {cause})",
                        timestamp=_iso_now(),
                        details={
                            "component": name,
                            "stage": state.stage,
                            "root_cause": cause,
                            "output_lag_sec": round(
                                state.last_input_time - state.last_output_time, 2,
                            ) if state.last_input_time and state.last_output_time else None,
                        },
                    )
                )

        return {
            "healthy": len(self._components) - len(blocked_components) - len(degraded_components),
            "degraded": degraded_components,
            "blocked": blocked_components,
            "total_components": len(self._components),
        }

    def get_report(self) -> Dict[str, Any]:
        """Full pipeline status with per-stage flow visualization."""

        now = time.monotonic()
        self.check_pipeline_health()

        stages = self._build_stage_view()
        throughput = self._compute_throughput(now)

        return {
            "pipeline": self._settings.pipeline_name,
            "stages": stages,
            "throughput": throughput,
            "components": {
                name: {
                    "stage": s.stage,
                    "status": s.status,
                    "root_cause": s.root_cause,
                    "input_topic": s.input_topic,
                    "output_topic": s.output_topic,
                    "input_count": s.input_count,
                    "output_count": s.output_count,
                }
                for name, s in self._components.items()
            },
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "message": i.message,
                    "timestamp": i.timestamp,
                    "details": i.details,
                }
                for i in self._issues[-50:]
            ],
        }

    # ── Internal helpers ────────────────────────────────────────────────────

    def _evaluate_component(
        self, state: ComponentState, now: float,
    ) -> tuple:
        """Return (status, root_cause) for a single component."""

        # Data-source components (no input topic) are healthy if they produce
        if state.input_topic is None:
            if state.output_count == 0 and now > self._block_timeout:
                return BLOCKED, CAUSE_MISSING_KAFKA
            return HEALTHY, None

        has_input = state.input_count > 0
        has_output = state.output_count > 0
        input_age = (now - state.last_input_time) if state.last_input_time > 0 else float("inf")
        output_age = (now - state.last_output_time) if state.last_output_time > 0 else float("inf")

        # Case 1: no input at all — upstream problem
        if not has_input:
            # Check if an upstream component is also blocked
            if self._is_upstream_blocked(state):
                return BLOCKED, CAUSE_DEPENDENCY
            return BLOCKED, CAUSE_MISSING_KAFKA

        # Case 2: input present but no output for > timeout
        if has_input and not has_output:
            if input_age < self._block_timeout:
                # Just started — give it time
                return HEALTHY, None
            return BLOCKED, CAUSE_LOGIC_FAILURE

        # Case 3: both present — check staleness
        if has_input and has_output:
            output_lag = state.last_input_time - state.last_output_time
            if output_lag > _PROCESSING_DELAY_THRESHOLD_SEC:
                return DEGRADED, CAUSE_PROCESSING_DELAY

            if output_age > self._block_timeout and input_age < self._block_timeout:
                # Input is fresh but output is stale
                if self._is_upstream_blocked(state):
                    return BLOCKED, CAUSE_DEPENDENCY
                return BLOCKED, CAUSE_LOGIC_FAILURE

        return HEALTHY, None

    def _is_upstream_blocked(self, state: ComponentState) -> bool:
        """Check whether the component feeding *state* is itself blocked."""

        if state.input_topic is None:
            return False

        # Find components whose output_topic == this component's input_topic
        upstream_names = self._output_topic_map.get(state.input_topic, set())
        for name in upstream_names:
            upstream = self._components.get(name)
            if upstream is not None and upstream.status == BLOCKED:
                return True
        return False

    def _build_stage_view(self) -> List[Dict[str, Any]]:
        """Ordered list of stages with aggregated status."""

        # Preserve insertion order from SCALPING_COMPONENTS to get stage
        # ordering (DATA → ANALYSIS → QUALITY → RISK → EXECUTION).
        seen_stages: List[str] = []
        for meta in SCALPING_COMPONENTS.values():
            stage = meta["stage"]
            if stage not in seen_stages:
                seen_stages.append(stage)

        stages: List[Dict[str, Any]] = []
        for stage_name in seen_stages:
            members = [
                s for s in self._components.values()
                if s.stage == stage_name
            ]
            statuses = [m.status for m in members]

            if all(s == HEALTHY for s in statuses):
                stage_status = HEALTHY
            elif any(s == BLOCKED for s in statuses):
                stage_status = BLOCKED
            else:
                stage_status = DEGRADED

            stages.append({
                "stage": stage_name,
                "status": stage_status,
                "components": [
                    {
                        "name": m.name,
                        "status": m.status,
                        "root_cause": m.root_cause,
                        "input_count": m.input_count,
                        "output_count": m.output_count,
                    }
                    for m in members
                ],
            })

        return stages

    def _compute_throughput(self, now: float) -> Dict[str, float]:
        """Messages per second per topic over the last metrics window."""

        window = self._settings.metrics_window_sec
        cutoff = now - window
        result: Dict[str, float] = {}

        for topic, timestamps in self._topic_timestamps.items():
            recent = [t for t in timestamps if t >= cutoff]
            if window > 0:
                result[topic] = round(len(recent) / window, 2)
            else:
                result[topic] = 0.0

        return result


# ── Module-level utility ────────────────────────────────────────────────────

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

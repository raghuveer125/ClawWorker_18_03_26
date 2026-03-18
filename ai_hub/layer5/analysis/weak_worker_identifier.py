"""
Weak Worker Identifier - Identifies underperforming workers.

Analyzes worker performance to find:
- Low success rates
- Slow execution times
- High error rates
- Declining performance trends
"""

import time
import logging
from enum import Enum
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


class WeaknessType(Enum):
    """Types of worker weaknesses."""
    LOW_SUCCESS_RATE = "low_success_rate"
    SLOW_EXECUTION = "slow_execution"
    HIGH_ERROR_RATE = "high_error_rate"
    DECLINING_TREND = "declining_trend"
    FREQUENT_TIMEOUTS = "frequent_timeouts"
    RESOURCE_HUNGRY = "resource_hungry"


@dataclass
class WeaknessReport:
    """Report on worker weakness."""
    worker_id: str
    worker_type: str
    weakness_type: WeaknessType
    severity: float  # 0.0 to 1.0
    current_value: float
    threshold: float
    trend: str  # "improving", "stable", "declining"
    sample_size: int
    details: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class WorkerMetrics:
    """Performance metrics for a worker."""
    worker_id: str
    worker_type: str
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    timeout_count: int = 0
    total_time: float = 0.0
    error_types: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    history: List[Dict] = field(default_factory=list)  # Recent performance snapshots


class WeakWorkerIdentifier:
    """
    Identifies workers that need improvement.

    Features:
    - Success rate analysis
    - Execution time analysis
    - Error pattern detection
    - Trend analysis
    - Comparative benchmarking
    """

    AGENT_TYPE = "weak_worker_identifier"

    # Default thresholds
    SUCCESS_RATE_THRESHOLD = 0.7
    EXECUTION_TIME_THRESHOLD = 2.0  # multiplier vs average
    ERROR_RATE_THRESHOLD = 0.2
    MIN_SAMPLE_SIZE = 10
    TREND_WINDOW = 20  # tasks

    def __init__(self, synapse=None):
        self._synapse = synapse
        self._metrics: Dict[str, WorkerMetrics] = {}
        self._benchmarks: Dict[str, Dict] = {}  # worker_type -> avg metrics

    def record_task(
        self,
        worker_id: str,
        worker_type: str,
        success: bool,
        execution_time: float,
        error_type: Optional[str] = None,
        timed_out: bool = False,
    ):
        """Record task completion for analysis."""
        if worker_id not in self._metrics:
            self._metrics[worker_id] = WorkerMetrics(
                worker_id=worker_id,
                worker_type=worker_type,
            )

        m = self._metrics[worker_id]
        m.total_tasks += 1
        m.total_time += execution_time

        if success:
            m.successful_tasks += 1
        else:
            m.failed_tasks += 1
            if error_type:
                m.error_types[error_type] += 1

        if timed_out:
            m.timeout_count += 1

        # Record snapshot for trend analysis
        m.history.append({
            "success": success,
            "time": execution_time,
            "timestamp": time.time(),
        })
        # Keep last N
        if len(m.history) > 100:
            m.history = m.history[-100:]

        # Update benchmarks
        self._update_benchmark(worker_type, execution_time, success)

    def _update_benchmark(self, worker_type: str, exec_time: float, success: bool):
        """Update type benchmarks."""
        if worker_type not in self._benchmarks:
            self._benchmarks[worker_type] = {
                "total": 0,
                "success": 0,
                "total_time": 0.0,
            }

        b = self._benchmarks[worker_type]
        b["total"] += 1
        if success:
            b["success"] += 1
        b["total_time"] += exec_time

    def analyze_worker(self, worker_id: str) -> List[WeaknessReport]:
        """
        Analyze a worker for weaknesses.

        Returns:
            List of weakness reports
        """
        m = self._metrics.get(worker_id)
        if not m or m.total_tasks < self.MIN_SAMPLE_SIZE:
            return []

        reports = []

        # Check success rate
        success_rate = m.successful_tasks / m.total_tasks
        if success_rate < self.SUCCESS_RATE_THRESHOLD:
            reports.append(WeaknessReport(
                worker_id=worker_id,
                worker_type=m.worker_type,
                weakness_type=WeaknessType.LOW_SUCCESS_RATE,
                severity=1.0 - success_rate / self.SUCCESS_RATE_THRESHOLD,
                current_value=success_rate,
                threshold=self.SUCCESS_RATE_THRESHOLD,
                trend=self._calculate_trend(m, "success"),
                sample_size=m.total_tasks,
                recommendations=[
                    "Review task input validation",
                    "Check error patterns for root cause",
                    "Consider reducing task complexity",
                ],
            ))

        # Check execution time vs benchmark
        avg_time = m.total_time / m.total_tasks
        benchmark = self._benchmarks.get(m.worker_type, {})
        benchmark_avg = (
            benchmark.get("total_time", avg_time) /
            max(benchmark.get("total", 1), 1)
        )

        if avg_time > benchmark_avg * self.EXECUTION_TIME_THRESHOLD:
            reports.append(WeaknessReport(
                worker_id=worker_id,
                worker_type=m.worker_type,
                weakness_type=WeaknessType.SLOW_EXECUTION,
                severity=min((avg_time / benchmark_avg - 1) / 2, 1.0),
                current_value=avg_time,
                threshold=benchmark_avg * self.EXECUTION_TIME_THRESHOLD,
                trend=self._calculate_trend(m, "time"),
                sample_size=m.total_tasks,
                details={"benchmark_avg": benchmark_avg},
                recommendations=[
                    "Profile execution for bottlenecks",
                    "Check resource allocation",
                    "Consider task parallelization",
                ],
            ))

        # Check error rate
        error_rate = m.failed_tasks / m.total_tasks
        if error_rate > self.ERROR_RATE_THRESHOLD:
            top_errors = sorted(
                m.error_types.items(), key=lambda x: x[1], reverse=True
            )[:3]
            reports.append(WeaknessReport(
                worker_id=worker_id,
                worker_type=m.worker_type,
                weakness_type=WeaknessType.HIGH_ERROR_RATE,
                severity=min(error_rate / self.ERROR_RATE_THRESHOLD - 1, 1.0),
                current_value=error_rate,
                threshold=self.ERROR_RATE_THRESHOLD,
                trend=self._calculate_trend(m, "errors"),
                sample_size=m.total_tasks,
                details={"top_errors": top_errors},
                recommendations=[
                    f"Address top error: {top_errors[0][0] if top_errors else 'unknown'}",
                    "Improve error handling",
                    "Add input validation",
                ],
            ))

        # Check timeout rate
        timeout_rate = m.timeout_count / m.total_tasks
        if timeout_rate > 0.1:  # 10% timeout rate
            reports.append(WeaknessReport(
                worker_id=worker_id,
                worker_type=m.worker_type,
                weakness_type=WeaknessType.FREQUENT_TIMEOUTS,
                severity=min(timeout_rate * 5, 1.0),
                current_value=timeout_rate,
                threshold=0.1,
                trend="stable",
                sample_size=m.total_tasks,
                recommendations=[
                    "Increase timeout limits",
                    "Optimize slow operations",
                    "Add progress checkpoints",
                ],
            ))

        return reports

    def _calculate_trend(self, m: WorkerMetrics, metric: str) -> str:
        """Calculate trend for a metric."""
        if len(m.history) < self.TREND_WINDOW:
            return "stable"

        recent = m.history[-self.TREND_WINDOW // 2:]
        older = m.history[-self.TREND_WINDOW:-self.TREND_WINDOW // 2]

        if metric == "success":
            recent_rate = sum(1 for h in recent if h["success"]) / len(recent)
            older_rate = sum(1 for h in older if h["success"]) / len(older)
            diff = recent_rate - older_rate
        elif metric == "time":
            recent_avg = sum(h["time"] for h in recent) / len(recent)
            older_avg = sum(h["time"] for h in older) / len(older)
            diff = older_avg - recent_avg  # Lower is better
        else:
            return "stable"

        if diff > 0.1:
            return "improving"
        elif diff < -0.1:
            return "declining"
        return "stable"

    def analyze_all(self) -> List[WeaknessReport]:
        """Analyze all workers and return weakness reports."""
        all_reports = []
        for worker_id in self._metrics:
            reports = self.analyze_worker(worker_id)
            all_reports.extend(reports)

        # Sort by severity
        all_reports.sort(key=lambda r: r.severity, reverse=True)
        return all_reports

    def get_improvement_candidates(self, limit: int = 5) -> List[str]:
        """Get top workers needing improvement."""
        reports = self.analyze_all()

        # Group by worker
        by_worker = defaultdict(list)
        for r in reports:
            by_worker[r.worker_id].append(r)

        # Score workers by total severity
        scores = {
            wid: sum(r.severity for r in reps)
            for wid, reps in by_worker.items()
        }

        # Return top N
        sorted_workers = sorted(scores.keys(), key=lambda w: scores[w], reverse=True)
        return sorted_workers[:limit]

    def get_stats(self) -> Dict:
        """Get identifier statistics."""
        return {
            "workers_tracked": len(self._metrics),
            "total_tasks": sum(m.total_tasks for m in self._metrics.values()),
            "workers_with_issues": len(self.get_improvement_candidates(limit=100)),
        }

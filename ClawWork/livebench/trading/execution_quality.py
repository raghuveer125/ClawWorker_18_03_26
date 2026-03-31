"""
Execution Quality Tracking System
=================================
Captures and analyzes execution metrics for go-live validation:
- Slippage tracking (expected vs actual fill price)
- Latency measurement (order submit to fill)
- Fill rate analysis (complete, partial, rejected)
- API performance monitoring
- Automated gate validation for staged rollout

Storage Strategy:
- Individual trades: JSONL (append-only, time-series friendly)
- Daily summaries: JSON (quick access for dashboards)
- Gate status: JSON (current validation state)

Author: ClawWork Institutional Framework
Version: 1.0.0
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from enum import Enum
from collections import defaultdict
import statistics
import logging

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    """Order execution status"""
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class GateStatus(Enum):
    """Validation gate status"""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


@dataclass
class ExecutionMetrics:
    """Execution quality metrics for a single trade"""
    # Trade identification
    trade_id: str
    timestamp: str
    symbol: str
    mode: str  # paper or live

    # Price metrics
    signal_price: float           # Price when signal generated
    expected_price: float         # Price when order submitted
    actual_fill_price: float      # Actual fill price from broker
    slippage_amount: float = 0.0  # actual - expected (negative = favorable)
    slippage_pct: float = 0.0     # Slippage as percentage
    slippage_cost: float = 0.0    # Slippage in INR (slippage * quantity)

    # Timing metrics
    signal_time: Optional[str] = None      # When signal generated
    order_submit_time: Optional[str] = None  # When order sent to broker
    order_ack_time: Optional[str] = None     # When broker acknowledged
    fill_time: Optional[str] = None          # When order filled

    # Latency calculations (milliseconds)
    signal_to_submit_ms: Optional[int] = None
    submit_to_ack_ms: Optional[int] = None
    ack_to_fill_ms: Optional[int] = None
    total_latency_ms: Optional[int] = None
    api_response_time_ms: Optional[int] = None

    # Order status
    order_status: str = "unknown"
    order_quantity: int = 0
    filled_quantity: int = 0
    fill_rate_pct: float = 100.0
    rejection_reason: Optional[str] = None

    # Market context at execution
    spread_at_signal: Optional[float] = None
    spread_at_fill: Optional[float] = None
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None
    market_depth: Optional[int] = None

    # Trade outcome (filled after position closes)
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    outcome: Optional[str] = None  # WIN, LOSS, BREAKEVEN


@dataclass
class DailySummary:
    """Daily execution quality summary"""
    date: str
    mode: str  # paper or live

    # Trade counts
    total_orders: int = 0
    filled_orders: int = 0
    partial_fills: int = 0
    rejected_orders: int = 0
    timeout_orders: int = 0

    # Fill rate
    overall_fill_rate_pct: float = 100.0

    # Slippage statistics
    avg_slippage_pct: float = 0.0
    max_slippage_pct: float = 0.0
    min_slippage_pct: float = 0.0
    total_slippage_cost: float = 0.0
    favorable_slippage_count: int = 0
    adverse_slippage_count: int = 0

    # Latency statistics (milliseconds)
    avg_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    avg_api_response_ms: float = 0.0

    # P&L (if trades closed)
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    win_rate_pct: float = 0.0

    # System health
    api_errors: int = 0
    connection_issues: int = 0


@dataclass
class GateConfig:
    """Configuration for validation gates"""
    name: str
    duration_days: int
    min_trades: int
    max_slippage_pct: float
    max_avg_latency_ms: float
    min_fill_rate_pct: float
    max_rejection_rate_pct: float
    min_win_rate_pct: float
    max_drawdown_pct: float
    capital_pct: float  # % of total capital to use


@dataclass
class GateResult:
    """Result of a validation gate check"""
    gate_name: str
    status: str
    start_date: Optional[str]
    end_date: Optional[str]
    days_completed: int
    days_required: int
    trades_completed: int
    trades_required: int

    # Metric results
    avg_slippage_pct: float
    slippage_passed: bool
    avg_latency_ms: float
    latency_passed: bool
    fill_rate_pct: float
    fill_rate_passed: bool
    rejection_rate_pct: float
    rejection_passed: bool
    win_rate_pct: float
    win_rate_passed: bool
    max_drawdown_pct: float
    drawdown_passed: bool

    # Overall
    all_criteria_passed: bool
    failure_reasons: List[str] = field(default_factory=list)


class ExecutionQualityTracker:
    """
    Tracks and analyzes execution quality for go-live validation.

    Storage Layout:
        data/execution_quality/
        ├── trades/
        │   ├── 2026-03-01.jsonl      # Individual trade metrics
        │   ├── 2026-03-02.jsonl
        │   └── ...
        ├── summaries/
        │   ├── 2026-03-01.json       # Daily summary
        │   ├── 2026-03-02.json
        │   └── ...
        ├── gates/
        │   └── current_status.json   # Current gate validation status
        └── config.json               # Gate configurations
    """

    # Default gate configurations for staged rollout
    DEFAULT_GATES = [
        GateConfig(
            name="paper_validation",
            duration_days=10,
            min_trades=30,
            max_slippage_pct=1.0,  # Not applicable for paper
            max_avg_latency_ms=1000,  # Simulated
            min_fill_rate_pct=95.0,
            max_rejection_rate_pct=5.0,
            min_win_rate_pct=55.0,
            max_drawdown_pct=15.0,
            capital_pct=0.0  # Paper = no real capital
        ),
        GateConfig(
            name="micro_live",
            duration_days=20,
            min_trades=20,
            max_slippage_pct=0.5,
            max_avg_latency_ms=200,
            min_fill_rate_pct=98.0,
            max_rejection_rate_pct=2.0,
            min_win_rate_pct=50.0,
            max_drawdown_pct=10.0,
            capital_pct=20.0  # 20% of capital
        ),
        GateConfig(
            name="scale_up",
            duration_days=30,
            min_trades=50,
            max_slippage_pct=0.3,
            max_avg_latency_ms=150,
            min_fill_rate_pct=99.0,
            max_rejection_rate_pct=1.0,
            min_win_rate_pct=52.0,
            max_drawdown_pct=8.0,
            capital_pct=60.0  # 60% of capital
        ),
        GateConfig(
            name="full_capital",
            duration_days=0,  # Ongoing
            min_trades=0,
            max_slippage_pct=0.3,
            max_avg_latency_ms=150,
            min_fill_rate_pct=99.0,
            max_rejection_rate_pct=1.0,
            min_win_rate_pct=50.0,
            max_drawdown_pct=10.0,
            capital_pct=100.0  # Full capital
        )
    ]

    def __init__(self, data_dir: str = "data/execution_quality"):
        self.data_dir = Path(data_dir)
        self.trades_dir = self.data_dir / "trades"
        self.summaries_dir = self.data_dir / "summaries"
        self.gates_dir = self.data_dir / "gates"

        # Create directories
        self.trades_dir.mkdir(parents=True, exist_ok=True)
        self.summaries_dir.mkdir(parents=True, exist_ok=True)
        self.gates_dir.mkdir(parents=True, exist_ok=True)

        # Load or initialize gate config
        self.gates = self._load_gate_config()

        # In-memory cache for current day
        self._today_metrics: List[ExecutionMetrics] = []
        self._today_date: Optional[str] = None

        logger.info(f"ExecutionQualityTracker initialized at {self.data_dir}")

    def _load_gate_config(self) -> List[GateConfig]:
        """Load gate configurations from file or use defaults"""
        config_path = self.data_dir / "config.json"
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    data = json.load(f)
                    return [GateConfig(**g) for g in data.get('gates', [])]
            except Exception as e:
                logger.warning(f"Failed to load gate config: {e}, using defaults")

        # Save default config
        self._save_gate_config(self.DEFAULT_GATES)
        return self.DEFAULT_GATES

    def _save_gate_config(self, gates: List[GateConfig]):
        """Save gate configurations to file"""
        config_path = self.data_dir / "config.json"
        with open(config_path, 'w') as f:
            json.dump({'gates': [asdict(g) for g in gates]}, f, indent=2)

    # =========================================================================
    # TRADE RECORDING
    # =========================================================================

    def record_order_submit(
        self,
        trade_id: str,
        symbol: str,
        mode: str,
        signal_price: float,
        expected_price: float,
        quantity: int,
        signal_time: Optional[str] = None,
        spread: Optional[float] = None,
        bid: Optional[float] = None,
        ask: Optional[float] = None
    ) -> ExecutionMetrics:
        """
        Record when an order is submitted.
        Call this BEFORE sending order to broker.
        """
        now = datetime.now()
        metrics = ExecutionMetrics(
            trade_id=trade_id,
            timestamp=now.isoformat(),
            symbol=symbol,
            mode=mode,
            signal_price=signal_price,
            expected_price=expected_price,
            actual_fill_price=0.0,  # Will be updated on fill
            signal_time=signal_time or now.isoformat(),
            order_submit_time=now.isoformat(),
            order_status=OrderStatus.SUBMITTED.value,
            order_quantity=quantity,
            spread_at_signal=spread,
            bid_price=bid,
            ask_price=ask
        )

        # Calculate signal to submit latency if we have signal time
        if signal_time:
            try:
                signal_dt = datetime.fromisoformat(signal_time)
                metrics.signal_to_submit_ms = int((now - signal_dt).total_seconds() * 1000)
            except (ValueError, TypeError):
                pass  # intentional: skip latency calc on unparseable timestamp

        return metrics

    def record_order_ack(self, metrics: ExecutionMetrics, ack_time: Optional[str] = None) -> ExecutionMetrics:
        """Record when broker acknowledges order"""
        now = datetime.now()
        metrics.order_ack_time = ack_time or now.isoformat()

        # Calculate submit to ack latency
        if metrics.order_submit_time:
            try:
                submit_dt = datetime.fromisoformat(metrics.order_submit_time)
                ack_dt = datetime.fromisoformat(metrics.order_ack_time)
                metrics.submit_to_ack_ms = int((ack_dt - submit_dt).total_seconds() * 1000)
            except (ValueError, TypeError):
                pass  # intentional: skip latency calc on unparseable timestamp

        return metrics

    def record_order_fill(
        self,
        metrics: ExecutionMetrics,
        actual_fill_price: float,
        filled_quantity: int,
        fill_time: Optional[str] = None,
        api_response_time_ms: Optional[int] = None,
        spread_at_fill: Optional[float] = None
    ) -> ExecutionMetrics:
        """
        Record when order is filled.
        Call this AFTER receiving fill confirmation from broker.
        """
        now = datetime.now()
        metrics.fill_time = fill_time or now.isoformat()
        metrics.actual_fill_price = actual_fill_price
        metrics.filled_quantity = filled_quantity
        metrics.api_response_time_ms = api_response_time_ms
        metrics.spread_at_fill = spread_at_fill

        # Calculate slippage
        if metrics.expected_price > 0:
            metrics.slippage_amount = actual_fill_price - metrics.expected_price
            metrics.slippage_pct = (metrics.slippage_amount / metrics.expected_price) * 100
            metrics.slippage_cost = metrics.slippage_amount * filled_quantity

        # Calculate fill rate
        if metrics.order_quantity > 0:
            metrics.fill_rate_pct = (filled_quantity / metrics.order_quantity) * 100

        # Determine order status
        if filled_quantity == 0:
            metrics.order_status = OrderStatus.REJECTED.value
        elif filled_quantity < metrics.order_quantity:
            metrics.order_status = OrderStatus.PARTIAL.value
        else:
            metrics.order_status = OrderStatus.FILLED.value

        # Calculate ack to fill latency
        if metrics.order_ack_time:
            try:
                ack_dt = datetime.fromisoformat(metrics.order_ack_time)
                fill_dt = datetime.fromisoformat(metrics.fill_time)
                metrics.ack_to_fill_ms = int((fill_dt - ack_dt).total_seconds() * 1000)
            except (ValueError, TypeError):
                pass  # intentional: skip latency calc on unparseable timestamp

        # Calculate total latency
        if metrics.order_submit_time:
            try:
                submit_dt = datetime.fromisoformat(metrics.order_submit_time)
                fill_dt = datetime.fromisoformat(metrics.fill_time)
                metrics.total_latency_ms = int((fill_dt - submit_dt).total_seconds() * 1000)
            except (ValueError, TypeError):
                pass  # intentional: skip latency calc on unparseable timestamp

        # Save the metrics
        self._save_trade_metrics(metrics)

        return metrics

    def record_order_rejection(
        self,
        metrics: ExecutionMetrics,
        reason: str,
        api_response_time_ms: Optional[int] = None
    ) -> ExecutionMetrics:
        """Record order rejection"""
        metrics.order_status = OrderStatus.REJECTED.value
        metrics.rejection_reason = reason
        metrics.filled_quantity = 0
        metrics.fill_rate_pct = 0.0
        metrics.api_response_time_ms = api_response_time_ms
        metrics.fill_time = datetime.now().isoformat()

        self._save_trade_metrics(metrics)
        return metrics

    def record_trade_outcome(
        self,
        trade_id: str,
        pnl: float,
        pnl_pct: float,
        outcome: str
    ):
        """Update trade metrics with final P&L outcome"""
        # Find and update the trade in today's file or recent files
        today = date.today().isoformat()

        for days_back in range(7):  # Check last 7 days
            check_date = (date.today() - timedelta(days=days_back)).isoformat()
            file_path = self.trades_dir / f"{check_date}.jsonl"

            if not file_path.exists():
                continue

            # Read, update, and rewrite
            lines = []
            updated = False
            with open(file_path, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        if data.get('trade_id') == trade_id:
                            data['pnl'] = pnl
                            data['pnl_pct'] = pnl_pct
                            data['outcome'] = outcome
                            updated = True
                        lines.append(json.dumps(data))
                    except (json.JSONDecodeError, KeyError):
                        lines.append(line.strip())

            if updated:
                with open(file_path, 'w') as f:
                    f.write('\n'.join(lines) + '\n')
                logger.info(f"Updated trade {trade_id} with outcome: {outcome}, P&L: {pnl}")
                return

    def _save_trade_metrics(self, metrics: ExecutionMetrics):
        """Save trade metrics to JSONL file"""
        today = date.today().isoformat()
        file_path = self.trades_dir / f"{today}.jsonl"

        with open(file_path, 'a') as f:
            f.write(json.dumps(asdict(metrics)) + '\n')

        logger.debug(f"Saved execution metrics for trade {metrics.trade_id}")

    # =========================================================================
    # DAILY SUMMARIES
    # =========================================================================

    def generate_daily_summary(self, target_date: Optional[str] = None) -> Optional[DailySummary]:
        """Generate daily execution quality summary"""
        if target_date is None:
            target_date = date.today().isoformat()

        file_path = self.trades_dir / f"{target_date}.jsonl"
        if not file_path.exists():
            logger.warning(f"No trade data for {target_date}")
            return None

        # Load all trades for the day
        paper_metrics = []
        live_metrics = []

        with open(file_path, 'r') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    metrics = ExecutionMetrics(**data)
                    if metrics.mode == "paper":
                        paper_metrics.append(metrics)
                    else:
                        live_metrics.append(metrics)
                except Exception as e:
                    logger.warning(f"Failed to parse trade: {e}")

        summaries = []

        for mode, metrics_list in [("paper", paper_metrics), ("live", live_metrics)]:
            if not metrics_list:
                continue

            summary = self._calculate_summary(target_date, mode, metrics_list)
            summaries.append(summary)

            # Save summary
            summary_file = self.summaries_dir / f"{target_date}_{mode}.json"
            with open(summary_file, 'w') as f:
                json.dump(asdict(summary), f, indent=2)

        return summaries[0] if summaries else None

    def _calculate_summary(
        self,
        target_date: str,
        mode: str,
        metrics_list: List[ExecutionMetrics]
    ) -> DailySummary:
        """Calculate summary statistics from metrics list"""
        summary = DailySummary(date=target_date, mode=mode)

        if not metrics_list:
            return summary

        summary.total_orders = len(metrics_list)

        # Count by status
        summary.filled_orders = sum(1 for m in metrics_list if m.order_status == "filled")
        summary.partial_fills = sum(1 for m in metrics_list if m.order_status == "partial")
        summary.rejected_orders = sum(1 for m in metrics_list if m.order_status == "rejected")
        summary.timeout_orders = sum(1 for m in metrics_list if m.order_status == "timeout")

        # Fill rate
        total_ordered = sum(m.order_quantity for m in metrics_list)
        total_filled = sum(m.filled_quantity for m in metrics_list)
        summary.overall_fill_rate_pct = (total_filled / total_ordered * 100) if total_ordered > 0 else 100.0

        # Slippage statistics (only for filled orders)
        filled_metrics = [m for m in metrics_list if m.order_status in ["filled", "partial"]]
        if filled_metrics:
            slippages = [m.slippage_pct for m in filled_metrics]
            summary.avg_slippage_pct = statistics.mean(slippages)
            summary.max_slippage_pct = max(slippages)
            summary.min_slippage_pct = min(slippages)
            summary.total_slippage_cost = sum(m.slippage_cost for m in filled_metrics)
            summary.favorable_slippage_count = sum(1 for s in slippages if s < 0)
            summary.adverse_slippage_count = sum(1 for s in slippages if s > 0)

        # Latency statistics
        latencies = [m.total_latency_ms for m in metrics_list if m.total_latency_ms is not None]
        if latencies:
            summary.avg_latency_ms = statistics.mean(latencies)
            summary.max_latency_ms = max(latencies)
            summary.min_latency_ms = min(latencies)
            sorted_latencies = sorted(latencies)
            p95_idx = int(len(sorted_latencies) * 0.95)
            summary.p95_latency_ms = sorted_latencies[min(p95_idx, len(sorted_latencies) - 1)]

        api_times = [m.api_response_time_ms for m in metrics_list if m.api_response_time_ms is not None]
        if api_times:
            summary.avg_api_response_ms = statistics.mean(api_times)

        # P&L statistics
        trades_with_outcome = [m for m in metrics_list if m.outcome is not None]
        if trades_with_outcome:
            summary.total_pnl = sum(m.pnl or 0 for m in trades_with_outcome)
            summary.win_count = sum(1 for m in trades_with_outcome if m.outcome == "WIN")
            summary.loss_count = sum(1 for m in trades_with_outcome if m.outcome == "LOSS")
            total_closed = summary.win_count + summary.loss_count
            summary.win_rate_pct = (summary.win_count / total_closed * 100) if total_closed > 0 else 0.0

        return summary

    # =========================================================================
    # GATE VALIDATION
    # =========================================================================

    def validate_gate(self, gate_name: str) -> GateResult:
        """Validate a specific gate based on collected metrics"""
        # Find gate config
        gate_config = next((g for g in self.gates if g.name == gate_name), None)
        if not gate_config:
            raise ValueError(f"Unknown gate: {gate_name}")

        # Find gate index
        gate_idx = next(i for i, g in enumerate(self.gates) if g.name == gate_name)

        # Load gate status
        status_file = self.gates_dir / "current_status.json"
        gate_status = {}
        if status_file.exists():
            with open(status_file, 'r') as f:
                gate_status = json.load(f)

        # Check if previous gate is passed (if not first gate)
        if gate_idx > 0:
            prev_gate = self.gates[gate_idx - 1]
            prev_passed_key = f"{prev_gate.name}_passed"
            if not gate_status.get(prev_passed_key, False):
                # Previous gate not passed - this gate is pending
                return GateResult(
                    gate_name=gate_name,
                    status="pending",
                    start_date=None,
                    end_date=None,
                    days_completed=0,
                    days_required=gate_config.duration_days,
                    trades_completed=0,
                    trades_required=gate_config.min_trades,
                    avg_slippage_pct=0.0,
                    slippage_passed=False,
                    avg_latency_ms=0.0,
                    latency_passed=False,
                    fill_rate_pct=0.0,
                    fill_rate_passed=False,
                    rejection_rate_pct=0.0,
                    rejection_passed=False,
                    win_rate_pct=0.0,
                    win_rate_passed=False,
                    max_drawdown_pct=0.0,
                    drawdown_passed=False,
                    all_criteria_passed=False,
                    failure_reasons=["Previous gate not yet passed"]
                )

        # Get gate start date (only set if not exists)
        gate_key = f"{gate_name}_start"
        if gate_key not in gate_status:
            gate_status[gate_key] = date.today().isoformat()
            with open(status_file, 'w') as f:
                json.dump(gate_status, f, indent=2)

        start_date = date.fromisoformat(gate_status[gate_key])
        end_date = date.today()
        days_completed = (end_date - start_date).days + 1

        # Determine mode for this gate
        mode = "paper" if gate_name == "paper_validation" else "live"

        # Collect metrics for the gate period
        all_metrics = self._load_metrics_range(start_date, end_date, mode)

        # Calculate aggregate statistics
        trades_completed = len(all_metrics)

        # Slippage
        filled_metrics = [m for m in all_metrics if m.order_status in ["filled", "partial"]]
        avg_slippage = statistics.mean([m.slippage_pct for m in filled_metrics]) if filled_metrics else 0.0

        # Latency
        latencies = [m.total_latency_ms for m in all_metrics if m.total_latency_ms]
        avg_latency = statistics.mean(latencies) if latencies else 0.0

        # Fill rate
        total_ordered = sum(m.order_quantity for m in all_metrics)
        total_filled = sum(m.filled_quantity for m in all_metrics)
        fill_rate = (total_filled / total_ordered * 100) if total_ordered > 0 else 100.0

        # Rejection rate
        rejected = sum(1 for m in all_metrics if m.order_status == "rejected")
        rejection_rate = (rejected / len(all_metrics) * 100) if all_metrics else 0.0

        # Win rate
        trades_with_outcome = [m for m in all_metrics if m.outcome]
        wins = sum(1 for m in trades_with_outcome if m.outcome == "WIN")
        total_closed = len(trades_with_outcome)
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

        # Drawdown (simplified - max cumulative loss)
        cumulative_pnl = 0.0
        max_drawdown = 0.0
        peak_pnl = 0.0
        for m in sorted(all_metrics, key=lambda x: x.timestamp):
            if m.pnl is not None:
                cumulative_pnl += m.pnl
                peak_pnl = max(peak_pnl, cumulative_pnl)
                drawdown = peak_pnl - cumulative_pnl
                max_drawdown = max(max_drawdown, drawdown)

        # Calculate drawdown percentage (relative to capital)
        capital = 5000.0  # Should be from config
        drawdown_pct = (max_drawdown / capital * 100) if capital > 0 else 0.0

        # Check each criterion
        slippage_passed = avg_slippage <= gate_config.max_slippage_pct
        latency_passed = avg_latency <= gate_config.max_avg_latency_ms
        fill_rate_passed = fill_rate >= gate_config.min_fill_rate_pct
        rejection_passed = rejection_rate <= gate_config.max_rejection_rate_pct
        win_rate_passed = win_rate >= gate_config.min_win_rate_pct
        drawdown_passed = drawdown_pct <= gate_config.max_drawdown_pct

        # Duration and trade count checks
        duration_passed = days_completed >= gate_config.duration_days
        trades_passed = trades_completed >= gate_config.min_trades

        # Compile failure reasons
        failure_reasons = []
        if not duration_passed:
            failure_reasons.append(f"Duration: {days_completed}/{gate_config.duration_days} days")
        if not trades_passed:
            failure_reasons.append(f"Trades: {trades_completed}/{gate_config.min_trades}")
        if not slippage_passed:
            failure_reasons.append(f"Slippage: {avg_slippage:.2f}% > {gate_config.max_slippage_pct}%")
        if not latency_passed:
            failure_reasons.append(f"Latency: {avg_latency:.0f}ms > {gate_config.max_avg_latency_ms}ms")
        if not fill_rate_passed:
            failure_reasons.append(f"Fill rate: {fill_rate:.1f}% < {gate_config.min_fill_rate_pct}%")
        if not rejection_passed:
            failure_reasons.append(f"Rejection: {rejection_rate:.1f}% > {gate_config.max_rejection_rate_pct}%")
        if not win_rate_passed:
            failure_reasons.append(f"Win rate: {win_rate:.1f}% < {gate_config.min_win_rate_pct}%")
        if not drawdown_passed:
            failure_reasons.append(f"Drawdown: {drawdown_pct:.1f}% > {gate_config.max_drawdown_pct}%")

        all_passed = len(failure_reasons) == 0

        # Determine status
        if all_passed:
            status = GateStatus.PASSED.value
        elif duration_passed and trades_passed:
            status = GateStatus.FAILED.value
        else:
            status = GateStatus.IN_PROGRESS.value

        result = GateResult(
            gate_name=gate_name,
            status=status,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat() if all_passed else None,
            days_completed=days_completed,
            days_required=gate_config.duration_days,
            trades_completed=trades_completed,
            trades_required=gate_config.min_trades,
            avg_slippage_pct=avg_slippage,
            slippage_passed=slippage_passed,
            avg_latency_ms=avg_latency,
            latency_passed=latency_passed,
            fill_rate_pct=fill_rate,
            fill_rate_passed=fill_rate_passed,
            rejection_rate_pct=rejection_rate,
            rejection_passed=rejection_passed,
            win_rate_pct=win_rate,
            win_rate_passed=win_rate_passed,
            max_drawdown_pct=drawdown_pct,
            drawdown_passed=drawdown_passed,
            all_criteria_passed=all_passed,
            failure_reasons=failure_reasons
        )

        # Save result
        result_file = self.gates_dir / f"{gate_name}_result.json"
        with open(result_file, 'w') as f:
            json.dump(asdict(result), f, indent=2)

        # Mark gate as passed if all criteria met
        if all_passed:
            passed_key = f"{gate_name}_passed"
            gate_status[passed_key] = True
            gate_status[f"{gate_name}_passed_date"] = date.today().isoformat()
            with open(status_file, 'w') as f:
                json.dump(gate_status, f, indent=2)
            logger.info(f"Gate {gate_name} PASSED! Ready for next stage.")

        return result

    def _load_metrics_range(
        self,
        start_date: date,
        end_date: date,
        mode: str
    ) -> List[ExecutionMetrics]:
        """Load all metrics for a date range"""
        all_metrics = []
        current = start_date

        while current <= end_date:
            file_path = self.trades_dir / f"{current.isoformat()}.jsonl"
            if file_path.exists():
                with open(file_path, 'r') as f:
                    for line in f:
                        try:
                            data = json.loads(line.strip())
                            if data.get('mode') == mode:
                                all_metrics.append(ExecutionMetrics(**data))
                        except (json.JSONDecodeError, KeyError, TypeError):
                            pass  # intentional: skip malformed JSONL lines
            current += timedelta(days=1)

        return all_metrics

    def get_all_gate_status(self) -> Dict[str, GateResult]:
        """Get status of all gates - only validates up to current gate"""
        results = {}
        current_gate_found = False

        for i, gate in enumerate(self.gates):
            if current_gate_found:
                # Gates after current one are pending (not started)
                results[gate.name] = GateResult(
                    gate_name=gate.name,
                    status="pending",
                    start_date=None,
                    end_date=None,
                    days_completed=0,
                    days_required=gate.duration_days,
                    trades_completed=0,
                    trades_required=gate.min_trades,
                    avg_slippage_pct=0.0,
                    slippage_passed=False,
                    avg_latency_ms=0.0,
                    latency_passed=False,
                    fill_rate_pct=0.0,
                    fill_rate_passed=False,
                    rejection_rate_pct=0.0,
                    rejection_passed=False,
                    win_rate_pct=0.0,
                    win_rate_passed=False,
                    max_drawdown_pct=0.0,
                    drawdown_passed=False,
                    all_criteria_passed=False,
                    failure_reasons=["Previous gate not yet passed"]
                )
            else:
                try:
                    result = self.validate_gate(gate.name)
                    results[gate.name] = result
                    # If this gate is not passed, it's the current gate
                    if result.status != GateStatus.PASSED.value:
                        current_gate_found = True
                except Exception as e:
                    logger.error(f"Failed to validate gate {gate.name}: {e}")
                    current_gate_found = True  # Stop at failed validation

        return results

    def get_current_gate(self) -> Optional[str]:
        """Determine which gate we should be on based on progression"""
        for gate in self.gates:
            # Check if gate start exists - if not, this is the current gate
            status_file = self.gates_dir / "current_status.json"
            gate_status = {}
            if status_file.exists():
                with open(status_file, 'r') as f:
                    gate_status = json.load(f)

            gate_key = f"{gate.name}_passed"
            if not gate_status.get(gate_key, False):
                return gate.name

        return self.gates[-1].name  # All passed, on final gate

    # =========================================================================
    # REPORTING
    # =========================================================================

    def get_execution_quality_report(self, days: int = 10) -> Dict[str, Any]:
        """Generate execution quality report for API/dashboard"""
        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)

        paper_metrics = self._load_metrics_range(start_date, end_date, "paper")
        live_metrics = self._load_metrics_range(start_date, end_date, "live")

        def calculate_stats(metrics: List[ExecutionMetrics]) -> Dict:
            if not metrics:
                return {"total_trades": 0}

            filled = [m for m in metrics if m.order_status in ["filled", "partial"]]
            latencies = [m.total_latency_ms for m in metrics if m.total_latency_ms]

            return {
                "total_trades": len(metrics),
                "filled_orders": len(filled),
                "rejected_orders": sum(1 for m in metrics if m.order_status == "rejected"),
                "avg_slippage_pct": statistics.mean([m.slippage_pct for m in filled]) if filled else 0,
                "max_slippage_pct": max([m.slippage_pct for m in filled]) if filled else 0,
                "total_slippage_cost": sum(m.slippage_cost for m in filled),
                "avg_latency_ms": statistics.mean(latencies) if latencies else 0,
                "max_latency_ms": max(latencies) if latencies else 0,
                "fill_rate_pct": (sum(m.filled_quantity for m in metrics) /
                                  sum(m.order_quantity for m in metrics) * 100)
                                  if sum(m.order_quantity for m in metrics) > 0 else 100,
                "total_pnl": sum(m.pnl or 0 for m in metrics if m.pnl),
                "win_count": sum(1 for m in metrics if m.outcome == "WIN"),
                "loss_count": sum(1 for m in metrics if m.outcome == "LOSS"),
            }

        current_gate = self.get_current_gate()
        gate_status = self.get_all_gate_status()

        return {
            "period_days": days,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "paper": calculate_stats(paper_metrics),
            "live": calculate_stats(live_metrics),
            "current_gate": current_gate,
            "gates": {name: asdict(result) for name, result in gate_status.items()},
            "generated_at": datetime.now().isoformat()
        }

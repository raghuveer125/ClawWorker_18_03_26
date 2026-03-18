"""
Model Drift Detection System
==============================
Institutional-grade model governance with:
- Live vs backtest performance divergence monitoring
- Feature distribution drift detection (PSI, KL-divergence)
- Data quality and integrity validation
- Regime misclassification tracking
- Bot staleness detection and decay alerts
- Automated model quarantine and rehabilitation

Author: ClawWork Institutional Framework
Version: 1.0.0
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any, Set
from collections import defaultdict, deque
import math
import statistics
import logging

logger = logging.getLogger(__name__)


class DriftSeverity(Enum):
    """Severity levels for drift detection"""
    NONE = "none"           # No significant drift
    MINOR = "minor"         # Acceptable drift, monitor
    MODERATE = "moderate"   # Concerning, reduce exposure
    SEVERE = "severe"       # Critical, quarantine bot
    CRITICAL = "critical"   # Emergency, halt trading


class ModelStatus(Enum):
    """Model operational status"""
    HEALTHY = "healthy"
    MONITORING = "monitoring"
    DEGRADED = "degraded"
    QUARANTINED = "quarantined"
    SUSPENDED = "suspended"


class DriftType(Enum):
    """Types of drift detected"""
    PERFORMANCE = "performance"    # Live vs backtest divergence
    FEATURE = "feature"           # Input distribution shift
    CONCEPT = "concept"           # Target relationship change
    DATA = "data"                 # Data quality issues
    REGIME = "regime"             # Regime misclassification


@dataclass
class DriftAlert:
    """Single drift alert"""
    drift_type: DriftType
    severity: DriftSeverity
    metric_name: str
    expected_value: float
    actual_value: float
    deviation_pct: float
    timestamp: datetime = field(default_factory=datetime.now)
    bot_name: Optional[str] = None
    details: str = ""


@dataclass
class ModelHealth:
    """Comprehensive model health assessment"""
    bot_name: str
    status: ModelStatus
    overall_score: float          # 0-100 health score
    performance_drift: float      # Live vs backtest gap
    feature_drift: float          # PSI score
    regime_accuracy: float        # Regime prediction accuracy
    data_quality: float           # Data integrity score
    staleness_days: int          # Days since meaningful update
    recent_alerts: List[DriftAlert] = field(default_factory=list)
    recommendation: str = ""


@dataclass
class BacktestBaseline:
    """Backtest performance baseline for comparison"""
    bot_name: str
    win_rate: float
    avg_return: float
    sharpe_ratio: float
    max_drawdown: float
    profit_factor: float
    avg_trades_per_day: float
    avg_holding_time: float       # In minutes
    regime_performance: Dict[str, float] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class FeatureDistribution:
    """Feature distribution snapshot for drift detection"""
    feature_name: str
    mean: float
    std: float
    min_val: float
    max_val: float
    percentiles: Dict[int, float] = field(default_factory=dict)  # 10, 25, 50, 75, 90
    histogram: List[int] = field(default_factory=list)           # Bin counts
    sample_size: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


class ModelDriftDetector:
    """
    Institutional-grade model drift detection and governance.

    Features:
    - Real-time performance divergence monitoring
    - Feature drift detection using PSI and KL-divergence
    - Data quality validation with integrity checks
    - Regime misclassification tracking
    - Automated quarantine and rehabilitation
    - Model staleness detection
    """

    # Thresholds for drift detection
    PERFORMANCE_THRESHOLDS = {
        DriftSeverity.MINOR: 0.10,      # 10% deviation
        DriftSeverity.MODERATE: 0.20,    # 20% deviation
        DriftSeverity.SEVERE: 0.35,      # 35% deviation
        DriftSeverity.CRITICAL: 0.50,    # 50% deviation
    }

    PSI_THRESHOLDS = {
        DriftSeverity.MINOR: 0.10,       # PSI < 0.10 acceptable
        DriftSeverity.MODERATE: 0.15,    # PSI 0.10-0.15 needs monitoring
        DriftSeverity.SEVERE: 0.25,      # PSI 0.15-0.25 significant
        DriftSeverity.CRITICAL: 0.40,    # PSI > 0.25 major shift
    }

    def __init__(
        self,
        monitoring_window: int = 100,      # Trades to compare
        feature_window: int = 500,          # Data points for feature distribution
        regime_window: int = 50,            # Regime predictions to evaluate
        auto_quarantine: bool = True,
        quarantine_threshold: float = 0.35, # Performance drift threshold
        rehabilitation_days: int = 5,       # Days in quarantine before review
    ):
        self.monitoring_window = monitoring_window
        self.feature_window = feature_window
        self.regime_window = regime_window
        self.auto_quarantine = auto_quarantine
        self.quarantine_threshold = quarantine_threshold
        self.rehabilitation_days = rehabilitation_days

        # Baselines from backtesting
        self.baselines: Dict[str, BacktestBaseline] = {}

        # Live performance tracking
        self.live_trades: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=monitoring_window)
        )

        # Feature distributions (baseline and live)
        self.baseline_features: Dict[str, Dict[str, FeatureDistribution]] = {}
        self.live_features: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=feature_window))
        )

        # Regime predictions tracking
        self.regime_predictions: deque = deque(maxlen=regime_window)
        self.regime_actuals: deque = deque(maxlen=regime_window)

        # Model status tracking
        self.model_status: Dict[str, ModelStatus] = {}
        self.quarantine_start: Dict[str, datetime] = {}

        # Alert history
        self.alerts: List[DriftAlert] = []
        self.alert_history: Dict[str, List[DriftAlert]] = defaultdict(list)

        # Data quality tracking
        self.data_quality_scores: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )

        logger.info("Model Drift Detector initialized")

    def register_baseline(
        self,
        bot_name: str,
        backtest_results: Dict,
        feature_distributions: Optional[Dict] = None,
    ):
        """Register backtest baseline for a bot"""
        baseline = BacktestBaseline(
            bot_name=bot_name,
            win_rate=backtest_results.get("win_rate", 0.5),
            avg_return=backtest_results.get("avg_return", 0.0),
            sharpe_ratio=backtest_results.get("sharpe_ratio", 0.0),
            max_drawdown=backtest_results.get("max_drawdown", 0.0),
            profit_factor=backtest_results.get("profit_factor", 1.0),
            avg_trades_per_day=backtest_results.get("avg_trades_per_day", 5),
            avg_holding_time=backtest_results.get("avg_holding_time", 30),
            regime_performance=backtest_results.get("regime_performance", {}),
        )

        self.baselines[bot_name] = baseline
        self.model_status[bot_name] = ModelStatus.HEALTHY

        # Register feature distributions if provided
        if feature_distributions:
            self.baseline_features[bot_name] = {}
            for name, dist_data in feature_distributions.items():
                self.baseline_features[bot_name][name] = FeatureDistribution(
                    feature_name=name,
                    mean=dist_data.get("mean", 0),
                    std=dist_data.get("std", 1),
                    min_val=dist_data.get("min", 0),
                    max_val=dist_data.get("max", 1),
                    percentiles=dist_data.get("percentiles", {}),
                    histogram=dist_data.get("histogram", []),
                    sample_size=dist_data.get("sample_size", 0),
                )

        logger.info(f"Registered baseline for {bot_name}: WR={baseline.win_rate:.1%}, PF={baseline.profit_factor:.2f}")

    def record_trade(self, bot_name: str, trade: Dict):
        """Record a completed trade for drift monitoring"""
        self.live_trades[bot_name].append({
            "pnl": trade.get("pnl", 0),
            "pnl_pct": trade.get("pnl_pct", 0),
            "holding_time": trade.get("holding_time", 0),
            "regime": trade.get("regime", "UNKNOWN"),
            "entry_time": trade.get("entry_time", datetime.now()),
            "exit_time": trade.get("exit_time", datetime.now()),
            "direction": trade.get("direction", "LONG"),
        })

        # Check for drift after each trade
        self._calculate_performance_drift(bot_name)

    def record_features(self, bot_name: str, features: Dict):
        """Record feature values for distribution tracking"""
        for name, value in features.items():
            if isinstance(value, (int, float)):
                self.live_features[bot_name][name].append(value)

    def record_regime_prediction(self, predicted: str, actual: str):
        """Record regime prediction for accuracy tracking"""
        self.regime_predictions.append(predicted)
        self.regime_actuals.append(actual)

    def record_data_quality(self, bot_name: str, quality_score: float):
        """Record data quality score (0-1)"""
        self.data_quality_scores[bot_name].append(quality_score)

    def check_model_health(self, bot_name: str) -> ModelHealth:
        """Comprehensive health check for a specific model/bot"""
        # Get baseline
        baseline = self.baselines.get(bot_name)
        if not baseline:
            return ModelHealth(
                bot_name=bot_name,
                status=ModelStatus.MONITORING,
                overall_score=50,
                performance_drift=0,
                feature_drift=0,
                regime_accuracy=0.5,
                data_quality=0.5,
                staleness_days=0,
                recommendation="No baseline registered. Monitor closely.",
            )

        # Calculate performance drift
        perf_drift = self._calculate_performance_drift(bot_name)

        # Calculate feature drift (PSI)
        feature_drift = self._calculate_feature_drift(bot_name)

        # Calculate regime accuracy
        regime_acc = self._calculate_regime_accuracy()

        # Calculate data quality
        data_qual = self._calculate_data_quality(bot_name)

        # Calculate staleness
        staleness = self._calculate_staleness(bot_name)

        # Get recent alerts
        recent_alerts = [
            a for a in self.alert_history.get(bot_name, [])
            if datetime.now() - a.timestamp < timedelta(days=1)
        ]

        # Calculate overall score
        overall_score = self._calculate_overall_score(
            perf_drift, feature_drift, regime_acc, data_qual, staleness
        )

        # Determine status
        status = self._determine_status(overall_score, perf_drift, recent_alerts)

        # Generate recommendation
        recommendation = self._generate_recommendation(
            status, perf_drift, feature_drift, regime_acc, data_qual
        )

        return ModelHealth(
            bot_name=bot_name,
            status=status,
            overall_score=overall_score,
            performance_drift=perf_drift,
            feature_drift=feature_drift,
            regime_accuracy=regime_acc,
            data_quality=data_qual,
            staleness_days=staleness,
            recent_alerts=recent_alerts,
            recommendation=recommendation,
        )

    def should_allow_trade(self, bot_name: str) -> Tuple[bool, str]:
        """Check if bot should be allowed to trade"""
        status = self.model_status.get(bot_name, ModelStatus.HEALTHY)

        if status == ModelStatus.QUARANTINED:
            return False, f"Bot {bot_name} is QUARANTINED due to drift"

        if status == ModelStatus.SUSPENDED:
            return False, f"Bot {bot_name} is SUSPENDED"

        health = self.check_model_health(bot_name)

        if health.overall_score < 30:
            if self.auto_quarantine:
                self._quarantine_bot(bot_name, "Critical health score")
            return False, f"Bot {bot_name} health critical: {health.overall_score:.0f}/100"

        if health.overall_score < 50:
            return True, f"Bot {bot_name} degraded: {health.overall_score:.0f}/100 - reduce size"

        return True, f"Bot {bot_name} healthy: {health.overall_score:.0f}/100"

    def _calculate_performance_drift(self, bot_name: str) -> float:
        """Calculate performance drift from baseline"""
        baseline = self.baselines.get(bot_name)
        trades = list(self.live_trades.get(bot_name, []))

        if not baseline or len(trades) < 20:
            return 0.0

        # Calculate live metrics
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] < 0]

        live_win_rate = len(wins) / len(trades) if trades else 0.5
        live_avg_return = sum(t["pnl_pct"] for t in trades) / len(trades) if trades else 0

        # Calculate deviations
        wr_deviation = abs(live_win_rate - baseline.win_rate) / baseline.win_rate if baseline.win_rate > 0 else 0
        ret_deviation = abs(live_avg_return - baseline.avg_return) / abs(baseline.avg_return) if baseline.avg_return != 0 else 0

        # Weighted combination
        drift = wr_deviation * 0.6 + ret_deviation * 0.4

        # Check for alerts
        severity = self._get_drift_severity(drift, self.PERFORMANCE_THRESHOLDS)
        if severity != DriftSeverity.NONE:
            alert = DriftAlert(
                drift_type=DriftType.PERFORMANCE,
                severity=severity,
                metric_name="combined_performance",
                expected_value=baseline.win_rate,
                actual_value=live_win_rate,
                deviation_pct=drift,
                bot_name=bot_name,
                details=f"WR drift: {wr_deviation:.1%}, Return drift: {ret_deviation:.1%}",
            )
            self._add_alert(alert)

            if severity in (DriftSeverity.SEVERE, DriftSeverity.CRITICAL) and self.auto_quarantine:
                self._quarantine_bot(bot_name, f"Performance drift: {drift:.1%}")

        return drift

    def _calculate_feature_drift(self, bot_name: str) -> float:
        """Calculate feature drift using Population Stability Index (PSI)"""
        baseline_features = self.baseline_features.get(bot_name, {})
        live_features = self.live_features.get(bot_name, {})

        if not baseline_features or not live_features:
            return 0.0

        psi_scores = []

        for feature_name, baseline_dist in baseline_features.items():
            live_values = list(live_features.get(feature_name, []))

            if len(live_values) < 50:
                continue

            # Calculate PSI
            psi = self._calculate_psi(baseline_dist, live_values)
            psi_scores.append(psi)

            # Check for alerts
            severity = self._get_drift_severity(psi, self.PSI_THRESHOLDS)
            if severity != DriftSeverity.NONE:
                alert = DriftAlert(
                    drift_type=DriftType.FEATURE,
                    severity=severity,
                    metric_name=feature_name,
                    expected_value=baseline_dist.mean,
                    actual_value=statistics.mean(live_values) if live_values else 0,
                    deviation_pct=psi,
                    bot_name=bot_name,
                    details=f"PSI: {psi:.3f}",
                )
                self._add_alert(alert)

        return statistics.mean(psi_scores) if psi_scores else 0.0

    def _calculate_psi(self, baseline: FeatureDistribution, live_values: List[float]) -> float:
        """Calculate Population Stability Index"""
        if not live_values or baseline.sample_size == 0:
            return 0.0

        # Create bins based on baseline percentiles
        bins = [baseline.min_val]
        for p in [10, 25, 50, 75, 90]:
            if p in baseline.percentiles:
                bins.append(baseline.percentiles[p])
        bins.append(baseline.max_val)
        bins = sorted(set(bins))

        if len(bins) < 2:
            return 0.0

        # Calculate expected proportions from baseline histogram
        if baseline.histogram:
            expected = [h / sum(baseline.histogram) for h in baseline.histogram]
        else:
            expected = [1.0 / (len(bins) - 1)] * (len(bins) - 1)

        # Calculate actual proportions from live values
        actual_counts = [0] * (len(bins) - 1)
        for val in live_values:
            for i in range(len(bins) - 1):
                if bins[i] <= val < bins[i + 1]:
                    actual_counts[i] += 1
                    break
            else:
                if val >= bins[-1]:
                    actual_counts[-1] += 1

        total_live = sum(actual_counts)
        if total_live == 0:
            return 0.0

        actual = [c / total_live for c in actual_counts]

        # Calculate PSI
        psi = 0.0
        for i in range(len(expected)):
            exp = max(expected[i], 0.0001)  # Avoid division by zero
            act = max(actual[i], 0.0001)
            psi += (act - exp) * math.log(act / exp)

        return abs(psi)

    def _calculate_regime_accuracy(self) -> float:
        """Calculate regime prediction accuracy"""
        if len(self.regime_predictions) < 10:
            return 0.5  # Default neutral

        correct = sum(
            1 for p, a in zip(self.regime_predictions, self.regime_actuals)
            if p == a
        )

        return correct / len(self.regime_predictions)

    def _calculate_data_quality(self, bot_name: str) -> float:
        """Calculate average data quality score"""
        scores = list(self.data_quality_scores.get(bot_name, []))
        return statistics.mean(scores) if scores else 0.8

    def _calculate_staleness(self, bot_name: str) -> int:
        """Calculate days since last meaningful activity"""
        trades = list(self.live_trades.get(bot_name, []))

        if not trades:
            return 999  # No trades recorded

        last_trade = max(trades, key=lambda t: t.get("exit_time", datetime.min))
        last_time = last_trade.get("exit_time", datetime.now())

        return (datetime.now() - last_time).days

    def _calculate_overall_score(
        self,
        perf_drift: float,
        feature_drift: float,
        regime_acc: float,
        data_qual: float,
        staleness: int,
    ) -> float:
        """Calculate overall health score (0-100)"""
        scores = []

        # Performance drift score (lower is better)
        perf_score = max(0, 100 - perf_drift * 200)  # 50% drift = 0 score
        scores.append(perf_score * 0.35)

        # Feature drift score (PSI, lower is better)
        feature_score = max(0, 100 - feature_drift * 250)  # 0.4 PSI = 0 score
        scores.append(feature_score * 0.20)

        # Regime accuracy score
        regime_score = regime_acc * 100
        scores.append(regime_score * 0.20)

        # Data quality score
        data_score = data_qual * 100
        scores.append(data_score * 0.15)

        # Staleness penalty
        staleness_score = max(0, 100 - staleness * 5)  # 20 days = 0 score
        scores.append(staleness_score * 0.10)

        return sum(scores)

    def _determine_status(
        self,
        overall_score: float,
        perf_drift: float,
        recent_alerts: List[DriftAlert],
    ) -> ModelStatus:
        """Determine model operational status"""
        # Check if already quarantined
        if self.model_status.get("bot_name") == ModelStatus.QUARANTINED:
            return ModelStatus.QUARANTINED

        # Critical alerts
        critical_alerts = [a for a in recent_alerts if a.severity == DriftSeverity.CRITICAL]
        if critical_alerts:
            return ModelStatus.SUSPENDED

        severe_alerts = [a for a in recent_alerts if a.severity == DriftSeverity.SEVERE]
        if len(severe_alerts) >= 2:
            return ModelStatus.QUARANTINED

        if overall_score < 30:
            return ModelStatus.SUSPENDED

        if overall_score < 50 or perf_drift > 0.25:
            return ModelStatus.DEGRADED

        if overall_score < 70:
            return ModelStatus.MONITORING

        return ModelStatus.HEALTHY

    def _generate_recommendation(
        self,
        status: ModelStatus,
        perf_drift: float,
        feature_drift: float,
        regime_acc: float,
        data_qual: float,
    ) -> str:
        """Generate actionable recommendation"""
        issues = []

        if perf_drift > 0.20:
            issues.append(f"Performance drift {perf_drift:.0%} - consider parameter re-optimization")

        if feature_drift > 0.15:
            issues.append(f"Feature drift PSI={feature_drift:.2f} - market conditions changed")

        if regime_acc < 0.6:
            issues.append(f"Regime accuracy {regime_acc:.0%} - recalibrate regime detection")

        if data_qual < 0.7:
            issues.append(f"Data quality {data_qual:.0%} - investigate data source")

        if status == ModelStatus.HEALTHY:
            return "Model performing within expectations. Continue monitoring."

        if status == ModelStatus.MONITORING:
            return f"Minor concerns: {'; '.join(issues)}. Increase monitoring frequency."

        if status == ModelStatus.DEGRADED:
            return f"Degraded performance: {'; '.join(issues)}. Reduce position sizes by 50%."

        if status == ModelStatus.QUARANTINED:
            return f"Model quarantined: {'; '.join(issues)}. Manual review required."

        return f"Model suspended: {'; '.join(issues)}. Do not trade."

    def _get_drift_severity(
        self,
        value: float,
        thresholds: Dict[DriftSeverity, float],
    ) -> DriftSeverity:
        """Determine severity level from thresholds"""
        if value >= thresholds[DriftSeverity.CRITICAL]:
            return DriftSeverity.CRITICAL
        if value >= thresholds[DriftSeverity.SEVERE]:
            return DriftSeverity.SEVERE
        if value >= thresholds[DriftSeverity.MODERATE]:
            return DriftSeverity.MODERATE
        if value >= thresholds[DriftSeverity.MINOR]:
            return DriftSeverity.MINOR
        return DriftSeverity.NONE

    def _add_alert(self, alert: DriftAlert):
        """Add alert to history"""
        self.alerts.append(alert)
        if alert.bot_name:
            self.alert_history[alert.bot_name].append(alert)

        # Keep last 1000 alerts
        if len(self.alerts) > 1000:
            self.alerts = self.alerts[-500:]

        logger.warning(f"DRIFT ALERT [{alert.severity.value}]: {alert.bot_name} - {alert.drift_type.value}: {alert.details}")

    def _quarantine_bot(self, bot_name: str, reason: str):
        """Put bot in quarantine"""
        self.model_status[bot_name] = ModelStatus.QUARANTINED
        self.quarantine_start[bot_name] = datetime.now()

        logger.error(f"BOT QUARANTINED: {bot_name} - {reason}")

    def check_rehabilitation(self, bot_name: str) -> bool:
        """Check if quarantined bot can be rehabilitated"""
        if self.model_status.get(bot_name) != ModelStatus.QUARANTINED:
            return False

        start = self.quarantine_start.get(bot_name)
        if not start:
            return False

        days_in_quarantine = (datetime.now() - start).days

        if days_in_quarantine < self.rehabilitation_days:
            return False

        # Check recent health
        health = self.check_model_health(bot_name)

        if health.overall_score > 60:
            self.model_status[bot_name] = ModelStatus.MONITORING
            del self.quarantine_start[bot_name]
            logger.info(f"Bot {bot_name} rehabilitated from quarantine")
            return True

        return False

    def get_drift_report(self) -> Dict:
        """Generate comprehensive drift report"""
        report = {
            "timestamp": datetime.now().isoformat(),
            "models": {},
            "alerts_24h": [],
            "quarantined": [],
        }

        # Model health
        for bot_name in self.baselines:
            health = self.check_model_health(bot_name)
            report["models"][bot_name] = {
                "status": health.status.value,
                "score": health.overall_score,
                "performance_drift": health.performance_drift,
                "feature_drift": health.feature_drift,
                "regime_accuracy": health.regime_accuracy,
                "data_quality": health.data_quality,
                "recommendation": health.recommendation,
            }

        # Recent alerts
        cutoff = datetime.now() - timedelta(hours=24)
        report["alerts_24h"] = [
            {
                "type": a.drift_type.value,
                "severity": a.severity.value,
                "bot": a.bot_name,
                "metric": a.metric_name,
                "details": a.details,
                "time": a.timestamp.isoformat(),
            }
            for a in self.alerts if a.timestamp > cutoff
        ]

        # Quarantined models
        report["quarantined"] = [
            bot for bot, status in self.model_status.items()
            if status == ModelStatus.QUARANTINED
        ]

        return report

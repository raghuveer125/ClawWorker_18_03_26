"""
Meta Layer Agents - High-level strategy allocation and correlation.

Agents:
14. MetaAllocatorAgent - Regime-based strategy selection
15. CorrelationGuardAgent - Prevent overlapping/correlated trades

Uses LLM Debate for regime transition decisions and correlation assessment.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# Use local base module
from ..base import BaseBot, BotContext, BotResult, BotStatus
from ..config import ScalpingConfig

# Import debate integration
try:
    from ..debate_integration import debate_analysis, check_debate_available
    HAS_DEBATE = True
except ImportError:
    HAS_DEBATE = False


@dataclass
class StrategyAllocation:
    """Allocation decision for a strategy."""
    strategy: str
    regime: str
    allocation_pct: float
    reason: str
    confidence: float


@dataclass
class CorrelationAlert:
    """Alert for correlated positions."""
    positions: List[str]
    correlation: float
    risk_level: str  # low, medium, high
    recommendation: str


class MetaAllocatorAgent(BaseBot):
    """
    Agent 14: Meta Allocator Agent

    Chooses strategy based on regime:
    - Trending market → breakout scalping
    - Range market → mean-reversion
    - Volatility expansion → gamma scalping

    Uses LLM debate for regime transition decisions.
    """

    BOT_TYPE = "meta_allocator"
    REQUIRES_LLM = False  # DISABLED - debate causes latency in execution path

    # Strategy-regime mapping
    REGIME_STRATEGIES = {
        "trending_bullish": {
            "primary": "breakout_scalping",
            "allocation": 0.7,
            "secondary": "momentum_ce",
            "sec_allocation": 0.3,
        },
        "trending_bearish": {
            "primary": "breakdown_scalping",
            "allocation": 0.7,
            "secondary": "momentum_pe",
            "sec_allocation": 0.3,
        },
        "high_volatility_range": {
            "primary": "gamma_scalping",
            "allocation": 0.6,
            "secondary": "mean_reversion",
            "sec_allocation": 0.4,
        },
        "low_volatility_range": {
            "primary": "mean_reversion",
            "allocation": 0.8,
            "secondary": "theta_decay",
            "sec_allocation": 0.2,
        },
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_regime: Dict[str, str] = {}
        self._regime_confidence: Dict[str, float] = {}

    def get_description(self) -> str:
        return "Regime-based strategy allocation"

    async def execute(self, context: BotContext) -> BotResult:
        """Allocate strategies based on current regime."""
        structures = context.data.get("market_structure", {})
        config = context.data.get("config", ScalpingConfig())

        allocations = []
        regime_changes = []

        for symbol, structure in structures.items():
            regime = structure.get("trend", "unknown")
            confidence = structure.get("confidence", 0.5)

            # Detect regime change
            if symbol in self._last_regime and self._last_regime[symbol] != regime:
                regime_changes.append({
                    "symbol": symbol,
                    "from": self._last_regime[symbol],
                    "to": regime,
                    "confidence": confidence,
                })

            self._last_regime[symbol] = regime
            self._regime_confidence[symbol] = confidence

            # Get strategy allocation for regime
            allocation = self._get_allocation(symbol, regime, confidence)
            if allocation:
                allocations.append(allocation)

        context.data["strategy_allocations"] = allocations

        # Skip debate in execution path - causes latency
        # context.data["transition_decision"] = None

        # Emit regime change event
        for change in regime_changes:
            await self._emit_event("regime_transition", change)

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "allocations": [a.__dict__ for a in allocations],
                "regime_changes": regime_changes,
                "current_regimes": {s: self._last_regime.get(s, "unknown") for s in structures},
            },
            metrics={
                "symbols_allocated": len(allocations),
                "regime_changes": len(regime_changes),
            },
        )

    def _get_allocation(
        self, symbol: str, regime: str, confidence: float
    ) -> Optional[StrategyAllocation]:
        """Get strategy allocation for a regime."""
        # Map simple regime to detailed regime
        regime_key = None
        if "bullish" in regime:
            regime_key = "trending_bullish"
        elif "bearish" in regime:
            regime_key = "trending_bearish"
        elif "high_volatility" in regime or "volatile" in regime:
            regime_key = "high_volatility_range"
        elif "low_volatility" in regime or "range" in regime:
            regime_key = "low_volatility_range"

        if not regime_key or regime_key not in self.REGIME_STRATEGIES:
            return StrategyAllocation(
                strategy="neutral",
                regime=regime,
                allocation_pct=0.5,
                reason="Unknown regime - using neutral allocation",
                confidence=0.3,
            )

        regime_config = self.REGIME_STRATEGIES[regime_key]

        return StrategyAllocation(
            strategy=regime_config["primary"],
            regime=regime,
            allocation_pct=regime_config["allocation"] * confidence,
            reason=f"Regime {regime_key} favors {regime_config['primary']}",
            confidence=confidence,
        )

    async def _debate_transition(
        self, changes: List[Dict], context: BotContext
    ) -> Optional[Dict]:
        """Use LLM Debate to validate regime transition decisions."""
        if not HAS_DEBATE:
            return None

        transition_results = {}

        for change in changes:
            try:
                is_valid, reason, result = await debate_analysis(
                    analysis_type="regime",
                    context={
                        "symbol": change["symbol"],
                        "from_regime": change["from"],
                        "to_regime": change["to"],
                        "confidence": change["confidence"],
                        "question": "Should we adjust strategy for this regime change?",
                        "considerations": [
                            "Is the regime change significant or noise?",
                            "Risk of whipsaw if regime reverts?",
                            "Current position implications?",
                        ],
                    }
                )

                transition_results[change["symbol"]] = {
                    "valid": is_valid,
                    "reason": reason,
                    "confidence": result.confidence if result else 0,
                    "action": "adjust" if is_valid else "wait",
                }

            except Exception as e:
                transition_results[change["symbol"]] = {"error": str(e)}

        return transition_results


class CorrelationGuardAgent(BaseBot):
    """
    Agent 15: Correlation Guard Agent

    Prevents:
    - Overlapping trades across strikes
    - Correlated positions in same direction
    - Excessive exposure to single index

    Reduces exposure when multiple strategies align.
    """

    BOT_TYPE = "correlation_guard"
    REQUIRES_LLM = False

    def __init__(self, max_correlation: float = 0.7, **kwargs):
        super().__init__(**kwargs)
        self.max_correlation = max_correlation

    def get_description(self) -> str:
        return "Prevents correlated and overlapping positions"

    async def execute(self, context: BotContext) -> BotResult:
        """Check for correlated positions and reduce exposure."""
        positions = context.data.get("positions", [])
        pending_orders = context.data.get("pending_orders", [])
        candidate_signals = context.data.get("liquidity_filtered_selections", [])
        config = context.data.get("config", ScalpingConfig())

        alerts = []
        blocked_orders = []
        blocked_signal_keys = []
        signal_penalties: Dict[str, Dict[str, Any]] = {}
        exposure_adjustments = []

        # Check existing position correlation
        position_alerts = self._check_position_correlation(positions)
        alerts.extend(position_alerts)

        # Check if pending orders would increase correlation
        for order in pending_orders:
            order_alert = self._check_order_correlation(order, positions)
            if order_alert:
                alerts.append(order_alert)
                if order_alert.risk_level == "high":
                    blocked_orders.append(order.order_id)

        accepted_batch_signals: List[Dict[str, Any]] = []
        for signal in candidate_signals if isinstance(candidate_signals, list) else []:
            batch_alert = self._check_batch_signal_correlation(signal, accepted_batch_signals)
            if batch_alert:
                alerts.append(batch_alert)
                self._record_signal_penalty(signal_penalties, signal, batch_alert, config)
            signal_alert = self._check_signal_correlation(signal, positions)
            if signal_alert:
                alerts.append(signal_alert)
                self._record_signal_penalty(signal_penalties, signal, signal_alert, config)
            accepted_batch_signals.append(signal)

        # Check index concentration
        concentration_alerts = self._check_index_concentration(positions, config)
        alerts.extend(concentration_alerts)

        # Calculate exposure adjustments
        if alerts:
            adjustments = self._calculate_adjustments(alerts, positions)
            exposure_adjustments.extend(adjustments)

        context.data["correlation_alerts"] = alerts
        context.data["correlation_blocked_orders"] = blocked_orders
        context.data["correlation_blocked_signal_keys"] = blocked_signal_keys
        context.data["correlation_signal_penalties"] = signal_penalties
        context.data["exposure_adjustments"] = exposure_adjustments

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "alerts": [a.__dict__ for a in alerts],
                "blocked_orders": blocked_orders,
                "blocked_signal_keys": blocked_signal_keys,
                "signal_penalties": signal_penalties,
                "adjustments": exposure_adjustments,
            },
            metrics={
                "alerts_count": len(alerts),
                "high_risk_alerts": len([a for a in alerts if a.risk_level == "high"]),
                "orders_blocked": len(blocked_orders),
                "signals_adjusted": len(signal_penalties),
            },
            warnings=[a.recommendation for a in alerts if a.risk_level == "high"],
        )

    def _check_position_correlation(self, positions: List) -> List[CorrelationAlert]:
        """Check correlation between existing positions."""
        alerts = []
        open_positions = [p for p in positions if p.status != "closed"]

        # Group by index
        index_positions = {}
        for pos in open_positions:
            index = self._get_index_from_symbol(pos.symbol)
            if index not in index_positions:
                index_positions[index] = []
            index_positions[index].append(pos)

        # Check for same-direction positions on same index
        for index, pos_list in index_positions.items():
            ce_positions = [p for p in pos_list if p.option_type == "CE"]
            pe_positions = [p for p in pos_list if p.option_type == "PE"]

            # Multiple CE positions
            if len(ce_positions) > 1:
                correlation = self._calculate_strike_correlation(ce_positions)
                if correlation >= self.max_correlation:
                    alerts.append(CorrelationAlert(
                        positions=[p.position_id for p in ce_positions],
                        correlation=correlation,
                        risk_level="high" if correlation > 0.85 else "medium",
                        recommendation=f"Consider closing one CE position on {index}",
                    ))

            # Multiple PE positions
            if len(pe_positions) > 1:
                correlation = self._calculate_strike_correlation(pe_positions)
                if correlation >= self.max_correlation:
                    alerts.append(CorrelationAlert(
                        positions=[p.position_id for p in pe_positions],
                        correlation=correlation,
                        risk_level="high" if correlation > 0.85 else "medium",
                        recommendation=f"Consider closing one PE position on {index}",
                    ))

        return alerts

    def _check_order_correlation(self, order, positions: List) -> Optional[CorrelationAlert]:
        """Check if a pending order would create correlation."""
        # Find existing positions in same direction on same index
        index = self._get_index_from_symbol(order.symbol)

        same_direction = [
            p for p in positions
            if p.status != "closed"
            and self._get_index_from_symbol(p.symbol) == index
            and p.option_type == order.option_type
        ]

        if same_direction:
            # Calculate potential correlation
            strike_diff = min(abs(order.strike - p.strike) for p in same_direction)
            cluster_window = self._strike_cluster_window(order.symbol)
            correlation = max(0.0, 1.0 - (strike_diff / max(cluster_window, 1)))
            clustered = strike_diff <= cluster_window

            if clustered or correlation >= self.max_correlation:
                return CorrelationAlert(
                    positions=[order.order_id] + [p.position_id for p in same_direction],
                    correlation=correlation,
                    risk_level="high" if clustered or correlation > 0.85 else "medium",
                    recommendation="Order would create highly correlated position",
                )

        return None

    def _check_signal_correlation(self, signal: Dict[str, Any], positions: List) -> Optional[CorrelationAlert]:
        symbol = str(signal.get("symbol", ""))
        strike = int(float(signal.get("strike", 0) or 0))
        option_type = str(signal.get("option_type", signal.get("side", ""))).upper()
        expiry = str(signal.get("expiry", ""))
        same_direction = [
            p for p in positions
            if p.status != "closed"
            and self._get_index_from_symbol(p.symbol) == self._get_index_from_symbol(symbol)
            and p.option_type == option_type
        ]
        if not same_direction:
            return None

        strike_diff = min(abs(strike - p.strike) for p in same_direction)
        cluster_window = self._strike_cluster_window(symbol)
        correlation = max(0.0, 1.0 - (strike_diff / max(cluster_window, 1)))
        clustered = strike_diff <= cluster_window
        same_expiry_cluster = expiry and any(str(getattr(p, "expiry", "")) == expiry for p in same_direction)
        if clustered or correlation >= self.max_correlation or same_expiry_cluster:
            return CorrelationAlert(
                positions=[self._signal_key_from_signal(signal)] + [p.position_id for p in same_direction],
                correlation=max(correlation, 0.9 if same_expiry_cluster else correlation),
                risk_level="high" if same_expiry_cluster or clustered or correlation > 0.85 else "medium",
                recommendation="Signal would create clustered same-direction exposure",
            )
        return None

    def _check_batch_signal_correlation(
        self,
        signal: Dict[str, Any],
        accepted_signals: List[Dict[str, Any]],
    ) -> Optional[CorrelationAlert]:
        symbol = str(signal.get("symbol", ""))
        strike = int(float(signal.get("strike", 0) or 0))
        option_type = str(signal.get("option_type", signal.get("side", ""))).upper()
        expiry = str(signal.get("expiry", ""))
        same_direction = [
            existing for existing in accepted_signals
            if self._get_index_from_symbol(str(existing.get("symbol", ""))) == self._get_index_from_symbol(symbol)
            and str(existing.get("option_type", existing.get("side", ""))).upper() == option_type
        ]
        if not same_direction:
            return None

        strike_diff = min(abs(strike - int(float(existing.get("strike", 0) or 0))) for existing in same_direction)
        cluster_window = self._strike_cluster_window(symbol)
        correlation = max(0.0, 1.0 - (strike_diff / max(cluster_window, 1)))
        same_expiry_cluster = expiry and any(str(existing.get("expiry", "")) == expiry for existing in same_direction)
        if strike_diff <= cluster_window or same_expiry_cluster:
            return CorrelationAlert(
                positions=[self._signal_key_from_signal(signal)] + [self._signal_key_from_signal(existing) for existing in same_direction],
                correlation=max(correlation, 0.9 if same_expiry_cluster else correlation),
                risk_level="high",
                recommendation="Signal batch would create clustered same-direction exposure",
            )
        return None

    def _check_index_concentration(
        self, positions: List, config: ScalpingConfig
    ) -> List[CorrelationAlert]:
        """Check for excessive concentration in one index."""
        alerts = []
        open_positions = [p for p in positions if p.status != "closed"]

        if not open_positions:
            return alerts

        # Calculate exposure per index
        index_exposure = {}
        total_exposure = 0

        for pos in open_positions:
            index = self._get_index_from_symbol(pos.symbol)
            exposure = pos.quantity * pos.entry_price

            if index not in index_exposure:
                index_exposure[index] = 0
            index_exposure[index] += exposure
            total_exposure += exposure

        # Check concentration
        if total_exposure > 0:
            for index, exposure in index_exposure.items():
                concentration = exposure / total_exposure

                if concentration > 0.7:  # More than 70% in one index
                    alerts.append(CorrelationAlert(
                        positions=[p.position_id for p in open_positions
                                  if self._get_index_from_symbol(p.symbol) == index],
                        correlation=concentration,
                        risk_level="high" if concentration > 0.85 else "medium",
                        recommendation=f"Reduce {index} exposure from {concentration:.0%}",
                    ))

        return alerts

    def _calculate_strike_correlation(self, positions: List) -> float:
        """Calculate correlation based on strike proximity."""
        if len(positions) < 2:
            return 0.0

        strikes = [p.strike for p in positions]
        avg_strike = sum(strikes) / len(strikes)
        max_diff = max(abs(s - avg_strike) for s in strikes)
        cluster_window = self._strike_cluster_window(getattr(positions[0], "symbol", "")) if positions else 500

        # Closer strikes = higher correlation
        if max_diff <= cluster_window * 0.25:
            return 0.95
        elif max_diff <= cluster_window * 0.5:
            return 0.85
        elif max_diff <= cluster_window:
            return 0.7
        else:
            return 0.5

    def _calculate_adjustments(
        self, alerts: List[CorrelationAlert], positions: List
    ) -> List[Dict]:
        """Calculate position size adjustments."""
        adjustments = []

        for alert in alerts:
            if alert.risk_level == "high":
                # Recommend 50% reduction
                for pos_id in alert.positions:
                    pos = next((p for p in positions if p.position_id == pos_id), None)
                    if pos:
                        adjustments.append({
                            "position_id": pos_id,
                            "current_qty": pos.quantity,
                            "recommended_qty": int(pos.quantity * 0.5),
                            "reason": alert.recommendation,
                        })
            elif alert.risk_level == "medium":
                # Recommend 25% reduction
                for pos_id in alert.positions:
                    pos = next((p for p in positions if p.position_id == pos_id), None)
                    if pos:
                        adjustments.append({
                            "position_id": pos_id,
                            "current_qty": pos.quantity,
                            "recommended_qty": int(pos.quantity * 0.75),
                            "reason": alert.recommendation,
                        })

        return adjustments

    def _record_signal_penalty(
        self,
        signal_penalties: Dict[str, Dict[str, Any]],
        signal: Dict[str, Any],
        alert: CorrelationAlert,
        config: ScalpingConfig,
    ) -> None:
        signal_key = self._signal_key_from_signal(signal)
        new_scale = self._alert_size_scale(alert, config)
        existing = dict(signal_penalties.get(signal_key, {}) or {})
        existing_scale = float(existing.get("size_scale", 1.0) or 1.0)
        reasons = list(existing.get("reasons", []) or [])
        recommendation = str(alert.recommendation or "Correlation guard reduced position size")
        if recommendation not in reasons:
            reasons.append(recommendation)
        signal_penalties[signal_key] = {
            "size_scale": round(min(existing_scale, new_scale), 4),
            "risk": self._higher_risk_level(str(existing.get("risk", "low") or "low"), alert.risk_level),
            "reasons": reasons,
            "correlation": round(float(alert.correlation or 0.0), 4),
        }

    def _alert_size_scale(self, alert: CorrelationAlert, config: ScalpingConfig) -> float:
        if alert.risk_level == "high":
            return float(getattr(config, "correlation_high_risk_size_scale", 0.5) or 0.5)
        if alert.risk_level == "medium":
            return float(getattr(config, "correlation_medium_risk_size_scale", 0.75) or 0.75)
        return 1.0

    def _higher_risk_level(self, left: str, right: str) -> str:
        priority = {"low": 0, "medium": 1, "high": 2}
        return left if priority.get(left, 0) >= priority.get(right, 0) else right

    def _get_index_from_symbol(self, symbol: str) -> str:
        """Extract index from option symbol."""
        if "NIFTY" in symbol and "BANK" not in symbol:
            return "NIFTY"
        elif "BANKNIFTY" in symbol:
            return "BANKNIFTY"
        elif "SENSEX" in symbol:
            return "SENSEX"
        elif "FINNIFTY" in symbol:
            return "FINNIFTY"
        else:
            return "UNKNOWN"

    def _strike_cluster_window(self, symbol: str) -> int:
        normalized = self._get_index_from_symbol(symbol)
        if normalized in {"BANKNIFTY", "SENSEX"}:
            return 500
        if normalized == "FINNIFTY":
            return 250
        if normalized == "NIFTY":
            return 250
        return 250

    def _signal_key_from_signal(self, signal: Dict[str, Any]) -> str:
        symbol = str(signal.get("symbol", ""))
        strike = int(float(signal.get("strike", 0) or 0))
        option_type = str(signal.get("option_type", signal.get("side", ""))).upper()
        return f"{symbol}|{strike}|{option_type}"

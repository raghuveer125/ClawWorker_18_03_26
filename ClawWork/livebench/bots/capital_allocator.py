"""
Institutional Capital Allocation Engine
========================================
Hedge fund grade capital management with:
- Strategy sleeves (Trend, Mean Reversion, Momentum, Event-driven)
- Dynamic capital shifting based on regime and performance
- Cross-strategy risk budgeting with correlation awareness
- Kelly Criterion optimization with fractional sizing
- Drawdown-based capital protection and recovery
- Risk parity allocation

Author: ClawWork Institutional Framework
Version: 1.0.0
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
import math
import logging

logger = logging.getLogger(__name__)


class StrategySleeve(Enum):
    """Strategy categories for capital allocation"""
    TREND = "trend"              # TrendFollower, MomentumBot
    MEAN_REVERSION = "mean_rev"  # OIAnalyzer, Contrarian plays
    MOMENTUM = "momentum"         # Quick momentum captures
    EVENT = "event"              # News/earnings driven
    DEFENSIVE = "defensive"      # Capital preservation mode


@dataclass
class SleeveAllocation:
    """Capital allocation for a strategy sleeve"""
    sleeve: StrategySleeve
    base_allocation: float        # Base % of total capital (0-1)
    current_allocation: float     # Current dynamic allocation
    min_allocation: float         # Floor allocation
    max_allocation: float         # Ceiling allocation
    active_capital: float         # Actually deployed capital
    reserved_capital: float       # Reserved for opportunities
    pnl_today: float = 0.0
    pnl_week: float = 0.0
    pnl_month: float = 0.0
    sharpe_30d: float = 0.0
    sortino_30d: float = 0.0
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    win_rate: float = 0.5
    avg_win: float = 0.0
    avg_loss: float = 0.0
    trade_count: int = 0
    correlation_to_index: float = 0.0


@dataclass
class RiskBudget:
    """Cross-strategy risk budget"""
    total_var_limit: float        # Total Value at Risk limit
    current_var: float            # Current VaR consumption
    var_by_sleeve: Dict[StrategySleeve, float] = field(default_factory=dict)
    max_correlation: float = 0.7  # Max correlation between sleeves
    concentration_limit: float = 0.4  # Max single sleeve
    stress_loss_limit: float = 0.15   # Max loss in stress scenario


@dataclass
class CapitalDecision:
    """Capital allocation decision output"""
    approved: bool
    allocated_capital: float
    sleeve: StrategySleeve
    position_size_factor: float   # 0-1 sizing factor
    reason: str
    risk_budget_used: float
    kelly_fraction: float
    warnings: List[str] = field(default_factory=list)


@dataclass
class DrawdownState:
    """Drawdown tracking and recovery"""
    peak_capital: float
    current_capital: float
    drawdown_pct: float
    drawdown_days: int
    recovery_mode: bool
    capital_reduction_factor: float  # 1.0 = normal, <1 = reduced
    last_peak_date: datetime = field(default_factory=datetime.now)


class InstitutionalCapitalAllocator:
    """
    Institutional-grade capital allocation engine.

    Features:
    - Multi-sleeve capital management
    - Dynamic rebalancing based on performance
    - Risk parity allocation
    - Drawdown protection with staged recovery
    - Kelly Criterion position sizing
    - Correlation-aware diversification
    """

    # Map bots to strategy sleeves
    BOT_TO_SLEEVE = {
        "TrendFollower": StrategySleeve.TREND,
        "OIAnalyzer": StrategySleeve.MEAN_REVERSION,
        "MomentumBot": StrategySleeve.MOMENTUM,
        "PatternScanner": StrategySleeve.TREND,
        "VolatilityBot": StrategySleeve.EVENT,
        "MeanReversion": StrategySleeve.MEAN_REVERSION,
    }

    def __init__(
        self,
        total_capital: float,
        max_daily_loss_pct: float = 0.02,      # 2% max daily loss
        max_drawdown_pct: float = 0.10,         # 10% max drawdown
        kelly_fraction: float = 0.25,           # Quarter Kelly
        enable_dynamic_allocation: bool = True,
        enable_drawdown_protection: bool = True,
    ):
        self.total_capital = total_capital
        self.available_capital = total_capital
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.kelly_fraction = kelly_fraction
        self.enable_dynamic = enable_dynamic_allocation
        self.enable_drawdown_protection = enable_drawdown_protection

        # Initialize sleeves with base allocations
        self.sleeves: Dict[StrategySleeve, SleeveAllocation] = {}
        self._initialize_sleeves()

        # Risk budget tracking
        self.risk_budget = RiskBudget(
            total_var_limit=total_capital * 0.05,  # 5% VaR limit
            current_var=0.0,
            var_by_sleeve={s: 0.0 for s in StrategySleeve},
        )

        # Drawdown tracking
        self.drawdown = DrawdownState(
            peak_capital=total_capital,
            current_capital=total_capital,
            drawdown_pct=0.0,
            drawdown_days=0,
            recovery_mode=False,
            capital_reduction_factor=1.0,
        )

        # Performance history
        self.daily_pnl: List[Tuple[datetime, float]] = []
        self.sleeve_history: Dict[StrategySleeve, List[Tuple[datetime, float]]] = {
            s: [] for s in StrategySleeve
        }

        # Trade records per sleeve
        self.sleeve_trades: Dict[StrategySleeve, List[Dict]] = {
            s: [] for s in StrategySleeve
        }

        # Correlation matrix between sleeves
        self.correlation_matrix: Dict[Tuple[StrategySleeve, StrategySleeve], float] = {}

        # Capital deployment tracking
        self.deployed_capital: Dict[str, float] = {}  # position_id -> capital

        logger.info(f"Capital Allocator initialized: {total_capital:,.0f} total capital")

    def _initialize_sleeves(self):
        """Initialize strategy sleeves with base allocations"""
        # Base allocation percentages (total = 100%)
        base_allocations = {
            StrategySleeve.TREND: 0.35,         # 35% to trend
            StrategySleeve.MEAN_REVERSION: 0.25, # 25% to mean reversion
            StrategySleeve.MOMENTUM: 0.20,       # 20% to momentum
            StrategySleeve.EVENT: 0.10,          # 10% to event-driven
            StrategySleeve.DEFENSIVE: 0.10,      # 10% defensive/cash
        }

        for sleeve, base_pct in base_allocations.items():
            self.sleeves[sleeve] = SleeveAllocation(
                sleeve=sleeve,
                base_allocation=base_pct,
                current_allocation=base_pct,
                min_allocation=base_pct * 0.3,  # Can go to 30% of base
                max_allocation=min(base_pct * 2.0, 0.5),  # Max 2x base or 50%
                active_capital=0.0,
                reserved_capital=self.total_capital * base_pct,
            )

    def request_capital(
        self,
        bot_name: str,
        proposed_trade: Dict,
        market_regime: str,
        signals: Dict,
    ) -> CapitalDecision:
        """
        Request capital allocation for a trade.

        Returns approval decision with allocated capital and sizing.
        """
        # Determine sleeve for this bot
        sleeve = self.BOT_TO_SLEEVE.get(bot_name, StrategySleeve.TREND)
        sleeve_alloc = self.sleeves[sleeve]

        warnings = []

        # Check 1: Drawdown protection
        if self.enable_drawdown_protection:
            if self.drawdown.recovery_mode:
                if self.drawdown.drawdown_pct > 0.08:  # >8% drawdown
                    return CapitalDecision(
                        approved=False,
                        allocated_capital=0,
                        sleeve=sleeve,
                        position_size_factor=0,
                        reason="CAPITAL PROTECTED: In recovery mode (>8% drawdown)",
                        risk_budget_used=0,
                        kelly_fraction=0,
                    )
                warnings.append(f"Recovery mode active: {self.drawdown.drawdown_pct:.1%} drawdown")

        # Check 2: Daily loss limit
        daily_pnl = self._get_daily_pnl()
        if daily_pnl < -self.total_capital * self.max_daily_loss_pct:
            return CapitalDecision(
                approved=False,
                allocated_capital=0,
                sleeve=sleeve,
                position_size_factor=0,
                reason=f"DAILY LOSS LIMIT: {daily_pnl:,.0f} exceeds {self.max_daily_loss_pct:.1%} limit",
                risk_budget_used=0,
                kelly_fraction=0,
            )

        # Check 3: Sleeve allocation availability
        max_sleeve_capital = self.total_capital * sleeve_alloc.current_allocation
        available_sleeve = max_sleeve_capital - sleeve_alloc.active_capital

        if available_sleeve <= 0:
            return CapitalDecision(
                approved=False,
                allocated_capital=0,
                sleeve=sleeve,
                position_size_factor=0,
                reason=f"SLEEVE EXHAUSTED: {sleeve.value} fully deployed",
                risk_budget_used=0,
                kelly_fraction=0,
            )

        # Check 4: Risk budget (VaR)
        proposed_var = self._estimate_var(proposed_trade, signals)
        remaining_var = self.risk_budget.total_var_limit - self.risk_budget.current_var

        if proposed_var > remaining_var:
            warnings.append(f"VaR constrained: reducing size by {(1 - remaining_var/proposed_var):.0%}")
            proposed_var = remaining_var

        # Calculate Kelly optimal size
        kelly_size = self._calculate_kelly_size(sleeve_alloc, proposed_trade)

        # Calculate position size with all constraints
        position_size = self._calculate_position_size(
            sleeve_alloc=sleeve_alloc,
            kelly_size=kelly_size,
            available_sleeve=available_sleeve,
            proposed_var=proposed_var,
            market_regime=market_regime,
        )

        # Apply drawdown reduction factor
        position_size *= self.drawdown.capital_reduction_factor

        # Minimum viable position check
        min_position = self.total_capital * 0.005  # 0.5% minimum
        if position_size < min_position:
            return CapitalDecision(
                approved=False,
                allocated_capital=0,
                sleeve=sleeve,
                position_size_factor=0,
                reason=f"SIZE TOO SMALL: {position_size:,.0f} < {min_position:,.0f} minimum",
                risk_budget_used=0,
                kelly_fraction=kelly_size,
                warnings=warnings,
            )

        # Calculate sizing factor (0-1)
        max_position = self.total_capital * 0.10  # 10% max single position
        size_factor = min(position_size / max_position, 1.0)

        return CapitalDecision(
            approved=True,
            allocated_capital=position_size,
            sleeve=sleeve,
            position_size_factor=size_factor,
            reason=f"APPROVED: {position_size:,.0f} from {sleeve.value} sleeve",
            risk_budget_used=proposed_var,
            kelly_fraction=kelly_size,
            warnings=warnings,
        )

    def deploy_capital(self, position_id: str, capital: float, sleeve: StrategySleeve):
        """Record capital deployment for a position"""
        self.deployed_capital[position_id] = capital
        self.sleeves[sleeve].active_capital += capital
        self.available_capital -= capital

        # Update VaR consumption (simplified)
        estimated_var = capital * 0.02  # 2% VaR assumption
        self.risk_budget.current_var += estimated_var
        self.risk_budget.var_by_sleeve[sleeve] = \
            self.risk_budget.var_by_sleeve.get(sleeve, 0) + estimated_var

        logger.info(f"Deployed {capital:,.0f} to {sleeve.value}: {position_id}")

    def release_capital(self, position_id: str, pnl: float, sleeve: StrategySleeve):
        """Release capital when position is closed"""
        if position_id not in self.deployed_capital:
            return

        capital = self.deployed_capital.pop(position_id)
        self.sleeves[sleeve].active_capital -= capital
        self.available_capital += capital + pnl

        # Update sleeve PnL
        self.sleeves[sleeve].pnl_today += pnl

        # Record trade
        self.sleeve_trades[sleeve].append({
            "position_id": position_id,
            "capital": capital,
            "pnl": pnl,
            "pnl_pct": pnl / capital if capital > 0 else 0,
            "timestamp": datetime.now(),
        })

        # Update win/loss stats
        trades = self.sleeve_trades[sleeve]
        if len(trades) > 0:
            wins = [t for t in trades if t["pnl"] > 0]
            losses = [t for t in trades if t["pnl"] < 0]

            self.sleeves[sleeve].win_rate = len(wins) / len(trades)
            self.sleeves[sleeve].avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
            self.sleeves[sleeve].avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 0
            self.sleeves[sleeve].trade_count = len(trades)

        # Update VaR
        released_var = capital * 0.02
        self.risk_budget.current_var = max(0, self.risk_budget.current_var - released_var)

        # Update drawdown tracking
        self._update_drawdown(pnl)

        logger.info(f"Released {capital:,.0f} from {sleeve.value}, PnL: {pnl:+,.0f}")

    def _calculate_kelly_size(
        self,
        sleeve_alloc: SleeveAllocation,
        proposed_trade: Dict,
    ) -> float:
        """Calculate Kelly Criterion optimal position size"""
        win_rate = sleeve_alloc.win_rate
        avg_win = sleeve_alloc.avg_win if sleeve_alloc.avg_win > 0 else 1.0
        avg_loss = sleeve_alloc.avg_loss if sleeve_alloc.avg_loss > 0 else 1.0

        # Minimum history required
        if sleeve_alloc.trade_count < 20:
            return self.kelly_fraction * 0.5  # Half Kelly until we have data

        # Calculate Kelly %
        # Kelly = W - [(1-W) / R]
        # W = win probability
        # R = win/loss ratio
        r_ratio = avg_win / avg_loss if avg_loss > 0 else 1.0

        kelly = win_rate - ((1 - win_rate) / r_ratio)

        # Apply fractional Kelly (more conservative)
        kelly = max(0, kelly) * self.kelly_fraction

        # Cap at reasonable maximum
        return min(kelly, 0.25)  # Max 25% of capital

    def _calculate_position_size(
        self,
        sleeve_alloc: SleeveAllocation,
        kelly_size: float,
        available_sleeve: float,
        proposed_var: float,
        market_regime: str,
    ) -> float:
        """Calculate final position size with all constraints"""
        # Start with Kelly-based size
        base_size = self.total_capital * kelly_size

        # Regime adjustments
        regime_factors = {
            "TRENDING_BULL": 1.0,
            "TRENDING_BEAR": 1.0,
            "HIGH_VOL": 0.5,
            "LOW_VOL": 0.8,
            "CHOPPY": 0.3,
            "RANGING": 0.6,
            "BREAKOUT": 0.9,
        }
        regime_factor = regime_factors.get(market_regime, 0.7)

        # Apply regime factor
        sized = base_size * regime_factor

        # Cap by sleeve availability
        sized = min(sized, available_sleeve)

        # Cap by VaR budget
        max_var_size = proposed_var / 0.02 * 1.5  # 1.5x implied from VaR
        sized = min(sized, max_var_size)

        # Apply sleeve performance adjustment
        if self.enable_dynamic:
            perf_factor = self._sleeve_performance_factor(sleeve_alloc)
            sized *= perf_factor

        return sized

    def _sleeve_performance_factor(self, sleeve_alloc: SleeveAllocation) -> float:
        """Calculate performance-based sizing factor for sleeve"""
        # Need minimum trades for meaningful calculation
        if sleeve_alloc.trade_count < 10:
            return 0.8  # Conservative until data

        factors = []

        # Win rate factor (0.5-1.5)
        wr_factor = sleeve_alloc.win_rate / 0.5  # 50% is baseline
        factors.append(max(0.5, min(1.5, wr_factor)))

        # Sharpe factor (0.5-1.5)
        if sleeve_alloc.sharpe_30d != 0:
            sharpe_factor = (sleeve_alloc.sharpe_30d + 1) / 2  # 1.0 Sharpe is baseline
            factors.append(max(0.5, min(1.5, sharpe_factor)))

        # Drawdown penalty
        if sleeve_alloc.current_drawdown > 0.05:  # >5% drawdown
            dd_penalty = 1 - (sleeve_alloc.current_drawdown - 0.05) * 5
            factors.append(max(0.3, dd_penalty))

        return sum(factors) / len(factors) if factors else 1.0

    def _estimate_var(self, proposed_trade: Dict, signals: Dict) -> float:
        """Estimate Value at Risk for proposed trade"""
        # Simplified VaR estimation
        position_value = proposed_trade.get("position_value", self.total_capital * 0.05)

        # Volatility-based VaR
        volatility = signals.get("implied_volatility", 0.20)  # Default 20%

        # 95% VaR (1.65 std devs)
        daily_var = position_value * volatility / math.sqrt(252) * 1.65

        return daily_var

    def _get_daily_pnl(self) -> float:
        """Get today's total PnL"""
        return sum(s.pnl_today for s in self.sleeves.values())

    def _update_drawdown(self, pnl: float):
        """Update drawdown tracking after PnL change"""
        self.drawdown.current_capital += pnl

        if self.drawdown.current_capital > self.drawdown.peak_capital:
            # New high
            self.drawdown.peak_capital = self.drawdown.current_capital
            self.drawdown.last_peak_date = datetime.now()
            self.drawdown.drawdown_pct = 0.0
            self.drawdown.drawdown_days = 0
            self.drawdown.recovery_mode = False
            self.drawdown.capital_reduction_factor = 1.0
        else:
            # In drawdown
            self.drawdown.drawdown_pct = 1 - (
                self.drawdown.current_capital / self.drawdown.peak_capital
            )
            self.drawdown.drawdown_days = (
                datetime.now() - self.drawdown.last_peak_date
            ).days

            # Enter recovery mode if drawdown exceeds threshold
            if self.drawdown.drawdown_pct > 0.05:  # 5% drawdown threshold
                self.drawdown.recovery_mode = True

                # Staged capital reduction
                if self.drawdown.drawdown_pct > 0.10:
                    self.drawdown.capital_reduction_factor = 0.25
                elif self.drawdown.drawdown_pct > 0.07:
                    self.drawdown.capital_reduction_factor = 0.50
                elif self.drawdown.drawdown_pct > 0.05:
                    self.drawdown.capital_reduction_factor = 0.75

    def rebalance_sleeves(self, regime: str):
        """
        Dynamically rebalance sleeve allocations based on regime and performance.
        Call this periodically (e.g., daily or when regime changes).
        """
        if not self.enable_dynamic:
            return

        # Regime-based allocation shifts
        regime_weights = {
            "TRENDING_BULL": {
                StrategySleeve.TREND: 1.3,
                StrategySleeve.MOMENTUM: 1.2,
                StrategySleeve.MEAN_REVERSION: 0.6,
                StrategySleeve.EVENT: 0.9,
                StrategySleeve.DEFENSIVE: 0.5,
            },
            "TRENDING_BEAR": {
                StrategySleeve.TREND: 1.2,
                StrategySleeve.MOMENTUM: 0.8,
                StrategySleeve.MEAN_REVERSION: 0.8,
                StrategySleeve.EVENT: 1.0,
                StrategySleeve.DEFENSIVE: 1.2,
            },
            "HIGH_VOL": {
                StrategySleeve.TREND: 0.7,
                StrategySleeve.MOMENTUM: 0.5,
                StrategySleeve.MEAN_REVERSION: 1.0,
                StrategySleeve.EVENT: 1.3,
                StrategySleeve.DEFENSIVE: 1.5,
            },
            "CHOPPY": {
                StrategySleeve.TREND: 0.3,
                StrategySleeve.MOMENTUM: 0.4,
                StrategySleeve.MEAN_REVERSION: 1.5,
                StrategySleeve.EVENT: 0.8,
                StrategySleeve.DEFENSIVE: 2.0,
            },
            "RANGING": {
                StrategySleeve.TREND: 0.5,
                StrategySleeve.MOMENTUM: 0.7,
                StrategySleeve.MEAN_REVERSION: 1.5,
                StrategySleeve.EVENT: 0.8,
                StrategySleeve.DEFENSIVE: 1.0,
            },
        }

        weights = regime_weights.get(regime, {s: 1.0 for s in StrategySleeve})

        # Calculate new allocations
        total_weighted = 0
        for sleeve, alloc in self.sleeves.items():
            weight = weights.get(sleeve, 1.0)
            # Factor in performance
            perf_factor = self._sleeve_performance_factor(alloc)
            alloc.current_allocation = alloc.base_allocation * weight * perf_factor
            total_weighted += alloc.current_allocation

        # Normalize to 100%
        if total_weighted > 0:
            for alloc in self.sleeves.values():
                alloc.current_allocation /= total_weighted
                # Apply min/max constraints
                alloc.current_allocation = max(
                    alloc.min_allocation,
                    min(alloc.max_allocation, alloc.current_allocation)
                )

        logger.info(f"Rebalanced sleeves for {regime} regime")

    def reset_daily(self):
        """Reset daily tracking"""
        for sleeve in self.sleeves.values():
            sleeve.pnl_today = 0.0

        # Record daily PnL for history
        total_pnl = self._get_daily_pnl()
        self.daily_pnl.append((datetime.now(), total_pnl))

        # Keep last 90 days
        cutoff = datetime.now() - timedelta(days=90)
        self.daily_pnl = [(d, p) for d, p in self.daily_pnl if d > cutoff]

        # Reset VaR
        self.risk_budget.current_var = 0.0
        for sleeve in StrategySleeve:
            self.risk_budget.var_by_sleeve[sleeve] = 0.0

    def get_allocation_report(self) -> Dict:
        """Generate comprehensive allocation report"""
        return {
            "total_capital": self.total_capital,
            "available_capital": self.available_capital,
            "deployed_capital": self.total_capital - self.available_capital,
            "deployment_pct": (self.total_capital - self.available_capital) / self.total_capital,
            "daily_pnl": self._get_daily_pnl(),
            "drawdown": {
                "current_pct": self.drawdown.drawdown_pct,
                "days_in_drawdown": self.drawdown.drawdown_days,
                "recovery_mode": self.drawdown.recovery_mode,
                "capital_factor": self.drawdown.capital_reduction_factor,
            },
            "risk_budget": {
                "var_limit": self.risk_budget.total_var_limit,
                "var_used": self.risk_budget.current_var,
                "var_remaining": self.risk_budget.total_var_limit - self.risk_budget.current_var,
                "var_utilization": self.risk_budget.current_var / self.risk_budget.total_var_limit,
            },
            "sleeves": {
                sleeve.value: {
                    "base_allocation": alloc.base_allocation,
                    "current_allocation": alloc.current_allocation,
                    "active_capital": alloc.active_capital,
                    "pnl_today": alloc.pnl_today,
                    "win_rate": alloc.win_rate,
                    "trade_count": alloc.trade_count,
                }
                for sleeve, alloc in self.sleeves.items()
            },
        }

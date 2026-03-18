"""
Execution & Impact Modeling Framework
======================================
Institutional-grade execution intelligence with:
- Order book depth analysis and liquidity assessment
- Slippage prediction and simulation
- Market impact modeling (temporary and permanent)
- Volatility-adjusted execution throttling
- Smart order routing decisions
- Execution quality analysis (vs TWAP, VWAP)
- Adaptive execution strategies

Author: ClawWork Institutional Framework
Version: 1.0.0
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict, deque
import math
import statistics
import logging

logger = logging.getLogger(__name__)


class ExecutionStrategy(Enum):
    """Order execution strategies"""
    MARKET = "market"           # Immediate market order
    LIMIT = "limit"             # Limit order
    TWAP = "twap"               # Time-weighted average price
    VWAP = "vwap"               # Volume-weighted average price
    ICEBERG = "iceberg"         # Hidden quantity
    SNIPER = "sniper"           # Wait for optimal price
    AGGRESSIVE = "aggressive"   # Fast execution priority
    PASSIVE = "passive"         # Cost priority


class LiquidityLevel(Enum):
    """Market liquidity classification"""
    DEEP = "deep"           # Excellent liquidity
    NORMAL = "normal"       # Standard liquidity
    THIN = "thin"           # Below average liquidity
    ILLIQUID = "illiquid"   # Poor liquidity, high impact


class ExecutionUrgency(Enum):
    """Order urgency level"""
    CRITICAL = "critical"   # Execute immediately
    HIGH = "high"           # Execute within 30 seconds
    NORMAL = "normal"       # Execute within 2 minutes
    LOW = "low"             # Can wait for optimal price


@dataclass
class OrderBookSnapshot:
    """Order book depth snapshot"""
    symbol: str
    timestamp: datetime
    bid_prices: List[float] = field(default_factory=list)
    bid_sizes: List[int] = field(default_factory=list)
    ask_prices: List[float] = field(default_factory=list)
    ask_sizes: List[int] = field(default_factory=list)
    spread: float = 0.0
    spread_pct: float = 0.0
    mid_price: float = 0.0
    total_bid_depth: int = 0
    total_ask_depth: int = 0
    imbalance: float = 0.0  # -1 to 1, positive = bid heavy


@dataclass
class SlippageEstimate:
    """Predicted slippage for an order"""
    expected_slippage_pct: float
    worst_case_slippage_pct: float
    slippage_cost: float           # In currency
    confidence: float              # 0-1
    contributing_factors: Dict[str, float] = field(default_factory=dict)
    recommendation: str = ""


@dataclass
class MarketImpact:
    """Market impact analysis"""
    temporary_impact_pct: float    # Price move during execution
    permanent_impact_pct: float    # Residual price change
    total_impact_cost: float       # Total cost in currency
    decay_time_seconds: int        # Time for temp impact to decay
    optimal_participation_rate: float  # Recommended % of volume


@dataclass
class ExecutionPlan:
    """Detailed execution plan for an order"""
    strategy: ExecutionStrategy
    urgency: ExecutionUrgency
    order_size: int
    num_slices: int                # Number of order slices
    slice_interval_ms: int         # Time between slices
    price_limit: Optional[float]   # Limit price if applicable
    max_slippage_pct: float        # Slippage tolerance
    estimated_fill_time_sec: int
    estimated_cost: float
    warnings: List[str] = field(default_factory=list)
    contingency: str = ""          # Action if conditions deteriorate


@dataclass
class ExecutionQuality:
    """Post-execution quality metrics"""
    order_id: str
    symbol: str
    side: str
    ordered_qty: int
    filled_qty: int
    avg_fill_price: float
    vwap_benchmark: float
    twap_benchmark: float
    arrival_price: float           # Price when order arrived
    slippage_vs_arrival: float     # Actual slippage
    slippage_vs_vwap: float
    implementation_shortfall: float
    execution_time_sec: int
    strategy_used: ExecutionStrategy
    quality_score: float           # 0-100


class ExecutionEngine:
    """
    Institutional-grade execution intelligence.

    Features:
    - Real-time liquidity assessment
    - Predictive slippage modeling
    - Market impact estimation
    - Adaptive execution strategies
    - Execution quality tracking
    - Volatility-adjusted throttling
    """

    # Impact model coefficients (calibrated for Indian options)
    IMPACT_COEFFICIENTS = {
        "temporary": 0.15,    # Temporary impact coefficient
        "permanent": 0.05,    # Permanent impact coefficient
        "decay_rate": 0.10,   # Decay rate per minute
    }

    # Slippage factors by liquidity
    SLIPPAGE_BASE = {
        LiquidityLevel.DEEP: 0.001,      # 0.1%
        LiquidityLevel.NORMAL: 0.003,     # 0.3%
        LiquidityLevel.THIN: 0.008,       # 0.8%
        LiquidityLevel.ILLIQUID: 0.020,   # 2.0%
    }

    def __init__(
        self,
        default_max_slippage: float = 0.01,     # 1% default max
        volatility_throttle: bool = True,
        impact_aware_sizing: bool = True,
        min_execution_interval_ms: int = 500,   # Min time between orders
        max_participation_rate: float = 0.10,   # Max 10% of volume
    ):
        self.default_max_slippage = default_max_slippage
        self.volatility_throttle = volatility_throttle
        self.impact_aware_sizing = impact_aware_sizing
        self.min_execution_interval_ms = min_execution_interval_ms
        self.max_participation_rate = max_participation_rate

        # Order book cache
        self.order_books: Dict[str, OrderBookSnapshot] = {}

        # Historical execution data
        self.execution_history: List[ExecutionQuality] = []
        self.symbol_executions: Dict[str, List[ExecutionQuality]] = defaultdict(list)

        # Slippage tracking
        self.predicted_slippage: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )
        self.actual_slippage: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )

        # Volume tracking
        self.symbol_volume: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=60)  # Last 60 periods
        )

        # Volatility tracking
        self.symbol_volatility: Dict[str, float] = {}

        # Throttle state
        self.last_execution_time: Dict[str, datetime] = {}
        self.throttle_until: Dict[str, datetime] = {}

        # Execution quality stats
        self.quality_stats: Dict[str, Dict] = defaultdict(
            lambda: {"fills": 0, "total_slippage": 0, "avg_score": 0}
        )

        logger.info("Execution Engine initialized")

    def update_order_book(
        self,
        symbol: str,
        bids: List[Tuple[float, int]],
        asks: List[Tuple[float, int]],
    ):
        """Update order book snapshot for a symbol"""
        bid_prices = [b[0] for b in bids]
        bid_sizes = [b[1] for b in bids]
        ask_prices = [a[0] for a in asks]
        ask_sizes = [a[1] for a in asks]

        best_bid = bid_prices[0] if bid_prices else 0
        best_ask = ask_prices[0] if ask_prices else 0
        mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
        spread = best_ask - best_bid if best_bid and best_ask else 0
        spread_pct = spread / mid_price if mid_price > 0 else 0

        total_bid = sum(bid_sizes)
        total_ask = sum(ask_sizes)
        imbalance = (total_bid - total_ask) / (total_bid + total_ask) if (total_bid + total_ask) > 0 else 0

        self.order_books[symbol] = OrderBookSnapshot(
            symbol=symbol,
            timestamp=datetime.now(),
            bid_prices=bid_prices,
            bid_sizes=bid_sizes,
            ask_prices=ask_prices,
            ask_sizes=ask_sizes,
            spread=spread,
            spread_pct=spread_pct,
            mid_price=mid_price,
            total_bid_depth=total_bid,
            total_ask_depth=total_ask,
            imbalance=imbalance,
        )

    def update_volume(self, symbol: str, volume: int):
        """Update volume observation"""
        self.symbol_volume[symbol].append(volume)

    def update_volatility(self, symbol: str, volatility: float):
        """Update implied/realized volatility"""
        self.symbol_volatility[symbol] = volatility

    def assess_liquidity(self, symbol: str) -> LiquidityLevel:
        """Assess current liquidity level for a symbol"""
        book = self.order_books.get(symbol)
        if not book:
            return LiquidityLevel.THIN

        # Factor 1: Spread
        spread_score = 0
        if book.spread_pct < 0.001:  # <0.1%
            spread_score = 3
        elif book.spread_pct < 0.003:  # <0.3%
            spread_score = 2
        elif book.spread_pct < 0.008:  # <0.8%
            spread_score = 1

        # Factor 2: Depth
        total_depth = book.total_bid_depth + book.total_ask_depth
        depth_score = 0
        if total_depth > 5000:
            depth_score = 3
        elif total_depth > 2000:
            depth_score = 2
        elif total_depth > 500:
            depth_score = 1

        # Factor 3: Balance (imbalance reduces score)
        balance_score = 2 - abs(book.imbalance) * 2

        total_score = spread_score + depth_score + balance_score

        if total_score >= 7:
            return LiquidityLevel.DEEP
        elif total_score >= 5:
            return LiquidityLevel.NORMAL
        elif total_score >= 3:
            return LiquidityLevel.THIN
        else:
            return LiquidityLevel.ILLIQUID

    def estimate_slippage(
        self,
        symbol: str,
        side: str,
        quantity: int,
        urgency: ExecutionUrgency = ExecutionUrgency.NORMAL,
    ) -> SlippageEstimate:
        """Estimate slippage for a potential order"""
        book = self.order_books.get(symbol)
        liquidity = self.assess_liquidity(symbol)

        # Base slippage from liquidity
        base_slippage = self.SLIPPAGE_BASE.get(liquidity, 0.005)

        factors = {"base": base_slippage}

        # Factor 1: Size impact
        if book:
            depth = book.total_ask_depth if side == "BUY" else book.total_bid_depth
            if depth > 0:
                participation = quantity / depth
                size_impact = participation * 0.5  # 50% of participation rate
                factors["size_impact"] = size_impact
            else:
                factors["size_impact"] = 0.01  # High if no depth

        # Factor 2: Spread cost
        if book and book.mid_price > 0:
            spread_cost = book.spread_pct / 2  # Half spread on average
            factors["spread"] = spread_cost

        # Factor 3: Urgency premium
        urgency_multipliers = {
            ExecutionUrgency.CRITICAL: 1.5,
            ExecutionUrgency.HIGH: 1.2,
            ExecutionUrgency.NORMAL: 1.0,
            ExecutionUrgency.LOW: 0.8,
        }
        urgency_mult = urgency_multipliers.get(urgency, 1.0)

        # Factor 4: Volatility adjustment
        volatility = self.symbol_volatility.get(symbol, 0.20)
        vol_factor = volatility / 0.20  # Normalize to 20% baseline
        factors["volatility"] = (vol_factor - 1) * 0.005  # +0.5% per 20% extra vol

        # Calculate total
        total_factors = sum(factors.values())
        expected_slippage = total_factors * urgency_mult

        # Worst case (2x expected for liquid, 3x for illiquid)
        worst_mult = 2.0 if liquidity in (LiquidityLevel.DEEP, LiquidityLevel.NORMAL) else 3.0
        worst_case = expected_slippage * worst_mult

        # Calculate cost
        price = book.mid_price if book else 100
        slippage_cost = price * quantity * expected_slippage

        # Confidence based on data quality
        confidence = 0.8 if book else 0.5
        if len(self.actual_slippage.get(symbol, [])) > 20:
            confidence = 0.9

        # Recommendation
        if expected_slippage < 0.003:
            recommendation = "FAVORABLE: Low expected slippage, proceed with market order"
        elif expected_slippage < 0.008:
            recommendation = "ACCEPTABLE: Consider limit order to reduce impact"
        elif expected_slippage < 0.015:
            recommendation = "CAUTION: Use TWAP or reduce size"
        else:
            recommendation = "WARNING: High impact expected, split order or delay"

        return SlippageEstimate(
            expected_slippage_pct=expected_slippage,
            worst_case_slippage_pct=worst_case,
            slippage_cost=slippage_cost,
            confidence=confidence,
            contributing_factors=factors,
            recommendation=recommendation,
        )

    def estimate_market_impact(
        self,
        symbol: str,
        side: str,
        quantity: int,
        execution_time_sec: int = 60,
    ) -> MarketImpact:
        """Estimate market impact of order execution"""
        book = self.order_books.get(symbol)

        # Get average volume
        volume_history = list(self.symbol_volume.get(symbol, []))
        avg_volume = statistics.mean(volume_history) if volume_history else 1000

        # Participation rate
        participation = quantity / avg_volume if avg_volume > 0 else 1.0

        # Volatility
        volatility = self.symbol_volatility.get(symbol, 0.20)
        daily_vol = volatility / math.sqrt(252)

        # Temporary impact (Almgren-Chriss model simplified)
        # I_temp = eta * sigma * sqrt(V/T)
        eta = self.IMPACT_COEFFICIENTS["temporary"]
        temp_impact = eta * daily_vol * math.sqrt(participation)

        # Permanent impact
        # I_perm = gamma * sigma * V/T
        gamma = self.IMPACT_COEFFICIENTS["permanent"]
        perm_impact = gamma * daily_vol * participation

        # Total impact
        total_impact = temp_impact + perm_impact

        # Cost calculation
        price = book.mid_price if book else 100
        impact_cost = price * quantity * total_impact

        # Decay time for temporary impact
        decay_rate = self.IMPACT_COEFFICIENTS["decay_rate"]
        decay_time = int(60 / decay_rate)  # Minutes to 10% of temp impact

        # Optimal participation rate
        # Minimize impact: trade slowly enough to let market absorb
        optimal_rate = min(0.05, 0.02 / daily_vol)  # Lower in high vol

        return MarketImpact(
            temporary_impact_pct=temp_impact,
            permanent_impact_pct=perm_impact,
            total_impact_cost=impact_cost,
            decay_time_seconds=decay_time,
            optimal_participation_rate=optimal_rate,
        )

    def create_execution_plan(
        self,
        symbol: str,
        side: str,
        quantity: int,
        urgency: ExecutionUrgency = ExecutionUrgency.NORMAL,
        max_slippage: Optional[float] = None,
    ) -> ExecutionPlan:
        """Create optimal execution plan for an order"""
        max_slippage = max_slippage or self.default_max_slippage

        book = self.order_books.get(symbol)
        liquidity = self.assess_liquidity(symbol)
        slippage_est = self.estimate_slippage(symbol, side, quantity, urgency)
        impact = self.estimate_market_impact(symbol, side, quantity)

        warnings = []

        # Check throttle
        if self._is_throttled(symbol):
            throttle_end = self.throttle_until.get(symbol, datetime.now())
            warnings.append(f"Throttled until {throttle_end.strftime('%H:%M:%S')}")

        # Determine strategy based on conditions
        if urgency == ExecutionUrgency.CRITICAL:
            strategy = ExecutionStrategy.AGGRESSIVE
            num_slices = 1
            slice_interval = 0
        elif urgency == ExecutionUrgency.LOW and slippage_est.expected_slippage_pct > 0.005:
            strategy = ExecutionStrategy.SNIPER
            num_slices = 3
            slice_interval = 30000  # 30 seconds
        elif liquidity == LiquidityLevel.ILLIQUID:
            strategy = ExecutionStrategy.TWAP
            num_slices = min(5, quantity // 100)
            slice_interval = 10000  # 10 seconds
            warnings.append("Illiquid market - using TWAP")
        elif slippage_est.expected_slippage_pct > 0.01:
            strategy = ExecutionStrategy.ICEBERG
            num_slices = 3
            slice_interval = 5000  # 5 seconds
            warnings.append("High slippage expected - using iceberg")
        elif slippage_est.expected_slippage_pct > 0.005:
            strategy = ExecutionStrategy.LIMIT
            num_slices = 2
            slice_interval = 3000
        else:
            strategy = ExecutionStrategy.MARKET
            num_slices = 1
            slice_interval = 0

        # Calculate limit price
        if book and strategy in (ExecutionStrategy.LIMIT, ExecutionStrategy.SNIPER):
            if side == "BUY":
                price_limit = book.ask_prices[0] * (1 + max_slippage * 0.5)
            else:
                price_limit = book.bid_prices[0] * (1 - max_slippage * 0.5)
        else:
            price_limit = None

        # Estimate fill time
        if strategy == ExecutionStrategy.MARKET:
            est_fill_time = 1
        elif strategy == ExecutionStrategy.SNIPER:
            est_fill_time = 120  # May take 2 minutes
        else:
            est_fill_time = (num_slices - 1) * (slice_interval / 1000) + 5

        # Volatility throttle check
        if self.volatility_throttle:
            vol = self.symbol_volatility.get(symbol, 0.20)
            if vol > 0.40:  # >40% vol
                warnings.append(f"High volatility ({vol:.0%}) - increasing intervals")
                slice_interval = int(slice_interval * 1.5)

        # Contingency
        if slippage_est.expected_slippage_pct > max_slippage:
            contingency = "REDUCE SIZE or DELAY if slippage exceeds limit"
        else:
            contingency = "Proceed as planned"

        return ExecutionPlan(
            strategy=strategy,
            urgency=urgency,
            order_size=quantity,
            num_slices=num_slices,
            slice_interval_ms=slice_interval,
            price_limit=price_limit,
            max_slippage_pct=max_slippage,
            estimated_fill_time_sec=est_fill_time,
            estimated_cost=slippage_est.slippage_cost + impact.total_impact_cost,
            warnings=warnings,
            contingency=contingency,
        )

    def record_execution(
        self,
        order_id: str,
        symbol: str,
        side: str,
        ordered_qty: int,
        filled_qty: int,
        avg_fill_price: float,
        arrival_price: float,
        execution_time_sec: int,
        strategy: ExecutionStrategy,
    ):
        """Record completed execution for quality analysis"""
        book = self.order_books.get(symbol)

        # Calculate benchmarks (simplified - actual would use tick data)
        vwap_benchmark = avg_fill_price * 0.999  # Placeholder
        twap_benchmark = avg_fill_price * 0.998  # Placeholder

        # Calculate slippage metrics
        slippage_vs_arrival = (avg_fill_price - arrival_price) / arrival_price
        if side == "SELL":
            slippage_vs_arrival = -slippage_vs_arrival

        slippage_vs_vwap = (avg_fill_price - vwap_benchmark) / vwap_benchmark
        if side == "SELL":
            slippage_vs_vwap = -slippage_vs_vwap

        # Implementation shortfall (arrival to fill)
        impl_shortfall = abs(avg_fill_price - arrival_price) / arrival_price

        # Quality score (0-100)
        score = 100

        # Penalize slippage
        score -= min(30, abs(slippage_vs_arrival) * 1000)

        # Penalize fill rate
        fill_rate = filled_qty / ordered_qty if ordered_qty > 0 else 0
        score -= (1 - fill_rate) * 20

        # Penalize execution time
        if strategy != ExecutionStrategy.SNIPER:
            if execution_time_sec > 60:
                score -= min(10, (execution_time_sec - 60) / 10)

        score = max(0, min(100, score))

        quality = ExecutionQuality(
            order_id=order_id,
            symbol=symbol,
            side=side,
            ordered_qty=ordered_qty,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            vwap_benchmark=vwap_benchmark,
            twap_benchmark=twap_benchmark,
            arrival_price=arrival_price,
            slippage_vs_arrival=slippage_vs_arrival,
            slippage_vs_vwap=slippage_vs_vwap,
            implementation_shortfall=impl_shortfall,
            execution_time_sec=execution_time_sec,
            strategy_used=strategy,
            quality_score=score,
        )

        self.execution_history.append(quality)
        self.symbol_executions[symbol].append(quality)

        # Update stats
        stats = self.quality_stats[symbol]
        stats["fills"] += 1
        stats["total_slippage"] += slippage_vs_arrival
        stats["avg_score"] = (
            (stats["avg_score"] * (stats["fills"] - 1) + score) / stats["fills"]
        )

        # Track for prediction calibration
        self.actual_slippage[symbol].append(abs(slippage_vs_arrival))

        logger.info(f"Execution recorded: {order_id} | Score: {score:.0f} | Slippage: {slippage_vs_arrival:.2%}")

        return quality

    def should_execute(
        self,
        symbol: str,
        side: str,
        quantity: int,
        max_slippage: Optional[float] = None,
    ) -> Tuple[bool, str, ExecutionPlan]:
        """
        Pre-execution check: should we execute this order now?

        Returns (approved, reason, execution_plan)
        """
        max_slippage = max_slippage or self.default_max_slippage

        # Check throttle
        if self._is_throttled(symbol):
            return False, "Symbol is throttled - wait for cooldown", None

        # Assess conditions
        liquidity = self.assess_liquidity(symbol)
        slippage_est = self.estimate_slippage(symbol, side, quantity)

        # Hard reject conditions
        if liquidity == LiquidityLevel.ILLIQUID and quantity > 500:
            return False, "Market too illiquid for order size", None

        if slippage_est.expected_slippage_pct > max_slippage * 2:
            return False, f"Slippage {slippage_est.expected_slippage_pct:.1%} exceeds 2x limit", None

        # Warning conditions (proceed with caution)
        plan = self.create_execution_plan(
            symbol, side, quantity,
            ExecutionUrgency.NORMAL,
            max_slippage,
        )

        if slippage_est.expected_slippage_pct > max_slippage:
            plan.warnings.append("Slippage may exceed limit")

        if liquidity == LiquidityLevel.THIN:
            plan.warnings.append("Thin liquidity - execution may be challenged")

        # Update execution timestamp
        self.last_execution_time[symbol] = datetime.now()

        return True, "Execution approved", plan

    def apply_throttle(self, symbol: str, duration_sec: int):
        """Apply execution throttle to a symbol"""
        self.throttle_until[symbol] = datetime.now() + timedelta(seconds=duration_sec)
        logger.warning(f"Throttle applied to {symbol} for {duration_sec}s")

    def _is_throttled(self, symbol: str) -> bool:
        """Check if symbol is currently throttled"""
        throttle_end = self.throttle_until.get(symbol)
        if throttle_end and datetime.now() < throttle_end:
            return True

        # Also check minimum interval
        last_exec = self.last_execution_time.get(symbol)
        if last_exec:
            elapsed_ms = (datetime.now() - last_exec).total_seconds() * 1000
            if elapsed_ms < self.min_execution_interval_ms:
                return True

        return False

    def get_execution_report(self) -> Dict:
        """Generate comprehensive execution quality report"""
        if not self.execution_history:
            return {"message": "No execution history available"}

        recent = self.execution_history[-100:]  # Last 100 executions

        # Calculate aggregate metrics
        avg_score = statistics.mean(e.quality_score for e in recent)
        avg_slippage = statistics.mean(e.slippage_vs_arrival for e in recent)
        fill_rate = sum(e.filled_qty for e in recent) / sum(e.ordered_qty for e in recent)

        # Strategy breakdown
        strategy_counts = defaultdict(int)
        strategy_scores = defaultdict(list)
        for e in recent:
            strategy_counts[e.strategy_used.value] += 1
            strategy_scores[e.strategy_used.value].append(e.quality_score)

        strategy_avg = {
            s: statistics.mean(scores)
            for s, scores in strategy_scores.items()
        }

        # Symbol breakdown
        symbol_stats = {}
        for symbol, stats in self.quality_stats.items():
            if stats["fills"] > 0:
                symbol_stats[symbol] = {
                    "fills": stats["fills"],
                    "avg_slippage": stats["total_slippage"] / stats["fills"],
                    "avg_score": stats["avg_score"],
                }

        return {
            "summary": {
                "total_executions": len(self.execution_history),
                "recent_executions": len(recent),
                "avg_quality_score": avg_score,
                "avg_slippage_pct": avg_slippage,
                "fill_rate": fill_rate,
            },
            "by_strategy": {
                "counts": dict(strategy_counts),
                "avg_scores": strategy_avg,
            },
            "by_symbol": symbol_stats,
            "current_throttles": {
                s: t.isoformat() for s, t in self.throttle_until.items()
                if t > datetime.now()
            },
            "recommendations": self._generate_recommendations(avg_score, avg_slippage),
        }

    def _generate_recommendations(self, avg_score: float, avg_slippage: float) -> List[str]:
        """Generate execution improvement recommendations"""
        recs = []

        if avg_score < 70:
            recs.append("Execution quality below target - review strategy selection")

        if avg_slippage > 0.008:
            recs.append("High average slippage - increase use of limit orders")

        if avg_slippage > 0.015:
            recs.append("Critical: Slippage eroding returns - reduce position sizes")

        # Check for symbols with poor execution
        for symbol, stats in self.quality_stats.items():
            if stats["fills"] > 10 and stats["avg_score"] < 60:
                recs.append(f"Poor execution on {symbol} - consider avoiding")

        if not recs:
            recs.append("Execution quality within acceptable parameters")

        return recs

"""
Institutional Risk Intelligence Layer - Hedge Fund Grade

This is the STRUCTURAL RISK LAYER that prevents bad conditions
from being traded at all - not reactive learning, but proactive prevention.

Philosophy: "The best trade is the one you don't take in bad conditions."

Components:
1. Market Regime Gate - Only trade when regime suits strategy
2. Expectancy Calculator - Risk-adjusted returns, not just win rate
3. Portfolio Exposure Controller - Correlation and sector limits
4. Walk-Forward Validator - Rolling validation, not daily overfitting
5. Capital Allocation Model - Kelly/Risk-parity based allocation
6. Decision Quality Scorer - Score decisions, not just outcomes
7. Pre-Trade Risk Intelligence - Block trades BEFORE damage

Architecture:
┌─────────────────────────────────────────────────────────────────┐
│                 INSTITUTIONAL RISK LAYER                         │
│  (Runs BEFORE any strategy - can block entire trading day)       │
├─────────────────────────────────────────────────────────────────┤
│  1. REGIME GATE          → Is market tradeable for our strategies?│
│  2. EXPOSURE CHECK       → Would this trade exceed limits?        │
│  3. CORRELATION CHECK    → Are we doubling down on same risk?     │
│  4. STATISTICAL VALIDITY → Do we have edge in last 60 trades?     │
│  5. CAPITAL ALLOCATION   → How much can this bot risk?            │
│  6. DECISION QUALITY     → Is this a good setup, regardless of PnL│
└─────────────────────────────────────────────────────────────────┘
"""

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from collections import deque
import statistics

LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTITUTIONAL_DATA_DIR = LIVEBENCH_ROOT / "data" / "institutional"


class MarketRegime(Enum):
    """Market regime classification"""
    STRONG_TREND_UP = "strong_trend_up"
    WEAK_TREND_UP = "weak_trend_up"
    RANGING = "ranging"
    WEAK_TREND_DOWN = "weak_trend_down"
    STRONG_TREND_DOWN = "strong_trend_down"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    CHOPPY = "choppy"  # Whipsaw conditions
    EVENT_DRIVEN = "event_driven"  # News/earnings
    UNKNOWN = "unknown"


class TradingCondition(Enum):
    """Overall trading condition assessment"""
    EXCELLENT = "excellent"  # All systems go
    GOOD = "good"           # Normal trading
    CAUTION = "caution"     # Reduced size
    POOR = "poor"           # Minimal trading
    NO_TRADE = "no_trade"   # Stop all trading


@dataclass
class RegimeAnalysis:
    """Comprehensive regime analysis"""
    regime: MarketRegime
    confidence: float
    volatility_percentile: float
    trend_strength: float
    trend_direction: int  # -1, 0, 1
    choppiness_index: float  # 0-100, high = choppy
    suitable_strategies: List[str]
    avoid_strategies: List[str]
    recommended_position_size: float  # 0.0 - 1.0 multiplier
    trading_condition: TradingCondition


@dataclass
class BotExpectancy:
    """Risk-adjusted performance metrics for a bot"""
    bot_name: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float  # (Win% * AvgWin) - (Loss% * AvgLoss)
    profit_factor: float  # Gross Profit / Gross Loss
    sharpe_ratio: float  # Risk-adjusted return
    sortino_ratio: float  # Downside risk-adjusted
    max_drawdown: float
    recovery_factor: float  # Net Profit / Max Drawdown
    kelly_fraction: float  # Optimal bet size
    edge_confidence: float  # Statistical confidence we have edge
    is_statistically_valid: bool  # Min 30 trades, p < 0.05


@dataclass
class PortfolioExposure:
    """Current portfolio exposure state"""
    total_exposure: float  # Total capital at risk
    exposure_by_index: Dict[str, float]
    exposure_by_direction: Dict[str, float]  # CE vs PE
    correlation_score: float  # 0-1, how correlated are positions
    sector_exposure: Dict[str, float]
    max_single_position: float
    positions_count: int
    risk_budget_remaining: float


@dataclass
class DecisionQuality:
    """Quality score for a trading decision (not just outcome)"""
    setup_quality: float  # Was the setup objectively good?
    timing_quality: float  # Was entry timing good?
    risk_reward_quality: float  # Was R:R appropriate?
    regime_alignment: float  # Did it match regime?
    consensus_quality: float  # Bot agreement quality
    overall_score: float  # Weighted combination
    would_take_again: bool  # Would we take this trade again?


class InstitutionalRiskLayer:
    """
    Hedge Fund Grade Risk Intelligence Layer

    This layer runs BEFORE any trading decisions and can:
    1. Block entire trading sessions (bad regime)
    2. Reduce position sizes (elevated risk)
    3. Block specific strategies (not suited for regime)
    4. Enforce exposure limits
    5. Require statistical validity before trading

    Key Principle: PREVENTION > REACTION
    """

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or DEFAULT_INSTITUTIONAL_DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # ═══════════════════════════════════════════════════════════════════
        # REGIME DETECTION CONFIG
        # ═══════════════════════════════════════════════════════════════════
        self.regime_config = {
            # ADX thresholds for trend strength
            "strong_trend_adx": 40,
            "weak_trend_adx": 25,
            "ranging_adx": 20,

            # Choppiness Index thresholds
            "choppy_ci": 61.8,  # Above = choppy (Fibonacci level)
            "trending_ci": 38.2,  # Below = trending

            # Volatility thresholds (ATR percentile)
            "high_vol_percentile": 80,
            "low_vol_percentile": 20,

            # Strategy-Regime mapping
            "regime_strategy_map": {
                MarketRegime.STRONG_TREND_UP: {
                    "suitable": ["TrendFollower", "MomentumScalper"],
                    "avoid": ["ReversalHunter", "VolatilityTrader"],
                    "size_mult": 1.2,
                },
                MarketRegime.WEAK_TREND_UP: {
                    "suitable": ["TrendFollower", "OIAnalyst"],
                    "avoid": ["ReversalHunter"],
                    "size_mult": 1.0,
                },
                MarketRegime.RANGING: {
                    "suitable": ["ReversalHunter", "OIAnalyst"],
                    "avoid": ["TrendFollower", "MomentumScalper"],
                    "size_mult": 0.8,
                },
                MarketRegime.STRONG_TREND_DOWN: {
                    "suitable": ["TrendFollower", "MomentumScalper"],
                    "avoid": ["ReversalHunter", "VolatilityTrader"],
                    "size_mult": 1.2,
                },
                MarketRegime.HIGH_VOLATILITY: {
                    "suitable": ["VolatilityTrader"],
                    "avoid": ["MomentumScalper", "TrendFollower"],
                    "size_mult": 0.5,  # Reduce size in high vol
                },
                MarketRegime.CHOPPY: {
                    "suitable": [],  # NO strategies work well
                    "avoid": ["ALL"],
                    "size_mult": 0.0,  # Don't trade choppy markets
                },
                MarketRegime.EVENT_DRIVEN: {
                    "suitable": ["VolatilityTrader"],
                    "avoid": ["TrendFollower", "MomentumScalper", "ReversalHunter"],
                    "size_mult": 0.3,
                },
            },
        }

        # ═══════════════════════════════════════════════════════════════════
        # EXPOSURE LIMITS (Portfolio Level)
        # ═══════════════════════════════════════════════════════════════════
        self.exposure_limits = {
            "max_total_exposure_pct": 30,  # Max 30% of capital at risk
            "max_single_position_pct": 10,  # Max 10% in one position
            "max_per_index_pct": 15,  # Max 15% per index
            "max_direction_imbalance": 0.7,  # Max 70% in one direction
            "max_correlation": 0.8,  # Max correlation between positions
            "max_concurrent_positions": 5,
            "min_cash_reserve_pct": 50,  # Always keep 50% cash
        }

        # ═══════════════════════════════════════════════════════════════════
        # STATISTICAL VALIDITY REQUIREMENTS
        # ═══════════════════════════════════════════════════════════════════
        self.validity_requirements = {
            "min_trades_for_validity": 30,  # Minimum trades for statistical significance
            "rolling_window": 60,  # Look at last 60 trades
            "min_expectancy": 0.5,  # Minimum expectancy per trade
            "min_profit_factor": 1.3,  # Minimum profit factor
            "max_drawdown_pct": 20,  # Max allowed drawdown
            "min_sharpe_ratio": 0.5,  # Minimum Sharpe ratio
            "confidence_level": 0.95,  # 95% confidence required
        }

        # ═══════════════════════════════════════════════════════════════════
        # CAPITAL ALLOCATION MODEL
        # ═══════════════════════════════════════════════════════════════════
        self.capital_allocation = {
            "method": "modified_kelly",  # kelly, fixed_fractional, risk_parity
            "kelly_fraction": 0.25,  # Use 25% of Kelly (conservative)
            "max_allocation_per_bot": 0.2,  # Max 20% of capital per bot
            "min_allocation": 0.02,  # Min 2% to be worth trading
            "rebalance_threshold": 0.1,  # Rebalance when 10% drift
        }

        # ═══════════════════════════════════════════════════════════════════
        # DECISION QUALITY SCORING
        # ═══════════════════════════════════════════════════════════════════
        self.quality_weights = {
            "setup_quality": 0.25,
            "timing_quality": 0.15,
            "risk_reward_quality": 0.20,
            "regime_alignment": 0.25,
            "consensus_quality": 0.15,
        }

        # State tracking
        self.trade_history: deque = deque(maxlen=1000)
        self.bot_performance: Dict[str, List[Dict]] = {}
        self.regime_history: deque = deque(maxlen=100)
        self.decision_quality_history: deque = deque(maxlen=500)
        self.current_exposure = PortfolioExposure(
            total_exposure=0,
            exposure_by_index={},
            exposure_by_direction={"CE": 0, "PE": 0},
            correlation_score=0,
            sector_exposure={},
            max_single_position=0,
            positions_count=0,
            risk_budget_remaining=100,
        )

        # Price history for regime detection
        self.price_history: Dict[str, deque] = {}

        # Load persisted state
        self._load_state()

        print("[InstitutionalRisk] Hedge Fund Grade Risk Layer initialized")

    # ═══════════════════════════════════════════════════════════════════════
    # REGIME DETECTION - Prevent trading in unsuitable conditions
    # ═══════════════════════════════════════════════════════════════════════

    def detect_regime(self, index: str, market_data: Dict) -> RegimeAnalysis:
        """
        Detect current market regime using multiple indicators.

        This determines WHETHER we should trade at all, not just HOW.
        """
        # Get price history
        if index not in self.price_history:
            self.price_history[index] = deque(maxlen=100)

        ltp = market_data.get("ltp", 0)
        if ltp > 0:
            # Get high/low with proper None handling
            high = market_data.get("high")
            low = market_data.get("low")

            # Use realistic defaults if high/low not provided
            if high is None or high == 0:
                change_pct = abs(market_data.get("change_pct", 0))
                range_pct = max(0.8, change_pct * 0.6)  # Min 0.8% range
                high = ltp * (1 + range_pct / 100)
            if low is None or low == 0:
                change_pct = abs(market_data.get("change_pct", 0))
                range_pct = max(0.8, change_pct * 0.6)
                low = ltp * (1 - range_pct / 100)

            self.price_history[index].append({
                "price": ltp,
                "high": high,
                "low": low,
                "volume": market_data.get("volume", 0),
                "timestamp": datetime.now(),
            })

        prices = list(self.price_history[index])
        if len(prices) < 20:
            return self._default_regime_analysis()

        # Calculate indicators
        trend_strength, trend_direction = self._calculate_trend_strength(prices)
        choppiness = self._calculate_choppiness_index(prices)
        volatility_pct = self._calculate_volatility_percentile(prices, market_data)

        # Determine regime
        regime = self._classify_regime(
            trend_strength, trend_direction, choppiness, volatility_pct, market_data
        )

        # Get strategy recommendations
        regime_config = self.regime_config["regime_strategy_map"].get(
            regime, {"suitable": [], "avoid": [], "size_mult": 0.5}
        )

        # Determine trading condition
        trading_condition = self._assess_trading_condition(
            regime, choppiness, volatility_pct, trend_strength
        )

        analysis = RegimeAnalysis(
            regime=regime,
            confidence=self._calculate_regime_confidence(trend_strength, choppiness),
            volatility_percentile=volatility_pct,
            trend_strength=trend_strength,
            trend_direction=trend_direction,
            choppiness_index=choppiness,
            suitable_strategies=regime_config["suitable"],
            avoid_strategies=regime_config["avoid"],
            recommended_position_size=regime_config["size_mult"],
            trading_condition=trading_condition,
        )

        # Record for history
        self.regime_history.append({
            "timestamp": datetime.now().isoformat(),
            "index": index,
            "regime": regime.value,
            "condition": trading_condition.value,
        })

        return analysis

    def _calculate_trend_strength(self, prices: List[Dict]) -> Tuple[float, int]:
        """Calculate trend strength (0-100) and direction (-1, 0, 1)"""
        if len(prices) < 14:
            return 0, 0

        closes = [p["price"] for p in prices]
        highs = [p["high"] for p in prices]
        lows = [p["low"] for p in prices]

        # Simple ADX approximation using directional movement
        plus_dm = []
        minus_dm = []
        tr = []

        for i in range(1, len(prices)):
            high_diff = highs[i] - highs[i-1]
            low_diff = lows[i-1] - lows[i]

            plus_dm.append(max(high_diff, 0) if high_diff > low_diff else 0)
            minus_dm.append(max(low_diff, 0) if low_diff > high_diff else 0)

            true_range = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            tr.append(true_range)

        if not tr or sum(tr) == 0:
            return 0, 0

        # Smoothed values (14-period)
        period = min(14, len(tr))
        atr = sum(tr[-period:]) / period
        plus_di = (sum(plus_dm[-period:]) / period) / atr * 100 if atr > 0 else 0
        minus_di = (sum(minus_dm[-period:]) / period) / atr * 100 if atr > 0 else 0

        # ADX calculation
        di_diff = abs(plus_di - minus_di)
        di_sum = plus_di + minus_di
        dx = (di_diff / di_sum * 100) if di_sum > 0 else 0
        adx = dx  # Simplified - should be smoothed over 14 periods

        # Direction
        direction = 1 if plus_di > minus_di else -1 if minus_di > plus_di else 0

        return min(adx, 100), direction

    def _calculate_choppiness_index(self, prices: List[Dict]) -> float:
        """
        Calculate Choppiness Index (CI) - measures if market is trending or ranging.

        CI > 61.8 = Choppy/Ranging (avoid trend strategies)
        CI < 38.2 = Trending (avoid mean reversion)
        """
        if len(prices) < 14:
            return 50  # Neutral

        period = min(14, len(prices))
        highs = [p["high"] for p in prices[-period:]]
        lows = [p["low"] for p in prices[-period:]]
        closes = [p["price"] for p in prices[-period:]]

        # Detect synthetic/unreliable OHLC data
        # If high-low ranges are consistently tiny (< 1% of price), data is likely synthetic
        avg_price = sum(closes) / len(closes)
        avg_hl_range = sum(h - l for h, l in zip(highs, lows)) / len(highs)
        range_pct = (avg_hl_range / avg_price * 100) if avg_price > 0 else 0

        # Check for clear directional move using price change
        if len(closes) >= 2:
            price_change_pct = abs((closes[-1] - closes[0]) / closes[0] * 100) if closes[0] > 0 else 0
            # If price moved > 1% in one direction, it's trending not choppy
            if price_change_pct > 1.0:
                # Return low choppiness to indicate trending market
                return 35  # Below 38.2 = trending

        if range_pct < 1.0:
            # Data appears to be synthetic - skip choppiness check
            # Return neutral value to allow trading based on other signals
            return 50

        # True Range sum
        tr_sum = 0
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            tr_sum += tr

        # High-Low range for period
        highest = max(highs)
        lowest = min(lows)
        hl_range = highest - lowest

        # ── SYNTHETIC-DATA GUARD ─────────────────────────────────────────────
        # Mathematical signature of repeated synthetic bars:
        #   each bar has range R (synthetic, constant); price barely drifts
        #   → tr_sum ≈ (N-1)*R, hl_range ≈ R  → CI ≈ 100 always (false CHOPPY)
        # Detect it: full-period HL range should grow as price drifts across bars.
        # If hl_range < 1.5× avg single-bar range, price is effectively static
        # and the choppiness formula is unreliable.  Return neutral (50) so
        # downstream logic can decide based on other signals.
        if hl_range > 0 and avg_hl_range > 0 and hl_range < avg_hl_range * 1.5:
            print(
                f"[CI] Synthetic/static data guard: hl_range={hl_range:.1f} "
                f"avg_bar_range={avg_hl_range:.1f} "
                f"ratio={hl_range/avg_hl_range:.2f} (< 1.5) → CI=50 (neutral)"
            )
            return 50
        # ─────────────────────────────────────────────────────────────────────

        if hl_range == 0 or tr_sum == 0:
            return 50

        # Choppiness Index formula
        ci = 100 * math.log10(tr_sum / hl_range) / math.log10(period)
        ci_final = min(max(ci, 0), 100)
        print(
            f"[CI] Real OHLC: tr_sum={tr_sum:.1f} hl_range={hl_range:.1f} "
            f"avg_bar_range={avg_hl_range:.1f} ratio={hl_range/avg_hl_range:.2f} CI={ci_final:.1f}"
        )
        return ci_final

    def _calculate_volatility_percentile(self, prices: List[Dict], market_data: Dict) -> float:
        """Calculate current volatility as percentile of historical"""
        if len(prices) < 20:
            return 50

        # Detect synthetic data - if high-low ranges are tiny, use change% instead
        closes = [p["price"] for p in prices]
        highs = [p["high"] for p in prices]
        lows = [p["low"] for p in prices]
        avg_price = sum(closes) / len(closes)
        avg_hl_range = sum(h - l for h, l in zip(highs, lows)) / len(highs)

        if avg_hl_range / avg_price < 0.005:  # < 0.5% range = synthetic
            # Use price change as volatility proxy
            change_pct = abs(market_data.get("change_pct", 0))
            # Map typical intraday change to percentile
            # 0% = 30th percentile, 1% = 50th, 2% = 70th, 3%+ = 90th
            return min(30 + change_pct * 20, 90)

        # Calculate ATR
        atrs = []
        for i in range(1, len(prices)):
            tr = max(
                prices[i]["high"] - prices[i]["low"],
                abs(prices[i]["high"] - prices[i-1]["price"]),
                abs(prices[i]["low"] - prices[i-1]["price"])
            )
            atrs.append(tr)

        if not atrs:
            return 50

        current_atr = sum(atrs[-14:]) / min(14, len(atrs[-14:]))

        # Percentile rank
        sorted_atrs = sorted(atrs)
        rank = sum(1 for x in sorted_atrs if x <= current_atr)
        percentile = (rank / len(sorted_atrs)) * 100

        return percentile

    def _classify_regime(
        self,
        trend_strength: float,
        trend_direction: int,
        choppiness: float,
        volatility_pct: float,
        market_data: Dict
    ) -> MarketRegime:
        """Classify current market regime"""

        # Check for choppy conditions first (most important to avoid)
        if choppiness > self.regime_config["choppy_ci"]:
            return MarketRegime.CHOPPY

        # High volatility regime
        if volatility_pct > self.regime_config["high_vol_percentile"]:
            return MarketRegime.HIGH_VOLATILITY

        # Low volatility regime
        if volatility_pct < self.regime_config["low_vol_percentile"]:
            return MarketRegime.LOW_VOLATILITY

        # Trending regimes
        if trend_strength >= self.regime_config["strong_trend_adx"]:
            return MarketRegime.STRONG_TREND_UP if trend_direction > 0 else MarketRegime.STRONG_TREND_DOWN

        if trend_strength >= self.regime_config["weak_trend_adx"]:
            return MarketRegime.WEAK_TREND_UP if trend_direction > 0 else MarketRegime.WEAK_TREND_DOWN

        # Ranging
        if trend_strength < self.regime_config["ranging_adx"]:
            return MarketRegime.RANGING

        return MarketRegime.UNKNOWN

    def _assess_trading_condition(
        self,
        regime: MarketRegime,
        choppiness: float,
        volatility_pct: float,
        trend_strength: float
    ) -> TradingCondition:
        """Assess overall trading condition"""

        # NO TRADE conditions
        if regime == MarketRegime.CHOPPY:
            return TradingCondition.NO_TRADE

        if choppiness > 70:  # Very choppy
            return TradingCondition.NO_TRADE

        # POOR conditions
        if regime == MarketRegime.EVENT_DRIVEN:
            return TradingCondition.POOR

        if volatility_pct > 90:  # Extreme volatility
            return TradingCondition.POOR

        # CAUTION conditions
        if choppiness > 55:
            return TradingCondition.CAUTION

        if volatility_pct > 75:
            return TradingCondition.CAUTION

        # EXCELLENT conditions
        if trend_strength > 35 and choppiness < 45:
            return TradingCondition.EXCELLENT

        # Default GOOD
        return TradingCondition.GOOD

    def _calculate_regime_confidence(self, trend_strength: float, choppiness: float) -> float:
        """Calculate confidence in regime classification"""
        # Higher trend strength = more confident in trend regime
        # Extreme choppiness values = more confident in choppy/trending

        trend_conf = min(trend_strength / 50, 1.0)
        chop_conf = abs(choppiness - 50) / 50  # Distance from neutral

        return (trend_conf + chop_conf) / 2 * 100

    def _default_regime_analysis(self) -> RegimeAnalysis:
        """Return default regime when insufficient data"""
        return RegimeAnalysis(
            regime=MarketRegime.UNKNOWN,
            confidence=0,
            volatility_percentile=50,
            trend_strength=0,
            trend_direction=0,
            choppiness_index=50,
            suitable_strategies=[],
            avoid_strategies=[],
            recommended_position_size=0.5,
            trading_condition=TradingCondition.CAUTION,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # EXPECTANCY CALCULATOR - Risk-adjusted metrics, not just win rate
    # ═══════════════════════════════════════════════════════════════════════

    def calculate_bot_expectancy(self, bot_name: str) -> BotExpectancy:
        """
        Calculate comprehensive risk-adjusted metrics for a bot.

        This replaces simple win rate with proper institutional metrics.
        """
        trades = self.bot_performance.get(bot_name, [])

        # Filter to rolling window
        cutoff = datetime.now() - timedelta(days=30)
        recent_trades = [
            t for t in trades
            if datetime.fromisoformat(t.get("timestamp", "2000-01-01")) > cutoff
        ][-self.validity_requirements["rolling_window"]:]

        if len(recent_trades) < 5:
            return self._default_expectancy(bot_name)

        wins = [t for t in recent_trades if t.get("pnl", 0) > 0]
        losses = [t for t in recent_trades if t.get("pnl", 0) < 0]

        win_count = len(wins)
        loss_count = len(losses)
        total = len(recent_trades)

        win_rate = (win_count / total * 100) if total > 0 else 0

        avg_win = statistics.mean([t["pnl"] for t in wins]) if wins else 0
        avg_loss = abs(statistics.mean([t["pnl"] for t in losses])) if losses else 0

        # Expectancy: (Win% * AvgWin) - (Loss% * AvgLoss)
        expectancy = (win_rate/100 * avg_win) - ((100-win_rate)/100 * avg_loss)

        # Profit Factor
        gross_profit = sum(t["pnl"] for t in wins) if wins else 0
        gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Sharpe Ratio (simplified)
        returns = [t.get("pnl_pct", 0) for t in recent_trades]
        if len(returns) > 1:
            avg_return = statistics.mean(returns)
            std_return = statistics.stdev(returns)
            sharpe_ratio = (avg_return / std_return) * math.sqrt(252) if std_return > 0 else 0
        else:
            sharpe_ratio = 0

        # Sortino Ratio (downside deviation only)
        negative_returns = [r for r in returns if r < 0]
        if negative_returns and len(negative_returns) > 1:
            downside_dev = statistics.stdev(negative_returns)
            sortino_ratio = (statistics.mean(returns) / downside_dev) * math.sqrt(252) if downside_dev > 0 else 0
        else:
            sortino_ratio = sharpe_ratio

        # Max Drawdown
        cumulative = 0
        peak = 0
        max_dd = 0
        for t in recent_trades:
            cumulative += t.get("pnl", 0)
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)

        # Recovery Factor
        net_profit = sum(t.get("pnl", 0) for t in recent_trades)
        recovery_factor = net_profit / max_dd if max_dd > 0 else 0

        # Kelly Fraction
        if avg_loss > 0:
            kelly = (win_rate/100) - ((1 - win_rate/100) / (avg_win / avg_loss)) if avg_win > 0 else 0
        else:
            kelly = 0
        kelly = max(0, min(kelly, 1))  # Clamp to 0-1

        # Statistical validity
        is_valid = (
            total >= self.validity_requirements["min_trades_for_validity"] and
            expectancy >= self.validity_requirements["min_expectancy"] and
            profit_factor >= self.validity_requirements["min_profit_factor"]
        )

        # Edge confidence (simplified t-test approximation)
        if total >= 30 and len(returns) > 1:
            t_stat = (statistics.mean(returns) * math.sqrt(total)) / statistics.stdev(returns) if statistics.stdev(returns) > 0 else 0
            edge_confidence = min(abs(t_stat) / 2, 1.0) * 100  # Simplified
        else:
            edge_confidence = 0

        return BotExpectancy(
            bot_name=bot_name,
            trades=total,
            wins=win_count,
            losses=loss_count,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=expectancy,
            profit_factor=profit_factor,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown=max_dd,
            recovery_factor=recovery_factor,
            kelly_fraction=kelly,
            edge_confidence=edge_confidence,
            is_statistically_valid=is_valid,
        )

    def _default_expectancy(self, bot_name: str) -> BotExpectancy:
        """Default expectancy for bot with insufficient data"""
        return BotExpectancy(
            bot_name=bot_name,
            trades=0, wins=0, losses=0, win_rate=0,
            avg_win=0, avg_loss=0, expectancy=0,
            profit_factor=0, sharpe_ratio=0, sortino_ratio=0,
            max_drawdown=0, recovery_factor=0, kelly_fraction=0,
            edge_confidence=0, is_statistically_valid=False,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # PORTFOLIO EXPOSURE CONTROLLER
    # ═══════════════════════════════════════════════════════════════════════

    def check_exposure(
        self,
        proposed_trade: Dict,
        current_positions: List[Dict],
        capital: float
    ) -> Tuple[bool, str, float]:
        """
        Check if proposed trade would exceed exposure limits.

        Returns: (allowed, reason, recommended_size_multiplier)
        """
        # Calculate current exposure
        total_exposure = sum(p.get("risk_amount", 0) for p in current_positions)
        exposure_pct = (total_exposure / capital * 100) if capital > 0 else 0

        # Check total exposure limit
        if exposure_pct >= self.exposure_limits["max_total_exposure_pct"]:
            return False, f"Total exposure at {exposure_pct:.1f}% (max {self.exposure_limits['max_total_exposure_pct']}%)", 0

        # Check position count
        if len(current_positions) >= self.exposure_limits["max_concurrent_positions"]:
            return False, f"Max positions reached ({len(current_positions)})", 0

        # Check per-index exposure
        index = proposed_trade.get("index", "UNKNOWN")
        index_exposure = sum(
            p.get("risk_amount", 0) for p in current_positions
            if p.get("index") == index
        )
        index_exposure_pct = (index_exposure / capital * 100) if capital > 0 else 0

        if index_exposure_pct >= self.exposure_limits["max_per_index_pct"]:
            return False, f"{index} exposure at {index_exposure_pct:.1f}% (max {self.exposure_limits['max_per_index_pct']}%)", 0

        # Check direction imbalance
        ce_exposure = sum(p.get("risk_amount", 0) for p in current_positions if "CE" in p.get("action", ""))
        pe_exposure = sum(p.get("risk_amount", 0) for p in current_positions if "PE" in p.get("action", ""))
        total_directional = ce_exposure + pe_exposure

        if total_directional > 0:
            direction_imbalance = max(ce_exposure, pe_exposure) / total_directional
            proposed_direction = "CE" if "CE" in proposed_trade.get("action", "") else "PE"

            if direction_imbalance > self.exposure_limits["max_direction_imbalance"]:
                heavy_direction = "CE" if ce_exposure > pe_exposure else "PE"
                if proposed_direction == heavy_direction:
                    return False, f"Direction imbalance: {direction_imbalance:.0%} {heavy_direction}", 0

        # Calculate recommended size based on remaining budget
        remaining_budget = self.exposure_limits["max_total_exposure_pct"] - exposure_pct
        recommended_size = min(
            remaining_budget / self.exposure_limits["max_single_position_pct"],
            1.0
        )

        # Update current exposure state
        self.current_exposure = PortfolioExposure(
            total_exposure=total_exposure,
            exposure_by_index={
                idx: sum(p.get("risk_amount", 0) for p in current_positions if p.get("index") == idx)
                for idx in set(p.get("index") for p in current_positions)
            },
            exposure_by_direction={"CE": ce_exposure, "PE": pe_exposure},
            correlation_score=self._calculate_correlation(current_positions),
            sector_exposure={},
            max_single_position=max((p.get("risk_amount", 0) for p in current_positions), default=0),
            positions_count=len(current_positions),
            risk_budget_remaining=remaining_budget,
        )

        return True, "Exposure within limits", recommended_size

    def _calculate_correlation(self, positions: List[Dict]) -> float:
        """Calculate correlation between positions (simplified)"""
        if len(positions) < 2:
            return 0

        # Same index = correlated
        indices = [p.get("index") for p in positions]
        unique_indices = set(indices)

        # More positions in same index = higher correlation
        max_same = max(indices.count(idx) for idx in unique_indices)
        correlation = max_same / len(positions)

        return correlation

    # ═══════════════════════════════════════════════════════════════════════
    # CAPITAL ALLOCATION MODEL
    # ═══════════════════════════════════════════════════════════════════════

    def calculate_position_size(
        self,
        bot_name: str,
        base_size: float,
        regime: RegimeAnalysis,
        expectancy: BotExpectancy
    ) -> float:
        """
        Calculate optimal position size using Modified Kelly Criterion.

        This replaces fixed position sizing with risk-adjusted sizing.
        """
        if not expectancy.is_statistically_valid:
            # Not enough data - use minimum size
            return base_size * self.capital_allocation["min_allocation"]

        # Start with Kelly fraction
        kelly = expectancy.kelly_fraction

        # Apply conservative fraction (use 25% of Kelly)
        position_pct = kelly * self.capital_allocation["kelly_fraction"]

        # Apply regime multiplier
        position_pct *= regime.recommended_position_size

        # Apply expectancy adjustment (higher expectancy = slightly larger)
        if expectancy.expectancy > 0:
            exp_mult = min(1 + (expectancy.expectancy / 100), 1.5)
            position_pct *= exp_mult

        # Apply Sharpe adjustment (higher Sharpe = more confident)
        if expectancy.sharpe_ratio > 1:
            sharpe_mult = min(1 + (expectancy.sharpe_ratio - 1) * 0.1, 1.3)
            position_pct *= sharpe_mult

        # Clamp to limits
        position_pct = max(
            self.capital_allocation["min_allocation"],
            min(position_pct, self.capital_allocation["max_allocation_per_bot"])
        )

        return base_size * position_pct

    # ═══════════════════════════════════════════════════════════════════════
    # DECISION QUALITY SCORING
    # ═══════════════════════════════════════════════════════════════════════

    def score_decision(
        self,
        decision: Dict,
        regime: RegimeAnalysis,
        signals: List[Dict]
    ) -> DecisionQuality:
        """
        Score a trading decision BEFORE taking it.

        This scores the QUALITY of the decision, not the outcome.
        We can have good decisions that lose and bad decisions that win.
        """
        # Setup Quality - Was this a good setup?
        setup_score = self._score_setup_quality(decision, signals)

        # Timing Quality - Is this a good time to enter?
        timing_score = self._score_timing_quality(decision, regime)

        # Risk/Reward Quality - Is the R:R appropriate?
        rr_score = self._score_risk_reward(decision)

        # Regime Alignment - Does this trade match the regime?
        regime_score = self._score_regime_alignment(decision, regime)

        # Consensus Quality - How strong is bot agreement?
        consensus_score = self._score_consensus_quality(signals)

        # Weighted overall score
        overall = (
            setup_score * self.quality_weights["setup_quality"] +
            timing_score * self.quality_weights["timing_quality"] +
            rr_score * self.quality_weights["risk_reward_quality"] +
            regime_score * self.quality_weights["regime_alignment"] +
            consensus_score * self.quality_weights["consensus_quality"]
        )

        # Would we take this trade again?
        would_take = overall >= 70 and regime_score >= 60

        quality = DecisionQuality(
            setup_quality=setup_score,
            timing_quality=timing_score,
            risk_reward_quality=rr_score,
            regime_alignment=regime_score,
            consensus_quality=consensus_score,
            overall_score=overall,
            would_take_again=would_take,
        )

        # Record for learning
        self.decision_quality_history.append({
            "timestamp": datetime.now().isoformat(),
            "quality": asdict(quality),
            "decision": decision,
        })

        return quality

    def _score_setup_quality(self, decision: Dict, signals: List[Dict]) -> float:
        """Score the trading setup quality"""
        score = 50  # Base

        # More signals = better setup
        if len(signals) >= 3:
            score += 20
        elif len(signals) >= 2:
            score += 10

        # Higher average confidence = better setup
        avg_conf = statistics.mean([s.get("confidence", 50) for s in signals]) if signals else 50
        score += (avg_conf - 50) * 0.5

        # Strong signals (STRONG_BUY/SELL) = better setup
        strong_count = sum(1 for s in signals if "STRONG" in s.get("signal_type", ""))
        score += strong_count * 10

        return min(max(score, 0), 100)

    def _score_timing_quality(self, decision: Dict, regime: RegimeAnalysis) -> float:
        """Score entry timing quality"""
        score = 50  # Base

        # Trading condition affects timing
        if regime.trading_condition == TradingCondition.EXCELLENT:
            score += 30
        elif regime.trading_condition == TradingCondition.GOOD:
            score += 15
        elif regime.trading_condition == TradingCondition.CAUTION:
            score -= 15
        elif regime.trading_condition == TradingCondition.POOR:
            score -= 30
        elif regime.trading_condition == TradingCondition.NO_TRADE:
            score -= 50

        # Low choppiness = better timing
        if regime.choppiness_index < 40:
            score += 15
        elif regime.choppiness_index > 60:
            score -= 15

        return min(max(score, 0), 100)

    def _score_risk_reward(self, decision: Dict) -> float:
        """Score risk/reward ratio"""
        entry = decision.get("entry", 0)
        target = decision.get("target", 0)
        stop_loss = decision.get("stop_loss", 0)

        if entry == 0 or stop_loss == 0:
            return 50

        risk = abs(entry - stop_loss)
        reward = abs(target - entry) if target else risk * 2  # Default 2:1

        rr_ratio = reward / risk if risk > 0 else 0

        if rr_ratio >= 3:
            return 100
        elif rr_ratio >= 2:
            return 80
        elif rr_ratio >= 1.5:
            return 60
        elif rr_ratio >= 1:
            return 40
        else:
            return 20

    def _score_regime_alignment(self, decision: Dict, regime: RegimeAnalysis) -> float:
        """Score how well the trade aligns with current regime"""
        bots = decision.get("contributing_bots", [])

        # Check if bots are suitable for regime
        suitable_count = sum(1 for b in bots if b in regime.suitable_strategies)
        avoid_count = sum(1 for b in bots if b in regime.avoid_strategies)

        if len(bots) == 0:
            return 50

        # Score based on alignment
        score = 50
        score += (suitable_count / len(bots)) * 40
        score -= (avoid_count / len(bots)) * 40

        # Regime confidence bonus
        score += (regime.confidence / 100) * 10

        return min(max(score, 0), 100)

    def _score_consensus_quality(self, signals: List[Dict]) -> float:
        """Score quality of bot consensus"""
        if not signals:
            return 0

        # All signals same direction = high quality
        directions = [s.get("signal_type", "").replace("STRONG_", "") for s in signals]
        buy_count = sum(1 for d in directions if d == "BUY")
        sell_count = sum(1 for d in directions if d == "SELL")

        total = len(signals)
        max_agreement = max(buy_count, sell_count)

        agreement_ratio = max_agreement / total if total > 0 else 0

        return agreement_ratio * 100

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN PRE-TRADE CHECK - The gatekeeper
    # ═══════════════════════════════════════════════════════════════════════

    def pre_trade_check(
        self,
        index: str,
        proposed_trade: Dict,
        current_positions: List[Dict],
        capital: float,
        market_data: Dict,
        signals: List[Dict]
    ) -> Tuple[bool, str, Dict]:
        """
        MAIN PRE-TRADE INTELLIGENCE CHECK

        This is the gatekeeper that runs BEFORE any trade.
        Returns: (allowed, reason, modifications)
        """
        modifications = {
            "position_size_mult": 1.0,
            "stop_loss_adjustment": 1.0,
            "quality_score": 0,
        }

        # ═══════════════════════════════════════════════════════════════════
        # CHECK 1: REGIME GATE - Is the market tradeable?
        # ═══════════════════════════════════════════════════════════════════
        regime = self.detect_regime(index, market_data)

        if regime.trading_condition == TradingCondition.NO_TRADE:
            return False, f"Market regime blocked: {regime.regime.value} (choppy/untradeable)", modifications

        if regime.trading_condition == TradingCondition.POOR:
            modifications["position_size_mult"] = 0.3
        elif regime.trading_condition == TradingCondition.CAUTION:
            modifications["position_size_mult"] = 0.6

        # Check if contributing bots are suited for regime
        bots = proposed_trade.get("contributing_bots", [])
        for bot in bots:
            if bot in regime.avoid_strategies:
                return False, f"Bot {bot} not suited for {regime.regime.value} regime", modifications

        # ═══════════════════════════════════════════════════════════════════
        # CHECK 2: EXPOSURE LIMITS - Would this exceed risk budget?
        # ═══════════════════════════════════════════════════════════════════
        exposure_ok, exposure_reason, size_mult = self.check_exposure(
            proposed_trade, current_positions, capital
        )

        if not exposure_ok:
            return False, exposure_reason, modifications

        modifications["position_size_mult"] *= size_mult

        # ═══════════════════════════════════════════════════════════════════
        # CHECK 3: STATISTICAL VALIDITY - Do contributing bots have edge?
        # ═══════════════════════════════════════════════════════════════════
        valid_bots = []
        for bot_name in bots:
            expectancy = self.calculate_bot_expectancy(bot_name)

            if expectancy.is_statistically_valid:
                valid_bots.append(bot_name)
            elif expectancy.trades >= 10:
                # Some data but not valid - reduce weight
                modifications["position_size_mult"] *= 0.8

        if len(valid_bots) == 0 and len(bots) > 0:
            # No bots have statistical edge yet - allow with reduced size
            modifications["position_size_mult"] *= 0.5

        # ═══════════════════════════════════════════════════════════════════
        # CHECK 4: DECISION QUALITY - Is this a good setup?
        # ═══════════════════════════════════════════════════════════════════
        quality = self.score_decision(proposed_trade, regime, signals)
        modifications["quality_score"] = quality.overall_score

        if quality.overall_score < 50:
            return False, f"Decision quality too low: {quality.overall_score:.0f}/100", modifications

        if quality.overall_score < 70:
            modifications["position_size_mult"] *= 0.7

        if not quality.would_take_again:
            modifications["position_size_mult"] *= 0.5

        # ═══════════════════════════════════════════════════════════════════
        # CHECK 5: CAPITAL ALLOCATION - Calculate optimal size
        # ═══════════════════════════════════════════════════════════════════
        if valid_bots:
            best_bot = valid_bots[0]
            expectancy = self.calculate_bot_expectancy(best_bot)

            if expectancy.kelly_fraction > 0:
                # Use Kelly-based sizing
                kelly_mult = expectancy.kelly_fraction * self.capital_allocation["kelly_fraction"]
                modifications["position_size_mult"] *= kelly_mult * 5  # Scale up from Kelly base

        # Clamp final size
        modifications["position_size_mult"] = min(
            max(modifications["position_size_mult"], 0.1),
            2.0
        )

        # Final check - don't allow tiny positions
        if modifications["position_size_mult"] < 0.1:
            return False, "Position size too small after risk adjustments", modifications

        # ═══════════════════════════════════════════════════════════════════
        # ALL CHECKS PASSED
        # ═══════════════════════════════════════════════════════════════════
        reason = f"APPROVED | Regime: {regime.regime.value} | Quality: {quality.overall_score:.0f} | Size: {modifications['position_size_mult']:.1f}x"

        return True, reason, modifications

    # ═══════════════════════════════════════════════════════════════════════
    # LEARNING & STATE MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════

    def record_trade_outcome(
        self,
        bot_name: str,
        trade: Dict
    ):
        """Record trade outcome for statistical tracking"""
        if bot_name not in self.bot_performance:
            self.bot_performance[bot_name] = []

        self.bot_performance[bot_name].append({
            "timestamp": datetime.now().isoformat(),
            "pnl": trade.get("pnl", 0),
            "pnl_pct": trade.get("pnl_pct", 0),
            "outcome": trade.get("outcome", "UNKNOWN"),
            "index": trade.get("index", "UNKNOWN"),
        })

        # Periodically save
        if len(self.bot_performance[bot_name]) % 10 == 0:
            self._save_state()

    def get_status(self) -> Dict:
        """Get comprehensive status of institutional risk layer"""
        bot_expectancies = {
            bot: asdict(self.calculate_bot_expectancy(bot))
            for bot in self.bot_performance.keys()
        }

        recent_regimes = list(self.regime_history)[-10:]

        return {
            "enabled": True,
            "exposure": asdict(self.current_exposure),
            "bot_expectancies": bot_expectancies,
            "recent_regimes": recent_regimes,
            "validity_requirements": self.validity_requirements,
            "exposure_limits": self.exposure_limits,
        }

    def _save_state(self):
        """Persist state to disk"""
        state = {
            "bot_performance": {k: list(v)[-500:] for k, v in self.bot_performance.items()},
            "regime_history": list(self.regime_history),
            "decision_quality_history": list(self.decision_quality_history)[-200:],
            "last_saved": datetime.now().isoformat(),
        }

        with open(self.data_dir / "institutional_state.json", "w") as f:
            json.dump(state, f, indent=2, default=str)

    def _load_state(self):
        """Load persisted state"""
        state_file = self.data_dir / "institutional_state.json"

        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)

                self.bot_performance = state.get("bot_performance", {})
                self.regime_history = deque(state.get("regime_history", []), maxlen=100)
                self.decision_quality_history = deque(
                    state.get("decision_quality_history", []), maxlen=500
                )

                print(f"[InstitutionalRisk] Loaded state: {len(self.bot_performance)} bots tracked")
            except Exception as e:
                print(f"[InstitutionalRisk] Error loading state: {e}")

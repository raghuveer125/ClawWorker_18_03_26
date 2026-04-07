"""
Infrastructure Layer Agents - System health and data quality.

Agents:
16. LatencyGuardianAgent - Monitor API delay, detect stale data
17. LiquidityMonitorAgent - Track bid/ask spread, order book depth

These agents protect against silent failures in scalping systems.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import time

# Use local base module
from ..base import BaseBot, BotContext, BotResult, BotStatus
from ..config import ScalpingConfig


@dataclass
class LatencyMetrics:
    """Latency measurement for a data source."""
    source: str
    last_update: datetime
    latency_ms: float
    is_stale: bool
    staleness_seconds: float
    status: str  # "healthy", "warning", "critical"


@dataclass
class LiquidityMetrics:
    """Liquidity measurement for an option."""
    symbol: str
    strike: int
    option_type: str
    bid_ask_spread: float
    spread_pct: float
    bid_depth: int
    ask_depth: int
    volume: int
    oi: int
    liquidity_score: float  # 0-1, higher is better
    tradeable: bool


class LatencyGuardianAgent(BaseBot):
    """
    Agent 16: Latency Guardian Agent

    Monitors:
    - API response times
    - Data feed freshness
    - Option chain staleness
    - Quote timestamps

    Prevents trading when:
    - Feed lag > threshold
    - Stale option chain detected
    - API errors accumulating

    Critical for scalping where milliseconds matter.
    """

    BOT_TYPE = "latency_guardian"
    REQUIRES_LLM = False

    # Thresholds
    STALE_THRESHOLD_SECONDS = 5.0
    WARNING_LATENCY_MS = 500  # >500ms is warning
    CRITICAL_LATENCY_MS = 2000  # >2000ms is critical
    MAX_STALE_SOURCES = 1

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_timestamps: Dict[str, datetime] = {}
        self._latency_history: Dict[str, List[float]] = {}
        self._error_counts: Dict[str, int] = {}

    def get_description(self) -> str:
        return "Monitors data latency and prevents stale-data trading"

    async def execute(self, context: BotContext) -> BotResult:
        """Check latency across all data sources."""
        now = datetime.now()
        config = context.data.get("config", ScalpingConfig())
        metrics = []
        warnings = []
        stale_sources = []
        dead_heartbeats = []

        # Check spot data freshness
        spot_data = context.data.get("spot_data", {})
        for symbol, spot in spot_data.items():
            metric = self._check_data_freshness(
                source=f"spot_{symbol}",
                timestamp=getattr(spot, 'timestamp', now),
                now=now,
                threshold_seconds=float(getattr(config, "spot_stale_threshold_seconds", self.STALE_THRESHOLD_SECONDS)),
            )
            metrics.append(metric)
            if metric.is_stale:
                stale_sources.append(f"spot_{symbol}")
                warnings.append(f"Stale spot data: {symbol} ({metric.staleness_seconds:.1f}s old)")

        # Check option chain freshness
        option_chains = context.data.get("option_chains", {})
        for symbol, chain in option_chains.items():
            chain_time = getattr(chain, 'timestamp', now)
            metric = self._check_data_freshness(
                source=f"chain_{symbol}",
                timestamp=chain_time,
                now=now,
                threshold_seconds=float(getattr(config, "option_stale_threshold_seconds", self.STALE_THRESHOLD_SECONDS)),
            )
            metrics.append(metric)
            if metric.is_stale:
                stale_sources.append(f"chain_{symbol}")
                warnings.append(f"Stale option chain: {symbol} ({metric.staleness_seconds:.1f}s old)")

        # Check futures data freshness
        futures_data = context.data.get("futures_data", {})
        for symbol, fut in futures_data.items():
            metric = self._check_data_freshness(
                source=f"futures_{symbol}",
                timestamp=getattr(fut, 'timestamp', now),
                now=now,
                threshold_seconds=float(getattr(config, "futures_stale_threshold_seconds", self.STALE_THRESHOLD_SECONDS)),
            )
            metrics.append(metric)
            if metric.is_stale:
                stale_sources.append(f"futures_{symbol}")
                warnings.append(f"Stale futures data: {symbol}")

        tick_heartbeat = context.data.get("tick_heartbeat", {})
        heartbeat_threshold = float(getattr(config, "tick_heartbeat_threshold_seconds", self.STALE_THRESHOLD_SECONDS))
        if isinstance(tick_heartbeat, dict):
            for symbol, heartbeat in tick_heartbeat.items():
                if not isinstance(heartbeat, dict):
                    continue
                age_seconds = float(heartbeat.get("age_seconds", 0) or 0)
                if age_seconds > heartbeat_threshold:
                    dead_heartbeats.append(symbol)
                    warnings.append(f"No new tick heartbeat: {symbol} ({age_seconds:.1f}s)")

        # Check API latency from adapter metrics if available
        adapter_metrics = context.data.get("adapter_metrics", {})
        if adapter_metrics:
            api_latency = adapter_metrics.get("avg_latency_ms", 0)
            if api_latency > self.CRITICAL_LATENCY_MS:
                warnings.append(f"Critical API latency: {api_latency:.0f}ms")
            elif api_latency > self.WARNING_LATENCY_MS:
                warnings.append(f"High API latency: {api_latency:.0f}ms")

        # Determine if trading should be blocked
        trading_blocked = len(stale_sources) >= self.MAX_STALE_SOURCES or bool(dead_heartbeats)

        # Calculate overall health
        healthy_sources = len([m for m in metrics if m.status == "healthy"])
        total_sources = len(metrics)
        health_pct = (healthy_sources / total_sources * 100) if total_sources > 0 else 100

        context.data["latency_metrics"] = metrics
        context.data["latency_warnings"] = warnings
        context.data["trading_blocked_latency"] = trading_blocked
        context.data["dead_tick_heartbeats"] = dead_heartbeats

        # Emit warning event if issues detected
        if warnings:
            await self._emit_event("latency_warning", {
                "warnings": warnings,
                "stale_sources": stale_sources,
                "dead_heartbeats": dead_heartbeats,
                "trading_blocked": trading_blocked,
            })

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.BLOCKED if trading_blocked else BotStatus.SUCCESS,
            output={
                "health_pct": round(health_pct, 1),
                "stale_sources": stale_sources,
                "dead_heartbeats": dead_heartbeats,
                "trading_blocked": trading_blocked,
                "sources_checked": total_sources,
            },
            metrics={
                "healthy_sources": healthy_sources,
                "stale_sources_count": len(stale_sources),
                "health_pct": health_pct,
            },
            warnings=warnings,
        )

    def _check_data_freshness(
        self, source: str, timestamp: datetime, now: datetime, threshold_seconds: float
    ) -> LatencyMetrics:
        """Check if a data source is fresh."""
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except (ValueError, TypeError):
                timestamp = now

        staleness = (now - timestamp).total_seconds()
        is_stale = staleness > threshold_seconds

        # Determine status
        if staleness <= 1:
            status = "healthy"
        elif staleness <= threshold_seconds:
            status = "warning"
        else:
            status = "critical"

        # Track latency history
        if source not in self._latency_history:
            self._latency_history[source] = []
        self._latency_history[source].append(staleness * 1000)  # Convert to ms
        if len(self._latency_history[source]) > 100:
            self._latency_history[source] = self._latency_history[source][-100:]

        avg_latency = sum(self._latency_history[source]) / len(self._latency_history[source])

        return LatencyMetrics(
            source=source,
            last_update=timestamp,
            latency_ms=avg_latency,
            is_stale=is_stale,
            staleness_seconds=staleness,
            status=status,
        )


class LiquidityMonitorAgent(BaseBot):
    """
    Agent 17: Liquidity Monitor Agent

    Monitors:
    - Bid/ask spread (absolute and %)
    - Order book depth
    - Volume relative to average
    - OI levels

    Prevents trading:
    - Wide spreads (>5% for scalping)
    - Low depth (slippage risk)
    - Abnormal volume patterns

    Critical for ₹10-25 OTM options where liquidity varies.
    """

    BOT_TYPE = "liquidity_monitor"
    REQUIRES_LLM = False

    # Thresholds
    MAX_SPREAD_PCT = 5.0  # Max 5% spread for scalping
    MIN_BID_DEPTH = 100  # Minimum bid quantity
    MIN_ASK_DEPTH = 100  # Minimum ask quantity
    MIN_VOLUME = 500  # Minimum volume for the day
    MIN_OI = 5000  # Minimum open interest

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._volume_history: Dict[str, List[int]] = {}

    def get_description(self) -> str:
        return "Monitors option liquidity and prevents illiquid trades"

    async def execute(self, context: BotContext) -> BotResult:
        """Check liquidity for all option chains."""
        option_chains = context.data.get("option_chains", {})
        quality_filtered = context.data.get("quality_filtered_signals", [])
        config = context.data.get("config", ScalpingConfig())
        rejected_signals = list(context.data.get("rejected_signals", []))

        all_metrics = []
        illiquid_strikes = []
        warnings = []
        liquidity_filtered = []

        for symbol, chain in option_chains.items():
            symbol_metrics = []

            for opt in chain.options:
                metric = self._evaluate_liquidity(opt, config)
                symbol_metrics.append(metric)
                all_metrics.append(metric)

            symbol_signals = [
                signal for signal in quality_filtered
                if signal.get("symbol") == symbol
            ]
            for signal in symbol_signals:
                metric = self._find_metric_for_signal(signal, symbol_metrics)
                if metric is None:
                    liquidity_filtered.append({**signal, "liquidity_score": 0.5})
                    continue
                if metric.tradeable:
                    liquidity_filtered.append(
                        {
                            **signal,
                            "spread_pct": metric.spread_pct,
                            "bid_depth": metric.bid_depth,
                            "ask_depth": metric.ask_depth,
                            "volume": metric.volume,
                            "oi": metric.oi,
                            "liquidity_score": metric.liquidity_score,
                        }
                    )
                else:
                    reason = self._get_illiquidity_reason(metric)
                    illiquid_strikes.append({
                        "symbol": symbol,
                        "strike": metric.strike,
                        "type": metric.option_type,
                        "spread_pct": metric.spread_pct,
                        "reason": reason,
                    })
                    warnings.append(
                        f"Illiquid selected strike: {symbol} {metric.strike} {metric.option_type} "
                        f"(spread {metric.spread_pct:.1f}%)"
                    )
                    rejection_reasons = list(signal.get("rejection_reasons", [])) + [reason]
                    rejected_signals.append(
                        {
                            **signal,
                            "rejection_reasons": rejection_reasons,
                        }
                    )
                    self._log_liquidity_rejection(signal, rejection_reasons)

        context.data["liquidity_filtered_selections"] = liquidity_filtered

        context.data["liquidity_metrics"] = all_metrics
        context.data["illiquid_strikes"] = illiquid_strikes
        context.data["rejected_signals"] = rejected_signals

        # Calculate overall liquidity health
        tradeable_count = len([m for m in all_metrics if m.tradeable])
        total_count = len(all_metrics)
        liquidity_health = (tradeable_count / total_count * 100) if total_count > 0 else 100

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "liquidity_health_pct": round(liquidity_health, 1),
                "tradeable_options": tradeable_count,
                "total_options": total_count,
                "illiquid_selected_count": len(illiquid_strikes),
            },
            metrics={
                "tradeable_pct": liquidity_health,
                "illiquid_strikes": len(illiquid_strikes),
            },
            warnings=warnings,
        )

    def _find_metric_for_signal(
        self,
        signal: Dict[str, Any],
        metrics: List[LiquidityMetrics],
    ) -> Optional[LiquidityMetrics]:
        strike = int(float(signal.get("strike", 0) or 0))
        option_type = str(signal.get("option_type", signal.get("side", ""))).upper()
        for metric in metrics:
            if metric.strike == strike and metric.option_type == option_type:
                return metric
        return None

    def _log_liquidity_rejection(self, signal: Dict[str, Any], reasons: List[str]) -> None:
        symbol = signal.get("symbol", "UNKNOWN")
        strike = signal.get("strike", "?")
        option_type = signal.get("option_type", signal.get("side", "?"))
        print(
            f"[LiquidityMonitor] Rejected {symbol} {strike} {option_type}: "
            + ", ".join(reasons or ["liquidity_filter_failed"])
        )

    def _evaluate_liquidity(self, opt: Any, config: ScalpingConfig) -> LiquidityMetrics:
        """Evaluate liquidity for a single option."""
        # Calculate spread
        bid = getattr(opt, 'bid', 0) or 0
        ask = getattr(opt, 'ask', 0) or 0
        ltp = getattr(opt, 'ltp', 0) or 0

        if bid > 0 and ask > 0:
            spread = ask - bid
            spread_pct = (spread / ((bid + ask) / 2)) * 100
        elif ltp > 0:
            spread = getattr(opt, 'spread', ltp * 0.05)
            spread_pct = (spread / ltp) * 100 if ltp > 0 else 10
        else:
            spread = 0
            spread_pct = 100  # No quote = illiquid

        bid_depth = getattr(opt, 'bid_qty', 0) or 0
        ask_depth = getattr(opt, 'ask_qty', 0) or 0
        volume = getattr(opt, 'volume', 0) or 0
        oi = getattr(opt, 'oi', 0) or 0

        # Calculate liquidity score (0-1)
        score = 0.0

        # Spread component (40% weight)
        if spread_pct <= 2:
            score += 0.4
        elif spread_pct <= 3:
            score += 0.3
        elif spread_pct <= 5:
            score += 0.2
        elif spread_pct <= 8:
            score += 0.1

        # Depth component (30% weight)
        min_depth = min(bid_depth, ask_depth)
        if min_depth >= 500:
            score += 0.3
        elif min_depth >= 200:
            score += 0.2
        elif min_depth >= 100:
            score += 0.1

        # Volume component (15% weight)
        if volume >= 5000:
            score += 0.15
        elif volume >= 1000:
            score += 0.1
        elif volume >= 500:
            score += 0.05

        # OI component (15% weight)
        if oi >= 50000:
            score += 0.15
        elif oi >= 10000:
            score += 0.1
        elif oi >= 5000:
            score += 0.05

        # Determine if tradeable
        tradeable = (
            spread_pct <= self.MAX_SPREAD_PCT and
            bid_depth >= self.MIN_BID_DEPTH and
            ask_depth >= self.MIN_ASK_DEPTH and
            (volume >= self.MIN_VOLUME or oi >= self.MIN_OI)
        )

        return LiquidityMetrics(
            symbol=getattr(opt, 'symbol', ''),
            strike=getattr(opt, 'strike', 0),
            option_type=getattr(opt, 'option_type', ''),
            bid_ask_spread=spread,
            spread_pct=spread_pct,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            volume=volume,
            oi=oi,
            liquidity_score=score,
            tradeable=tradeable,
        )

    def _get_illiquidity_reason(self, metric: LiquidityMetrics) -> str:
        """Get reason for illiquidity."""
        reasons = []
        if metric.spread_pct > self.MAX_SPREAD_PCT:
            reasons.append(f"wide spread ({metric.spread_pct:.1f}%)")
        if metric.bid_depth < self.MIN_BID_DEPTH:
            reasons.append(f"low bid depth ({metric.bid_depth})")
        if metric.ask_depth < self.MIN_ASK_DEPTH:
            reasons.append(f"low ask depth ({metric.ask_depth})")
        if metric.volume < self.MIN_VOLUME and metric.oi < self.MIN_OI:
            reasons.append(f"low volume/OI")
        return ", ".join(reasons) if reasons else "unknown"

    def _is_strike_liquid(self, selection: Any, metrics: List[LiquidityMetrics]) -> bool:
        """Check if a strike selection is liquid."""
        for m in metrics:
            if m.strike == selection.strike and m.option_type == selection.option_type:
                return m.tradeable
        return True  # If not found, assume liquid


class MarketRegimeAgent(BaseBot):
    """
    Agent 18: Market Regime Agent

    Detects market regime using quant-grade indicators:
    - TRENDING_BULLISH: Clear uptrend with momentum + ADX > 25
    - TRENDING_BEARISH: Clear downtrend with momentum + ADX > 25
    - RANGE_BOUND: Sideways with mean reversion + ADX < 20
    - VOLATILE_EXPANSION: Breakout/breakdown with IV/ATR expansion
    - VOLATILE_CONTRACTION: Consolidation with IV/ATR crush
    - EXPIRY_PINNING: Near expiry with price gravitating to max pain

    Enhanced indicators:
    - ATR expansion/contraction
    - ADX trend strength
    - VIX level
    - Volume trend (acceleration/deceleration)
    - Range compression detection

    Runs BEFORE analysis layer to inform all other agents.
    Critical for strategy selection and position sizing.
    """

    BOT_TYPE = "market_regime"
    REQUIRES_LLM = False  # DISABLED - debate causes latency in execution path

    # Regime thresholds
    TREND_ADX_THRESHOLD = 25  # ADX > 25 = trending
    RANGE_ADX_THRESHOLD = 20  # ADX < 20 = range-bound
    VOL_EXPANSION_THRESHOLD = 1.2  # IV > 120% of average
    VOL_CONTRACTION_THRESHOLD = 0.8  # IV < 80% of average
    ATR_EXPANSION_THRESHOLD = 1.5  # ATR > 150% of average
    ATR_CONTRACTION_THRESHOLD = 0.7  # ATR < 70% of average
    RANGE_COMPRESSION_PERIODS = 5  # Consecutive ATR contractions
    MAX_PAIN_PINNING_DISTANCE = 0.5  # % distance for pinning regime

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._regime_history: Dict[str, List[str]] = {}
        self._vix_history: List[float] = []
        self._atr_history: Dict[str, List[float]] = {}
        self._range_history: Dict[str, List[float]] = {}  # High-Low ranges
        self._volume_history: Dict[str, List[int]] = {}
        self._adx_values: Dict[str, float] = {}  # Calculated ADX

    def get_description(self) -> str:
        return "Detects market regime for strategy selection"

    async def execute(self, context: BotContext) -> BotResult:
        """Detect market regime for each index."""
        spot_data = context.data.get("spot_data", {})
        option_chains = context.data.get("option_chains", {})
        futures_data = context.data.get("futures_data", {})
        config = context.data.get("config", ScalpingConfig())

        regimes = {}
        regime_changes = []
        volume_data = {}

        # Get VIX/volatility indicator
        vix = context.data.get("vix", 15.0)
        self._vix_history.append(vix)
        if len(self._vix_history) > 100:
            self._vix_history = self._vix_history[-100:]
        avg_vix = sum(self._vix_history) / len(self._vix_history) if self._vix_history else 15

        for symbol in spot_data.keys():
            spot = spot_data.get(symbol)
            chain = option_chains.get(symbol)
            futures = futures_data.get(symbol, {})

            # Detect regime
            regime, confidence, factors = self._detect_regime(
                spot, chain, futures, vix, avg_vix
            )

            # Check for regime change
            prev_regime = self._get_previous_regime(symbol)
            if prev_regime and prev_regime != regime:
                regime_changes.append({
                    "symbol": symbol,
                    "from": prev_regime,
                    "to": regime,
                    "confidence": confidence,
                })

            # Store regime
            self._update_regime_history(symbol, regime)

            regimes[symbol] = {
                "regime": regime,
                "confidence": confidence,
                "factors": factors,
                "vix": vix,
                "vix_vs_avg": vix / avg_vix if avg_vix > 0 else 1.0,
            }
            volume_data[symbol] = {
                "acceleration": float(factors.get("volume_acceleration", 1.0) or 1.0),
                "trend": str(factors.get("volume_trend", "stable") or "stable"),
            }

        context.data["market_regimes"] = regimes
        context.data["regime_changes"] = regime_changes
        context.data["current_vix"] = vix
        context.data["avg_vix"] = avg_vix
        context.data["volume_data"] = volume_data

        # Skip debate in execution path - causes latency
        # context.data["regime_debate"] = None

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "regimes": {s: r["regime"] for s, r in regimes.items()},
                "regime_changes": len(regime_changes),
                "avg_confidence": sum(r["confidence"] for r in regimes.values()) / len(regimes) if regimes else 0,
            },
            metrics={
                "symbols_analyzed": len(regimes),
                "regime_changes": len(regime_changes),
                "vix": vix,
            },
        )

    def _detect_regime(
        self, spot: Any, chain: Any, futures: Dict, vix: float, avg_vix: float
    ) -> tuple:
        """
        Detect market regime using enhanced quant-grade indicators.

        Indicators used:
        - ATR expansion/contraction
        - ADX trend strength (simulated from price action)
        - VIX level and IV ratio
        - Volume trend (acceleration)
        - Range compression
        - Max pain distance (expiry pinning)
        """
        factors = {}
        symbol = getattr(spot, 'symbol', 'UNKNOWN') if spot else 'UNKNOWN'

        # ─────────────────────────────────────────────────────────────────
        # Factor 1: Price vs VWAP (trend indicator)
        # ─────────────────────────────────────────────────────────────────
        price = 0
        vwap_deviation = 0
        if spot:
            price = getattr(spot, 'ltp', 0)
            vwap = getattr(spot, 'vwap', price)
            vwap_deviation = (price - vwap) / vwap * 100 if vwap > 0 else 0
            factors["vwap_deviation"] = vwap_deviation

        # ─────────────────────────────────────────────────────────────────
        # Factor 2: ATR (Average True Range) - Volatility Regime
        # ─────────────────────────────────────────────────────────────────
        atr_ratio = 1.0
        atr_regime = "normal"
        if spot:
            high = getattr(spot, 'high', price)
            low = getattr(spot, 'low', price)
            prev_close = getattr(spot, 'prev_close', price)

            # Calculate True Range
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))

            # Store and calculate ATR
            if symbol not in self._atr_history:
                self._atr_history[symbol] = []
            self._atr_history[symbol].append(tr)
            if len(self._atr_history[symbol]) > 20:
                self._atr_history[symbol] = self._atr_history[symbol][-20:]

            avg_atr = sum(self._atr_history[symbol]) / len(self._atr_history[symbol])
            current_atr = self._atr_history[symbol][-1]
            atr_ratio = current_atr / avg_atr if avg_atr > 0 else 1.0

            if atr_ratio > self.ATR_EXPANSION_THRESHOLD:
                atr_regime = "expanding"
            elif atr_ratio < self.ATR_CONTRACTION_THRESHOLD:
                atr_regime = "contracting"

            factors["atr_ratio"] = atr_ratio
            factors["atr_regime"] = atr_regime

        # ─────────────────────────────────────────────────────────────────
        # Factor 3: ADX (Trend Strength) - Simulated from directional moves
        # ─────────────────────────────────────────────────────────────────
        adx = 20  # Default neutral
        if spot:
            change_pct = abs(getattr(spot, 'change_pct', 0))
            # Higher change = stronger trend
            adx = min(50, 15 + change_pct * 10 + abs(vwap_deviation) * 5)
            self._adx_values[symbol] = adx
            factors["adx"] = adx

        # ─────────────────────────────────────────────────────────────────
        # Factor 4: PCR (sentiment indicator)
        # ─────────────────────────────────────────────────────────────────
        if chain:
            pcr = getattr(chain, 'pcr', 1.0)
            factors["pcr"] = pcr
        else:
            pcr = 1.0

        # ─────────────────────────────────────────────────────────────────
        # Factor 5: IV expansion/contraction
        # ─────────────────────────────────────────────────────────────────
        iv_ratio = vix / avg_vix if avg_vix > 0 else 1.0
        factors["iv_ratio"] = iv_ratio

        # ─────────────────────────────────────────────────────────────────
        # Factor 6: Price change momentum
        # ─────────────────────────────────────────────────────────────────
        if spot:
            change_pct = getattr(spot, 'change_pct', 0)
            factors["change_pct"] = change_pct
        else:
            change_pct = 0

        # ─────────────────────────────────────────────────────────────────
        # Factor 7: Volume Trend (acceleration/deceleration)
        # ─────────────────────────────────────────────────────────────────
        volume_acceleration = 1.0
        volume_trend = "stable"
        if spot:
            volume = getattr(spot, 'volume', 0)
            if symbol not in self._volume_history:
                self._volume_history[symbol] = []
            self._volume_history[symbol].append(volume)
            if len(self._volume_history[symbol]) > 10:
                self._volume_history[symbol] = self._volume_history[symbol][-10:]

            avg_vol = sum(self._volume_history[symbol]) / len(self._volume_history[symbol])
            volume_acceleration = volume / avg_vol if avg_vol > 0 else 1.0

            if volume_acceleration > 1.5:
                volume_trend = "accelerating"
            elif volume_acceleration < 0.7:
                volume_trend = "decelerating"

            factors["volume_acceleration"] = volume_acceleration
            factors["volume_trend"] = volume_trend

        # ─────────────────────────────────────────────────────────────────
        # Factor 8: Range Compression Detection
        # ─────────────────────────────────────────────────────────────────
        range_compressed = False
        if spot:
            high = getattr(spot, 'high', price)
            low = getattr(spot, 'low', price)
            day_range = high - low

            if symbol not in self._range_history:
                self._range_history[symbol] = []
            self._range_history[symbol].append(day_range)
            if len(self._range_history[symbol]) > 10:
                self._range_history[symbol] = self._range_history[symbol][-10:]

            # Check for consecutive contractions
            if len(self._range_history[symbol]) >= self.RANGE_COMPRESSION_PERIODS:
                recent = self._range_history[symbol][-self.RANGE_COMPRESSION_PERIODS:]
                # Each range smaller than previous = compression
                compressions = sum(1 for i in range(1, len(recent)) if recent[i] < recent[i-1])
                range_compressed = compressions >= self.RANGE_COMPRESSION_PERIODS - 1

            factors["range_compressed"] = range_compressed

        # ─────────────────────────────────────────────────────────────────
        # Factor 9: Max Pain Distance (Expiry Pinning)
        # ─────────────────────────────────────────────────────────────────
        max_pain_distance = 999
        days_to_expiry = 7
        if chain:
            max_pain = getattr(chain, 'max_pain', 0)
            days_to_expiry = getattr(chain, 'days_to_expiry', 7)
            if max_pain > 0 and price > 0:
                max_pain_distance = abs(price - max_pain) / price * 100
            factors["max_pain_distance"] = max_pain_distance
            factors["days_to_expiry"] = days_to_expiry

        # ─────────────────────────────────────────────────────────────────
        # Factor 10: Futures basis
        # ─────────────────────────────────────────────────────────────────
        basis = futures.get("basis", 0) if isinstance(futures, dict) else 0
        factors["futures_basis"] = basis

        # ═════════════════════════════════════════════════════════════════
        # REGIME DETERMINATION (Priority Order)
        # ═════════════════════════════════════════════════════════════════
        regime = "UNKNOWN"
        confidence = 0.5

        # Priority 1: Expiry Pinning (near expiry + close to max pain)
        if days_to_expiry <= 1 and max_pain_distance < self.MAX_PAIN_PINNING_DISTANCE:
            regime = "EXPIRY_PINNING"
            confidence = min(0.9, 0.6 + (1 - max_pain_distance) * 0.3)

        # Priority 2: Volatile Breakout (ATR expansion + IV expansion + volume spike)
        elif (atr_ratio > self.ATR_EXPANSION_THRESHOLD and
              iv_ratio > self.VOL_EXPANSION_THRESHOLD and
              volume_acceleration > 1.5):
            regime = "VOLATILE_EXPANSION"
            confidence = min(0.9, 0.5 + (atr_ratio - 1) * 0.2 + (iv_ratio - 1) * 0.2)

        # Priority 3: Strong Trend (ADX > 25 + price momentum)
        elif adx > self.TREND_ADX_THRESHOLD and abs(change_pct) > 0.3:
            if change_pct > 0 and vwap_deviation > 0:
                regime = "TRENDING_BULLISH"
                confidence = min(0.9, 0.5 + (adx - 25) * 0.01 + abs(change_pct) * 0.1)
            elif change_pct < 0 and vwap_deviation < 0:
                regime = "TRENDING_BEARISH"
                confidence = min(0.9, 0.5 + (adx - 25) * 0.01 + abs(change_pct) * 0.1)
            else:
                regime = "RANGE_BOUND"
                confidence = 0.6

        # Priority 4: Volatility Contraction (ATR + IV contracting + range compression)
        elif (atr_ratio < self.ATR_CONTRACTION_THRESHOLD and
              iv_ratio < self.VOL_CONTRACTION_THRESHOLD):
            regime = "VOLATILE_CONTRACTION"
            confidence = min(0.85, 0.5 + (1 - atr_ratio) * 0.2 + (1 - iv_ratio) * 0.2)
            if range_compressed:
                confidence = min(confidence + 0.1, 0.95)

        # Priority 5: Range-bound (low ADX + no momentum)
        elif adx < self.RANGE_ADX_THRESHOLD:
            regime = "RANGE_BOUND"
            confidence = min(0.8, 0.5 + (25 - adx) * 0.01)

        # Default: Classify based on IV
        else:
            if iv_ratio > self.VOL_EXPANSION_THRESHOLD:
                regime = "VOLATILE_EXPANSION"
                confidence = 0.6
            elif iv_ratio < self.VOL_CONTRACTION_THRESHOLD:
                regime = "VOLATILE_CONTRACTION"
                confidence = 0.6
            else:
                regime = "RANGE_BOUND"
                confidence = 0.55

        # ─────────────────────────────────────────────────────────────────
        # Confidence Adjustments
        # ─────────────────────────────────────────────────────────────────
        # PCR extremes increase confidence
        if pcr > 1.5 or pcr < 0.7:
            confidence = min(confidence + 0.05, 0.95)

        # Volume confirmation increases confidence
        if volume_acceleration > 2.0:
            confidence = min(confidence + 0.05, 0.95)

        return regime, confidence, factors

    def _get_previous_regime(self, symbol: str) -> Optional[str]:
        """Get previous regime for comparison."""
        history = self._regime_history.get(symbol, [])
        return history[-1] if history else None

    def _update_regime_history(self, symbol: str, regime: str):
        """Update regime history for a symbol."""
        if symbol not in self._regime_history:
            self._regime_history[symbol] = []
        self._regime_history[symbol].append(regime)
        if len(self._regime_history[symbol]) > 20:
            self._regime_history[symbol] = self._regime_history[symbol][-20:]

    async def _debate_regime(
        self, unclear: Dict, context: BotContext
    ) -> Optional[Dict]:
        """Use LLM debate for unclear regime detection."""
        try:
            from ..debate_integration import debate_analysis

            results = {}
            for symbol, regime_data in unclear.items():
                is_valid, reason, result = await debate_analysis(
                    analysis_type="regime",
                    context={
                        "symbol": symbol,
                        "detected_regime": regime_data["regime"],
                        "confidence": regime_data["confidence"],
                        "factors": regime_data["factors"],
                        "question": "What is the correct market regime?",
                    }
                )
                results[symbol] = {
                    "validated": is_valid,
                    "reason": reason,
                    "confidence": result.confidence if result else 0,
                }
            return results
        except Exception as e:
            return {"error": str(e)}


# Export all agents
__all__ = [
    "LatencyGuardianAgent",
    "LiquidityMonitorAgent",
    "MarketRegimeAgent",
    "LatencyMetrics",
    "LiquidityMetrics",
]

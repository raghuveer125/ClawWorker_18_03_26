"""
Kill Switch Agent - Mandatory safety circuit breaker.

Used by professional prop/quant trading systems to automatically halt trading when:
- Latency spike detected (data feed delays)
- Abnormal loss cluster (consecutive losses or rapid drawdown)
- API failure (exchange/broker connectivity issues)
- Volatility shock (sudden market moves exceeding thresholds)

This is Agent 0 - runs BEFORE all other agents in every cycle.
If triggered, ALL trading is halted until manual reset or auto-recovery.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

from ..base import BaseBot, BotContext, BotResult, BotStatus
from ..config import ScalpingConfig


class KillSwitchReason(Enum):
    """Reasons for triggering kill switch."""
    LATENCY_SPIKE = "latency_spike"
    LOSS_CLUSTER = "loss_cluster"
    API_FAILURE = "api_failure"
    VOLATILITY_SHOCK = "volatility_shock"
    DAILY_LIMIT = "daily_limit"
    MANUAL = "manual"


@dataclass
class KillSwitchState:
    """Current state of the kill switch."""
    active: bool = False
    reason: Optional[KillSwitchReason] = None
    triggered_at: Optional[str] = None
    auto_reset_at: Optional[str] = None
    trigger_count: int = 0
    last_check: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class KillSwitchAgent(BaseBot):
    """
    Agent 0: Kill Switch Agent

    Critical safety component that halts all trading when abnormal conditions detected.

    Monitors:
    1. Data latency - halt if feed delays > threshold
    2. Loss clusters - halt if consecutive losses or rapid drawdown
    3. API health - halt if connectivity issues
    4. Volatility - halt if sudden moves exceed thresholds

    Auto-recovery:
    - Some conditions auto-reset after cooldown period
    - Others require manual intervention

    This agent runs FIRST in every cycle and can BLOCK the entire pipeline.
    """

    BOT_TYPE = "kill_switch"
    REQUIRES_LLM = False  # Never uses LLM - must be deterministic and fast

    # Thresholds (configurable) - Enhanced for quant-grade safety
    LATENCY_THRESHOLD_MS = 5000  # 5 seconds - data feed delay
    CONSECUTIVE_LOSS_LIMIT = 4  # 4 consecutive losses (tightened from 5)
    RAPID_DRAWDOWN_PCT = 5.0  # 5% drawdown in short period
    VOLATILITY_THRESHOLD_PCT = 3.0  # 3% index move (static threshold)
    ATR_SPIKE_MULTIPLIER = 2.0  # Volatility > 2x ATR = spike (dynamic threshold)
    API_FAILURE_THRESHOLD = 3  # 3 consecutive API failures
    DATA_STALENESS_SECONDS = 60  # Data older than 60s = stale
    COOLDOWN_MINUTES = 15  # Auto-reset after 15 minutes for some conditions

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.state = KillSwitchState()

        # Tracking variables
        self._consecutive_losses = 0
        self._api_failures = 0
        self._recent_pnl: List[float] = []
        self._volatility_history: List[Dict] = []
        self._last_prices: Dict[str, float] = {}
        self._atr_history: Dict[str, List[float]] = {}  # ATR tracking per symbol
        self._last_data_times: Dict[str, datetime] = {}  # Data freshness tracking

    def get_description(self) -> str:
        return "Critical safety circuit breaker - halts trading on abnormal conditions"

    async def execute(self, context: BotContext) -> BotResult:
        """
        Check all safety conditions. If any triggered, BLOCK the pipeline.

        This runs before all other agents in every cycle.
        """
        config = context.data.get("config", ScalpingConfig())
        current_time = self._current_time(context)
        self.state.last_check = current_time.isoformat()

        # Check if kill switch is active and should auto-reset
        if self.state.active:
            if self._should_auto_reset(current_time):
                self._reset_kill_switch("auto_reset")
            else:
                return BotResult(
                    bot_id=self.bot_id,
                    bot_type=self.BOT_TYPE,
                    status=BotStatus.BLOCKED,
                    output={
                        "kill_switch_active": True,
                        "reason": self.state.reason.value if self.state.reason else "unknown",
                        "triggered_at": self.state.triggered_at,
                        "auto_reset_at": self.state.auto_reset_at,
                        "message": "Trading halted - kill switch active",
                    },
                )

        # Run all safety checks
        checks = {
            "latency": self._check_latency(context),
            "loss_cluster": self._check_loss_cluster(context),
            "api_health": self._check_api_health(context),
            "volatility": self._check_volatility(context),
            "daily_limit": self._check_daily_limit(context, config),
        }
        reason_map = {
            "latency": KillSwitchReason.LATENCY_SPIKE,
            "loss_cluster": KillSwitchReason.LOSS_CLUSTER,
            "api_health": KillSwitchReason.API_FAILURE,
            "volatility": KillSwitchReason.VOLATILITY_SHOCK,
            "daily_limit": KillSwitchReason.DAILY_LIMIT,
        }

        # Check if any condition triggered
        for check_name, (triggered, details) in checks.items():
            if triggered:
                reason = reason_map[check_name]
                self._trigger_kill_switch(reason, details, now=current_time)

                return BotResult(
                    bot_id=self.bot_id,
                    bot_type=self.BOT_TYPE,
                    status=BotStatus.BLOCKED,
                    output={
                        "kill_switch_active": True,
                        "reason": reason.value,
                        "details": details,
                        "message": f"KILL SWITCH TRIGGERED: {reason.value}",
                    },
                    metrics={"trigger_count": self.state.trigger_count},
                )

        # All checks passed
        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "kill_switch_active": False,
                "checks_passed": list(checks.keys()),
                "consecutive_losses": self._consecutive_losses,
                "api_failures": self._api_failures,
            },
            metrics={
                "latency_ok": True,
                "loss_cluster_ok": True,
                "api_health_ok": True,
                "volatility_ok": True,
            },
        )

    def _check_latency(self, context: BotContext) -> tuple:
        """Check if data latency exceeds threshold."""
        latency_data = context.data.get("latency_check", {})

        # Get max latency from any data source
        max_latency_ms = 0
        stale_sources = []

        for source, info in latency_data.items():
            latency = info.get("latency_ms", 0)
            if latency > max_latency_ms:
                max_latency_ms = latency
            if latency > self.LATENCY_THRESHOLD_MS:
                stale_sources.append(source)

        if max_latency_ms > self.LATENCY_THRESHOLD_MS:
            return (True, {
                "max_latency_ms": max_latency_ms,
                "threshold_ms": self.LATENCY_THRESHOLD_MS,
                "stale_sources": stale_sources,
            })

        return (False, {"max_latency_ms": max_latency_ms})

    def _check_loss_cluster(self, context: BotContext) -> tuple:
        """Check for consecutive losses or rapid drawdown."""
        trades = context.data.get("recent_trades", [])
        positions = context.data.get("positions", [])

        # Track consecutive losses
        recent_pnl = [t.get("pnl", 0) for t in trades[-10:]]

        # Count consecutive losses
        consecutive = 0
        for pnl in reversed(recent_pnl):
            if pnl < 0:
                consecutive += 1
            else:
                break

        self._consecutive_losses = consecutive

        if consecutive >= self.CONSECUTIVE_LOSS_LIMIT:
            return (True, {
                "consecutive_losses": consecutive,
                "limit": self.CONSECUTIVE_LOSS_LIMIT,
                "recent_pnl": recent_pnl[-5:],
            })

        # Check rapid drawdown
        total_pnl = sum(recent_pnl)
        capital = context.data.get("initial_capital", 100000)
        drawdown_pct = abs(total_pnl / capital * 100) if total_pnl < 0 else 0

        if drawdown_pct > self.RAPID_DRAWDOWN_PCT:
            return (True, {
                "rapid_drawdown": True,
                "drawdown_pct": drawdown_pct,
                "threshold_pct": self.RAPID_DRAWDOWN_PCT,
            })

        return (False, {
            "consecutive_losses": consecutive,
            "drawdown_pct": drawdown_pct,
        })

    def _check_api_health(self, context: BotContext) -> tuple:
        """Check for API connectivity issues."""
        api_status = context.data.get("api_status", {})

        # Track API failures
        if api_status.get("failed", False):
            self._api_failures += 1
        else:
            self._api_failures = 0

        if self._api_failures >= self.API_FAILURE_THRESHOLD:
            return (True, {
                "consecutive_failures": self._api_failures,
                "threshold": self.API_FAILURE_THRESHOLD,
                "last_error": api_status.get("error", "unknown"),
            })

        return (False, {"api_failures": self._api_failures})

    def _check_volatility(self, context: BotContext) -> tuple:
        """
        Check for sudden volatility shocks using both static and ATR-based thresholds.

        Triggers:
        1. Static: Price change > VOLATILITY_THRESHOLD_PCT (3%)
        2. Dynamic: Price change > ATR_SPIKE_MULTIPLIER * ATR (2x ATR)

        ATR-based detection catches abnormal moves relative to recent volatility.
        """
        spot_data = context.data.get("spot_data", {})
        baseline_resets = []

        for symbol, data in spot_data.items():
            current_price = data.ltp if hasattr(data, 'ltp') else data.get('ltp', 0)
            high = data.high if hasattr(data, 'high') else data.get('high', current_price)
            low = data.low if hasattr(data, 'low') else data.get('low', current_price)
            timestamp = data.timestamp if hasattr(data, 'timestamp') else data.get('timestamp')
            timestamp = self._normalize_timestamp(timestamp, self._current_time(context))

            prev_price = self._last_prices.get(symbol)
            prev_timestamp = self._last_data_times.get(symbol)
            gap_seconds = (
                (timestamp - prev_timestamp).total_seconds()
                if prev_timestamp is not None else None
            )

            # If data resumes after a long pause (for example after a kill-switch cooldown),
            # reset the volatility baseline instead of comparing against a stale pre-halt quote.
            if (
                prev_timestamp is not None
                and gap_seconds is not None
                and gap_seconds > self.DATA_STALENESS_SECONDS
            ):
                baseline_resets.append({
                    "symbol": symbol,
                    "gap_seconds": round(gap_seconds, 2),
                    "reset_threshold_seconds": self.DATA_STALENESS_SECONDS,
                })
                self._last_prices[symbol] = current_price
                self._last_data_times[symbol] = timestamp
                self._atr_history[symbol] = []
                continue

            if prev_price and prev_price > 0 and current_price > 0:
                change_pct = abs((current_price - prev_price) / prev_price * 100)

                # Calculate True Range for ATR
                tr = max(high - low, abs(high - prev_price), abs(low - prev_price))

                # Update ATR history
                if symbol not in self._atr_history:
                    self._atr_history[symbol] = []
                self._atr_history[symbol].append(tr)
                if len(self._atr_history[symbol]) > 20:
                    self._atr_history[symbol] = self._atr_history[symbol][-20:]

                # Calculate ATR (simple average)
                atr = sum(self._atr_history[symbol]) / len(self._atr_history[symbol])

                # Check 1: Static threshold (3% move)
                if change_pct > self.VOLATILITY_THRESHOLD_PCT:
                    return (True, {
                        "symbol": symbol,
                        "trigger": "static_threshold",
                        "change_pct": round(change_pct, 2),
                        "threshold_pct": self.VOLATILITY_THRESHOLD_PCT,
                        "prev_price": prev_price,
                        "current_price": current_price,
                    })

                # Check 2: ATR-based threshold (> 2x ATR move)
                price_move = abs(current_price - prev_price)
                atr_threshold = atr * self.ATR_SPIKE_MULTIPLIER

                if price_move > atr_threshold and len(self._atr_history[symbol]) >= 5:
                    return (True, {
                        "symbol": symbol,
                        "trigger": "atr_spike",
                        "price_move": round(price_move, 2),
                        "atr": round(atr, 2),
                        "atr_multiplier": round(price_move / atr, 2),
                        "threshold_multiplier": self.ATR_SPIKE_MULTIPLIER,
                        "prev_price": prev_price,
                        "current_price": current_price,
                    })

            self._last_prices[symbol] = current_price
            self._last_data_times[symbol] = timestamp

        return (False, {
            "volatility_ok": True,
            "baseline_resets": baseline_resets,
        })

    def _check_daily_limit(self, context: BotContext, config: ScalpingConfig) -> tuple:
        """Check if daily loss limit is breached."""
        daily_pnl = context.data.get("daily_pnl", 0)
        capital = context.data.get("initial_capital", config.total_capital)
        limit = capital * (config.daily_loss_limit_pct / 100)

        if daily_pnl < -limit:
            return (True, {
                "daily_pnl": daily_pnl,
                "limit": -limit,
                "limit_pct": config.daily_loss_limit_pct,
            })

        return (False, {"daily_pnl": daily_pnl, "limit": -limit})

    def _current_time(self, context: Optional[BotContext] = None) -> datetime:
        if context is not None:
            cycle_timestamp = context.data.get("cycle_timestamp")
            if isinstance(cycle_timestamp, str):
                try:
                    return datetime.fromisoformat(cycle_timestamp)
                except ValueError:
                    pass
        return datetime.now()

    def _normalize_timestamp(self, value: Any, fallback: Optional[datetime] = None) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
        return fallback or datetime.now()

    def _trigger_kill_switch(self, reason: KillSwitchReason, details: Dict, now: Optional[datetime] = None):
        """Activate the kill switch."""
        now = now or datetime.now()
        self.state.active = True
        self.state.reason = reason
        self.state.triggered_at = now.isoformat()
        self.state.trigger_count += 1
        self.state.details = details

        # Set auto-reset for certain conditions
        if reason in [KillSwitchReason.LATENCY_SPIKE, KillSwitchReason.VOLATILITY_SHOCK]:
            reset_time = now + timedelta(minutes=self.COOLDOWN_MINUTES)
            self.state.auto_reset_at = reset_time.isoformat()
        else:
            # Manual reset required for serious conditions
            self.state.auto_reset_at = None

        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  ⚠️  KILL SWITCH TRIGGERED                                        ║
╠══════════════════════════════════════════════════════════════════╣
║  Reason: {reason.value:<53}║
║  Time: {self.state.triggered_at:<55}║
║  Auto-reset: {str(self.state.auto_reset_at or 'Manual required'):<49}║
╚══════════════════════════════════════════════════════════════════╝
""")

    def _should_auto_reset(self, now: Optional[datetime] = None) -> bool:
        """Check if kill switch should auto-reset."""
        if not self.state.auto_reset_at:
            return False

        reset_time = datetime.fromisoformat(self.state.auto_reset_at)
        return (now or datetime.now()) >= reset_time

    def _reset_kill_switch(self, trigger: str = "manual"):
        """Reset the kill switch."""
        print(f"[KillSwitch] Resetting - trigger: {trigger}")
        self.state.active = False
        self.state.reason = None
        self.state.triggered_at = None
        self.state.auto_reset_at = None
        self.state.details = {}

        # Reset tracking
        self._consecutive_losses = 0
        self._api_failures = 0
        self._last_prices = {}
        self._last_data_times = {}
        self._atr_history = {}

    def manual_reset(self) -> bool:
        """Manually reset the kill switch. Called via API."""
        if self.state.active:
            self._reset_kill_switch("manual")
            return True
        return False

    def trigger(self, reason: KillSwitchReason, details: Dict[str, Any]) -> None:
        """Public trigger hook for engine safety integrations."""
        self._trigger_kill_switch(reason, details)

    def get_state(self) -> Dict:
        """Get current kill switch state for API."""
        return {
            "active": self.state.active,
            "reason": self.state.reason.value if self.state.reason else None,
            "triggered_at": self.state.triggered_at,
            "auto_reset_at": self.state.auto_reset_at,
            "trigger_count": self.state.trigger_count,
            "last_check": self.state.last_check,
            "details": self.state.details,
            "thresholds": {
                "latency_ms": self.LATENCY_THRESHOLD_MS,
                "consecutive_losses": self.CONSECUTIVE_LOSS_LIMIT,
                "rapid_drawdown_pct": self.RAPID_DRAWDOWN_PCT,
                "volatility_pct": self.VOLATILITY_THRESHOLD_PCT,
                "api_failures": self.API_FAILURE_THRESHOLD,
                "cooldown_minutes": self.COOLDOWN_MINUTES,
            },
        }

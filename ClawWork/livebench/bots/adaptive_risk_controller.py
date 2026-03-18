"""
Adaptive Risk Controller - Independent Learning Layer

This is the FINAL DEFENSE layer that:
1. Monitors ALL trades in real-time
2. Detects patterns of failure BEFORE they become catastrophic
3. Automatically adapts the entire system
4. Implements circuit breakers for worst-case scenarios
5. Learns continuously from every trade

Philosophy: "The system that can adapt survives"

This layer operates INDEPENDENTLY and can override any bot decision.
"""

import json
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from collections import deque

LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADAPTIVE_DATA_DIR = LIVEBENCH_ROOT / "data" / "adaptive"


class RiskLevel(Enum):
    """Current risk level of the system"""
    NORMAL = "NORMAL"           # Business as usual
    ELEVATED = "ELEVATED"       # Some caution needed
    HIGH = "HIGH"               # Significant caution
    CRITICAL = "CRITICAL"       # Near circuit breaker
    HALTED = "HALTED"           # Trading stopped


class AdaptiveAction(Enum):
    """Actions the controller can take"""
    NONE = "NONE"
    REDUCE_SIZE = "REDUCE_SIZE"
    INCREASE_CONFIDENCE = "INCREASE_CONFIDENCE"
    DISABLE_BOT = "DISABLE_BOT"
    SWITCH_MTF_MODE = "SWITCH_MTF_MODE"
    PAUSE_TRADING = "PAUSE_TRADING"
    HALT_TRADING = "HALT_TRADING"
    RECOVERY_MODE = "RECOVERY_MODE"


@dataclass
class TradeOutcome:
    """Record of a trade outcome for learning"""
    timestamp: datetime
    index: str
    option_type: str
    bots_involved: List[str]
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    outcome: str  # WIN, LOSS, BREAKEVEN
    market_conditions: Dict[str, Any]
    mtf_mode: str
    confidence: float
    holding_time_minutes: float


@dataclass
class LearningInsight:
    """Learned pattern from trade history"""
    pattern_type: str  # "loss_condition", "win_condition", "avoid_pattern"
    description: str
    conditions: Dict[str, Any]
    occurrences: int
    success_rate: float
    last_seen: datetime
    action_taken: str


class AdaptiveRiskController:
    """
    Independent Adaptive Risk Controller

    Monitors the entire trading system and makes autonomous decisions
    to protect capital and improve performance over time.

    Key Features:
    1. Real-time performance monitoring
    2. Circuit breakers (automatic trading halt)
    3. Adaptive parameter adjustment
    4. Pattern learning from trades
    5. Recovery mode after losses
    """

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or DEFAULT_ADAPTIVE_DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Trade history (rolling window)
        self.trade_history: deque = deque(maxlen=500)  # Last 500 trades
        self.today_trades: List[TradeOutcome] = []

        # Performance tracking
        self.rolling_stats = {
            "last_10_trades": deque(maxlen=10),
            "last_20_trades": deque(maxlen=20),
            "last_50_trades": deque(maxlen=50),
        }

        # Circuit breaker thresholds
        self.circuit_breakers = {
            "max_consecutive_losses": 3,        # Pause after 3 consecutive losses
            "max_daily_loss_pct": 3.0,          # Halt at 3% daily loss
            "max_daily_loss_amount": 5000,      # Halt at 5000 INR daily loss
            "min_win_rate_threshold": 35,       # Alert if win rate drops below 35%
            "max_drawdown_pct": 5.0,            # Halt at 5% drawdown
        }

        # Current state
        self.current_risk_level = RiskLevel.NORMAL
        self.consecutive_losses = 0
        self.consecutive_wins = 0
        self.daily_pnl = 0.0
        self.peak_equity = 0.0
        self.current_drawdown = 0.0
        self.is_halted = False
        self.halt_reason = ""
        self.recovery_mode = False

        # Adaptive parameters (can be modified by controller)
        self.adaptive_params = {
            "position_size_multiplier": 1.0,    # 1.0 = normal, 0.5 = half size
            "confidence_boost": 0,               # Extra confidence required
            "min_bots_override": None,           # Override min bots requirement
            "blocked_bots": set(),               # Bots temporarily disabled
            "forced_mtf_mode": None,             # Force specific MTF mode
        }

        # Learning database
        self.learned_patterns: List[LearningInsight] = []
        self.conditions_to_avoid: List[Dict] = []

        # Bot performance tracking
        self.bot_performance: Dict[str, Dict] = {}

        # Load persisted state
        self._load_state()

        print("[AdaptiveRisk] Controller initialized - Independent learning layer active")

    def record_trade(self, trade: TradeOutcome):
        """
        Record a trade outcome and trigger learning/adaptation

        This is the main entry point - called after every trade.
        """
        # Add to history
        self.trade_history.append(trade)
        self.today_trades.append(trade)

        # Update rolling stats
        for window in self.rolling_stats.values():
            window.append(trade)

        # Update counters
        self.daily_pnl += trade.pnl

        if trade.outcome == "WIN":
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        elif trade.outcome == "LOSS":
            self.consecutive_losses += 1
            self.consecutive_wins = 0

        # Update bot performance
        for bot in trade.bots_involved:
            self._update_bot_performance(bot, trade)

        # Check circuit breakers
        self._check_circuit_breakers()

        # Learn from this trade
        self._learn_from_trade(trade)

        # Adapt parameters if needed
        self._adapt_parameters()

        # Update risk level
        self._update_risk_level()

        # Save state
        self._save_state()

        # Log status
        self._log_status(trade)

    def should_allow_trade(
        self,
        index: str,
        option_type: str,
        bots: List[str],
        confidence: float,
        market_conditions: Dict[str, Any]
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Final check before allowing a trade

        Returns:
            (allowed: bool, reason: str, modifications: dict)
        """
        modifications = {}

        # Check if halted
        if self.is_halted:
            return False, f"Trading halted: {self.halt_reason}", {}

        # Check if in recovery mode - require higher standards
        if self.recovery_mode:
            if confidence < 80:
                return False, "Recovery mode: Requires 80%+ confidence", {}
            if len(bots) < 3:
                return False, "Recovery mode: Requires 3+ agreeing bots", {}
            modifications["position_size"] = 0.5  # Half position size

        # Check blocked bots
        active_bots = [b for b in bots if b not in self.adaptive_params["blocked_bots"]]
        if not active_bots:
            return False, "All contributing bots are currently blocked", {}

        # Check if conditions match learned "avoid" patterns
        for avoid_pattern in self.conditions_to_avoid:
            if self._matches_pattern(market_conditions, avoid_pattern):
                return False, f"Matches learned avoid pattern: {avoid_pattern.get('reason', 'unknown')}", {}

        # Apply adaptive parameters
        if self.adaptive_params["confidence_boost"] > 0:
            required_conf = confidence + self.adaptive_params["confidence_boost"]
            if confidence < required_conf:
                return False, f"Adaptive: Requires {required_conf}% confidence (current: {confidence}%)", {}

        # Check risk level restrictions
        if self.current_risk_level == RiskLevel.CRITICAL:
            if confidence < 85:
                return False, "Critical risk: Only highest confidence trades allowed", {}
        elif self.current_risk_level == RiskLevel.HIGH:
            if confidence < 75:
                return False, "High risk: Requires 75%+ confidence", {}

        # Apply position size multiplier
        modifications["position_size_multiplier"] = self.adaptive_params["position_size_multiplier"]

        # Apply forced MTF mode if set
        if self.adaptive_params["forced_mtf_mode"]:
            modifications["mtf_mode"] = self.adaptive_params["forced_mtf_mode"]

        return True, "Trade approved by Adaptive Risk Controller", modifications

    def _check_circuit_breakers(self):
        """Check all circuit breakers and halt if necessary"""

        # Check consecutive losses
        if self.consecutive_losses >= self.circuit_breakers["max_consecutive_losses"]:
            self._trigger_pause(f"Consecutive losses: {self.consecutive_losses}")
            return

        # Check daily loss percentage (assuming 100k capital)
        capital = 100000
        daily_loss_pct = abs(self.daily_pnl / capital * 100) if self.daily_pnl < 0 else 0

        if daily_loss_pct >= self.circuit_breakers["max_daily_loss_pct"]:
            self._trigger_halt(f"Daily loss exceeded {self.circuit_breakers['max_daily_loss_pct']}%")
            return

        # Check daily loss amount
        if self.daily_pnl <= -self.circuit_breakers["max_daily_loss_amount"]:
            self._trigger_halt(f"Daily loss exceeded {self.circuit_breakers['max_daily_loss_amount']} INR")
            return

        # Check rolling win rate
        if len(self.rolling_stats["last_20_trades"]) >= 10:
            wins = sum(1 for t in self.rolling_stats["last_20_trades"] if t.outcome == "WIN")
            win_rate = (wins / len(self.rolling_stats["last_20_trades"])) * 100

            if win_rate < self.circuit_breakers["min_win_rate_threshold"]:
                self._enter_recovery_mode(f"Win rate dropped to {win_rate:.1f}%")

    def _trigger_pause(self, reason: str):
        """Pause trading temporarily"""
        print(f"[AdaptiveRisk] PAUSE TRIGGERED: {reason}")
        self.recovery_mode = True
        self.adaptive_params["position_size_multiplier"] = 0.5
        self.adaptive_params["confidence_boost"] = 15

    def _trigger_halt(self, reason: str):
        """Halt all trading"""
        print(f"[AdaptiveRisk] HALT TRIGGERED: {reason}")
        self.is_halted = True
        self.halt_reason = reason
        self.current_risk_level = RiskLevel.HALTED

    def _enter_recovery_mode(self, reason: str):
        """Enter conservative recovery mode"""
        print(f"[AdaptiveRisk] RECOVERY MODE: {reason}")
        self.recovery_mode = True
        self.adaptive_params["position_size_multiplier"] = 0.5
        self.adaptive_params["confidence_boost"] = 20
        self.adaptive_params["forced_mtf_mode"] = "strict"

    def _update_bot_performance(self, bot_name: str, trade: TradeOutcome):
        """Track individual bot performance"""
        if bot_name not in self.bot_performance:
            self.bot_performance[bot_name] = {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0,
                "recent_outcomes": deque(maxlen=20),
            }

        perf = self.bot_performance[bot_name]
        perf["trades"] += 1
        perf["total_pnl"] += trade.pnl
        perf["recent_outcomes"].append(trade.outcome)

        if trade.outcome == "WIN":
            perf["wins"] += 1
        elif trade.outcome == "LOSS":
            perf["losses"] += 1

        # Check if bot should be blocked
        recent = list(perf["recent_outcomes"])
        if len(recent) >= 5:
            recent_losses = sum(1 for o in recent[-5:] if o == "LOSS")
            if recent_losses >= 4:  # 4 out of last 5 are losses
                self._block_bot(bot_name, f"4/5 recent trades lost")

    def _block_bot(self, bot_name: str, reason: str):
        """Temporarily block a bot"""
        self.adaptive_params["blocked_bots"].add(bot_name)
        print(f"[AdaptiveRisk] BOT BLOCKED: {bot_name} - {reason}")

        # Schedule unblock after some time/trades
        # (In production, this would use a timer or trade count)

    def _learn_from_trade(self, trade: TradeOutcome):
        """Learn patterns from trade outcome"""

        # Extract key conditions
        conditions = {
            "index": trade.index,
            "option_type": trade.option_type,
            "change_pct_range": self._get_range(trade.market_conditions.get("change_pct", 0)),
            "mtf_mode": trade.mtf_mode,
            "time_of_day": self._get_time_bucket(trade.timestamp),
            "confidence_range": self._get_confidence_range(trade.confidence),
        }

        if trade.outcome == "LOSS":
            # Check if this pattern already exists in avoid list
            existing = self._find_similar_pattern(conditions, self.conditions_to_avoid)

            if existing:
                existing["occurrences"] = existing.get("occurrences", 1) + 1
                if existing["occurrences"] >= 3:
                    existing["confidence"] = "HIGH"
            else:
                # New pattern to potentially avoid
                pattern = {
                    **conditions,
                    "occurrences": 1,
                    "confidence": "LOW",
                    "reason": f"Loss pattern detected",
                    "first_seen": trade.timestamp.isoformat(),
                }
                self.conditions_to_avoid.append(pattern)

        elif trade.outcome == "WIN":
            # Remove from avoid list if it was there
            self.conditions_to_avoid = [
                p for p in self.conditions_to_avoid
                if not self._matches_pattern(conditions, p) or p.get("occurrences", 0) > 2
            ]

    def _adapt_parameters(self):
        """Adapt system parameters based on recent performance"""

        # Calculate recent win rate
        recent_trades = list(self.rolling_stats["last_10_trades"])
        if len(recent_trades) < 5:
            return

        wins = sum(1 for t in recent_trades if t.outcome == "WIN")
        win_rate = (wins / len(recent_trades)) * 100

        # Adapt based on win rate
        if win_rate >= 70:
            # Performing well - can be slightly more aggressive
            self.adaptive_params["position_size_multiplier"] = min(1.2, self.adaptive_params["position_size_multiplier"] + 0.1)
            self.adaptive_params["confidence_boost"] = max(0, self.adaptive_params["confidence_boost"] - 5)
            if self.recovery_mode and self.consecutive_wins >= 3:
                self._exit_recovery_mode()

        elif win_rate <= 40:
            # Performing poorly - become more conservative
            self.adaptive_params["position_size_multiplier"] = max(0.3, self.adaptive_params["position_size_multiplier"] - 0.1)
            self.adaptive_params["confidence_boost"] = min(30, self.adaptive_params["confidence_boost"] + 5)

        else:
            # Normalize slowly
            self.adaptive_params["position_size_multiplier"] = 0.9 * self.adaptive_params["position_size_multiplier"] + 0.1 * 1.0
            self.adaptive_params["confidence_boost"] = int(0.9 * self.adaptive_params["confidence_boost"])

    def _exit_recovery_mode(self):
        """Exit recovery mode after sustained good performance"""
        print("[AdaptiveRisk] Exiting recovery mode - performance restored")
        self.recovery_mode = False
        self.adaptive_params["position_size_multiplier"] = 1.0
        self.adaptive_params["confidence_boost"] = 0
        self.adaptive_params["forced_mtf_mode"] = None

    def _update_risk_level(self):
        """Update current risk level based on all factors"""

        if self.is_halted:
            self.current_risk_level = RiskLevel.HALTED
            return

        risk_score = 0

        # Factor: Consecutive losses
        risk_score += self.consecutive_losses * 15

        # Factor: Daily P&L
        if self.daily_pnl < -2000:
            risk_score += 30
        elif self.daily_pnl < -1000:
            risk_score += 20
        elif self.daily_pnl < 0:
            risk_score += 10

        # Factor: Recent win rate
        recent = list(self.rolling_stats["last_10_trades"])
        if len(recent) >= 5:
            wins = sum(1 for t in recent if t.outcome == "WIN")
            win_rate = (wins / len(recent)) * 100
            if win_rate < 30:
                risk_score += 30
            elif win_rate < 50:
                risk_score += 15

        # Factor: Recovery mode
        if self.recovery_mode:
            risk_score += 20

        # Determine level
        if risk_score >= 70:
            self.current_risk_level = RiskLevel.CRITICAL
        elif risk_score >= 50:
            self.current_risk_level = RiskLevel.HIGH
        elif risk_score >= 25:
            self.current_risk_level = RiskLevel.ELEVATED
        else:
            self.current_risk_level = RiskLevel.NORMAL

    def _get_range(self, value: float) -> str:
        """Convert value to range bucket"""
        if value > 1.5:
            return "HIGH_POSITIVE"
        elif value > 0.5:
            return "POSITIVE"
        elif value > -0.5:
            return "NEUTRAL"
        elif value > -1.5:
            return "NEGATIVE"
        else:
            return "HIGH_NEGATIVE"

    def _get_time_bucket(self, dt: datetime) -> str:
        """Convert time to bucket"""
        hour = dt.hour
        if hour < 10:
            return "OPENING"
        elif hour < 12:
            return "MORNING"
        elif hour < 14:
            return "MIDDAY"
        else:
            return "CLOSING"

    def _get_confidence_range(self, conf: float) -> str:
        """Convert confidence to range"""
        if conf >= 80:
            return "HIGH"
        elif conf >= 65:
            return "MEDIUM"
        else:
            return "LOW"

    def _find_similar_pattern(self, conditions: Dict, patterns: List[Dict]) -> Optional[Dict]:
        """Find a similar pattern in the list"""
        for pattern in patterns:
            matches = 0
            total = 0
            for key, value in conditions.items():
                if key in pattern:
                    total += 1
                    if pattern[key] == value:
                        matches += 1

            if total > 0 and matches / total >= 0.7:  # 70% match
                return pattern
        return None

    def _matches_pattern(self, conditions: Dict, pattern: Dict) -> bool:
        """Check if conditions match a pattern"""
        # Only check if pattern has HIGH confidence
        if pattern.get("confidence") != "HIGH":
            return False

        matches = 0
        total = 0
        for key, value in conditions.items():
            if key in pattern and key not in ["occurrences", "confidence", "reason", "first_seen"]:
                total += 1
                if pattern[key] == value:
                    matches += 1

        return total > 0 and matches / total >= 0.8

    def _log_status(self, trade: TradeOutcome):
        """Log current status after trade"""
        status_icon = "✓" if trade.outcome == "WIN" else "✗" if trade.outcome == "LOSS" else "="

        print(f"[AdaptiveRisk] {status_icon} {trade.outcome} | "
              f"Daily P&L: {self.daily_pnl:+.0f} | "
              f"Risk: {self.current_risk_level.value} | "
              f"Size: {self.adaptive_params['position_size_multiplier']:.1f}x | "
              f"Conf+: {self.adaptive_params['confidence_boost']}")

    def reset_daily(self):
        """Reset daily counters (call at market open)"""
        self.today_trades = []
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.consecutive_wins = 0

        # Reset halt if it was daily-loss related
        if self.is_halted and "daily" in self.halt_reason.lower():
            self.is_halted = False
            self.halt_reason = ""

        # Gradually restore blocked bots
        if self.adaptive_params["blocked_bots"]:
            print(f"[AdaptiveRisk] Unblocking bots for new day: {self.adaptive_params['blocked_bots']}")
            self.adaptive_params["blocked_bots"] = set()

        self._save_state()
        print("[AdaptiveRisk] Daily reset complete")

    def resume_trading(self, override_reason: str = "manual"):
        """Manually resume trading after halt"""
        if self.is_halted:
            print(f"[AdaptiveRisk] Trading resumed (was halted for: {self.halt_reason})")
            self.is_halted = False
            self.halt_reason = ""
            self.current_risk_level = RiskLevel.ELEVATED  # Start cautious
            self.recovery_mode = True  # Enter recovery mode
            self._save_state()

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive status report"""
        recent_10 = list(self.rolling_stats["last_10_trades"])
        win_rate_10 = (sum(1 for t in recent_10 if t.outcome == "WIN") / len(recent_10) * 100) if recent_10 else 0

        return {
            "risk_level": self.current_risk_level.value,
            "is_halted": self.is_halted,
            "halt_reason": self.halt_reason,
            "recovery_mode": self.recovery_mode,
            "daily_pnl": round(self.daily_pnl, 2),
            "consecutive_losses": self.consecutive_losses,
            "consecutive_wins": self.consecutive_wins,
            "recent_win_rate": round(win_rate_10, 1),
            "trades_today": len(self.today_trades),
            "adaptive_params": {
                "position_size": self.adaptive_params["position_size_multiplier"],
                "confidence_boost": self.adaptive_params["confidence_boost"],
                "blocked_bots": list(self.adaptive_params["blocked_bots"]),
                "forced_mtf_mode": self.adaptive_params["forced_mtf_mode"],
            },
            "circuit_breakers": self.circuit_breakers,
            "patterns_to_avoid": len(self.conditions_to_avoid),
            "bot_performance": {
                name: {
                    "trades": perf["trades"],
                    "win_rate": round(perf["wins"] / perf["trades"] * 100, 1) if perf["trades"] > 0 else 0,
                    "pnl": round(perf["total_pnl"], 2),
                }
                for name, perf in self.bot_performance.items()
            }
        }

    def _save_state(self):
        """Persist state to disk"""
        state = {
            "current_risk_level": self.current_risk_level.value,
            "is_halted": self.is_halted,
            "halt_reason": self.halt_reason,
            "recovery_mode": self.recovery_mode,
            "consecutive_losses": self.consecutive_losses,
            "consecutive_wins": self.consecutive_wins,
            "adaptive_params": {
                **self.adaptive_params,
                "blocked_bots": list(self.adaptive_params["blocked_bots"]),
            },
            "conditions_to_avoid": self.conditions_to_avoid,
            "bot_performance": {
                name: {**perf, "recent_outcomes": list(perf["recent_outcomes"])}
                for name, perf in self.bot_performance.items()
            },
            "last_updated": datetime.now().isoformat(),
        }

        with open(self.data_dir / "adaptive_state.json", 'w') as f:
            json.dump(state, f, indent=2, default=str)

    def _load_state(self):
        """Load persisted state"""
        state_file = self.data_dir / "adaptive_state.json"

        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)

                self.current_risk_level = RiskLevel(state.get("current_risk_level", "NORMAL"))
                self.is_halted = state.get("is_halted", False)
                self.halt_reason = state.get("halt_reason", "")
                self.recovery_mode = state.get("recovery_mode", False)
                self.consecutive_losses = state.get("consecutive_losses", 0)
                self.consecutive_wins = state.get("consecutive_wins", 0)

                params = state.get("adaptive_params", {})
                self.adaptive_params.update(params)
                self.adaptive_params["blocked_bots"] = set(params.get("blocked_bots", []))

                self.conditions_to_avoid = state.get("conditions_to_avoid", [])

                # Restore bot performance
                for name, perf in state.get("bot_performance", {}).items():
                    self.bot_performance[name] = {
                        **perf,
                        "recent_outcomes": deque(perf.get("recent_outcomes", []), maxlen=20),
                    }

                print(f"[AdaptiveRisk] State loaded - Risk: {self.current_risk_level.value}")

            except Exception as e:
                print(f"[AdaptiveRisk] Error loading state: {e}")

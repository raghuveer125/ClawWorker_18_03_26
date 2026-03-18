"""
Deep Learning Module - Persistent Pattern Recognition

This module provides:
- Full trade context storage (every detail persisted to disk)
- Multi-condition pattern recognition
- Automatic pattern discovery from historical trades
- Win/loss pattern identification
- No dependency on RAM - everything on disk

Philosophy: "Those who don't learn from history are doomed to repeat it"
"""

import json
import os
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import hashlib

DEFAULT_BOT_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "bots"


@dataclass
class TradeContext:
    """Complete context of a trade for learning"""
    # Trade identification
    trade_id: str
    timestamp: str

    # Market conditions at entry
    index: str
    ltp: float
    change_pct: float
    high: float
    low: float
    volume: float

    # Options data
    pcr: float
    ce_oi: float
    pe_oi: float
    ce_oi_change: float
    pe_oi_change: float
    max_pain: float
    iv: float
    iv_percentile: float

    # Time context
    market_session: str  # PRE_OPEN, OPENING_NOISE, PRIME_TIME, etc.
    day_type: str  # NORMAL, WEEKLY_EXPIRY, MONTHLY_EXPIRY
    day_of_week: int
    hour: int
    minute: int

    # Regime context
    market_regime: str  # TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOL, LOW_VOL
    vix: float

    # Signal details
    action: str  # BUY_CE, BUY_PE
    strike: int
    entry_price: float
    target_price: float
    stop_loss: float
    confidence: float
    consensus_level: float
    contributing_bots: List[str]

    # Bot signals breakdown
    bot_signals: Dict[str, Dict] = field(default_factory=dict)

    # Outcome (filled after trade closes)
    exit_price: float = 0.0
    exit_time: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    outcome: str = ""  # WIN, LOSS, BREAKEVEN
    exit_reason: str = ""  # TARGET_HIT, SL_HIT, TIME_EXIT, MANUAL


@dataclass
class Pattern:
    """A discovered pattern from historical trades"""
    pattern_id: str
    conditions: Dict[str, Any]  # The conditions that define this pattern
    total_occurrences: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float
    avg_pnl: float
    avg_win_pnl: float
    avg_loss_pnl: float
    expectancy: float  # (win_rate * avg_win) - ((1-win_rate) * avg_loss)
    confidence_score: float  # Based on sample size and consistency
    last_updated: str

    # Pattern strength
    is_reliable: bool = False  # True if 10+ occurrences and consistent
    recommendation: str = ""  # TRADE, AVOID, NEEDS_MORE_DATA


class DeepLearningEngine:
    """
    Persistent deep learning engine for trading patterns

    All data stored on disk - survives restarts, no RAM dependency
    """

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.getenv(
            "BOT_DATA_DIR",
            str(DEFAULT_BOT_DATA_DIR)
        ))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # File paths
        self.trades_file = self.data_dir / "trade_contexts.jsonl"
        self.patterns_file = self.data_dir / "discovered_patterns.json"
        self.insights_file = self.data_dir / "trading_insights.jsonl"
        self.regime_history_file = self.data_dir / "regime_history.jsonl"

        # Pattern definitions - conditions to track
        self.pattern_conditions = [
            "market_session",
            "day_type",
            "market_regime",
            "pcr_bucket",
            "oi_signal",
            "vix_bucket",
            "hour_bucket",
            "index",
        ]

        # Load existing patterns
        self.patterns: Dict[str, Pattern] = self._load_patterns()

    def _load_patterns(self) -> Dict[str, Pattern]:
        """Load patterns from disk"""
        patterns = {}
        if self.patterns_file.exists():
            try:
                with open(self.patterns_file, "r") as f:
                    data = json.load(f)
                    for pid, pdata in data.items():
                        patterns[pid] = Pattern(**pdata)
            except (json.JSONDecodeError, TypeError):
                pass
        return patterns

    def _save_patterns(self):
        """Save patterns to disk"""
        data = {pid: asdict(p) for pid, p in self.patterns.items()}
        with open(self.patterns_file, "w") as f:
            json.dump(data, f, indent=2)

    def record_trade_entry(self, context: TradeContext):
        """Record a trade entry with full context"""
        # Append to trades file
        with open(self.trades_file, "a") as f:
            f.write(json.dumps(asdict(context)) + "\n")

    def record_trade_exit(
        self,
        trade_id: str,
        exit_price: float,
        outcome: str,
        pnl: float,
        pnl_pct: float,
        exit_reason: str
    ):
        """Record trade exit and trigger learning"""
        # Read all trades, update the matching one
        trades = self._load_all_trades()

        for trade in trades:
            if trade.get("trade_id") == trade_id:
                trade["exit_price"] = exit_price
                trade["exit_time"] = datetime.now().isoformat()
                trade["outcome"] = outcome
                trade["pnl"] = pnl
                trade["pnl_pct"] = pnl_pct
                trade["exit_reason"] = exit_reason
                break

        # Rewrite trades file
        with open(self.trades_file, "w") as f:
            for trade in trades:
                f.write(json.dumps(trade) + "\n")

        # Trigger pattern analysis
        self._analyze_and_update_patterns()

    def _load_all_trades(self) -> List[Dict]:
        """Load all trades from disk"""
        trades = []
        if self.trades_file.exists():
            with open(self.trades_file, "r") as f:
                for line in f:
                    if line.strip():
                        try:
                            trades.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        return trades

    def _bucket_value(self, key: str, value: Any) -> str:
        """Convert continuous values to buckets for pattern matching"""
        if key == "pcr_bucket":
            if value <= 0.6:
                return "VERY_LOW"
            elif value <= 0.8:
                return "LOW"
            elif value <= 1.0:
                return "NEUTRAL"
            elif value <= 1.2:
                return "HIGH"
            else:
                return "VERY_HIGH"

        elif key == "vix_bucket":
            if value <= 12:
                return "COMPLACENT"
            elif value <= 15:
                return "LOW"
            elif value <= 18:
                return "NORMAL"
            elif value <= 22:
                return "ELEVATED"
            else:
                return "FEAR"

        elif key == "hour_bucket":
            if value < 10:
                return "EARLY"
            elif value < 12:
                return "MORNING"
            elif value < 14:
                return "MIDDAY"
            else:
                return "AFTERNOON"

        return str(value)

    def _extract_conditions(self, trade: Dict) -> Dict[str, str]:
        """Extract pattern conditions from a trade"""
        conditions = {}

        for key in self.pattern_conditions:
            if key == "pcr_bucket":
                conditions[key] = self._bucket_value(key, trade.get("pcr", 1.0))
            elif key == "vix_bucket":
                conditions[key] = self._bucket_value(key, trade.get("vix", 15))
            elif key == "hour_bucket":
                conditions[key] = self._bucket_value(key, trade.get("hour", 12))
            elif key == "oi_signal":
                # Derive from OI changes
                ce_change = trade.get("ce_oi_change", 0)
                pe_change = trade.get("pe_oi_change", 0)
                change_pct = trade.get("change_pct", 0)

                oi_increasing = (ce_change + pe_change) > 0
                price_up = change_pct > 0.1

                if price_up and oi_increasing:
                    conditions[key] = "BULLISH_BUILDUP"
                elif price_up and not oi_increasing:
                    conditions[key] = "SHORT_COVERING"
                elif not price_up and oi_increasing:
                    conditions[key] = "BEARISH_BUILDUP"
                else:
                    conditions[key] = "LONG_UNWINDING"
            else:
                conditions[key] = str(trade.get(key, "UNKNOWN"))

        return conditions

    def _generate_pattern_id(self, conditions: Dict[str, str]) -> str:
        """Generate unique pattern ID from conditions"""
        sorted_items = sorted(conditions.items())
        data = json.dumps(sorted_items)
        return hashlib.md5(data.encode()).hexdigest()[:12]

    def _analyze_and_update_patterns(self):
        """Analyze all trades and update patterns"""
        trades = self._load_all_trades()

        # Only analyze completed trades
        completed = [t for t in trades if t.get("outcome")]

        # Group by pattern
        pattern_trades: Dict[str, List[Dict]] = defaultdict(list)

        for trade in completed:
            conditions = self._extract_conditions(trade)
            pattern_id = self._generate_pattern_id(conditions)
            pattern_trades[pattern_id].append({
                "conditions": conditions,
                "outcome": trade["outcome"],
                "pnl": trade.get("pnl", 0),
            })

        # Update patterns
        for pattern_id, trades_list in pattern_trades.items():
            wins = [t for t in trades_list if t["outcome"] == "WIN"]
            losses = [t for t in trades_list if t["outcome"] == "LOSS"]
            breakeven = [t for t in trades_list if t["outcome"] == "BREAKEVEN"]

            total = len(trades_list)
            win_count = len(wins)
            loss_count = len(losses)

            win_rate = win_count / total * 100 if total > 0 else 0
            avg_pnl = sum(t["pnl"] for t in trades_list) / total if total > 0 else 0
            avg_win_pnl = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
            avg_loss_pnl = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

            # Expectancy = (Win% * AvgWin) - (Loss% * AvgLoss)
            expectancy = (win_rate/100 * avg_win_pnl) - ((1 - win_rate/100) * abs(avg_loss_pnl))

            # Confidence score based on sample size
            confidence = min(100, total * 10)  # Max confidence at 10+ trades

            # Determine reliability
            is_reliable = total >= 10 and (win_rate >= 60 or win_rate <= 40)

            # Recommendation
            if total < 5:
                recommendation = "NEEDS_MORE_DATA"
            elif win_rate >= 60 and expectancy > 0:
                recommendation = "TRADE"
            elif win_rate <= 40 or expectancy < 0:
                recommendation = "AVOID"
            else:
                recommendation = "NEUTRAL"

            self.patterns[pattern_id] = Pattern(
                pattern_id=pattern_id,
                conditions=trades_list[0]["conditions"],
                total_occurrences=total,
                wins=win_count,
                losses=loss_count,
                breakeven=len(breakeven),
                win_rate=round(win_rate, 1),
                avg_pnl=round(avg_pnl, 2),
                avg_win_pnl=round(avg_win_pnl, 2),
                avg_loss_pnl=round(avg_loss_pnl, 2),
                expectancy=round(expectancy, 2),
                confidence_score=round(confidence, 1),
                last_updated=datetime.now().isoformat(),
                is_reliable=is_reliable,
                recommendation=recommendation,
            )

        # Save patterns
        self._save_patterns()

        # Generate insights
        self._generate_insights()

    def _generate_insights(self):
        """Generate actionable insights from patterns"""
        insights = []

        # Find best patterns
        reliable_patterns = [p for p in self.patterns.values() if p.is_reliable]

        # Best winning patterns
        best_winning = sorted(
            [p for p in reliable_patterns if p.recommendation == "TRADE"],
            key=lambda p: p.expectancy,
            reverse=True
        )[:5]

        for p in best_winning:
            insights.append({
                "type": "WINNING_PATTERN",
                "pattern_id": p.pattern_id,
                "conditions": p.conditions,
                "win_rate": p.win_rate,
                "expectancy": p.expectancy,
                "sample_size": p.total_occurrences,
                "insight": f"High probability setup: {self._describe_conditions(p.conditions)}",
                "timestamp": datetime.now().isoformat(),
            })

        # Patterns to avoid
        avoid_patterns = sorted(
            [p for p in reliable_patterns if p.recommendation == "AVOID"],
            key=lambda p: p.win_rate
        )[:5]

        for p in avoid_patterns:
            insights.append({
                "type": "AVOID_PATTERN",
                "pattern_id": p.pattern_id,
                "conditions": p.conditions,
                "win_rate": p.win_rate,
                "expectancy": p.expectancy,
                "sample_size": p.total_occurrences,
                "insight": f"Avoid trading when: {self._describe_conditions(p.conditions)}",
                "timestamp": datetime.now().isoformat(),
            })

        # Save insights
        with open(self.insights_file, "a") as f:
            for insight in insights:
                f.write(json.dumps(insight) + "\n")

    def _describe_conditions(self, conditions: Dict) -> str:
        """Create human-readable description of conditions"""
        parts = []
        for key, value in conditions.items():
            if key == "market_session":
                parts.append(f"Session: {value}")
            elif key == "day_type":
                parts.append(f"Day: {value}")
            elif key == "pcr_bucket":
                parts.append(f"PCR: {value}")
            elif key == "vix_bucket":
                parts.append(f"VIX: {value}")
            elif key == "oi_signal":
                parts.append(f"OI: {value}")
            elif key == "market_regime":
                parts.append(f"Regime: {value}")
        return " | ".join(parts)

    def get_pattern_for_conditions(self, conditions: Dict) -> Optional[Pattern]:
        """Get pattern matching current conditions"""
        pattern_id = self._generate_pattern_id(conditions)
        return self.patterns.get(pattern_id)

    def should_trade(self, market_data: Dict) -> Tuple[bool, str, float]:
        """
        Check if current conditions are favorable for trading

        Returns: (should_trade, reason, confidence_adjustment)
        """
        conditions = self._extract_conditions(market_data)
        pattern = self.get_pattern_for_conditions(conditions)

        if not pattern:
            return True, "No historical data for this pattern", 0

        if pattern.recommendation == "AVOID":
            return False, f"Historical win rate only {pattern.win_rate}% for these conditions", -20

        if pattern.recommendation == "TRADE":
            return True, f"High probability setup ({pattern.win_rate}% win rate)", +10

        if pattern.recommendation == "NEEDS_MORE_DATA":
            return True, "Insufficient data, proceed with caution", -5

        return True, "Neutral conditions", 0

    def get_confidence_adjustment(self, market_data: Dict) -> float:
        """Get confidence adjustment based on historical patterns"""
        conditions = self._extract_conditions(market_data)
        pattern = self.get_pattern_for_conditions(conditions)

        if not pattern:
            return 0

        # Adjust based on historical performance
        if pattern.win_rate >= 70:
            return 15
        elif pattern.win_rate >= 60:
            return 10
        elif pattern.win_rate <= 40:
            return -15
        elif pattern.win_rate <= 50:
            return -10

        return 0

    def get_best_patterns(self, limit: int = 10) -> List[Pattern]:
        """Get best performing patterns"""
        reliable = [p for p in self.patterns.values() if p.is_reliable]
        return sorted(reliable, key=lambda p: p.expectancy, reverse=True)[:limit]

    def get_avoid_patterns(self, limit: int = 10) -> List[Pattern]:
        """Get patterns to avoid"""
        reliable = [p for p in self.patterns.values() if p.is_reliable]
        return sorted(reliable, key=lambda p: p.win_rate)[:limit]

    def get_learning_summary(self) -> Dict:
        """Get summary of what the system has learned"""
        total_trades = len(self._load_all_trades())
        completed = len([t for t in self._load_all_trades() if t.get("outcome")])

        return {
            "total_trades_recorded": total_trades,
            "completed_trades": completed,
            "patterns_discovered": len(self.patterns),
            "reliable_patterns": len([p for p in self.patterns.values() if p.is_reliable]),
            "high_probability_setups": len([p for p in self.patterns.values() if p.recommendation == "TRADE"]),
            "patterns_to_avoid": len([p for p in self.patterns.values() if p.recommendation == "AVOID"]),
            "best_patterns": [
                {
                    "conditions": self._describe_conditions(p.conditions),
                    "win_rate": p.win_rate,
                    "expectancy": p.expectancy,
                }
                for p in self.get_best_patterns(3)
            ],
            "worst_patterns": [
                {
                    "conditions": self._describe_conditions(p.conditions),
                    "win_rate": p.win_rate,
                    "expectancy": p.expectancy,
                }
                for p in self.get_avoid_patterns(3)
            ],
        }

    def record_regime(self, index: str, regime: str, indicators: Dict):
        """Record market regime for historical analysis"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "index": index,
            "regime": regime,
            "indicators": indicators,
        }
        with open(self.regime_history_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

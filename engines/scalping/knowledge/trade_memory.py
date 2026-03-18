"""
Trade Memory - Records what works and what doesn't.
Persistent knowledge base for trading insights.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import sqlite3


@dataclass
class TradeRecord:
    """A single trade record."""
    trade_id: str
    symbol: str
    direction: str  # long, short
    entry_time: str
    exit_time: Optional[str]
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    strategy: str
    regime: str  # market regime at entry
    setup: Dict[str, Any]  # entry conditions
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    status: str = "open"  # open, closed, cancelled
    notes: str = ""
    tags: List[str] = field(default_factory=list)

    def close(self, exit_price: float, exit_time: Optional[str] = None):
        """Close the trade."""
        self.exit_price = exit_price
        self.exit_time = exit_time or datetime.now().isoformat()
        self.status = "closed"

        if self.direction == "long":
            self.pnl = (exit_price - self.entry_price) * self.quantity
            self.pnl_pct = (exit_price - self.entry_price) / self.entry_price * 100
        else:
            self.pnl = (self.entry_price - exit_price) * self.quantity
            self.pnl_pct = (self.entry_price - exit_price) / self.entry_price * 100


@dataclass
class StrategyInsight:
    """Learned insight about a strategy."""
    strategy: str
    insight_type: str  # win_condition, lose_condition, regime_fit, parameter
    description: str
    confidence: float  # 0-1
    evidence: Dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def update_confidence(self, new_evidence: Dict, adjustment: float):
        """Update confidence based on new evidence."""
        self.evidence = {**self.evidence, **new_evidence}
        self.confidence = max(0, min(1, self.confidence + adjustment))
        self.updated_at = datetime.now().isoformat()


class TradeMemory:
    """
    Persistent memory for trading knowledge.

    Tracks:
    - Trade history with outcomes
    - Strategy performance by regime
    - Winning/losing patterns
    - Parameter effectiveness
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path(__file__).parent / "trade_memory.db"
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                symbol TEXT,
                direction TEXT,
                entry_time TEXT,
                exit_time TEXT,
                entry_price REAL,
                exit_price REAL,
                quantity REAL,
                strategy TEXT,
                regime TEXT,
                setup TEXT,
                pnl REAL,
                pnl_pct REAL,
                status TEXT,
                notes TEXT,
                tags TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT,
                insight_type TEXT,
                description TEXT,
                confidence REAL,
                evidence TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS strategy_stats (
                strategy TEXT,
                regime TEXT,
                total_trades INTEGER,
                win_count INTEGER,
                lose_count INTEGER,
                total_pnl REAL,
                avg_win REAL,
                avg_loss REAL,
                win_rate REAL,
                profit_factor REAL,
                updated_at TEXT,
                PRIMARY KEY (strategy, regime)
            )
        """)

        conn.commit()
        conn.close()

    def record_trade(self, trade: TradeRecord):
        """Record a new trade."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.trade_id,
            trade.symbol,
            trade.direction,
            trade.entry_time,
            trade.exit_time,
            trade.entry_price,
            trade.exit_price,
            trade.quantity,
            trade.strategy,
            trade.regime,
            json.dumps(trade.setup),
            trade.pnl,
            trade.pnl_pct,
            trade.status,
            trade.notes,
            json.dumps(trade.tags),
        ))

        conn.commit()
        conn.close()

        # Update stats if trade is closed
        if trade.status == "closed":
            self._update_strategy_stats(trade)

    def _update_strategy_stats(self, trade: TradeRecord):
        """Update strategy statistics."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get current stats
        cursor.execute("""
            SELECT * FROM strategy_stats WHERE strategy = ? AND regime = ?
        """, (trade.strategy, trade.regime))

        row = cursor.fetchone()

        if row:
            total = row[2] + 1
            wins = row[3] + (1 if trade.pnl > 0 else 0)
            losses = row[4] + (1 if trade.pnl <= 0 else 0)
            total_pnl = row[5] + trade.pnl

            # Recalculate averages
            if wins > 0:
                avg_win = (row[6] * row[3] + (trade.pnl if trade.pnl > 0 else 0)) / wins
            else:
                avg_win = 0

            if losses > 0:
                avg_loss = abs((row[7] * row[4] + (trade.pnl if trade.pnl <= 0 else 0)) / losses)
            else:
                avg_loss = 0

            win_rate = wins / total if total > 0 else 0
            profit_factor = (avg_win * wins) / (avg_loss * losses) if avg_loss * losses > 0 else 0

            cursor.execute("""
                UPDATE strategy_stats SET
                    total_trades = ?,
                    win_count = ?,
                    lose_count = ?,
                    total_pnl = ?,
                    avg_win = ?,
                    avg_loss = ?,
                    win_rate = ?,
                    profit_factor = ?,
                    updated_at = ?
                WHERE strategy = ? AND regime = ?
            """, (
                total, wins, losses, total_pnl, avg_win, avg_loss,
                win_rate, profit_factor, datetime.now().isoformat(),
                trade.strategy, trade.regime,
            ))
        else:
            # Insert new stats
            wins = 1 if trade.pnl > 0 else 0
            losses = 0 if trade.pnl > 0 else 1

            cursor.execute("""
                INSERT INTO strategy_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.strategy,
                trade.regime,
                1,  # total
                wins,
                losses,
                trade.pnl,
                trade.pnl if trade.pnl > 0 else 0,  # avg_win
                abs(trade.pnl) if trade.pnl <= 0 else 0,  # avg_loss
                wins,  # win_rate
                0,  # profit_factor
                datetime.now().isoformat(),
            ))

        conn.commit()
        conn.close()

    def add_insight(self, insight: StrategyInsight):
        """Add or update a strategy insight."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO insights (strategy, insight_type, description, confidence, evidence, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            insight.strategy,
            insight.insight_type,
            insight.description,
            insight.confidence,
            json.dumps(insight.evidence),
            insight.created_at,
            insight.updated_at,
        ))

        conn.commit()
        conn.close()

    def get_strategy_stats(self, strategy: str, regime: Optional[str] = None) -> List[Dict]:
        """Get statistics for a strategy."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if regime:
            cursor.execute("""
                SELECT * FROM strategy_stats WHERE strategy = ? AND regime = ?
            """, (strategy, regime))
        else:
            cursor.execute("""
                SELECT * FROM strategy_stats WHERE strategy = ?
            """, (strategy,))

        columns = ["strategy", "regime", "total_trades", "win_count", "lose_count",
                   "total_pnl", "avg_win", "avg_loss", "win_rate", "profit_factor", "updated_at"]

        results = []
        for row in cursor.fetchall():
            results.append(dict(zip(columns, row)))

        conn.close()
        return results

    def get_insights(self, strategy: str, insight_type: Optional[str] = None) -> List[StrategyInsight]:
        """Get insights for a strategy."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if insight_type:
            cursor.execute("""
                SELECT * FROM insights WHERE strategy = ? AND insight_type = ?
                ORDER BY confidence DESC
            """, (strategy, insight_type))
        else:
            cursor.execute("""
                SELECT * FROM insights WHERE strategy = ?
                ORDER BY confidence DESC
            """, (strategy,))

        insights = []
        for row in cursor.fetchall():
            insights.append(StrategyInsight(
                strategy=row[1],
                insight_type=row[2],
                description=row[3],
                confidence=row[4],
                evidence=json.loads(row[5]),
                created_at=row[6],
                updated_at=row[7],
            ))

        conn.close()
        return insights

    def get_best_regime_for_strategy(self, strategy: str) -> Optional[str]:
        """Get the best performing regime for a strategy."""
        stats = self.get_strategy_stats(strategy)
        if not stats:
            return None

        # Filter for sufficient data
        valid_stats = [s for s in stats if s["total_trades"] >= 10]
        if not valid_stats:
            return None

        # Sort by profit factor
        sorted_stats = sorted(valid_stats, key=lambda x: x["profit_factor"], reverse=True)
        return sorted_stats[0]["regime"]

    def get_summary(self) -> Dict:
        """Get overall trading summary."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'closed'")
        total_trades = cursor.fetchone()[0]

        cursor.execute("SELECT SUM(pnl) FROM trades WHERE status = 'closed'")
        total_pnl = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'closed' AND pnl > 0")
        wins = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT strategy) FROM trades")
        strategies = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM insights")
        insights = cursor.fetchone()[0]

        conn.close()

        return {
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(wins / total_trades * 100, 1) if total_trades > 0 else 0,
            "strategies_tracked": strategies,
            "insights_recorded": insights,
        }


# Singleton
_memory = None


def get_trade_memory() -> TradeMemory:
    global _memory
    if _memory is None:
        _memory = TradeMemory()
    return _memory

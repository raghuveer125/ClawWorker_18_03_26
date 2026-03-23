"""
Autonomous Trading System - Self-Learning Auto-Execution

This system automatically:
1. Monitors market conditions
2. Gets signals from 6 bots
3. Executes trades when consensus is HIGH
4. Manages positions (stop loss, target)
5. Learns from every trade outcome
6. Adjusts strategy based on performance

SAFETY FEATURES:
- Daily loss limit (auto-stops if exceeded)
- Position size limits
- Time filters (no trading in volatile periods)
- Paper trading mode for testing
- Emergency kill switch

Philosophy: "Consistent small profits > occasional big wins"
"""

import json
import os
import shutil
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
from enum import Enum
import threading
import logging

# Import Fyers client for live trading
try:
    from .fyers_client import FyersClient
    FYERS_AVAILABLE = True
except ImportError:
    FYERS_AVAILABLE = False

# Import index config from shared engine
try:
    import sys
    _PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))
    from shared_project_engine.indices import INDEX_CONFIG, ACTIVE_INDICES
    from shared_project_engine.strategy_isolation import (
        DEFAULT_AUTO_TRADER_STRATEGY_ID,
        StrategyRuntimeLock,
        normalize_strategy_id,
        resolve_strategy_component_dir,
    )
    _INDEX_LOT_SIZES = {name: cfg["lot_size"] for name, cfg in INDEX_CONFIG.items()}
except ImportError:
    _INDEX_LOT_SIZES = {"NIFTY50": 25, "BANKNIFTY": 15, "SENSEX": 10, "FINNIFTY": 25, "MIDCPNIFTY": 50}
    ACTIVE_INDICES = ["SENSEX", "NIFTY50", "BANKNIFTY", "FINNIFTY"]
    DEFAULT_AUTO_TRADER_STRATEGY_ID = "clawwork-autotrader"

    class StrategyRuntimeLock:
        def __init__(self, runtime_dir, strategy_id, component):
            self.runtime_dir = Path(runtime_dir)

        def acquire(self, extra_metadata=None):
            self.runtime_dir.mkdir(parents=True, exist_ok=True)

        def release(self):
            return None

    def normalize_strategy_id(value: Optional[str], default: str) -> str:
        return str(value or default).strip() or default

    def resolve_strategy_component_dir(root_dir: Path, strategy_id: str, component: str) -> Path:
        return Path(root_dir) / strategy_id / component


def get_lot_size(index: str) -> int:
    """Get lot size for an index from shared config."""
    return _INDEX_LOT_SIZES.get(index, 50)

# Import execution quality tracker for go-live validation
try:
    from .execution_quality import ExecutionQualityTracker, ExecutionMetrics
    EXECUTION_TRACKING_AVAILABLE = True
except ImportError:
    EXECUTION_TRACKING_AVAILABLE = False
    ExecutionQualityTracker = None
    ExecutionMetrics = None

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _default_strategy_runtime_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "paper_strategies_runtime"


def _default_strategy_data_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "paper_strategies"


def resolve_auto_trader_strategy_id(explicit: Optional[str] = None) -> str:
    return normalize_strategy_id(
        explicit or os.getenv("AUTO_TRADER_STRATEGY_ID"),
        DEFAULT_AUTO_TRADER_STRATEGY_ID,
    )


def resolve_auto_trader_data_dir(strategy_id: Optional[str] = None) -> Path:
    explicit_dir = os.getenv("AUTO_TRADER_DATA_DIR")
    if explicit_dir:
        return Path(explicit_dir)

    normalized_strategy = resolve_auto_trader_strategy_id(strategy_id)
    legacy_dir = Path("data/auto_trader")
    if normalized_strategy == DEFAULT_AUTO_TRADER_STRATEGY_ID and legacy_dir.exists():
        return legacy_dir

    base_root = Path(os.getenv("AUTO_TRADER_DATA_ROOT", str(_default_strategy_data_root())))
    return resolve_strategy_component_dir(base_root, normalized_strategy, "auto_trader")


def resolve_auto_trader_runtime_dir(strategy_id: Optional[str] = None) -> Path:
    base_root = Path(os.getenv("PAPER_TRADING_RUNTIME_ROOT", str(_default_strategy_runtime_root())))
    normalized_strategy = resolve_auto_trader_strategy_id(strategy_id)
    return Path(base_root) / normalized_strategy


def get_trading_mode_from_env() -> "TradingMode":
    """
    Determine trading mode from environment variables.

    Environment Variables:
        FYERS_DRY_RUN: "true" = Paper mode, "false" = May be live
        FYERS_ALLOW_LIVE_ORDERS: "true" = Live allowed, "false" = Paper only

    Returns:
        TradingMode.PAPER if DRY_RUN=true OR ALLOW_LIVE_ORDERS=false
        TradingMode.LIVE only if DRY_RUN=false AND ALLOW_LIVE_ORDERS=true
    """
    dry_run = os.getenv("FYERS_DRY_RUN", "true").lower() == "true"
    allow_live = os.getenv("FYERS_ALLOW_LIVE_ORDERS", "false").lower() == "true"

    if dry_run or not allow_live:
        logger.info(f"Paper trading mode (DRY_RUN={dry_run}, ALLOW_LIVE={allow_live})")
        return TradingMode.PAPER
    else:
        logger.warning("LIVE TRADING MODE ENABLED - Real money at risk!")
        return TradingMode.LIVE


class TradingMode(Enum):
    """Trading modes"""
    PAPER = "paper"      # Simulated trades (no real money)
    LIVE = "live"        # Real money trading
    DISABLED = "disabled"  # No trading


class PositionStatus(Enum):
    """Position status"""
    OPEN = "open"
    CLOSED = "closed"
    PENDING = "pending"


@dataclass
class RiskConfig:
    """Risk management configuration - LIVE TRADING with ₹5,000 capital"""
    # Capital
    total_capital: float = 5000.0        # Total trading capital

    # Daily limits (conservative for small capital)
    max_daily_loss: float = 500.0        # Stop trading if daily loss exceeds 10% of capital
    max_daily_trades: int = 5            # Limited trades to reduce brokerage impact
    max_daily_profit: float = 1000.0     # Take profit for the day (20% of capital)

    # Position limits
    max_position_size: float = 4000.0    # Maximum capital per trade (80% of capital)
    max_concurrent_positions: int = 1    # Only 1 position at a time (small capital)
    position_size_pct: float = 80.0      # % of capital per trade

    # Risk per trade
    max_loss_per_trade: float = 250.0    # Maximum loss per single trade (5% of capital)
    stop_loss_pct: float = 20.0          # Stop loss 20% of premium (options are volatile)
    target_pct: float = 30.0             # Target 30% of premium (1:1.5 R:R)

    # Filters (AI-optimized based on log analysis)
    min_probability: int = 55            # Minimum weighted confidence
    min_conviction: str = "HIGH"         # Minimum conviction level
    min_consensus: float = 0.33          # Minimum bot consensus

    # Time filters
    no_trade_first_minutes: int = 15     # No trading first 15 min
    no_trade_last_minutes: int = 30      # No trading last 30 min
    no_trade_around_news: bool = True    # Avoid trading around major news

    # Brokerage (Fyers)
    brokerage_per_order: float = 20.0    # ₹20 per order
    stt_rate: float = 0.0005             # 0.05% STT on sell
    other_charges: float = 15.0          # Exchange, GST, etc.


@dataclass
class Position:
    """Active position"""
    id: str
    symbol: str
    index: str
    option_type: str  # CE or PE
    strike: int
    entry_price: float
    quantity: int
    entry_time: str
    stop_loss: float
    target: float
    status: str = "open"
    current_price: Optional[float] = None  # Live price for P&L calculation
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    pnl: float = 0.0
    exit_reason: Optional[str] = None
    bot_signals: Dict = None
    mode: str = "paper"  # paper or live
    strategy_id: str = ""


@dataclass
class TradeLog:
    """Complete trade log for learning"""
    trade_id: str
    timestamp: str
    symbol: str
    index: str
    option_type: str
    strike: int
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    outcome: str  # WIN, LOSS, BREAKEVEN
    exit_reason: str
    duration_minutes: int
    market_bias: str
    bot_signals: Dict
    probability: int
    conviction: str
    # Market context
    index_change_pct: float
    vix: float
    pcr: float
    # Learning flags
    was_counter_trend: bool
    was_gap_trade: bool
    was_overbought: bool
    was_oversold: bool
    # Trading mode
    mode: str = "paper"  # paper or live
    strategy_id: str = ""

    # =========================================================================
    # EXECUTION QUALITY METRICS (for go-live validation)
    # =========================================================================
    # Price execution
    signal_price: Optional[float] = None       # Price when signal generated
    expected_price: Optional[float] = None     # Price when order submitted
    slippage_pct: Optional[float] = None       # (actual - expected) / expected * 100
    slippage_cost: Optional[float] = None      # Slippage in INR

    # Timing metrics (milliseconds)
    order_submit_time: Optional[str] = None    # When order sent to broker
    fill_time: Optional[str] = None            # When order filled
    total_latency_ms: Optional[int] = None     # Time from submit to fill
    api_response_time_ms: Optional[int] = None # Broker API response time

    # Order status
    order_status: str = "filled"               # filled, partial, rejected, timeout
    filled_quantity: Optional[int] = None      # Actual quantity filled
    fill_rate_pct: Optional[float] = None      # filled / ordered * 100
    rejection_reason: Optional[str] = None     # If order rejected

    # Market context at execution
    spread_at_entry: Optional[float] = None    # Bid-ask spread when entered
    spread_at_exit: Optional[float] = None     # Bid-ask spread when exited


def _build_fyers_option_symbol(index: str, strike: int, option_type: str) -> Optional[str]:
    """Build a Fyers-format option symbol using IST time for expiry calculation.

    Pure module-level function — no instance state required.
    Returns None if the index is not recognised, strike is missing, or the
    index has no active weekly expiry contract.
    Callers must check for None before using the symbol.
    """
    # All recognised indices and their Fyers symbol prefix.
    _index_prefix_map = {
        "NIFTY50": "NSE:NIFTY",
        "BANKNIFTY": "NSE:BANKNIFTY",
        "FINNIFTY": "NSE:FINNIFTY",
        "MIDCPNIFTY": "NSE:MIDCPNIFTY",
        "SENSEX": "BSE:SENSEX",
    }
    # Indices with an active weekly expiry contract and the weekday they expire
    # (Monday=0 … Sunday=6).  BANKNIFTY, FINNIFTY, and MIDCPNIFTY are intentionally
    # absent: SEBI's October 2024 circular restricted weekly options to one contract
    # per exchange, leaving only NIFTY50 (NSE, Thursday) and SENSEX (BSE, Friday).
    _weekly_expiry_weekday = {
        "NIFTY50": 3,  # Thursday
        "SENSEX": 4,   # Friday
    }

    prefix = _index_prefix_map.get(index, "")
    if not prefix:
        logger.error("_build_fyers_option_symbol: unknown index '%s' — skipping", index)
        return None
    if not strike:
        logger.error("_build_fyers_option_symbol: strike is 0 or None for %s — skipping", index)
        return None
    expiry_weekday = _weekly_expiry_weekday.get(index)
    if expiry_weekday is None:
        logger.error(
            "_build_fyers_option_symbol: %s has no active weekly expiry — skipping (unsupported)",
            index,
        )
        return None

    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    days = (expiry_weekday - now_ist.weekday()) % 7
    if days == 0 and now_ist.hour >= 15:
        days = 7
    expiry_str = (now_ist + timedelta(days=days)).strftime("%d%b%y").upper()
    return f"{prefix}{expiry_str}{strike}{option_type}"


class AutoTrader:
    """
    Autonomous Trading System

    Integrates with:
    - EnsembleCoordinator for signals
    - FYERS API for execution (or paper trading)
    - Deep Learning for pattern recognition
    - Self-learning from every trade
    """

    def __init__(
        self,
        ensemble,
        fyers_client=None,
        risk_config: Optional[RiskConfig] = None,
        mode: TradingMode = TradingMode.PAPER,
        data_dir: str = "data/auto_trader",
        strategy_id: Optional[str] = None,
    ):
        self.ensemble = ensemble
        self.fyers = fyers_client
        self.risk = risk_config or RiskConfig()
        self.mode = mode
        self.strategy_id = resolve_auto_trader_strategy_id(strategy_id)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir = resolve_auto_trader_runtime_dir(self.strategy_id)
        self.runtime_lock = StrategyRuntimeLock(
            runtime_dir=self.runtime_dir,
            strategy_id=self.strategy_id,
            component="auto_trader",
        )

        # State - separate P&L tracking for paper and live modes
        self.positions: Dict[str, Position] = {}
        self.daily_pnl: Dict[str, float] = {"paper": 0.0, "live": 0.0}
        self.daily_trades: Dict[str, int] = {"paper": 0, "live": 0}
        self.is_running: bool = False
        self.is_paused: bool = False
        self.last_trade_time: Optional[datetime] = None

        # Price history for momentum calculation
        self.price_history: Dict[str, List[Dict]] = {}  # index -> [{ltp, change_pct, timestamp}]

        # Files
        self.positions_file = self.data_dir / "positions.json"
        self.trades_log_file = self.data_dir / "trades_log.jsonl"
        self.trades_quarantine_file = self.data_dir / "trades_log_quarantine.jsonl"
        self.daily_summary_file = self.data_dir / "daily_summary.jsonl"
        self.learning_file = self.data_dir / "learning_insights.json"
        self.screener_dir = Path(
            os.getenv(
                "AUTO_TRADER_SCREENER_DIR",
                str(Path(__file__).parent.parent / "data" / "fyers"),
            )
        )
        self.screener_max_age_seconds = max(15, _env_int("AUTO_TRADER_SCREENER_MAX_AGE_SEC", 90))
        self.screener_refresh_cooldown_seconds = max(
            10,
            _env_int("AUTO_TRADER_SCREENER_REFRESH_COOLDOWN_SEC", 30),
        )
        self.market_data_status: Dict[str, Any] = {
            "healthy": False,
            "available": False,
            "message": "Waiting for fresh screener data",
            "source": None,
            "file": None,
            "updated_at": None,
            "age_seconds": None,
        }
        self._last_screener_refresh_attempt: Optional[datetime] = None
        self.recent_exit_times: Dict[str, str] = {}
        self.trade_history_status: Dict[str, Any] = {
            "healthy": True,
            "sanitized": False,
            "valid_rows": 0,
            "quarantined_rows": 0,
            "message": "Trade history not checked yet",
            "quarantine_file": str(self.trades_quarantine_file),
        }
        self.learning_adaptation_min_trades = max(5, _env_int("AUTO_TRADER_LEARNING_MIN_TRADES", 10))
        self.learning_adaptation_min_wins = max(3, _env_int("AUTO_TRADER_LEARNING_MIN_WINS", 5))
        self.execution_quality_status: Dict[str, Any] = {
            "available": False,
            "cleanup_performed": False,
            "canonical_dir": None,
            "legacy_dir": None,
            "migrated_files": [],
            "discarded_files": [],
            "message": "Execution quality tracker not initialized",
        }

        # Load state
        self._sanitize_trade_history()
        self._load_state()
        self._load_recent_exit_times()

        # Execution quality tracking for go-live validation
        if EXECUTION_TRACKING_AVAILABLE:
            exec_quality_dir = self.data_dir.parent / "execution_quality"
            self.execution_tracker = ExecutionQualityTracker(str(exec_quality_dir))
            self._cleanup_execution_quality_storage()
            logger.info("ExecutionQualityTracker initialized for go-live validation")
        else:
            self.execution_tracker = None
            self._set_execution_quality_status(
                available=False,
                cleanup_performed=False,
                canonical_dir=None,
                legacy_dir=None,
                migrated_files=[],
                discarded_files=[],
                message="ExecutionQualityTracker not available",
            )
            logger.warning("ExecutionQualityTracker not available - execution metrics will not be tracked")

        # Pending execution metrics (trade_id -> ExecutionMetrics)
        self._pending_executions: Dict[str, Any] = {}

        # Callbacks
        self.on_trade_executed: Optional[Callable] = None
        self.on_position_closed: Optional[Callable] = None
        self.on_daily_limit_hit: Optional[Callable] = None

        logger.info(
            "AutoTrader initialized in %s mode for strategy '%s' (%s)",
            mode.value,
            self.strategy_id,
            self.data_dir,
        )

    def _get_market_data_client(self):
        """Return the cached market data client, initializing it on first call.

        Returns the client instance, or None if initialization failed.
        """
        if not hasattr(self, '_market_data_client'):
            try:
                from .fyers_client import build_market_data_client
                self._market_data_client = build_market_data_client()
            except Exception:
                self._market_data_client = None
        return self._market_data_client

    def _load_state(self):
        """Load persisted state"""
        if self.positions_file.exists():
            try:
                with open(self.positions_file) as f:
                    data = json.load(f)
                    for pos_data in data.get("positions", []):
                        pos = Position(**pos_data)
                        if pos.status == "open":
                            self.positions[pos.id] = pos

                    # Handle both old (single value) and new (dict) formats
                    daily_pnl = data.get("daily_pnl", 0)
                    daily_trades = data.get("daily_trades", 0)

                    if isinstance(daily_pnl, dict):
                        self.daily_pnl = {"paper": daily_pnl.get("paper", 0), "live": daily_pnl.get("live", 0)}
                    else:
                        # Backwards compatibility: old single value goes to paper
                        self.daily_pnl = {"paper": float(daily_pnl), "live": 0.0}

                    if isinstance(daily_trades, dict):
                        self.daily_trades = {"paper": daily_trades.get("paper", 0), "live": daily_trades.get("live", 0)}
                    else:
                        self.daily_trades = {"paper": int(daily_trades), "live": 0}
            except (json.JSONDecodeError, TypeError):
                pass

    def _save_state(self):
        """Save state to disk atomically (write-then-rename)."""
        data = {
            "positions": [asdict(p) for p in self.positions.values()],
            "daily_pnl": self.daily_pnl,  # Dict with paper/live keys
            "daily_trades": self.daily_trades,  # Dict with paper/live keys
            "last_updated": datetime.now().isoformat(),
        }
        tmp_file = Path(str(self.positions_file) + ".tmp")
        with open(tmp_file, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, self.positions_file)

    def _get_current_daily_pnl(self) -> float:
        """Get daily P&L for current mode"""
        return self.daily_pnl.get(self.mode.value, 0.0)

    def _get_current_daily_trades(self) -> int:
        """Get daily trades count for current mode"""
        return self.daily_trades.get(self.mode.value, 0)

    def _log_trade(self, trade: TradeLog):
        """Log trade for learning"""
        with open(self.trades_log_file, "a") as f:
            f.write(json.dumps(asdict(trade)) + "\n")

    def _load_recent_exit_times(self):
        """Load the latest exit timestamp per symbol for cooldown enforcement."""
        latest_exit_times: Dict[str, str] = {}
        if not self.trades_log_file.exists():
            self.recent_exit_times = latest_exit_times
            return

        try:
            with open(self.trades_log_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    symbol = str(row.get("symbol", "")).strip()
                    timestamp = row.get("timestamp")
                    if not symbol or not timestamp:
                        continue
                    previous = latest_exit_times.get(symbol)
                    if previous is None or timestamp > previous:
                        latest_exit_times[symbol] = timestamp
        except OSError:
            latest_exit_times = {}

        self.recent_exit_times = latest_exit_times

    def _set_trade_history_status(
        self,
        *,
        healthy: bool,
        sanitized: bool,
        valid_rows: int,
        quarantined_rows: int,
        message: str,
    ):
        self.trade_history_status = {
            "healthy": healthy,
            "sanitized": sanitized,
            "valid_rows": valid_rows,
            "quarantined_rows": quarantined_rows,
            "message": message,
            "quarantine_file": str(self.trades_quarantine_file),
        }

    def _set_execution_quality_status(
        self,
        *,
        available: bool,
        cleanup_performed: bool,
        canonical_dir: Optional[Path],
        legacy_dir: Optional[Path],
        migrated_files: List[str],
        discarded_files: List[str],
        message: str,
    ):
        self.execution_quality_status = {
            "available": available,
            "cleanup_performed": cleanup_performed,
            "canonical_dir": str(canonical_dir) if canonical_dir else None,
            "legacy_dir": str(legacy_dir) if legacy_dir else None,
            "migrated_files": list(migrated_files),
            "discarded_files": list(discarded_files),
            "message": message,
        }

    def _cleanup_execution_quality_storage(self):
        """Remove the stale auto_trader-scoped execution-quality store in favor of the canonical path."""
        if self.execution_tracker is None:
            self._set_execution_quality_status(
                available=False,
                cleanup_performed=False,
                canonical_dir=None,
                legacy_dir=None,
                migrated_files=[],
                discarded_files=[],
                message="Execution quality tracker not initialized",
            )
            return

        canonical_dir = self.execution_tracker.data_dir
        legacy_dir = self.data_dir / "execution_quality"

        if not legacy_dir.exists():
            self._set_execution_quality_status(
                available=True,
                cleanup_performed=False,
                canonical_dir=canonical_dir,
                legacy_dir=legacy_dir,
                migrated_files=[],
                discarded_files=[],
                message="No legacy execution-quality store found",
            )
            return

        migrated_files: List[str] = []
        discarded_files: List[str] = []

        for legacy_file in sorted(legacy_dir.rglob("*")):
            if not legacy_file.is_file():
                continue
            relative_path = legacy_file.relative_to(legacy_dir)
            canonical_file = canonical_dir / relative_path
            canonical_file.parent.mkdir(parents=True, exist_ok=True)

            if not canonical_file.exists():
                shutil.move(str(legacy_file), str(canonical_file))
                migrated_files.append(str(relative_path))
                continue

            discarded_files.append(str(relative_path))
            try:
                legacy_file.unlink()
            except FileNotFoundError:
                pass

        for path in sorted(legacy_dir.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            legacy_dir.rmdir()
        except OSError:
            pass

        cleanup_performed = bool(migrated_files or discarded_files)
        self._set_execution_quality_status(
            available=True,
            cleanup_performed=cleanup_performed,
            canonical_dir=canonical_dir,
            legacy_dir=legacy_dir,
            migrated_files=migrated_files,
            discarded_files=discarded_files,
            message=(
                "Legacy execution-quality store collapsed into canonical path"
                if cleanup_performed
                else "Execution-quality store already canonical"
            ),
        )

    def _sanitize_trade_history(self):
        """Quarantine legacy/corrupt trade rows and rebuild learning from trusted rows."""
        if not self.trades_log_file.exists():
            self._set_trade_history_status(
                healthy=True,
                sanitized=False,
                valid_rows=0,
                quarantined_rows=0,
                message="No trade history found",
            )
            return

        quarantined_entries: List[Dict[str, Any]] = []
        candidate_rows: List[Dict[str, Any]] = []

        try:
            with open(self.trades_log_file) as f:
                raw_lines = f.readlines()
        except OSError as e:
            self._set_trade_history_status(
                healthy=False,
                sanitized=False,
                valid_rows=0,
                quarantined_rows=0,
                message=f"Failed to read trade history: {e}",
            )
            return

        for idx, line in enumerate(raw_lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                quarantined_entries.append(self._make_quarantine_entry("invalid_json", {"raw": line.strip()}))
                continue

            valid, reason = self._validate_trade_history_row(row)
            if not valid:
                quarantined_entries.append(self._make_quarantine_entry(reason, row))
                continue

            candidate_rows.append({
                "row": row,
                "line_index": idx,
                "sort_key": self._trade_history_sort_key(row),
            })

        keep_indices = set()
        by_trade_id: Dict[str, List[Dict[str, Any]]] = {}
        for entry in candidate_rows:
            trade_id = str(entry["row"].get("trade_id", "")).strip()
            by_trade_id.setdefault(trade_id, []).append(entry)

        for entries in by_trade_id.values():
            if len(entries) == 1:
                keep_indices.add(entries[0]["line_index"])
                continue

            winner = max(entries, key=lambda item: item["sort_key"])
            keep_indices.add(winner["line_index"])
            for entry in entries:
                if entry["line_index"] == winner["line_index"]:
                    continue
                quarantined_entries.append(self._make_quarantine_entry("duplicate_trade_id", entry["row"]))

        valid_rows = [
            entry["row"]
            for entry in candidate_rows
            if entry["line_index"] in keep_indices
        ]

        valid_rows.sort(key=self._trade_history_sort_key)

        sanitized = len(quarantined_entries) > 0 or len(valid_rows) != len(raw_lines)
        if quarantined_entries:
            with open(self.trades_quarantine_file, "a") as f:
                for entry in quarantined_entries:
                    f.write(json.dumps(entry) + "\n")

        if sanitized:
            with open(self.trades_log_file, "w") as f:
                for row in valid_rows:
                    f.write(json.dumps(row) + "\n")

        self._rebuild_learning_insights(valid_rows)
        self._set_trade_history_status(
            healthy=len(quarantined_entries) == 0,
            sanitized=sanitized,
            valid_rows=len(valid_rows),
            quarantined_rows=len(quarantined_entries),
            message=(
                f"Quarantined {len(quarantined_entries)} legacy/corrupt trade row(s)"
                if quarantined_entries
                else "Trade history verified"
            ),
        )

    def _make_quarantine_entry(self, reason: str, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "quarantined_at": datetime.now().isoformat(),
            "reason": reason,
            "row": row,
        }

    def _trade_history_sort_key(self, row: Dict[str, Any]) -> tuple:
        timestamp = str(row.get("timestamp", "") or "")
        duration = row.get("duration_minutes")
        duration_value = duration if isinstance(duration, (int, float)) else -1
        return (timestamp, duration_value)

    def _validate_trade_history_row(self, row: Dict[str, Any]) -> tuple[bool, str]:
        mode = str(row.get("mode", "") or "").strip().lower()
        if mode not in {"paper", "live"}:
            return False, "missing_or_invalid_mode"

        required_string_fields = ["trade_id", "timestamp", "symbol", "index", "option_type", "outcome", "exit_reason"]
        for field in required_string_fields:
            if not str(row.get(field, "") or "").strip():
                return False, f"missing_{field}"

        option_type = str(row.get("option_type", "")).strip().upper()
        if option_type not in {"CE", "PE"}:
            return False, "invalid_option_type"

        numeric_fields = ["strike", "entry_price", "exit_price", "quantity", "pnl", "pnl_pct", "duration_minutes", "probability"]
        for field in numeric_fields:
            if not isinstance(row.get(field), (int, float)):
                return False, f"invalid_{field}"

        entry_price = float(row.get("entry_price", 0))
        exit_price = float(row.get("exit_price", 0))
        quantity = float(row.get("quantity", 0))
        strike = float(row.get("strike", 0))
        if entry_price <= 0 or exit_price <= 0 or quantity <= 0 or strike <= 0:
            return False, "non_positive_trade_values"

        max_plausible_exit = max(entry_price * 25, strike * 0.25, 5000.0)
        if exit_price > max_plausible_exit:
            return False, "implausible_exit_price"

        return True, ""

    def _rebuild_learning_insights(self, trade_rows: List[Dict[str, Any]]):
        insights: Dict[str, Any] = {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "total_pnl_paper": 0.0,
            "total_pnl_live": 0.0,
            "win_rate": 0.0,
            "win_patterns": [],
            "loss_patterns": [],
        }
        for row in trade_rows:
            try:
                trade = TradeLog(**row)
            except TypeError:
                continue
            self._apply_trade_to_insights(insights, trade)
        self._save_learning_insights(insights)

    def _apply_trade_to_insights(self, insights: Dict[str, Any], trade: "TradeLog"):
        """Update learning insights in-memory from a single completed trade."""
        trade_mode = getattr(trade, 'mode', 'paper')
        consensus_value = 0.0
        if trade.bot_signals:
            try:
                consensus_value = float(trade.bot_signals.get("consensus", 0) or 0)
            except (TypeError, ValueError):
                consensus_value = 0.0

        insights["total_trades"] = insights.get("total_trades", 0) + 1

        if trade.outcome == "WIN":
            insights["wins"] = insights.get("wins", 0) + 1
        elif trade.outcome == "LOSS":
            insights["losses"] = insights.get("losses", 0) + 1

        insights["total_pnl"] = insights.get("total_pnl", 0) + trade.pnl

        pnl_key = f"total_pnl_{trade_mode}"
        insights[pnl_key] = insights.get(pnl_key, 0) + trade.pnl

        total = insights.get("wins", 0) + insights.get("losses", 0)
        if total > 0:
            insights["win_rate"] = insights["wins"] / total * 100

        if trade.outcome == "LOSS":
            loss_patterns = insights.get("loss_patterns", [])
            pattern = {
                "timestamp": trade.timestamp,
                "pnl": trade.pnl,
                "exit_reason": trade.exit_reason,
                "probability": trade.probability,
                "was_counter_trend": trade.was_counter_trend,
                "duration_minutes": trade.duration_minutes,
            }
            loss_patterns.append(pattern)
            insights["loss_patterns"] = loss_patterns[-100:]

            recent_losses = loss_patterns[-20:]
            if len(recent_losses) >= 5:
                stop_loss_exits = sum(1 for p in recent_losses if p["exit_reason"] == "STOP_LOSS")
                if stop_loss_exits > len(recent_losses) * 0.7:
                    insights["learning_note"] = "Too many stop losses - consider wider stops or better entries"

        if trade.outcome == "WIN":
            win_patterns = insights.get("win_patterns", [])
            win_patterns.append({
                "probability": trade.probability,
                "pnl_pct": trade.pnl_pct,
                "duration_minutes": trade.duration_minutes,
                "consensus": consensus_value,
            })
            insights["win_patterns"] = win_patterns[-100:]

            if len(win_patterns) >= self.learning_adaptation_min_wins:
                avg_winning_prob = sum(p["probability"] for p in win_patterns) / len(win_patterns)
                insights["optimal_probability_threshold"] = int(avg_winning_prob)
                consensus_samples = [p.get("consensus", 0) for p in win_patterns if p.get("consensus", 0) > 0]
                if consensus_samples:
                    insights["optimal_consensus_threshold"] = round(
                        sum(consensus_samples) / len(consensus_samples),
                        1,
                    )

    def get_effective_thresholds(self) -> Dict[str, Any]:
        """Return the currently active entry thresholds, including trusted learning overrides."""
        insights = self._load_learning_insights()
        base_probability = int(self.risk.min_probability)
        base_consensus_pct = round(self.risk.min_consensus * 100, 1)
        effective_probability = base_probability
        effective_consensus_pct = base_consensus_pct
        adaptive_applied = False
        reasons: List[str] = ["base"]

        total_trades = int(insights.get("total_trades", 0) or 0)
        wins = int(insights.get("wins", 0) or 0)

        if total_trades >= self.learning_adaptation_min_trades and wins >= self.learning_adaptation_min_wins:
            learned_probability = insights.get("optimal_probability_threshold")
            if isinstance(learned_probability, (int, float)):
                effective_probability = int(round(max(45, min(85, float(learned_probability)))))
                adaptive_applied = adaptive_applied or effective_probability != base_probability
                reasons.append(f"learned_probability={effective_probability}")

            learned_consensus = insights.get("optimal_consensus_threshold")
            if isinstance(learned_consensus, (int, float)):
                effective_consensus_pct = round(max(25.0, min(90.0, float(learned_consensus))), 1)
                adaptive_applied = adaptive_applied or effective_consensus_pct != base_consensus_pct
                reasons.append(f"learned_consensus={effective_consensus_pct}")
        else:
            reasons.append(
                f"waiting_for_{self.learning_adaptation_min_trades}_trusted_trades"
            )

        learning_note = str(insights.get("learning_note", "") or "")
        if "Too many stop losses" in learning_note:
            effective_probability = max(effective_probability, min(85, base_probability + 3))
            effective_consensus_pct = max(effective_consensus_pct, min(90.0, base_consensus_pct + 5))
            adaptive_applied = True
            reasons.append("stop_loss_guard")

        return {
            "min_probability": effective_probability,
            "min_consensus_pct": effective_consensus_pct,
            "base_probability": base_probability,
            "base_consensus_pct": base_consensus_pct,
            "adaptive_applied": adaptive_applied,
            "trusted_trades": total_trades,
            "trusted_wins": wins,
            "reason": ", ".join(reasons),
        }

    def _set_market_data_status(
        self,
        *,
        healthy: bool,
        available: bool,
        message: str,
        source: Optional[str] = None,
        file_name: Optional[str] = None,
        updated_at: Optional[str] = None,
        age_seconds: Optional[float] = None,
    ):
        self.market_data_status = {
            "healthy": healthy,
            "available": available,
            "message": message,
            "source": source,
            "file": file_name,
            "updated_at": updated_at,
            "age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        }

    def _get_latest_screener_file(self) -> Optional[Path]:
        if not self.screener_dir.exists():
            return None

        screener_files = sorted(
            self.screener_dir.glob("screener_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return screener_files[0] if screener_files else None

    def _read_screener_payload(self, file_path: Path) -> Dict[str, Any]:
        with open(file_path) as f:
            return json.load(f)

    def _write_screener_snapshot(self, payload: Dict[str, Any]) -> Path:
        self.screener_dir.mkdir(parents=True, exist_ok=True)
        out_file = self.screener_dir / f"screener_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_file

    def _refresh_screener_snapshot(self) -> Optional[tuple[Dict[str, Any], Path, datetime]]:
        """Run the screener directly when the cached snapshot is missing or stale."""
        self._last_screener_refresh_attempt = datetime.now()
        try:
            from .screener import run_screener

            payload = run_screener()
            if not payload.get("success"):
                error = payload.get("error") or payload.get("message") or "Unknown screener failure"
                self._set_market_data_status(
                    healthy=False,
                    available=False,
                    message=f"Screener refresh failed: {error}",
                    source="live_refresh",
                )
                logger.warning(f"AutoTrader screener refresh failed: {error}")
                return None

            snapshot_file = self._write_screener_snapshot(payload)
            refreshed_at = datetime.fromtimestamp(snapshot_file.stat().st_mtime)
            self._set_market_data_status(
                healthy=True,
                available=True,
                message="Using fresh screener snapshot",
                source="live_refresh",
                file_name=snapshot_file.name,
                updated_at=refreshed_at.isoformat(),
                age_seconds=0.0,
            )
            return payload, snapshot_file, refreshed_at
        except Exception as e:
            self._set_market_data_status(
                healthy=False,
                available=False,
                message=f"Screener refresh error: {e}",
                source="live_refresh",
            )
            logger.error(f"AutoTrader screener refresh error: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════════
    # RISK MANAGEMENT - THE MOST IMPORTANT PART
    # ═══════════════════════════════════════════════════════════════════════

    def check_can_trade(self) -> tuple[bool, str]:
        """Check if trading is allowed based on risk rules"""
        # Mode check
        if self.mode == TradingMode.DISABLED:
            return False, "Trading disabled"

        # Paused check
        if self.is_paused:
            return False, "Trading paused"

        # Market data freshness check
        if not self.market_data_status.get("healthy", False):
            return False, self.market_data_status.get("message", "Fresh market data not available")

        # Get mode-specific values
        current_pnl = self._get_current_daily_pnl()
        current_trades = self._get_current_daily_trades()

        # Daily loss limit
        if current_pnl <= -self.risk.max_daily_loss:
            self._on_daily_limit_hit("LOSS_LIMIT")
            return False, f"Daily loss limit hit ({current_pnl:.0f})"

        # Daily profit limit (optional - take profits)
        if current_pnl >= self.risk.max_daily_profit:
            return False, f"Daily profit target reached ({current_pnl:.0f})"

        # Max daily trades
        if current_trades >= self.risk.max_daily_trades:
            return False, f"Max daily trades reached ({current_trades})"

        # Max concurrent positions
        open_positions = len([p for p in self.positions.values() if p.status == "open"])
        if open_positions >= self.risk.max_concurrent_positions:
            return False, f"Max positions open ({open_positions})"

        # Time filters
        now = datetime.now()
        market_open = now.replace(hour=9, minute=15, second=0)
        market_close = now.replace(hour=15, minute=30, second=0)

        if now < market_open + timedelta(minutes=self.risk.no_trade_first_minutes):
            return False, "Too early - market opening volatility"

        if now > market_close - timedelta(minutes=self.risk.no_trade_last_minutes):
            return False, "Too late - market closing"

        return True, "OK"

    def calculate_position_size(self, entry_price: float, stop_loss: float) -> int:
        """Calculate position size based on risk"""
        # Risk per trade
        risk_amount = min(
            self.risk.max_loss_per_trade,
            self.risk.max_position_size * (self.risk.position_size_pct / 100)
        )

        # Stop distance
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            return 1

        # Quantity = Risk / Stop Distance
        quantity = int(risk_amount / stop_distance)

        # Ensure within limits
        max_qty_by_capital = int(self.risk.max_position_size / entry_price)
        quantity = min(quantity, max_qty_by_capital)

        return max(1, quantity)

    # ═══════════════════════════════════════════════════════════════════════
    # SIGNAL PROCESSING
    # ═══════════════════════════════════════════════════════════════════════

    def process_signal(self, index: str, market_data: Dict) -> Optional[Dict]:
        """Process signals and decide whether to trade"""
        # Check if can trade
        can_trade, reason = self.check_can_trade()
        if not can_trade:
            return {"action": "SKIP", "reason": reason}

        # Get ensemble decision first (to know the strike)
        decision = self.ensemble.analyze(index, market_data)
        if not decision:
            return {"action": "SKIP", "reason": "No signal from ensemble"}

        # Build expected symbol for this trade
        option_type = "CE" if "CE" in decision.action else "PE"
        expected_symbol = f"{index}_{decision.strike}{option_type}"

        # Check for existing open position with SAME STRIKE
        existing = [p for p in self.positions.values()
                    if p.symbol == expected_symbol and p.status == "open"]
        if existing:
            return {"action": "SKIP", "reason": f"Already have open position: {expected_symbol}"}

        # Also check for any open position in same index (max 1 per index)
        index_positions = [p for p in self.positions.values()
                          if p.index == index and p.status == "open"]
        if index_positions:
            return {"action": "SKIP", "reason": f"Already have position in {index}"}

        # Cooldown: Don't re-enter same strike within 2 minutes of last exit
        last_exit_time = self.recent_exit_times.get(expected_symbol)
        if last_exit_time:
            try:
                exit_time = datetime.fromisoformat(last_exit_time)
                if (datetime.now() - exit_time).total_seconds() < 120:  # 2 min cooldown
                    return {"action": "SKIP", "reason": f"Cooldown: Recently exited {expected_symbol}"}
            except (ValueError, TypeError):
                pass  # Ignore parsing errors

        # Check minimum requirements
        thresholds = self.get_effective_thresholds()
        min_probability = thresholds["min_probability"]
        min_consensus_pct = thresholds["min_consensus_pct"]
        threshold_mode = "adaptive" if thresholds["adaptive_applied"] else "base"

        if decision.confidence < min_probability:
            print(f"[AutoTrader] {index}: Low confidence ({decision.confidence:.0f}% < {min_probability}%)")
            return {
                "action": "SKIP",
                "reason": f"Low confidence ({decision.confidence}% < {min_probability}% {threshold_mode})"
            }

        if decision.consensus_level < min_consensus_pct:
            print(f"[AutoTrader] {index}: Low consensus ({decision.consensus_level:.0f}% < {min_consensus_pct:.0f}%)")
            return {
                "action": "SKIP",
                "reason": f"Low consensus ({decision.consensus_level}% < {min_consensus_pct}% {threshold_mode})"
            }

        # Valid signal - prepare trade
        print(f"[AutoTrader] {index}: ✅ SIGNAL READY - {decision.action} @ {decision.confidence:.0f}% conf")
        return {
            "action": "TRADE",
            "decision": decision,
            "index": index,
            "market_data": market_data,
        }

    # ═══════════════════════════════════════════════════════════════════════
    # TRADE EXECUTION
    # ═══════════════════════════════════════════════════════════════════════

    def execute_trade(self, signal: Dict) -> Optional[Position]:
        """Execute a trade based on signal"""
        print(f"[AutoTrader] execute_trade called with signal: {signal.get('index')}")
        decision = signal.get("decision")
        if not decision:
            print(f"[AutoTrader] execute_trade: No decision in signal")
            return None

        index = signal.get("index")
        market_data = signal.get("market_data", {})

        # Determine option type
        if decision.action == "BUY_CE":
            option_type = "CE"
        elif decision.action == "BUY_PE":
            option_type = "PE"
        else:
            return None

        # ── Duplicate guard (re-check at execution time, not just signal time) ──
        # Snapshot positions to avoid mutation during iteration in concurrent calls.
        if any(p.index == index and p.status == "open"
               for p in list(self.positions.values())):
            logger.warning(
                "[execute_trade] Duplicate blocked: open position already exists for %s — skipping",
                index,
            )
            return None

        # ═══════════════════════════════════════════════════════════════════════
        # CAPITAL & AFFORDABILITY CHECK (₹5,000 capital limit)
        # ═══════════════════════════════════════════════════════════════════════

        # For ₹5,000 capital, only NIFTY50 is affordable
        # BANKNIFTY: 30 lots × ~600 premium = ₹18,000 (too expensive)
        # NIFTY50: 50 lots × ~75 premium = ₹3,750 (affordable)
        ALLOWED_INDICES = ["NIFTY50"]  # Only trade NIFTY50 with ₹5,000 capital

        if self.mode == TradingMode.LIVE and index not in ALLOWED_INDICES:
            logger.warning(f"[LIVE] Skipping {index} - not affordable with ₹{self.risk.total_capital} capital")
            print(f"[AutoTrader] ❌ SKIPPED: {index} not affordable (only NIFTY50 allowed with ₹5,000)")
            return None

        # Estimate position value and check against available capital
        lot_size = get_lot_size(index)
        index_ltp = market_data.get("ltp", 0)

        # Estimate premium cost
        premium_pct = {
            "NIFTY50": 0.003,      # ~0.3% of index
            "BANKNIFTY": 0.01,     # ~1% of index
            "SENSEX": 0.003,
            "FINNIFTY": 0.004,
            "MIDCPNIFTY": 0.005,
        }.get(index, 0.004)
        estimated_premium = index_ltp * premium_pct
        estimated_cost = estimated_premium * lot_size

        # Add brokerage costs
        total_brokerage = (self.risk.brokerage_per_order * 2) + self.risk.other_charges  # Buy + Sell
        total_cost = estimated_cost + total_brokerage

        if self.mode == TradingMode.LIVE and total_cost > self.risk.max_position_size:
            logger.warning(f"[LIVE] Trade too expensive: ₹{total_cost:.0f} > max ₹{self.risk.max_position_size:.0f}")
            print(f"[AutoTrader] ❌ SKIPPED: Trade cost ₹{total_cost:.0f} exceeds limit ₹{self.risk.max_position_size:.0f}")
            return None

        # Check remaining capital after open positions (use mode-specific P&L)
        open_positions_value = sum(p.entry_price * p.quantity for p in self.positions.values() if p.status == "open")
        available_capital = self.risk.total_capital - open_positions_value - abs(self._get_current_daily_pnl())

        if self.mode == TradingMode.LIVE and total_cost > available_capital:
            logger.warning(f"[LIVE] Insufficient capital: need ₹{total_cost:.0f}, have ₹{available_capital:.0f}")
            print(f"[AutoTrader] ❌ SKIPPED: Insufficient capital (need ₹{total_cost:.0f}, available ₹{available_capital:.0f})")
            return None

        print(f"[AutoTrader] ✅ Capital check passed: ₹{total_cost:.0f} cost, ₹{available_capital:.0f} available")

        # Calculate levels - OPTION PREMIUM, not index price
        # ATM option premium is typically 0.3-0.5% of index for NIFTY, 0.8-1.2% for BANKNIFTY
        index_ltp = market_data.get("ltp", 0)

        # Step 1: Use decision.entry if it already looks like a valid option premium
        if decision.entry and decision.entry < index_ltp * 0.05:
            entry_price = decision.entry
        else:
            entry_price = None

            # Step 2: Fetch real option LTP from Fyers API
            strike = getattr(decision, 'strike', 0) or 0
            if strike and index_ltp > 0:
                if self._get_market_data_client():
                    fyers_sym = _build_fyers_option_symbol(index, strike, option_type)
                    if fyers_sym:
                        try:
                            ltp = self._market_data_client.get_quote_ltp(fyers_sym, ttl_seconds=5)
                            if ltp > 0:
                                entry_price = ltp
                                logger.info("Real entry price from Fyers: %s = %s", fyers_sym, ltp)
                        except Exception as e:
                            logger.debug("Fyers entry price fetch failed for %s: %s", fyers_sym, e)

            # Step 3: Heuristic fallback if API unavailable or returned no data
            if not entry_price:
                premium_pct = {
                    "NIFTY50": 0.003,      # ~0.3% of index (NIFTY 24700 → ~75)
                    "BANKNIFTY": 0.01,     # ~1% of index (BANKNIFTY 59600 → ~596)
                    "SENSEX": 0.003,       # ~0.3% of index
                    "FINNIFTY": 0.004,     # ~0.4% of index
                    "MIDCPNIFTY": 0.005,   # ~0.5% of index
                }.get(index, 0.004)
                entry_price = round(index_ltp * premium_pct, 2)

        stop_loss = decision.stop_loss or entry_price * (1 - self.risk.stop_loss_pct / 100)
        target = decision.target or entry_price * (1 + self.risk.target_pct / 100)

        # Calculate quantity - use lot sizes from shared config
        quantity = get_lot_size(index)  # Default 1 lot

        # Create position with unique ID (includes microseconds)
        position = Position(
            id=f"{index}_{option_type}_{datetime.now().strftime('%H%M%S%f')[:12]}",
            symbol=f"{index}_{decision.strike}{option_type}",
            index=index,
            option_type=option_type,
            strike=decision.strike or 0,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.now().isoformat(),
            stop_loss=stop_loss,
            target=target,
            status="open",
            bot_signals={
                "confidence": decision.confidence,
                "consensus": decision.consensus_level,
                "contributing_bots": decision.contributing_bots,
                "reasoning": decision.reasoning,
            },
            mode=self.mode.value,  # paper or live
            strategy_id=self.strategy_id,
        )

        # ═══════════════════════════════════════════════════════════════════════
        # EXECUTION QUALITY TRACKING - Record order submission
        # ═══════════════════════════════════════════════════════════════════════
        signal_time = datetime.now().isoformat()
        signal_price = index_ltp  # Price when signal was generated
        exec_metrics = None

        if self.execution_tracker:
            exec_metrics = self.execution_tracker.record_order_submit(
                trade_id=position.id,
                symbol=position.symbol,
                mode=self.mode.value,
                signal_price=signal_price,
                expected_price=entry_price,
                quantity=quantity,
                signal_time=signal_time,
                spread=market_data.get("spread"),
                bid=market_data.get("bid"),
                ask=market_data.get("ask")
            )
            self._pending_executions[position.id] = exec_metrics

        # Execute based on mode
        order_start_time = time.time()
        actual_fill_price = entry_price  # Default for paper trading

        if self.mode == TradingMode.PAPER:
            # Paper trade - simulate instant fill at expected price
            actual_fill_price = entry_price
            logger.info(f"[PAPER] Executed: {position.symbol} @ {entry_price}")
        elif self.mode == TradingMode.LIVE:
            # Real trade via FYERS
            if self.fyers:
                order_result = self._place_fyers_order(position)
                if not order_result.get("success"):
                    # Record rejection
                    if exec_metrics and self.execution_tracker:
                        api_time = int((time.time() - order_start_time) * 1000)
                        self.execution_tracker.record_order_rejection(
                            exec_metrics,
                            reason=order_result.get("error", "Unknown error"),
                            api_response_time_ms=api_time
                        )
                    logger.error(f"Order failed: {order_result}")
                    return None

                # Get actual fill price from response if available
                actual_fill_price = order_result.get("fill_price", entry_price)
            else:
                logger.error("FYERS client not configured for live trading")
                return None

        # Record execution quality - order filled
        api_response_time = int((time.time() - order_start_time) * 1000)
        if exec_metrics and self.execution_tracker:
            self.execution_tracker.record_order_fill(
                metrics=exec_metrics,
                actual_fill_price=actual_fill_price,
                filled_quantity=quantity,
                api_response_time_ms=api_response_time,
                spread_at_fill=market_data.get("spread")
            )

        # Update position with actual fill price if different
        if actual_fill_price != entry_price:
            position.entry_price = actual_fill_price
            logger.info(f"Fill price adjusted: expected {entry_price} -> actual {actual_fill_price}")

        # Record position
        self.positions[position.id] = position
        # Increment daily trades for current mode
        mode_key = self.mode.value
        self.daily_trades[mode_key] = self.daily_trades.get(mode_key, 0) + 1
        self.last_trade_time = datetime.now()
        self._save_state()

        # Record for ensemble learning
        self.ensemble.execute_trade(decision, market_data)

        # Callback
        if self.on_trade_executed:
            self.on_trade_executed(position)

        logger.info(f"Trade executed: {position.symbol} | Entry: {entry_price} | SL: {stop_loss} | Target: {target}")

        return position

    def _place_fyers_order(self, position: Position) -> Dict:
        """Place order via FYERS API for LIVE trading"""
        try:
            fyers_symbol = _build_fyers_option_symbol(
                position.index, position.strike, position.option_type
            )
            if not fyers_symbol:
                return {"success": False, "error": f"Unknown index: {position.index}"}

            # Fyers order payload
            order_data = {
                "symbol": fyers_symbol,
                "qty": position.quantity,
                "type": 2,           # 1=Limit, 2=Market
                "side": 1,           # 1=Buy, -1=Sell
                "productType": "INTRADAY",
                "limitPrice": 0,     # 0 for market orders
                "stopPrice": 0,
                "validity": "DAY",
                "disclosedQty": 0,
                "offlineOrder": False,
                "stopLoss": 0,
                "takeProfit": 0,
            }

            logger.info(f"[LIVE] Placing order: {fyers_symbol} x {position.quantity} @ MARKET")

            # Use Fyers client to place order
            if self.fyers and hasattr(self.fyers, 'place_order'):
                response = self.fyers.place_order(order_data)
                if response.get("success"):
                    order_id = response.get("data", {}).get("id", f"fyers_{position.id}")
                    logger.info(f"[LIVE] Order placed successfully: {order_id}")
                    return {"success": True, "order_id": order_id, "fyers_response": response}
                else:
                    error = response.get("error", "Unknown error")
                    logger.error(f"[LIVE] Order failed: {error}")
                    return {"success": False, "error": error, "fyers_response": response}
            else:
                # Fallback to creating FyersClient
                if FYERS_AVAILABLE:
                    client = FyersClient()
                    response = client.place_order(order_data)
                    if response.get("success"):
                        order_id = response.get("data", {}).get("id", f"fyers_{position.id}")
                        logger.info(f"[LIVE] Order placed successfully: {order_id}")
                        return {"success": True, "order_id": order_id, "fyers_response": response}
                    else:
                        error = response.get("error", "Unknown error")
                        logger.error(f"[LIVE] Order failed: {error}")
                        return {"success": False, "error": error, "fyers_response": response}
                else:
                    logger.error("[LIVE] FyersClient not available")
                    return {"success": False, "error": "FyersClient not imported"}

        except Exception as e:
            logger.error(f"[LIVE] Order exception: {e}")
            return {"success": False, "error": str(e)}

    def _place_fyers_sell_order(self, position: Position) -> Dict:
        """Place SELL order via FYERS API to close position"""
        try:
            fyers_symbol = _build_fyers_option_symbol(
                position.index, position.strike, position.option_type
            )
            if not fyers_symbol:
                return {"success": False, "error": f"Unknown index: {position.index}"}

            # Fyers SELL order payload
            order_data = {
                "symbol": fyers_symbol,
                "qty": position.quantity,
                "type": 2,           # Market order
                "side": -1,          # -1 = Sell
                "productType": "INTRADAY",
                "limitPrice": 0,
                "stopPrice": 0,
                "validity": "DAY",
                "disclosedQty": 0,
                "offlineOrder": False,
            }

            logger.info(f"[LIVE] Placing SELL order: {fyers_symbol} x {position.quantity}")

            if self.fyers and hasattr(self.fyers, 'place_order'):
                response = self.fyers.place_order(order_data)
            elif FYERS_AVAILABLE:
                client = FyersClient()
                response = client.place_order(order_data)
            else:
                return {"success": False, "error": "FyersClient not available"}

            if response.get("success"):
                order_id = response.get("data", {}).get("id", f"sell_{position.id}")
                logger.info(f"[LIVE] SELL order placed: {order_id}")
                return {"success": True, "order_id": order_id}
            else:
                error = response.get("error", "Unknown error")
                logger.error(f"[LIVE] SELL order failed: {error}")
                return {"success": False, "error": error}

        except Exception as e:
            logger.error(f"[LIVE] SELL order exception: {e}")
            return {"success": False, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════════════
    # POSITION MONITORING & EXIT
    # ═══════════════════════════════════════════════════════════════════════

    def monitor_positions(self, current_prices: Dict[str, float]):
        """Monitor open positions and exit if needed"""
        for pos_id, position in list(self.positions.items()):
            if position.status != "open":
                continue

            current_price = current_prices.get(position.symbol)
            if current_price is None:
                continue

            # Update live P&L for open positions
            position.pnl = round((current_price - position.entry_price) * position.quantity, 2)
            position.current_price = current_price

            exit_reason = None

            # Check stop loss and target - same for CE and PE when BUYING
            # Profit when price goes UP, loss when price goes DOWN
            if current_price <= position.stop_loss:
                exit_reason = "STOP_LOSS"
            elif current_price >= position.target:
                exit_reason = "TARGET"

            # Time-based exit (EOD)
            now = datetime.now()
            if now.hour >= 15 and now.minute >= 15:
                exit_reason = "EOD_SQUARE_OFF"

            if exit_reason:
                self.close_position(position, current_price, exit_reason)

    def close_position(self, position: Position, exit_price: float, exit_reason: str):
        """Close a position and learn from it"""
        # Place sell order for LIVE trading
        if self.mode == TradingMode.LIVE:
            sell_result = self._place_fyers_sell_order(position)
            if not sell_result.get("success"):
                logger.error(
                    "[LIVE] Sell order failed for %s (%s): %s — position kept open, will retry next cycle",
                    position.symbol, exit_reason, sell_result.get("error"),
                )
                position.exit_reason = f"PENDING_EXIT:{exit_reason}"
                return  # do NOT close locally — retry on next monitor cycle

        # Calculate P&L - same for CE and PE when BUYING options
        # Profit = (sell price - buy price) * quantity
        pnl = (exit_price - position.entry_price) * position.quantity

        # Deduct brokerage from P&L
        if self.mode == TradingMode.LIVE:
            brokerage = self.risk.brokerage_per_order + (exit_price * position.quantity * self.risk.stt_rate)
            pnl -= brokerage
            logger.info(f"[LIVE] P&L after brokerage: ₹{pnl:.2f} (brokerage: ₹{brokerage:.2f})")

        pnl_pct = ((exit_price - position.entry_price) / position.entry_price) * 100

        # Determine outcome
        if pnl > 0:
            outcome = "WIN"
        elif pnl < -10:  # Small threshold for breakeven
            outcome = "LOSS"
        else:
            outcome = "BREAKEVEN"

        # Update position
        position.status = "closed"
        position.exit_price = exit_price
        position.exit_time = datetime.now().isoformat()
        position.pnl = pnl
        position.exit_reason = exit_reason
        self.recent_exit_times[position.symbol] = position.exit_time

        # Update daily P&L for the position's mode (not current mode)
        pos_mode = getattr(position, 'mode', 'paper')  # Backwards compatible
        self.daily_pnl[pos_mode] = self.daily_pnl.get(pos_mode, 0.0) + pnl

        # Log trade
        entry_time = datetime.fromisoformat(position.entry_time)
        duration = int((datetime.now() - entry_time).total_seconds() / 60)

        trade_log = TradeLog(
            trade_id=position.id,
            timestamp=datetime.now().isoformat(),
            symbol=position.symbol,
            index=position.index,
            option_type=position.option_type,
            strike=position.strike,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            outcome=outcome,
            exit_reason=exit_reason,
            duration_minutes=duration,
            market_bias="",  # Would come from market data
            bot_signals=position.bot_signals or {},
            probability=position.bot_signals.get("confidence", 0) if position.bot_signals else 0,
            conviction="HIGH",
            index_change_pct=0,
            vix=0,
            pcr=0,
            was_counter_trend=False,
            was_gap_trade=False,
            was_overbought=False,
            was_oversold=False,
            mode=pos_mode,  # Track paper or live
            strategy_id=getattr(position, "strategy_id", self.strategy_id),
        )
        self._log_trade(trade_log)

        # Learn from trade
        self._learn_from_trade(trade_log)

        # Record trade outcome for execution quality tracking
        if self.execution_tracker:
            self.execution_tracker.record_trade_outcome(
                trade_id=position.id,
                pnl=pnl,
                pnl_pct=pnl_pct,
                outcome=outcome
            )
            # Clean up pending execution
            if position.id in self._pending_executions:
                del self._pending_executions[position.id]

        # Report to ensemble
        self.ensemble.close_trade(
            index=position.index,
            exit_price=exit_price,
            outcome=outcome,
            pnl=pnl,
            exit_reason=exit_reason
        )

        # Remove from active positions
        del self.positions[position.id]
        self._save_state()

        # Callback
        if self.on_position_closed:
            self.on_position_closed(position, trade_log)

        logger.info(
            f"Position closed: {position.symbol} | "
            f"P&L: {pnl:.0f} ({pnl_pct:.1f}%) | "
            f"Reason: {exit_reason} | "
            f"Outcome: {outcome}"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # SELF-LEARNING
    # ═══════════════════════════════════════════════════════════════════════

    def _learn_from_trade(self, trade: TradeLog):
        """Learn from every trade outcome"""
        insights = self._load_learning_insights()
        self._apply_trade_to_insights(insights, trade)
        self._save_learning_insights(insights)

    def _load_learning_insights(self) -> Dict:
        """Load learning insights"""
        if self.learning_file.exists():
            try:
                with open(self.learning_file) as f:
                    return json.load(f)
            except:
                pass
        return {}

    def _save_learning_insights(self, insights: Dict):
        """Save learning insights"""
        with open(self.learning_file, "w") as f:
            json.dump(insights, f, indent=2)

    # ═══════════════════════════════════════════════════════════════════════
    # CONTROLS
    # ═══════════════════════════════════════════════════════════════════════

    def start(self):
        """Start auto trading with background loop"""
        self.runtime_lock.acquire(
            extra_metadata={
                "mode": self.mode.value,
                "data_dir": str(self.data_dir),
            }
        )
        self.is_running = True
        self.is_paused = False

        # Start background trading loop
        if not hasattr(self, '_trading_thread') or not self._trading_thread.is_alive():
            self._trading_thread = threading.Thread(
                target=self._trading_loop,
                daemon=True,
                name="AutoTraderLoop"
            )
            self._trading_thread.start()
            logger.info(
                "AutoTrader background loop started in %s mode for strategy '%s'",
                self.mode.value,
                self.strategy_id,
            )
        else:
            logger.info(
                "AutoTrader resumed in %s mode for strategy '%s'",
                self.mode.value,
                self.strategy_id,
            )

    def stop(self):
        """Stop auto trading"""
        self.is_running = False
        trading_thread = getattr(self, "_trading_thread", None)
        if trading_thread and trading_thread.is_alive() and threading.current_thread() is not trading_thread:
            trading_thread.join(timeout=2)
        self.runtime_lock.release()
        logger.info("AutoTrader stopped for strategy '%s'", self.strategy_id)

    # ═══════════════════════════════════════════════════════════════════════
    # BACKGROUND TRADING LOOP - Autonomous Execution
    # ═══════════════════════════════════════════════════════════════════════

    def _trading_loop(self):
        """
        Background loop that automatically:
        1. Fetches market data from screener
        2. Analyzes with ensemble
        3. Executes trades when signals are strong
        4. Monitors positions for SL/Target exits

        Runs every 10 seconds while is_running=True
        """
        loop_interval = 10  # seconds
        print(f"[AutoTrader] Trading loop started in {self.mode.value} mode")
        logger.info("Trading loop started")

        loop_count = 0
        last_reset_date = None  # Track last reset date for auto-daily-reset

        while self.is_running:
            try:
                loop_count += 1

                # ═══════════════════════════════════════════════════════════════
                # AUTO-DAILY RESET: Trigger optimization at market open (9:15 AM)
                # ═══════════════════════════════════════════════════════════════
                now = datetime.now()
                today = now.date()
                market_open_time = now.replace(hour=9, minute=15, second=0, microsecond=0)

                # Check if it's a new trading day and we're past market open
                if last_reset_date != today and now >= market_open_time:
                    print(f"[AutoTrader] New trading day detected - triggering daily reset & optimization")
                    self.reset_daily()
                    last_reset_date = today

                if not self.is_paused:
                    # 1. Fetch latest screener data
                    market_data = self._fetch_screener_data()

                    if market_data:
                        indices = list(market_data.keys())
                        total_stocks = sum(len(d.get("stocks", [])) for d in market_data.values())
                        print(f"[AutoTrader] Loop {loop_count}: Processing {total_stocks} stocks across {indices}")

                        # 2. Process each index for new signals
                        for index, data in market_data.items():
                            if not self.is_running or self.is_paused:
                                break

                            signal = self.process_signal(index, data)

                            # 3. Execute trade if valid signal
                            if signal:
                                action = signal.get("action")
                                if action == "TRADE":
                                    position = self.execute_trade(signal)
                                    if position:
                                        print(f"[AutoTrader] ✅ EXECUTED: {position.symbol} @ {position.entry_price}")
                                        logger.info(f"[AUTO] Executed: {position.symbol}")
                                elif action == "SKIP" and loop_count % 6 == 0:  # Log skips every minute
                                    reason = signal.get("reason", "unknown")
                                    print(f"[AutoTrader] Skip {index}: {reason}")

                        # 4. Monitor existing positions
                        if self.positions:
                            current_prices = self._get_current_prices(market_data)
                            self.monitor_positions(current_prices)
                            print(f"[AutoTrader] Monitoring {len(self.positions)} positions")
                    else:
                        if loop_count % 6 == 0:  # Log every minute
                            print(f"[AutoTrader] Loop {loop_count}: No market data available")

            except Exception as e:
                print(f"[AutoTrader] ERROR: {e}")
                logger.error(f"Trading loop error: {e}")

            # Sleep before next iteration
            time.sleep(loop_interval)

        print("[AutoTrader] Trading loop stopped")
        logger.info("Trading loop stopped")

    def _fetch_screener_data(self) -> Optional[Dict[str, Dict]]:
        """
        Fetch latest market data from FYERS screener output.
        Returns dict of index -> market_data for NIFTY50, BANKNIFTY, SENSEX
        """
        try:
            latest_file = self._get_latest_screener_file()
            now = datetime.now()
            payload: Optional[Dict[str, Any]] = None

            if latest_file is not None:
                updated_at = datetime.fromtimestamp(latest_file.stat().st_mtime)
                age_seconds = (now - updated_at).total_seconds()
                if age_seconds <= self.screener_max_age_seconds:
                    payload = self._read_screener_payload(latest_file)
                    self._set_market_data_status(
                        healthy=True,
                        available=True,
                        message="Using fresh screener snapshot",
                        source="cache",
                        file_name=latest_file.name,
                        updated_at=updated_at.isoformat(),
                        age_seconds=age_seconds,
                    )
                else:
                    logger.warning(
                        "Latest screener snapshot is stale: %s (%ss old)",
                        latest_file.name,
                        int(age_seconds),
                    )

            if payload is None:
                cooldown_elapsed = (
                    self._last_screener_refresh_attempt is None or
                    (now - self._last_screener_refresh_attempt).total_seconds() >=
                    self.screener_refresh_cooldown_seconds
                )
                if cooldown_elapsed:
                    refreshed = self._refresh_screener_snapshot()
                    if refreshed is not None:
                        payload, _, _ = refreshed

            if payload is None:
                if latest_file is None:
                    self._set_market_data_status(
                        healthy=False,
                        available=False,
                        message="No screener snapshot available",
                        source="cache",
                    )
                else:
                    updated_at = datetime.fromtimestamp(latest_file.stat().st_mtime)
                    age_seconds = (now - updated_at).total_seconds()
                    self._set_market_data_status(
                        healthy=False,
                        available=True,
                        message=f"Screener data stale ({int(age_seconds)}s old)",
                        source="cache",
                        file_name=latest_file.name,
                        updated_at=updated_at.isoformat(),
                        age_seconds=age_seconds,
                    )
                return None

            market_data = self._build_market_data_from_payload(payload)
            return market_data if market_data else None

        except Exception as e:
            self._set_market_data_status(
                healthy=False,
                available=False,
                message=f"Error fetching screener data: {e}",
                source="auto_trader",
            )
            logger.error(f"Error fetching screener data: {e}")
            return None

    def _build_market_data_from_payload(self, data: Dict[str, Any]) -> Optional[Dict[str, Dict]]:
        """Transform screener JSON payload into bot-consumable index market data."""
        market_bias = data.get("market_bias", "NEUTRAL")

        # Use index_recommendations for direct index data (NIFTY50, BANKNIFTY, SENSEX)
        index_recs = data.get("index_recommendations", [])
        market_data = {}

        for rec in index_recs:
            index = rec.get("index", "")
            if not index:
                continue

            market_data[index] = {
                "ltp": rec.get("ltp", 0) or 0,
                "change_pct": rec.get("change_pct", 0) or 0,
                "signal": rec.get("signal", "NEUTRAL"),
                "option_side": rec.get("option_side", ""),
                "atm_strike": rec.get("atm_strike", 0),
                "preferred_strike": rec.get("preferred_strike", 0),
                "strike_step": rec.get("strike_step", 50),
                "confidence": rec.get("confidence", 0) or 0,
                "reason": rec.get("reason", ""),
                "market_bias": market_bias,
                "candidate_strikes": rec.get("candidate_strikes", []),
            }

        index_symbols = data.get("index_symbols", {})
        for index, symbols in index_symbols.items():
            if index in market_data:
                market_data[index]["stocks"] = []
                for stock in data.get("results", []):
                    if stock.get("symbol") in symbols:
                        market_data[index]["stocks"].append({
                            "symbol": stock.get("symbol"),
                            "ltp": stock.get("last_price") or 0,
                            "change_pct": stock.get("change_pct") or 0,
                            "signal": stock.get("signal", ""),
                            "probability": stock.get("probability") or 50,
                        })

        if market_data:
            indices = list(market_data.keys())
            print(f"[AutoTrader] Loaded index data: {indices}, bias={market_bias}")
            for idx in indices:
                conf = market_data[idx].get("confidence", 0)
                side = market_data[idx].get("option_side", "?")
                chg = market_data[idx].get("change_pct", 0)
                print(f"[AutoTrader]   {idx}: {side} signal, conf={conf}%, change={chg:.2f}%")

        market_data = self._enhance_market_data(market_data)
        return market_data if market_data else None

    def _enhance_market_data(self, market_data: Dict) -> Dict:
        """
        Enhance market data with derived values for bot analysis.
        Adds: prev_change_pct, momentum, estimated OI/PCR, IV estimates
        """
        for index, data in market_data.items():
            ltp = data.get("ltp", 0)
            change_pct = data.get("change_pct", 0)

            # Update price history
            if index not in self.price_history:
                self.price_history[index] = []

            self.price_history[index].append({
                "ltp": ltp,
                "change_pct": change_pct,
                "timestamp": datetime.now().isoformat()
            })

            # Keep last 100 entries
            if len(self.price_history[index]) > 100:
                self.price_history[index] = self.price_history[index][-100:]

            # Calculate prev_change_pct from history
            history = self.price_history[index]
            if len(history) >= 2:
                data["prev_change_pct"] = history[-2]["change_pct"]
            else:
                data["prev_change_pct"] = change_pct * 0.95  # Small estimate

            # Calculate momentum
            data["momentum"] = change_pct - data["prev_change_pct"]

            # Estimate high/low from change if not available
            # Use realistic intraday range (typically 0.8-1.5% for indices)
            # Base range + directional bias based on change
            base_range_pct = 0.8  # Minimum intraday range
            extra_range = abs(change_pct) * 0.5  # Add extra based on movement
            total_range_pct = base_range_pct + extra_range

            if "high" not in data or data.get("high") is None:
                if change_pct >= 0:
                    # Bullish day: high is further from current price
                    data["high"] = ltp * (1 + total_range_pct / 100 * 0.6)
                    data["low"] = ltp * (1 - total_range_pct / 100 * 0.4)
                else:
                    # Bearish day: low is further from current price
                    data["high"] = ltp * (1 + total_range_pct / 100 * 0.4)
                    data["low"] = ltp * (1 - total_range_pct / 100 * 0.6)
            if "low" not in data or data.get("low") is None:
                data["low"] = ltp * (1 - total_range_pct / 100 * 0.5)

            # Estimate PCR from signal (heuristic)
            signal = data.get("signal", "NEUTRAL")
            if signal == "BULLISH" or data.get("option_side") == "CE":
                data["pcr"] = 1.1 + (data.get("confidence", 50) / 100 * 0.4)  # 1.1 to 1.5
            elif signal == "BEARISH" or data.get("option_side") == "PE":
                data["pcr"] = 0.9 - (data.get("confidence", 50) / 100 * 0.4)  # 0.5 to 0.9
            else:
                data["pcr"] = 1.0

            # Estimate OI from signal direction (heuristic for OIAnalyst)
            conf = data.get("confidence", 50)
            if change_pct > 0:  # Price up
                data["ce_oi"] = 100000 * (1 + conf / 100)
                data["pe_oi"] = 100000 * (1 - conf / 200)
                data["ce_oi_change"] = 5 if conf > 60 else -5
                data["pe_oi_change"] = -5 if conf > 60 else 5
            else:  # Price down
                data["ce_oi"] = 100000 * (1 - conf / 200)
                data["pe_oi"] = 100000 * (1 + conf / 100)
                data["ce_oi_change"] = -5 if conf > 60 else 5
                data["pe_oi_change"] = 5 if conf > 60 else -5

            # Estimate IV percentile from volatility (heuristic)
            range_pct = abs(change_pct)
            if range_pct > 1.5:
                data["iv_percentile"] = 80 + min(20, range_pct * 5)
            elif range_pct > 0.8:
                data["iv_percentile"] = 50 + range_pct * 20
            else:
                data["iv_percentile"] = 30 + range_pct * 25

            # Estimate VIX from market movement
            data["vix"] = 12 + abs(change_pct) * 5  # 12-17 for normal, higher for volatile

            # Set volume estimates
            data["volume"] = 1000000
            data["avg_volume"] = 1000000

        return market_data

    def _get_current_prices(self, market_data: Dict) -> Dict[str, float]:
        """Get current prices for open positions from market data"""
        prices = {}

        # Lazily initialise market data client for live option quote fetching
        self._get_market_data_client()

        for pos in self.positions.values():
            if pos.status != "open":
                continue

            # Try to find price in market data
            index_data = market_data.get(pos.index, {})

            # Step 1: try to get option price from stocks list
            for stock in index_data.get("stocks", []):
                if stock.get("symbol") == pos.symbol:
                    prices[pos.symbol] = stock.get("ltp", pos.entry_price)
                    break

            # Step 2: fetch real option LTP via Fyers API
            if pos.symbol not in prices and self._market_data_client and pos.strike:
                fyers_sym = _build_fyers_option_symbol(pos.index, pos.strike, pos.option_type)
                if fyers_sym:
                    try:
                        ltp = self._market_data_client.get_quote_ltp(fyers_sym, ttl_seconds=5)
                        if ltp > 0:
                            prices[pos.symbol] = ltp
                            logger.debug("Option LTP from Fyers: %s = %s", fyers_sym, ltp)
                    except Exception as e:
                        logger.debug("Fyers quote fetch failed for %s: %s", fyers_sym, e)

            # Step 3: final fallback for paper trading — simulate via index change
            if pos.symbol not in prices and self.mode == TradingMode.PAPER and index_data:
                index_change_pct = index_data.get("change_pct", 0)
                # Options typically move 2-3x the underlying for ATM
                # CE profits when index goes up, PE profits when index goes down
                if pos.option_type == "CE":
                    option_change = index_change_pct * 2.5  # Leverage factor
                else:  # PE
                    option_change = -index_change_pct * 2.5  # Inverse for PE

                prices[pos.symbol] = pos.entry_price * (1 + option_change / 100)

        return prices

    def feed_market_data(self, index: str, market_data: Dict) -> Dict:
        """
        Manual market data feed - called by external systems.
        Use this when you want to feed data directly instead of polling screener.

        Returns the signal/action result.
        """
        if not self.is_running or self.is_paused:
            return {"action": "SKIP", "reason": "Auto-trader not running"}

        signal = self.process_signal(index, market_data)

        if signal and signal.get("action") == "TRADE":
            position = self.execute_trade(signal)
            if position:
                return {
                    "action": "EXECUTED",
                    "position_id": position.id,
                    "symbol": position.symbol,
                    "entry_price": position.entry_price,
                    "stop_loss": position.stop_loss,
                    "target": position.target,
                }

        return signal or {"action": "SKIP", "reason": "No signal"}

    def pause(self):
        """Pause trading (keep monitoring)"""
        self.is_paused = True
        logger.info("AutoTrader paused")

    def resume(self):
        """Resume trading"""
        self.is_paused = False
        logger.info("AutoTrader resumed")

    def emergency_stop(self):
        """Emergency stop - close all positions"""
        logger.warning("EMERGENCY STOP triggered!")
        self.is_running = False
        self.is_paused = True
        self.runtime_lock.release()

        # Close all open positions at market
        for position in list(self.positions.values()):
            if position.status == "open":
                # Would need current price - use entry as fallback
                self.close_position(position, position.entry_price, "EMERGENCY_STOP")

    def _on_daily_limit_hit(self, limit_type: str):
        """Handle daily limit being hit"""
        logger.warning(f"Daily limit hit: {limit_type}")
        self.is_paused = True

        if self.on_daily_limit_hit:
            self.on_daily_limit_hit(limit_type)

    def reset_daily(self):
        """Reset daily counters and trigger learning optimization (call at market open)"""
        # Reset both paper and live counters
        self.daily_pnl = {"paper": 0.0, "live": 0.0}
        self.daily_trades = {"paper": 0, "live": 0}
        self.is_paused = False
        self._save_state()

        # Also reset ensemble (triggers parameter optimization)
        if self.ensemble:
            self.ensemble.reset_daily()

        logger.info("Daily counters reset - parameter optimization triggered")

    # ═══════════════════════════════════════════════════════════════════════
    # STATUS & REPORTING
    # ═══════════════════════════════════════════════════════════════════════

    def get_status(self) -> Dict:
        """Get current status"""
        insights = self._load_learning_insights()

        # Get mode-specific values for display
        current_mode = self.mode.value

        return {
            "strategy_id": self.strategy_id,
            "data_dir": str(self.data_dir),
            "runtime_dir": str(self.runtime_dir),
            "mode": current_mode,
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            # Current mode's P&L (for backwards compatibility)
            "daily_pnl": self._get_current_daily_pnl(),
            "daily_trades": self._get_current_daily_trades(),
            # All P&L values for both modes
            "daily_pnl_all": self.daily_pnl,
            "daily_trades_all": self.daily_trades,
            "open_positions": len([p for p in self.positions.values() if p.status == "open"]),
            "positions": [asdict(p) for p in self.positions.values()],
            "risk_config": asdict(self.risk),
            "market_data_status": dict(self.market_data_status),
            "trade_history_status": dict(self.trade_history_status),
            "execution_quality_status": dict(self.execution_quality_status),
            "effective_thresholds": self.get_effective_thresholds(),
            "last_trade_time": self.last_trade_time.isoformat() if self.last_trade_time else None,
            "learning": {
                "total_trades": insights.get("total_trades", 0),
                "win_rate": round(insights.get("win_rate", 0), 1),
                "total_pnl": round(insights.get("total_pnl", 0), 0),
                # Separate total P&L by mode
                "total_pnl_paper": round(insights.get("total_pnl_paper", insights.get("total_pnl", 0)), 0),
                "total_pnl_live": round(insights.get("total_pnl_live", 0), 0),
                "optimal_probability": insights.get("optimal_probability_threshold", 70),
                "optimal_consensus": insights.get("optimal_consensus_threshold", round(self.risk.min_consensus * 100, 1)),
                "learning_note": insights.get("learning_note", ""),
            },
            "can_trade": self.check_can_trade(),
        }

    def get_performance_summary(self) -> Dict:
        """Get performance summary"""
        insights = self._load_learning_insights()

        wins = insights.get("wins", 0)
        losses = insights.get("losses", 0)
        total = wins + losses

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(insights.get("total_pnl", 0), 0),
            "avg_win": round(insights.get("avg_win", 0), 0),
            "avg_loss": round(insights.get("avg_loss", 0), 0),
            "best_trade": round(insights.get("best_trade", 0), 0),
            "worst_trade": round(insights.get("worst_trade", 0), 0),
            "current_streak": insights.get("current_streak", 0),
            "learning_insights": insights.get("learning_note", ""),
        }

    def get_recent_trades(self, limit: int = 100, mode: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return recently closed trades from the canonical auto-trader trade log."""
        if limit <= 0 or not self.trades_log_file.exists():
            return []

        normalized_mode = str(mode or "").strip().lower() or None
        trades: List[Dict[str, Any]] = []

        try:
            with open(self.trades_log_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        trade = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    trade_mode = str(trade.get("mode", "") or "").strip().lower()
                    if normalized_mode and trade_mode != normalized_mode:
                        continue
                    trades.append(trade)
        except OSError:
            return []

        trades.reverse()
        return trades[:limit]

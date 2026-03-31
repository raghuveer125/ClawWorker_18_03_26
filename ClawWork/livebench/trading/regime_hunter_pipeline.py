"""
Regime Hunter Independent Pipeline

Runs the RegimeHunterBot INDEPENDENTLY of the ensemble consensus.
Own risk limits, own capital allocation, own position tracking.

Philosophy:
  RegimeHunter detects regime *transitions* — the exact moment other bots
  are still seeing the old regime. Consensus would dilute or reject these
  contrarian signals. This pipeline lets RegimeHunter fire on its own
  conviction (≥85% confidence) without waiting for committee approval.

Architecture:
  ┌──────────────────┐     ┌──────────────────┐
  │  Ensemble Pipeline│     │ RegimeHunter     │
  │  (7 bots, vote)   │     │ Pipeline (solo)  │
  │  AutoTrader class │     │ This module      │
  └────────┬──────────┘     └────────┬─────────┘
           │                         │
           ▼                         ▼
  ┌────────────────────────────────────────────┐
  │        Shared FyersClient / Paper Engine    │
  └────────────────────────────────────────────┘

This pipeline is instantiated separately from the AutoTrader and has:
- Its own RegimeHunterBot instance (not shared with ensemble)
- Its own risk limits and daily counters
- Its own position tracking (data/regime_hunter/)
- Its own API endpoints (/api/regime-hunter-pipeline/*)
"""

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Callable, Dict, List, Optional

from bots.base import SharedMemory, SignalType, OptionType
from bots.regime_hunter import RegimeHunterBot
from trading.auto_trader import _build_fyers_option_symbol

# Import lot sizes; fallback to core/market.py
from core.market import INDEX_LOT_SIZES as _FALLBACK_LOTS

try:
    import sys
    _PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))
    from shared_project_engine.indices import INDEX_CONFIG
    _INDEX_LOT_SIZES = {name: cfg["lot_size"] for name, cfg in INDEX_CONFIG.items()}
except ImportError:
    _INDEX_LOT_SIZES = _FALLBACK_LOTS

try:
    from .fyers_client import FyersClient
    FYERS_AVAILABLE = True
except ImportError:
    FYERS_AVAILABLE = False

logger = logging.getLogger(__name__)


def _lot_size(index: str) -> int:
    return _INDEX_LOT_SIZES.get(index, 50)


# ─────────────────────────────────────────────────────────────────────
# Risk config — tighter than ensemble (solo signal, higher bar)
# ─────────────────────────────────────────────────────────────────────

@dataclass
class RegimeHunterRiskConfig:
    """Risk config specific to the solo regime-hunter pipeline."""
    total_capital: float = 5000.0

    # Daily limits
    max_daily_loss: float = 400.0       # 8% of capital
    max_daily_profit: float = 800.0     # 16% of capital — take profit
    max_daily_trades: int = 4           # Regime trades are rare — 4 max

    # Position limits
    max_position_size: float = 4000.0
    max_concurrent_positions: int = 1   # One position at a time

    # Entry bar (higher than ensemble — no consensus backup)
    min_confidence: float = 85.0        # Only fire on high-conviction transitions
    allowed_regimes: tuple = (
        "LIQUIDITY_SWEEP_REVERSAL",
        "BREAKOUT_INITIATION",
    )

    # SL / target
    stop_loss_pct: float = 20.0
    target_pct: float = 30.0
    max_loss_per_trade: float = 250.0

    # Time filters
    no_trade_first_minutes: int = 15
    no_trade_last_minutes: int = 30

    # Brokerage (Fyers)
    brokerage_per_order: float = 20.0
    other_charges: float = 15.0


# ─────────────────────────────────────────────────────────────────────
# Position / trade record (mirrors auto_trader.Position)
# ─────────────────────────────────────────────────────────────────────

@dataclass
class RHPosition:
    id: str
    symbol: str
    index: str
    option_type: str
    strike: int
    entry_price: float
    quantity: int
    entry_time: str
    stop_loss: float
    target: float
    status: str = "open"
    current_price: Optional[float] = None
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    pnl: float = 0.0
    exit_reason: Optional[str] = None
    regime: str = ""
    confidence: float = 0.0
    mode: str = "paper"


# ─────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────

class RegimeHunterPipeline:
    """
    Independent trading pipeline for RegimeHunterBot.

    Usage::

        pipeline = RegimeHunterPipeline()
        pipeline.start()
        result = pipeline.process(index="SENSEX", market_data={...})
    """

    def __init__(
        self,
        fyers_client=None,
        risk_config: Optional[RegimeHunterRiskConfig] = None,
        mode: str = "paper",
        data_dir: str = "data/regime_hunter",
    ):
        self.risk = risk_config or RegimeHunterRiskConfig()
        self.mode = mode   # "paper" | "live"
        self.fyers = fyers_client
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Own bot instance — not shared with ensemble
        self._memory = SharedMemory(data_dir=str(self.data_dir / "memory"))
        self.bot = RegimeHunterBot(self._memory)

        # State
        self.positions: Dict[str, RHPosition] = {}
        self.daily_pnl: float = 0.0
        self.daily_trades: int = 0
        self.is_running: bool = False
        self.is_paused: bool = False

        # Persistence
        self._state_file = self.data_dir / "state.json"
        self._trades_log = self.data_dir / "trades.jsonl"
        self._load_state()

        # Callbacks
        self.on_trade: Optional[Callable] = None
        self.on_exit: Optional[Callable] = None

        logger.info(
            f"[RH-Pipeline] Initialized in {self.mode} mode | "
            f"min_confidence={self.risk.min_confidence} | "
            f"regimes={self.risk.allowed_regimes}"
        )

    # ── lifecycle ──

    def start(self):
        self.is_running = True
        self.is_paused = False
        logger.info("[RH-Pipeline] Started")

    def stop(self):
        self.is_running = False
        logger.info("[RH-Pipeline] Stopped")

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self._save_state()
        logger.info("[RH-Pipeline] Daily counters reset")

    # ── core signal path ──

    def process(self, index: str, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Feed market data → RegimeHunterBot.analyze → risk check → execute.

        Returns a result dict with ``action`` in
        {TRADE, SKIP, MONITOR, ERROR}.
        """
        # Gate checks
        can, reason = self._can_trade()
        if not can:
            return {"action": "SKIP", "reason": reason}

        # Run bot analysis (entirely independent)
        signal = self.bot.analyze(index, market_data, option_chain=None)
        if signal is None:
            return {
                "action": "MONITOR",
                "regime": self.bot._current_regime,
                "reason": "No actionable regime transition",
            }

        # Confidence gate
        if signal.confidence < self.risk.min_confidence:
            return {
                "action": "SKIP",
                "regime": signal.factors.get("regime"),
                "confidence": signal.confidence,
                "reason": f"Confidence {signal.confidence:.0f}% < {self.risk.min_confidence}%",
            }

        # Regime gate
        regime = signal.factors.get("regime", "")
        if regime not in self.risk.allowed_regimes:
            return {
                "action": "SKIP",
                "regime": regime,
                "reason": f"Regime '{regime}' not in allowed list",
            }

        # Duplicate / cooldown
        option_type = "CE" if signal.option_type == OptionType.CE else "PE"
        expected_sym = f"{index}_{signal.strike}{option_type}"
        if any(
            p.symbol == expected_sym and p.status == "open"
            for p in self.positions.values()
        ):
            return {"action": "SKIP", "reason": f"Already open: {expected_sym}"}

        if any(p.index == index and p.status == "open" for p in self.positions.values()):
            return {"action": "SKIP", "reason": f"Already have position in {index}"}

        # 2-min cooldown after last exit in same symbol
        recent_exits = [
            p for p in self.positions.values()
            if p.symbol == expected_sym and p.status == "closed" and p.exit_time
        ]
        if recent_exits:
            last = max(recent_exits, key=lambda p: p.exit_time)
            try:
                if (datetime.now() - datetime.fromisoformat(last.exit_time)).total_seconds() < 120:
                    return {"action": "SKIP", "reason": "Cooldown after recent exit"}
            except (ValueError, TypeError):
                pass

        # ── Execute ──
        return self._execute(signal, index, market_data)

    # ── execution ──

    def _execute(self, signal, index: str, market_data: Dict) -> Dict:
        option_type = "CE" if signal.option_type == OptionType.CE else "PE"
        ltp = market_data.get("ltp", 0)

        # Entry price — prefer bot-computed or estimate
        entry = signal.entry
        if not entry or entry > ltp * 0.05:
            pct = {"NIFTY50": 0.003, "BANKNIFTY": 0.01, "SENSEX": 0.003}.get(index, 0.004)
            entry = round(ltp * pct, 2)

        sl = signal.stop_loss or round(entry * (1 - self.risk.stop_loss_pct / 100), 2)
        target = signal.target or round(entry * (1 + self.risk.target_pct / 100), 2)
        qty = _lot_size(index)

        pos = RHPosition(
            id=f"RH_{index}_{datetime.now().strftime('%H%M%S%f')[:12]}",
            symbol=f"{index}_{signal.strike}{option_type}",
            index=index,
            option_type=option_type,
            strike=signal.strike or 0,
            entry_price=entry,
            quantity=qty,
            entry_time=datetime.now().isoformat(),
            stop_loss=sl,
            target=target,
            regime=signal.factors.get("regime", ""),
            confidence=signal.confidence,
            mode=self.mode,
        )

        # Paper or live execution
        if self.mode == "live" and self.fyers:
            ok = self._place_live_order(pos)
            if not ok:
                return {"action": "ERROR", "reason": "Live order placement failed"}
        else:
            logger.info(f"[RH-Pipeline][PAPER] {pos.symbol} @ {entry}")

        self.positions[pos.id] = pos
        self.daily_trades += 1
        self._save_state()

        if self.on_trade:
            self.on_trade(pos)

        logger.info(
            f"[RH-Pipeline] TRADE: {pos.symbol} | regime={pos.regime} "
            f"| conf={pos.confidence:.0f}% | entry={entry} | SL={sl} | T={target}"
        )

        return {
            "action": "TRADE",
            "position_id": pos.id,
            "symbol": pos.symbol,
            "regime": pos.regime,
            "confidence": pos.confidence,
            "entry": entry,
            "stop_loss": sl,
            "target": target,
        }

    # ── position management ──

    def update_prices(self, prices: Dict[str, float]):
        """
        Update current prices for open positions and check SL/target.

        ``prices`` maps symbol → current premium price.
        """
        for pos in list(self.positions.values()):
            if pos.status != "open":
                continue
            price = prices.get(pos.symbol)
            if price is None:
                continue
            pos.current_price = price

            # Check target
            if price >= pos.target:
                self._close_position(pos, price, "TARGET_HIT")
            # Check stop loss
            elif price <= pos.stop_loss:
                self._close_position(pos, price, "STOP_LOSS")

    def close_position(self, position_id: str, price: float, reason: str = "MANUAL"):
        """Manually close a position."""
        pos = self.positions.get(position_id)
        if pos and pos.status == "open":
            self._close_position(pos, price, reason)

    def _close_position(self, pos: RHPosition, exit_price: float, reason: str):
        pos.exit_price = exit_price
        pos.exit_time = datetime.now().isoformat()
        pos.pnl = round((exit_price - pos.entry_price) * pos.quantity, 2)
        pos.exit_reason = reason
        pos.status = "closed"

        self.daily_pnl += pos.pnl
        self._save_state()

        # Log trade
        self._log_trade(pos)

        # Feed learnings back to bot
        from bots.base import TradeRecord
        trade = TradeRecord(
            trade_id=pos.id,
            index=pos.index,
            option_type=pos.option_type,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pos.pnl,
            pnl_pct=round((exit_price - pos.entry_price) / pos.entry_price * 100, 2) if pos.entry_price else 0,
            outcome="WIN" if pos.pnl > 0 else "LOSS",
            exit_reason=reason,
            market_conditions={
                "regime": pos.regime,
                "confidence": pos.confidence,
                "vote_diff": 0,
            },
        )
        self.bot.learn(trade)

        if self.on_exit:
            self.on_exit(pos)

        logger.info(
            f"[RH-Pipeline] EXIT: {pos.symbol} | {reason} | "
            f"PnL={pos.pnl:+.2f} | Daily={self.daily_pnl:+.2f}"
        )

    # ── live order ──

    def _place_live_order(self, pos: RHPosition) -> bool:
        """Place a live order via Fyers. Returns True on success."""
        if not self.fyers:
            return False
        try:
            fyers_sym = _build_fyers_option_symbol(pos.index, pos.strike, pos.option_type)
            if not fyers_sym:
                return False

            order = {
                "symbol": fyers_sym,
                "qty": pos.quantity,
                "type": 2, "side": 1,
                "productType": "INTRADAY",
                "limitPrice": 0, "stopPrice": 0,
                "validity": "DAY",
                "disclosedQty": 0, "offlineOrder": False,
                "stopLoss": 0, "takeProfit": 0,
            }
            resp = self.fyers.place_order(order)
            if resp.get("success"):
                logger.info(f"[RH-Pipeline][LIVE] Order placed: {fyers_sym}")
                return True
            logger.error(f"[RH-Pipeline][LIVE] Order failed: {resp}")
            return False
        except Exception as e:
            logger.error(f"[RH-Pipeline][LIVE] Exception: {e}")
            return False

    # ── risk gate ──

    def _can_trade(self) -> tuple:
        if not self.is_running:
            return False, "Pipeline not running"
        if self.is_paused:
            return False, "Pipeline paused"
        if self.daily_pnl <= -self.risk.max_daily_loss:
            return False, f"Daily loss limit ({self.daily_pnl:.0f})"
        if self.daily_pnl >= self.risk.max_daily_profit:
            return False, f"Daily profit target reached ({self.daily_pnl:.0f})"
        if self.daily_trades >= self.risk.max_daily_trades:
            return False, f"Max daily trades ({self.daily_trades})"
        open_ct = sum(1 for p in self.positions.values() if p.status == "open")
        if open_ct >= self.risk.max_concurrent_positions:
            return False, f"Max positions open ({open_ct})"

        now = datetime.now(ZoneInfo("Asia/Kolkata"))  # IST enforced
        mkt_open = now.replace(hour=9, minute=15, second=0)
        mkt_close = now.replace(hour=15, minute=30, second=0)
        if now < mkt_open + timedelta(minutes=self.risk.no_trade_first_minutes):
            return False, "Too early"
        if now > mkt_close - timedelta(minutes=self.risk.no_trade_last_minutes):
            return False, "Too late"

        return True, "OK"

    # ── persistence ──

    def _save_state(self):
        try:
            data = {
                "positions": [asdict(p) for p in self.positions.values()],
                "daily_pnl": self.daily_pnl,
                "daily_trades": self.daily_trades,
                "bot_regime": self.bot._current_regime,
                "last_updated": datetime.now().isoformat(),
            }
            tmp_file = Path(str(self._state_file) + ".tmp")
            with open(tmp_file, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self._state_file)
        except Exception as e:
            logger.critical(
                "[RH-Pipeline][_save_state] Failed to persist state to %s: %s — in-memory state intact, restart may lose recent changes",
                self._state_file, e,
            )

    def _load_state(self):
        if not self._state_file.exists():
            return
        try:
            with open(self._state_file) as f:
                data = json.load(f)
            self.daily_pnl = data.get("daily_pnl", 0.0)
            self.daily_trades = data.get("daily_trades", 0)
            for pd in data.get("positions", []):
                pos = RHPosition(**pd)
                if pos.status == "open":
                    self.positions[pos.id] = pos
        except (json.JSONDecodeError, TypeError):
            pass

    def _log_trade(self, pos: RHPosition):
        try:
            with open(self._trades_log, "a") as f:
                f.write(json.dumps(asdict(pos)) + "\n")
        except Exception as e:
            logger.error(
                "[RH-Pipeline][_log_trade] Failed to write trade log for %s: %s — trade occurred but record is missing",
                pos.id, e,
            )

    # ── status ──

    def get_status(self) -> Dict[str, Any]:
        open_pos = [p for p in self.positions.values() if p.status == "open"]
        closed_today = [
            p for p in self.positions.values()
            if p.status == "closed" and p.exit_time
            and p.exit_time.startswith(datetime.now().strftime("%Y-%m-%d"))
        ]
        wins = sum(1 for p in closed_today if p.pnl > 0)
        losses = sum(1 for p in closed_today if p.pnl <= 0)
        return {
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "mode": self.mode,
            "current_regime": self.bot._current_regime,
            "entries_this_regime": self.bot._entries_this_regime,
            "daily_pnl": self.daily_pnl,
            "daily_trades": self.daily_trades,
            "open_positions": [asdict(p) for p in open_pos],
            "closed_today": len(closed_today),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / max(1, wins + losses) * 100, 1),
            "bot_performance": {
                "total_signals": self.bot.performance.total_signals,
                "total_trades": self.bot.performance.total_trades,
                "weight": self.bot.performance.weight,
            },
            "parameters": dict(self.bot.parameters),
        }

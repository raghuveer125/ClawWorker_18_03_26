"""EOE Shadow Logger — read-only orchestrator with dynamic index selection.

Determines the expiring index at session start using the SAME source of truth
as the main scalping engine (shared_project_engine.indices). Does NOT maintain
a parallel expiry detection system.

SAFETY: No broker imports. No order creation. No context mutation.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .state_machine import EOEStateMachine
from .strike_evaluator import evaluate_strikes, best_tradable, StrikeCandidate
from .hypo_tracker import HypoTrade
from .log_writer import EOELogWriter
from .report_generator import generate_session_report

logger = logging.getLogger("scalping.eoe")

# Symbol universe (same as scalping engine config)
_INDEX_SYMBOL_MAP = {
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
}
_SYMBOL_INDEX_MAP = {v: k for k, v in _INDEX_SYMBOL_MAP.items()}


def _select_eoe_index(ctx: Dict[str, Any]) -> Tuple[Optional[str], str, str, str]:
    """Select which index EOE should monitor today.

    Uses the SAME source of truth as the main engine's startup expiry check.

    Returns (symbol, index_name, expiry_date, source) or (None, "", "", reason).
    """
    today = date.today()
    today_str = today.isoformat()

    # Ensure shared_project_engine is importable
    _project_root = Path(__file__).resolve().parents[3]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

    # ── Primary: Reuse main engine's expiry infrastructure ──
    try:
        from shared_project_engine.indices import get_todays_expiring_indices, get_expiry_schedule

        todays_expiry = get_todays_expiring_indices(use_live=True)
        schedule = get_expiry_schedule(use_live=True)

        if todays_expiry:
            # Filter to indices we support and have data for
            candidates = []
            for index_name in todays_expiry:
                symbol = _INDEX_SYMBOL_MAP.get(index_name)
                if not symbol:
                    continue
                expiry_info = schedule.get(index_name, {})
                expiry_date = expiry_info.get("nextExpiry", expiry_info.get("date", today_str))
                candidates.append((index_name, symbol, str(expiry_date)))

            if not candidates:
                return None, "", "", f"expiry_today={todays_expiry}_but_no_supported_symbol"

            # If multiple, pick the one with live spot data
            spot_data = ctx.get("spot_data", {})
            for name, sym, exp_date in candidates:
                spot = spot_data.get(sym)
                if spot and float(getattr(spot, "ltp", 0) or 0) > 0:
                    return sym, name, exp_date, f"engine_schedule_with_spot"

            # No spot yet (pre-market), pick by priority: NIFTY > BANKNIFTY > SENSEX
            priority = ["NIFTY50", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY"]
            for p in priority:
                for name, sym, exp_date in candidates:
                    if name == p:
                        return sym, name, exp_date, "engine_schedule_priority"

            # Fallback: first candidate
            name, sym, exp_date = candidates[0]
            return sym, name, exp_date, "engine_schedule_first"

        # No expiry today
        return None, "", "", "no_expiry_today_per_engine_schedule"

    except ImportError:
        logger.debug("EOE: shared_project_engine.indices not importable")
    except Exception as e:
        logger.debug("EOE: engine schedule error: %s", e)

    # ── Fallback: Read cache file directly ──
    for parents_up in (3, 4, 5):
        cache_path = Path(__file__).resolve().parents[parents_up] / "shared_project_engine" / "indices" / ".cache" / "expiry_schedule.json"
        try:
            if not cache_path.exists():
                continue
            with open(cache_path) as f:
                raw = json.load(f)
            data = raw.get("data", {})
            for index_name, info in data.items():
                if isinstance(info, dict) and info.get("next_expiry") == today_str:
                    symbol = _INDEX_SYMBOL_MAP.get(index_name)
                    if symbol:
                        return symbol, index_name, today_str, "cache_file_fallback"
            return None, "", "", f"cache_checked_no_expiry_today"
        except Exception:
            continue

    return None, "", "", "no_expiry_source_available"


class EOEShadowLogger:
    """Read-only shadow module for EOE validation.

    Dynamically selects the expiring index each session using the same
    infrastructure as the main scalping engine.

    Call on_cycle() after each engine cycle with a COPY of context.data.
    Never modifies engine state. All failures are caught and logged.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._index: Optional[str] = None
        self._index_name: str = ""
        self._expiry_date: str = ""
        self._selection_source: str = ""
        self._sm: Optional[EOEStateMachine] = None
        self._writer: Optional[EOELogWriter] = None
        self._trade: Optional[HypoTrade] = None
        self._trades: list = []
        self._cycle_count = 0
        self._active_cycles = 0
        self._tradable_cycles = 0
        self._session_meta: Dict[str, Any] = {}
        self._initialized = False
        self._disabled_reason: str = ""

    @property
    def active_index(self) -> Optional[str]:
        return self._index

    def on_cycle(self, ctx: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            self._process(ctx)
        except Exception as e:
            logger.debug("EOE shadow error: %s", e)

    def on_session_end(self) -> None:
        if not self.enabled or not self._initialized:
            return
        try:
            self._finalize()
        except Exception as e:
            logger.debug("EOE session end error: %s", e)

    def _process(self, ctx: Dict[str, Any]) -> None:
        if not self._initialized:
            self._init_session(ctx)

        if self._disabled_reason or self._index is None:
            return

        now = ctx.get("cycle_now") or ctx.get("cycle_timestamp") or datetime.now()
        if isinstance(now, str):
            try:
                now = datetime.fromisoformat(now)
            except ValueError:
                now = datetime.now()

        spot_data = ctx.get("spot_data", {})
        spot = spot_data.get(self._index)
        if spot is None:
            return

        ltp = float(getattr(spot, "ltp", 0) or 0)
        if ltp <= 0:
            return

        open_p = float(getattr(spot, "open", 0) or 0)
        vwap = float(getattr(spot, "vwap", ltp) or ltp)
        prev_close = float(getattr(spot, "prev_close", 0) or 0)

        # Structure breaks for selected index only
        breaks = ctx.get("structure_breaks", [])
        bos_events = []
        for b in breaks:
            b_sym = getattr(b, "symbol", b.get("symbol", "") if isinstance(b, dict) else "")
            if b_sym == self._index:
                bt = getattr(b, "break_type", b.get("break_type", "") if isinstance(b, dict) else "")
                bos_events.append({"break_type": bt})

        chains = ctx.get("option_chains", {})
        chain = chains.get(self._index)

        transition = self._sm.tick(
            now=now, ltp=ltp, vwap=vwap,
            open_price=open_p, prev_close=prev_close,
            bos_events=bos_events,
        )
        if transition and self._writer:
            self._writer.write_transition(transition)

        candidate = None
        tradable = False
        entry_signal = False
        entry_blocked = ""

        if self._sm.current in ("ACTIVE", "ARMED"):
            reversal_dir = "bullish" if self._sm.state.morning_direction == "bearish" else "bearish"
            if chain:
                candidates = evaluate_strikes(chain, ltp, reversal_dir)
                candidate = best_tradable(candidates)
                if candidate:
                    tradable = candidate.tradable

        if self._sm.current == "ACTIVE":
            self._active_cycles += 1
            if tradable:
                self._tradable_cycles += 1
            if tradable and candidate and self._trade is None:
                entry_signal = True
            elif self._trade is not None:
                entry_blocked = "trade_already_open"
            elif not tradable:
                entry_blocked = candidate.tradable_reason if candidate else "no_candidate"

        if entry_signal and candidate and self._trade is None:
            self._trade = HypoTrade(
                entry_time=now, strike=candidate.strike, option_type=candidate.option_type,
                entry_premium=candidate.premium, entry_bid=candidate.bid, entry_ask=candidate.ask,
                entry_spread_pct=candidate.spread_pct, entry_bid_qty=candidate.bid_qty,
                entry_ask_qty=candidate.ask_qty, tradable_at_entry=True,
            )

        if self._trade and self._trade.exit_time is None and chain:
            current_prem = self._get_option_premium(chain, self._trade.strike, self._trade.option_type)
            if current_prem > 0:
                exit_reason = self._trade.update(current_prem, now)
                if exit_reason:
                    self._trades.append(self._trade.to_dict())
                    if self._writer:
                        self._writer.write_hypo_trade(self._trade.to_dict())
                    self._trade = None

        self._cycle_count += 1
        if self._writer:
            reversal_bos = self._sm.state.bos_bullish_30min if self._sm.state.morning_direction == "bearish" else self._sm.state.bos_bearish_30min
            self._writer.write_cycle({
                "timestamp": now.isoformat(),
                "eoe_state": self._sm.current,
                "sensex_ltp": ltp,
                "vwap": vwap,
                "pct_from_extreme": round(self._sm.state.reversal_pct * 100, 2),
                "bos_active_count": reversal_bos,
                "bos_direction": "bullish" if self._sm.state.morning_direction == "bearish" else "bearish",
                "candidate_strike": candidate.strike if candidate else "",
                "candidate_type": candidate.option_type if candidate else "",
                "candidate_premium": candidate.premium if candidate else "",
                "candidate_bid": candidate.bid if candidate else "",
                "candidate_ask": candidate.ask if candidate else "",
                "candidate_spread_pct": candidate.spread_pct if candidate else "",
                "candidate_bid_qty": candidate.bid_qty if candidate else "",
                "candidate_ask_qty": candidate.ask_qty if candidate else "",
                "tradable": tradable,
                "entry_signal": entry_signal,
                "entry_blocked_reason": entry_blocked,
            })

    def _init_session(self, ctx: Dict[str, Any]) -> None:
        today = date.today()

        symbol, index_name, expiry_date, source = _select_eoe_index(ctx)

        if symbol is None:
            self._disabled_reason = source
            self._initialized = True
            logger.info("EOE shadow DISABLED: %s", source)
            return

        # Verify spot data will be available
        spot_data = ctx.get("spot_data", {})
        spot = spot_data.get(symbol)
        has_spot = spot is not None and float(getattr(spot, "ltp", 0) or 0) > 0

        # Verify option chain will be available
        chains = ctx.get("option_chains", {})
        has_chain = symbol in chains and hasattr(chains.get(symbol), "options")

        self._index = symbol
        self._index_name = index_name
        self._expiry_date = expiry_date
        self._selection_source = source
        self._sm = EOEStateMachine(is_expiry=True)
        self._writer = EOELogWriter()

        self._session_meta = {
            "session_date": today.isoformat(),
            "is_expiry": True,
            "index": symbol,
            "index_name": index_name,
            "expiry_date": expiry_date,
            "selection_source": source,
            "has_spot_at_init": has_spot,
            "has_chain_at_init": has_chain,
            "candidate_indices": list(_INDEX_SYMBOL_MAP.keys()),
        }
        self._initialized = True
        logger.info("EOE shadow: %s (%s) expiry=%s source=%s spot=%s chain=%s",
                     index_name, symbol, expiry_date, source, has_spot, has_chain)

    def _finalize(self) -> None:
        if self._disabled_reason:
            if self._writer is None:
                self._writer = EOELogWriter()
            self._session_meta.update({"disabled": True, "disabled_reason": self._disabled_reason, "total_cycles": 0})
            self._writer.write_session_meta(self._session_meta)
            return

        if self._trade and self._trade.exit_time is None:
            self._trade.force_close(self._trade.current_premium, datetime.now())
            self._trades.append(self._trade.to_dict())
            if self._writer:
                self._writer.write_hypo_trade(self._trade.to_dict())

        self._session_meta.update({
            "total_cycles": self._cycle_count,
            "active_cycles": self._active_cycles,
            "tradable_cycles": self._tradable_cycles,
            "total_trades": len(self._trades),
            "session_high": self._sm.state.session_high if self._sm else 0,
            "session_low": self._sm.state.session_low if self._sm else 0,
        })
        if self._writer:
            self._writer.write_session_meta(self._session_meta)
            generate_session_report(
                session_dir=self._writer.session_dir,
                meta=self._session_meta,
                transitions=self._sm.transitions if self._sm else [],
                trades=self._trades,
                cycle_count=self._cycle_count,
                active_cycles=self._active_cycles,
                tradable_cycles=self._tradable_cycles,
            )

    def _get_option_premium(self, chain: Any, strike: int, option_type: str) -> float:
        if not hasattr(chain, "options"):
            return 0.0
        for opt in chain.options:
            if int(getattr(opt, "strike", 0) or 0) == strike and \
               str(getattr(opt, "option_type", "")).upper() == option_type.upper():
                return float(getattr(opt, "ltp", 0) or 0)
        return 0.0

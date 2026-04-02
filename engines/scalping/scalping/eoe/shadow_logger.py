"""EOE Shadow Logger — read-only orchestrator with dynamic index selection.

Consumes engine context.data (via shallow copy), determines which index
is expiring today, runs EOE state machine, evaluates strikes, tracks
hypothetical trades, writes logs.

SAFETY: No broker imports. No order creation. No context mutation.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .state_machine import EOEStateMachine
from .strike_evaluator import evaluate_strikes, best_tradable, StrikeCandidate
from .hypo_tracker import HypoTrade
from .log_writer import EOELogWriter
from .report_generator import generate_session_report

logger = logging.getLogger("scalping.eoe")

# Index name → context.data key mapping
_INDEX_SYMBOL_MAP = {
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
}

# Reverse map
_SYMBOL_INDEX_MAP = {v: k for k, v in _INDEX_SYMBOL_MAP.items()}


def _detect_expiry_index(ctx: Dict[str, Any]) -> Tuple[Optional[str], str, str]:
    """Determine which index is expiring today.

    Returns (symbol, index_name, source) or (None, "", reason).

    Priority:
      A. Expiry schedule from shared_project_engine
      B. Option chain evidence (shortest-dated options)
      C. Safe fallback (disable)
    """
    today = date.today()

    # ── Priority A: Expiry schedule ──
    try:
        from shared_project_engine.indices import is_expiry_today
        expiring = []
        for index_name, symbol in _INDEX_SYMBOL_MAP.items():
            if is_expiry_today(index_name, today):
                expiring.append((index_name, symbol))

        if len(expiring) == 1:
            name, sym = expiring[0]
            return sym, name, "expiry_schedule"

        if len(expiring) > 1:
            # Multiple expiries: prefer the one with live spot data
            spot_data = ctx.get("spot_data", {})
            for name, sym in expiring:
                spot = spot_data.get(sym)
                if spot and float(getattr(spot, "ltp", 0) or 0) > 0:
                    return sym, name, f"expiry_schedule_multi({len(expiring)})_with_spot"
            # If no spot data yet, take first
            name, sym = expiring[0]
            return sym, name, f"expiry_schedule_multi({len(expiring)})_first"

        # No expiry today per schedule
    except ImportError:
        logger.debug("EOE: shared_project_engine.indices not available for schedule lookup")
    except Exception as e:
        logger.debug("EOE: expiry schedule lookup error: %s", e)

    # ── Priority B: Expiry cache file ──
    try:
        cache_path = Path(__file__).resolve().parents[4] / "shared_project_engine" / "indices" / ".cache" / "expiry_schedule.json"
        if cache_path.exists():
            with open(cache_path) as f:
                cache = json.load(f)
            schedule = cache.get("data", {})
            today_str = today.isoformat()
            for index_name, info in schedule.items():
                if isinstance(info, dict) and info.get("next_expiry") == today_str:
                    symbol = _INDEX_SYMBOL_MAP.get(index_name)
                    if symbol:
                        return symbol, index_name, "expiry_cache_file"
    except Exception as e:
        logger.debug("EOE: expiry cache read error: %s", e)

    # ── Priority C: Option chain evidence ──
    # Check if any chain has options expiring today (very short-dated premium behavior)
    chains = ctx.get("option_chains", {})
    spot_data = ctx.get("spot_data", {})
    for symbol, chain in chains.items():
        if not hasattr(chain, "options"):
            continue
        index_name = _SYMBOL_INDEX_MAP.get(symbol, "")
        if not index_name:
            continue
        # Heuristic: if near-ATM options have very low premium (< ₹5), likely expiry day
        spot = spot_data.get(symbol)
        if not spot:
            continue
        ltp = float(getattr(spot, "ltp", 0) or 0)
        if ltp <= 0:
            continue
        atm_strike = getattr(chain, "atm_strike", 0) or 0
        for opt in chain.options:
            strike = int(getattr(opt, "strike", 0) or 0)
            premium = float(getattr(opt, "ltp", 0) or 0)
            if abs(strike - atm_strike) <= 200 and 0 < premium < 5:
                return symbol, index_name, "chain_evidence_low_atm_premium"

    return None, "", "no_expiry_detected"


class EOEShadowLogger:
    """Read-only shadow module for EOE validation.

    Dynamically selects the expiring index each session.
    Call on_cycle() after each engine cycle with a COPY of context.data.
    Never modifies engine state. All failures are caught and logged.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._index: Optional[str] = None  # Selected dynamically
        self._index_name: str = ""
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
        """Process one engine cycle. ctx is a shallow copy — never modified."""
        if not self.enabled:
            return
        try:
            self._process(ctx)
        except Exception as e:
            logger.debug("EOE shadow error: %s", e)

    def on_session_end(self) -> None:
        """Call at session end to close trades and generate report."""
        if not self.enabled or not self._initialized:
            return
        try:
            self._finalize()
        except Exception as e:
            logger.debug("EOE session end error: %s", e)

    def _process(self, ctx: Dict[str, Any]) -> None:
        if not self._initialized:
            self._init_session(ctx)

        # If disabled (no expiry detected), skip silently
        if self._disabled_reason:
            return

        if self._index is None:
            return

        now = ctx.get("cycle_now") or ctx.get("cycle_timestamp") or datetime.now()
        if isinstance(now, str):
            try:
                now = datetime.fromisoformat(now)
            except ValueError:
                now = datetime.now()

        # Extract data for SELECTED index (read-only)
        spot_data = ctx.get("spot_data", {})
        spot = spot_data.get(self._index, None)
        if spot is None:
            return

        ltp = float(getattr(spot, "ltp", 0) or 0)
        if ltp <= 0:
            return

        open_p = float(getattr(spot, "open", 0) or 0)
        vwap = float(getattr(spot, "vwap", ltp) or ltp)
        prev_close = float(getattr(spot, "prev_close", 0) or 0)

        # Structure breaks
        breaks = ctx.get("structure_breaks", [])
        bos_events = []
        for b in breaks:
            b_sym = getattr(b, "symbol", b.get("symbol", "") if isinstance(b, dict) else "")
            if b_sym == self._index:
                bt = getattr(b, "break_type", b.get("break_type", "") if isinstance(b, dict) else "")
                bos_events.append({"break_type": bt})

        # Option chain for selected index
        chains = ctx.get("option_chains", {})
        chain = chains.get(self._index, None)

        # ── State machine tick ──
        transition = self._sm.tick(
            now=now, ltp=ltp, vwap=vwap,
            open_price=open_p, prev_close=prev_close,
            bos_events=bos_events,
        )

        if transition and self._writer:
            self._writer.write_transition(transition)

        # ── Strike evaluation ──
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

        # ── Create hypothetical trade ──
        if entry_signal and candidate and self._trade is None:
            self._trade = HypoTrade(
                entry_time=now,
                strike=candidate.strike,
                option_type=candidate.option_type,
                entry_premium=candidate.premium,
                entry_bid=candidate.bid,
                entry_ask=candidate.ask,
                entry_spread_pct=candidate.spread_pct,
                entry_bid_qty=candidate.bid_qty,
                entry_ask_qty=candidate.ask_qty,
                tradable_at_entry=True,
            )

        # ── Update open trade ──
        if self._trade and self._trade.exit_time is None and chain:
            current_prem = self._get_option_premium(chain, self._trade.strike, self._trade.option_type)
            if current_prem > 0:
                exit_reason = self._trade.update(current_prem, now)
                if exit_reason:
                    self._trades.append(self._trade.to_dict())
                    if self._writer:
                        self._writer.write_hypo_trade(self._trade.to_dict())
                    self._trade = None

        # ── Write cycle log ──
        self._cycle_count += 1
        if self._writer:
            reversal_bos = self._sm.state.bos_bullish_30min if self._sm.state.morning_direction == "bearish" else self._sm.state.bos_bearish_30min
            self._writer.write_cycle({
                "timestamp": now.isoformat(),
                "eoe_state": self._sm.current,
                "sensex_ltp": ltp,  # Field name kept for schema compat; actual index varies
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

        # Dynamic index selection
        symbol, index_name, source = _detect_expiry_index(ctx)

        if symbol is None:
            self._disabled_reason = source
            self._initialized = True
            logger.info("EOE shadow DISABLED: %s (no expiry index for %s)", source, today)
            return

        self._index = symbol
        self._index_name = index_name
        self._selection_source = source

        # Determine if this is truly expiry day for the selected index
        is_expiry = True  # We selected it because it's expiring

        self._sm = EOEStateMachine(is_expiry=is_expiry)
        self._writer = EOELogWriter()

        self._session_meta = {
            "session_date": today.isoformat(),
            "is_expiry": is_expiry,
            "index": symbol,
            "index_name": index_name,
            "selection_source": source,
            "candidate_indices": list(_INDEX_SYMBOL_MAP.keys()),
        }
        self._initialized = True
        logger.info("EOE shadow initialized: index=%s (%s), source=%s", index_name, symbol, source)

    def _finalize(self) -> None:
        if self._disabled_reason:
            # Write minimal meta even for disabled sessions
            if self._writer is None:
                self._writer = EOELogWriter()
            self._session_meta.update({
                "disabled": True,
                "disabled_reason": self._disabled_reason,
                "total_cycles": 0,
            })
            self._writer.write_session_meta(self._session_meta)
            return

        # Close open trade
        if self._trade and self._trade.exit_time is None:
            self._trade.force_close(self._trade.current_premium, datetime.now())
            self._trades.append(self._trade.to_dict())
            if self._writer:
                self._writer.write_hypo_trade(self._trade.to_dict())

        # Complete session meta
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

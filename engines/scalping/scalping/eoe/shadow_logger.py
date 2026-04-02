"""EOE Shadow Logger — read-only orchestrator.

Consumes engine context.data (via shallow copy), runs EOE state machine,
evaluates strikes, tracks hypothetical trades, writes logs.

SAFETY: No broker imports. No order creation. No context mutation.
"""

from __future__ import annotations

import logging
from datetime import datetime, date, timedelta
from typing import Any, Dict, Optional

from .state_machine import EOEStateMachine
from .strike_evaluator import evaluate_strikes, best_tradable, StrikeCandidate
from .hypo_tracker import HypoTrade
from .log_writer import EOELogWriter
from .report_generator import generate_session_report

logger = logging.getLogger("scalping.eoe")


class EOEShadowLogger:
    """Read-only shadow module for EOE validation.

    Call on_cycle() after each engine cycle with a COPY of context.data.
    Never modifies engine state. All failures are caught and logged.
    """

    INDEX = "BSE:SENSEX-INDEX"

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._sm: Optional[EOEStateMachine] = None
        self._writer: Optional[EOELogWriter] = None
        self._trade: Optional[HypoTrade] = None
        self._trades: list = []
        self._cycle_count = 0
        self._active_cycles = 0
        self._tradable_cycles = 0
        self._session_meta: Dict[str, Any] = {}
        self._initialized = False

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

        now = ctx.get("cycle_now") or ctx.get("cycle_timestamp") or datetime.now()
        if isinstance(now, str):
            try:
                now = datetime.fromisoformat(now)
            except ValueError:
                now = datetime.now()

        # Extract data (read-only)
        spot_data = ctx.get("spot_data", {})
        sensex = spot_data.get(self.INDEX, None)
        if sensex is None:
            return

        ltp = float(getattr(sensex, "ltp", 0) or 0)
        if ltp <= 0:
            return

        open_p = float(getattr(sensex, "open", 0) or 0)
        vwap = float(getattr(sensex, "vwap", ltp) or ltp)
        prev_close = float(getattr(sensex, "prev_close", 0) or 0)

        # Structure breaks
        breaks = ctx.get("structure_breaks", [])
        bos_events = []
        for b in breaks:
            bt = getattr(b, "break_type", b.get("break_type", "") if isinstance(b, dict) else "")
            bos_events.append({"break_type": bt})

        # Option chain
        chains = ctx.get("option_chains", {})
        chain = chains.get(self.INDEX, None)

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

            # Entry signal (simplified: tradable + no open trade)
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
        weekday = today.weekday()
        # Thu=3 (NSE weekly), Fri=4 (BSE weekly), Wed=2 (sometimes)
        is_expiry = weekday in (2, 3, 4)

        self._sm = EOEStateMachine(is_expiry=is_expiry)
        self._writer = EOELogWriter()
        self._session_meta = {
            "session_date": today.isoformat(),
            "is_expiry": is_expiry,
            "index": self.INDEX,
        }
        self._initialized = True
        logger.info("EOE shadow initialized: expiry=%s", is_expiry)

    def _finalize(self) -> None:
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

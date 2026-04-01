"""
Execution Layer Agents - Trade execution and risk management.

Agents:
8. EntryAgent - Entry logic and order placement
9. ExitAgent - Partial exit and runner management
10. PositionManagerAgent - Position tracking and P&L
11. RiskGuardianAgent - Capital protection and limits

All execution decisions use LLM Debate for validation when risk thresholds are exceeded.
"""

import asyncio
from dataclasses import asdict, dataclass, field, is_dataclass
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Use local base module
from ..base import BaseBot, BotContext, BotResult, BotStatus
from ..config import ScalpingConfig, IndexConfig, get_index_config, IndexType

# Import debate integration for trade validation
try:
    from ..debate_integration import (
        debate_entry_decision,
        debate_exit_decision,
        debate_risk_check,
        get_debate_config,
        check_debate_available,
    )
    HAS_DEBATE = True
except ImportError:
    HAS_DEBATE = False


@dataclass
class Position:
    """Active trading position."""
    position_id: str
    symbol: str
    strike: int
    option_type: str  # CE or PE
    entry_price: float
    entry_time: datetime
    quantity: int
    lots: int
    lot_size: int
    direction: str  # long or short
    status: str  # open, partial_exit, closed
    expiry: str = ""
    current_price: float = 0
    unrealized_pnl: float = 0
    realized_pnl: float = 0
    sl_price: float = 0
    target_price: float = 0
    partial_exit_done: bool = False
    partial_exit_qty: int = 0
    runner_qty: int = 0
    trail_stop: float = 0


@dataclass
class Order:
    """Order details."""
    order_id: str
    symbol: str
    strike: int
    option_type: str
    order_type: str  # market, limit
    side: str  # buy, sell
    quantity: int
    price: float
    status: str  # pending, filled, rejected, cancelled
    fill_price: float = 0
    fill_time: Optional[datetime] = None
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EntrySignal:
    """Entry signal with conditions."""
    symbol: str
    direction: str  # CE or PE
    strike: int
    premium: float
    lots: int
    confidence: float
    conditions_met: List[str]
    timestamp: datetime


def _normalize_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return datetime.now().isoformat()


def _context_now(context: Optional[BotContext] = None) -> datetime:
    if context is not None and isinstance(getattr(context, "data", None), dict):
        cycle_timestamp = context.data.get("cycle_timestamp")
        if isinstance(cycle_timestamp, str):
            try:
                return datetime.fromisoformat(cycle_timestamp)
            except ValueError:
                pass
    return datetime.now()


def _index_name(symbol: str) -> str:
    if "BANKNIFTY" in symbol:
        return "BANKNIFTY"
    if "SENSEX" in symbol:
        return "SENSEX"
    if "FINNIFTY" in symbol:
        return "FINNIFTY"
    if "NIFTY" in symbol:
        return "NIFTY50"
    return symbol


_option_prefix_cache: Dict[str, str] = {}


def _resolve_option_prefix(symbol: str) -> str:
    """Get cached option symbol prefix (e.g. NSE:NIFTY26407) for an index."""
    prefix = _option_prefix_cache.get(symbol)
    if prefix:
        return prefix
    try:
        from .data_agents import get_market_adapter
        adapter = get_market_adapter()
        if not adapter:
            return ""
        chain = adapter.get_option_chain_snapshot(symbol, strike_count=5)
        for opt in (chain or {}).get("data", {}).get("optionsChain", []):
            if not isinstance(opt, dict):
                continue
            opt_sym = str(opt.get("symbol", ""))
            opt_strike = str(int(float(opt.get("strike_price", 0) or 0)))
            ot = str(opt.get("option_type", "")).upper()
            if opt_strike and ot and opt_sym.endswith(f"{opt_strike}{ot}"):
                prefix = opt_sym[: -len(f"{opt_strike}{ot}")]
                _option_prefix_cache[symbol] = prefix
                return prefix
    except Exception:
        pass
    return ""


def _build_option_symbol(symbol: str, strike: int, option_type: str) -> str:
    """Build full option symbol like NSE:NIFTY2640722000PE."""
    prefix = _resolve_option_prefix(symbol)
    return f"{prefix}{strike}{option_type.upper()}" if prefix else ""


def _batch_fetch_position_ltp(positions: List, context_data: Dict[str, Any]) -> Dict[str, float]:
    """Batch-fetch live LTP for all open positions in a single API call.

    Returns {position_id: ltp} map. Stores results in context for reuse
    by both ExitAgent and PositionManager within the same cycle.
    """
    cache_key = "_position_ltp_cache"
    cached = context_data.get(cache_key)
    if isinstance(cached, dict) and cached:
        return cached

    result: Dict[str, float] = {}
    # Group positions by index to build option symbols
    symbols_to_fetch: Dict[str, str] = {}  # option_symbol -> position_id
    for pos in positions:
        if not hasattr(pos, "status") or pos.status == "closed":
            continue
        opt_sym = _build_option_symbol(pos.symbol, pos.strike, pos.option_type)
        if opt_sym:
            symbols_to_fetch[opt_sym] = pos.position_id

    if not symbols_to_fetch:
        return result

    try:
        from .data_agents import get_market_adapter
        adapter = get_market_adapter()
        if not adapter:
            return result

        # Single batch API call for all position quotes
        quotes_resp = adapter.get_quotes(list(symbols_to_fetch.keys()))
        rows = quotes_resp.get("data", {}).get("d", []) if isinstance(quotes_resp.get("data"), dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("n", row.get("symbol", ""))).upper()
            ltp = float(row.get("v", {}).get("lp", 0) or 0) if isinstance(row.get("v"), dict) else 0
            if not ltp:
                ltp = float(row.get("ltp", 0) or 0)
            pos_id = symbols_to_fetch.get(sym)
            if pos_id and ltp > 0:
                result[pos_id] = ltp

        # Fallback: for any position not found in batch, try individual quotes
        for opt_sym, pos_id in symbols_to_fetch.items():
            if pos_id not in result:
                ltp = adapter.get_quote_ltp(opt_sym)
                if ltp and float(ltp) > 0:
                    result[pos_id] = float(ltp)
    except Exception:
        pass

    context_data[cache_key] = result
    return result


def _fetch_live_option_ltp(symbol: str, strike: int, option_type: str) -> float:
    """Fetch live LTP for a specific option directly from broker (single quote)."""
    opt_sym = _build_option_symbol(symbol, strike, option_type)
    if not opt_sym:
        return 0.0
    try:
        from .data_agents import get_market_adapter
        adapter = get_market_adapter()
        if adapter:
            ltp = adapter.get_quote_ltp(opt_sym)
            return float(ltp) if ltp and ltp > 0 else 0.0
    except Exception:
        pass
    return 0.0


def _sync_dashboard_state(context: BotContext) -> None:
    try:
        from .. import api as api_state

        api_state.sync_engine_state(context)
    except Exception:
        pass


def _signal_key(symbol: str, strike: int, option_type: str) -> str:
    return f"{symbol}|{int(strike)}|{option_type.upper()}"


_HISTORICAL_ENTRY_CONDITIONS = {"historical_signal_approved", "historical_entry_ready"}


def _is_replay_journal_signal_payload(signal_payload: Dict[str, Any]) -> bool:
    source = str(signal_payload.get("source", "") or "").strip().lower()
    entry_ready = str(signal_payload.get("entry_ready", "") or "").strip().upper() == "Y"
    selected = str(signal_payload.get("selected", "") or "").strip().upper() == "Y"
    return source == "replay_journal" or entry_ready or selected


def _option_direction(option_type: str) -> str:
    normalized = str(option_type or "").upper()
    if normalized == "CE":
        return "bullish"
    if normalized == "PE":
        return "bearish"
    return "neutral"


def _resolve_index_config(symbol: str) -> Optional[IndexConfig]:
    normalized = str(symbol or "").upper()
    if not normalized:
        return None
    best_prefix_match: Optional[IndexConfig] = None
    best_prefix_length = -1
    for idx_type in IndexType:
        config = get_index_config(idx_type)
        exact_candidates = (
            str(idx_type.value).upper(),
            str(config.symbol).upper(),
        )
        if normalized in exact_candidates:
            return config
        option_prefix = str(config.option_prefix).upper()
        if option_prefix and normalized.startswith(option_prefix) and len(option_prefix) > best_prefix_length:
            best_prefix_match = config
            best_prefix_length = len(option_prefix)
    return best_prefix_match


def _tick_precision(tick_size: float) -> int:
    try:
        exponent = Decimal(str(tick_size)).normalize().as_tuple().exponent
    except Exception:
        return 2
    return max(0, -exponent)


def _round_to_tick(price: float, tick_size: float = 0.05) -> float:
    try:
        price_decimal = Decimal(str(price or 0.0))
        tick_decimal = Decimal(str(tick_size or 0.0))
    except Exception:
        return float(price or 0.0)
    if price_decimal <= 0 or tick_decimal <= 0:
        return float(price_decimal)
    ticks = (price_decimal / tick_decimal).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    rounded = ticks * tick_decimal
    precision = _tick_precision(float(tick_decimal))
    quantizer = Decimal("1").scaleb(-precision)
    return float(rounded.quantize(quantizer, rounding=ROUND_HALF_UP))


def _round_price_for_symbol(symbol: str, price: float) -> float:
    config = _resolve_index_config(symbol)
    tick_size = config.tick_size if config else 0.05
    return _round_to_tick(price, tick_size=tick_size)


def _coerce_signal_dict(candidate: Any, default_symbol: str = "") -> Dict[str, Any]:
    if isinstance(candidate, dict):
        payload = dict(candidate)
    elif is_dataclass(candidate):
        payload = asdict(candidate)
    elif hasattr(candidate, "__dict__"):
        payload = dict(getattr(candidate, "__dict__", {}))
    else:
        return {}
    if default_symbol and not payload.get("symbol"):
        payload["symbol"] = default_symbol
    return payload


def _signal_matches_contract(signal: Dict[str, Any], symbol: str, strike: int, option_type: str) -> bool:
    candidate_symbol = str(signal.get("underlying_symbol", signal.get("symbol", "")) or "")
    candidate_strike = int(float(signal.get("strike", 0) or 0))
    candidate_option_type = str(signal.get("option_type", signal.get("side", ""))).upper()
    return (
        candidate_symbol == symbol
        and candidate_strike == int(strike)
        and candidate_option_type == str(option_type).upper()
    )


def _stage_has_signal_support(stage_payload: Any, symbol: str, strike: int, option_type: str) -> bool:
    if isinstance(stage_payload, dict):
        for default_symbol, items in stage_payload.items():
            for item in items if isinstance(items, list) else []:
                if _signal_matches_contract(_coerce_signal_dict(item, default_symbol), symbol, strike, option_type):
                    return True
        return False
    if isinstance(stage_payload, list):
        for item in stage_payload:
            if _signal_matches_contract(_coerce_signal_dict(item), symbol, strike, option_type):
                return True
    return False


class EntryAgent(BaseBot):
    """
    Agent 8: Entry Agent

    Entry conditions:
    1. Spot structure breakout
    2. Futures momentum confirmation
    3. Option chain volume burst
    4. Trap detection confirmation (optional)

    Entry: 4-6 lots
    """

    BOT_TYPE = "entry"
    REQUIRES_LLM = False  # DISABLED - debate causes latency in execution path

    def __init__(self, dry_run: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.dry_run = dry_run

    def get_description(self) -> str:
        mode = "DRY RUN" if self.dry_run else "LIVE"
        return f"Entry execution ({mode})"

    async def execute(self, context: BotContext) -> BotResult:
        """Evaluate entry conditions and execute if valid."""
        config = context.data.get("config", ScalpingConfig())
        cycle_now = _context_now(context)
        structure_breaks = context.data.get("structure_breaks", [])
        momentum_signals = context.data.get("momentum_signals", [])
        trap_signals = context.data.get("trap_signals", [])
        positions = context.data.get("positions", [])
        rejected_signals = list(context.data.get("rejected_signals", []))
        raw_signals = context.data.get("execution_candidates") or context.data.get("liquidity_filtered_selections", [])
        replay_mode = bool(context.data.get("replay_mode"))
        trade_disabled = bool(context.data.get("trade_disabled"))
        blocked_signal_keys = set(context.data.get("correlation_blocked_signal_keys", []))
        correlation_penalty_map = context.data.get("correlation_signal_penalties", {})
        using_confirmed_candidates = bool(context.data.get("execution_candidates"))
        confirmation_map = context.data.get("entry_confirmation_state", {})
        liquidity_vacuum_map = context.data.get("liquidity_vacuum", {})
        momentum_strength_map = context.data.get("momentum_strength", {})
        queue_risk_map = context.data.get("queue_risk", {})
        volatility_burst_map = context.data.get("volatility_burst", {})

        if trade_disabled:
            context.data["entry_signals"] = []
            context.data["pending_orders"] = []
            context.data["execution_metrics"] = []
            _sync_dashboard_state(context)
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SKIPPED,
                output={"message": context.data.get("trade_disabled_reason", "trade disabled")},
                metrics={"signals_generated": 0, "orders_pending": 0},
            )

        if isinstance(raw_signals, dict):
            signals = []
            for symbol, selections in raw_signals.items():
                for selection in selections or []:
                    if isinstance(selection, dict):
                        signals.append({"symbol": symbol, **selection})
                    elif hasattr(selection, "__dict__"):
                        signals.append({"symbol": symbol, **asdict(selection)})
        else:
            signals = [signal for signal in raw_signals if isinstance(signal, dict)]

        if not signals:
            context.data["entry_signals"] = []
            context.data["pending_orders"] = []
            context.data["execution_metrics"] = []
            _sync_dashboard_state(context)
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SKIPPED,
                output={"message": "No liquid signals available for execution"},
                metrics={"signals_generated": 0, "orders_pending": 0},
            )

        if self._is_past_entry_cutoff(context, config):
            cutoff = str(getattr(config, "late_entry_cutoff_time", "14:50") or "14:50")
            for signal_payload in signals:
                rejected_signals.append(
                    {
                        **signal_payload,
                        "rejection_reasons": list(signal_payload.get("rejection_reasons", []))
                        + [f"Past late-entry cutoff: {cutoff}"],
                    }
                )
            context.data["entry_signals"] = []
            context.data["pending_orders"] = []
            context.data["rejected_signals"] = rejected_signals
            context.data["execution_metrics"] = []
            _sync_dashboard_state(context)
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SKIPPED,
                output={"message": f"New entries disabled after {cutoff}"},
                metrics={"signals_generated": 0, "orders_pending": 0},
            )

        # Check if we can take more positions
        open_positions = len([p for p in positions if p.status != "closed"])
        if open_positions >= config.max_positions:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SUCCESS,
                output={"message": "Max positions reached"},
            )

        entry_signals = []
        orders = []
        execution_metrics = []
        remaining_slots = max(0, int(config.max_positions) - open_positions)

        for signal_payload in signals:
            symbol = signal_payload.get("symbol", "")
            if not symbol:
                continue
            if remaining_slots <= 0:
                rejected_signals.append(
                    {
                        **signal_payload,
                        "rejection_reasons": list(signal_payload.get("rejection_reasons", []))
                        + [f"Position cap reached during batch: {config.max_positions}/{config.max_positions}"],
                    }
                )
                continue
            strike = int(float(signal_payload.get("strike", 0) or 0))
            option_type = str(signal_payload.get("option_type", signal_payload.get("side", "CE"))).upper()
            signal_key = _signal_key(symbol, strike, option_type)
            confirmation_state = (
                dict(confirmation_map.get(signal_key, {}) or {}) if isinstance(confirmation_map, dict) else {}
            )
            liquidity_vacuum = (
                dict(liquidity_vacuum_map.get(signal_key, {}) or {}) if isinstance(liquidity_vacuum_map, dict) else {}
            )
            micro_momentum = (
                dict(momentum_strength_map.get(signal_key, {}) or {}) if isinstance(momentum_strength_map, dict) else {}
            )
            queue_risk = (
                dict(queue_risk_map.get(signal_key, {}) or {}) if isinstance(queue_risk_map, dict) else {}
            )
            volatility_burst = (
                dict(volatility_burst_map.get(signal_key, {}) or {}) if isinstance(volatility_burst_map, dict) else {}
            )
            correlation_penalty = (
                dict(correlation_penalty_map.get(signal_key, {}) or {})
                if isinstance(correlation_penalty_map, dict)
                else {}
            )

            if signal_key in blocked_signal_keys and not correlation_penalty:
                rejected_signals.append(
                    {
                        **signal_payload,
                        "rejection_reasons": list(signal_payload.get("rejection_reasons", []))
                        + ["Correlation guard blocked signal"],
                    }
                )
                continue

            micro_timing = str(micro_momentum.get("timing", "unknown"))
            confirmation_status = str(confirmation_state.get("status", "unknown"))
            if micro_timing == "reject":
                rejected_signals.append(
                    {
                        **signal_payload,
                        "rejection_reasons": list(signal_payload.get("rejection_reasons", []))
                        + ["Adaptive entry timing rejected weak momentum"],
                    }
                )
                continue
            if using_confirmed_candidates and confirmation_status not in {"confirmed"}:
                rejected_signals.append(
                    {
                        **signal_payload,
                        "rejection_reasons": list(signal_payload.get("rejection_reasons", []))
                        + [f"Entry confirmation incomplete: {confirmation_status}"],
                    }
                )
                continue
            # Check entry conditions
            conditions_met = self._check_entry_conditions(
                symbol, structure_breaks, momentum_signals, trap_signals, config
            )
            if replay_mode:
                conditions_met = self._augment_replay_entry_conditions(signal_payload, conditions_met)
                replay_guard_reason = self._validate_replay_entry_conditions(signal_payload, conditions_met, config)
                if replay_guard_reason:
                    rejected_signals.append(
                        {
                            **signal_payload,
                            "rejection_reasons": list(signal_payload.get("rejection_reasons", []))
                            + [replay_guard_reason],
                        }
                    )
                    continue

            setup_assessment = self._assess_trade_setup(
                signal_payload=signal_payload,
                symbol=symbol,
                option_type=option_type,
                context=context,
                micro_momentum=micro_momentum,
                liquidity_vacuum=liquidity_vacuum,
                volatility_burst=volatility_burst,
                confirmation_state=confirmation_state,
            )
            strict_rejection = self._strict_entry_rejection_reason(setup_assessment, config)
            if strict_rejection:
                rejected_signals.append(
                    {
                        **signal_payload,
                        "rejection_reasons": list(signal_payload.get("rejection_reasons", []))
                        + [strict_rejection],
                    }
                )
                continue

            if len(conditions_met) >= 2:  # At least 2 conditions
                premium = float(signal_payload.get("premium", signal_payload.get("entry", 0)) or 0)
                premium = _round_price_for_symbol(symbol, premium)
                raw_confidence = float(
                    signal_payload.get("confidence", signal_payload.get("quality_score", signal_payload.get("score", 0)))
                    or 0
                )
                normalized_confidence = raw_confidence / 100.0 if raw_confidence > 1.0 else raw_confidence
                multiplier = self.compute_position_multiplier(
                    signal_payload,
                    context,
                    setup_assessment=setup_assessment,
                    confirmation_state=confirmation_state,
                )
                quote_ok, quote_reasons, fill_quote = self._validate_fill_conditions(signal_payload, context, config)
                if liquidity_vacuum.get("active"):
                    multiplier = min(1.0, multiplier * 1.05)
                correlation_size_scale = float(correlation_penalty.get("size_scale", 1.0) or 1.0)
                effective_multiplier = multiplier * correlation_size_scale

                execution_metrics.append(
                    {
                        "symbol": symbol,
                        "strike": strike,
                        "option_type": option_type,
                        "multiplier": round(effective_multiplier, 4),
                        "base_multiplier": round(multiplier, 4),
                        "confidence": signal_payload.get("confidence", signal_payload.get("quality_score", signal_payload.get("score", 0))),
                        "quote_valid": quote_ok,
                        "timing": micro_timing,
                        "queue_risk": queue_risk.get("risk", "unknown"),
                        "correlation_risk": correlation_penalty.get("risk", "low"),
                        "correlation_scale": round(correlation_size_scale, 4),
                        "volatility_burst": bool(volatility_burst.get("active")),
                        "setup_tag": setup_assessment.get("tag"),
                        "strict_pass": bool(setup_assessment.get("strict_pass")),
                    }
                )

                if not quote_ok:
                    rejected_signals.append(
                        {
                            **signal_payload,
                            "rejection_reasons": list(signal_payload.get("rejection_reasons", [])) + quote_reasons,
                        }
                    )
                    continue

                if multiplier < 0.2:
                    rejected_signals.append(
                        {
                            **signal_payload,
                            "rejection_reasons": list(signal_payload.get("rejection_reasons", []))
                            + [f"Execution multiplier too low: {multiplier:.2f}"],
                        }
                    )
                    continue

                queue_size_scale = float(queue_risk.get("size_scale", 1.0) or 0.0)
                if queue_size_scale <= 0.0:
                    rejected_signals.append(
                        {
                            **signal_payload,
                            "rejection_reasons": list(signal_payload.get("rejection_reasons", []))
                            + [f"Queue risk too high: {queue_risk.get('queue_ratio', 0)}x ahead"],
                        }
                    )
                    continue

                order_lots = max(1, round(config.entry_lots * effective_multiplier * queue_size_scale))

                # Create entry signal
                signal = EntrySignal(
                    symbol=symbol,
                    direction=option_type,
                    strike=strike,
                    premium=premium,
                    lots=order_lots,
                    confidence=min(1.0, normalized_confidence),
                    conditions_met=conditions_met,
                    timestamp=cycle_now,
                )
                entry_signals.append(signal)

                order = await self._create_entry_order(
                    signal,
                    symbol,
                    config,
                    multiplier=multiplier,
                    replay_mode=replay_mode,
                    current_time=cycle_now,
                    metadata={
                        **signal_payload,
                        "fill_quote": fill_quote,
                        "regime": context.data.get("market_structure", {}).get(symbol, {}).get("trend", "unknown"),
                        "momentum_strength": self._symbol_momentum_strength(symbol, momentum_signals),
                        "micro_momentum_strength": float(micro_momentum.get("score", 0.0) or 0.0),
                        "entry_confirmation": confirmation_state,
                        "liquidity_vacuum": liquidity_vacuum,
                        "queue_risk": queue_risk,
                        "correlation_penalty": correlation_penalty,
                        "volatility_burst": volatility_burst,
                        "setup_tag": setup_assessment.get("tag"),
                        "setup_reasons": list(setup_assessment.get("reasons", [])),
                        "strict_filter_pass": bool(setup_assessment.get("strict_pass")),
                        "rr_ratio": float(setup_assessment.get("rr_ratio", 0.0) or 0.0),
                        "timeframe_alignment": dict(setup_assessment.get("timeframe_alignment", {}) or {}),
                        "entry_trigger": dict(setup_assessment.get("entry_trigger", {}) or {}),
                        "micro_momentum": dict(setup_assessment.get("micro_momentum", {}) or {}),
                        "condition_count": len(conditions_met),
                        "target_scale": (context.data.get("volatility_surface", {}).get(symbol, {}) if isinstance(context.data.get("volatility_surface", {}), dict) else {}).get("target_scale", 1.0),
                        "stop_scale": float(volatility_burst.get("burst_stop_scale", 1.0) or 1.0),
                    },
                )
                orders.append(order)
                remaining_slots -= 1
            else:
                rejected_signals.append(
                    {
                        **signal_payload,
                        "rejection_reasons": list(signal_payload.get("rejection_reasons", []))
                        + [f"Insufficient entry conditions: {len(conditions_met)}/2"],
                    }
                )

        context.data["entry_signals"] = entry_signals
        context.data["pending_orders"] = orders
        context.data["rejected_signals"] = rejected_signals
        context.data["execution_metrics"] = execution_metrics
        _sync_dashboard_state(context)

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "entry_signals": [s.__dict__ for s in entry_signals],
                "orders_created": len(orders),
                "dry_run": self.dry_run,
            },
            metrics={
                "signals_generated": len(entry_signals),
                "orders_pending": len(orders),
            },
        )

    def _is_past_entry_cutoff(self, context: BotContext, config: ScalpingConfig) -> bool:
        if bool(context.data.get("replay_mode")):
            return False
        cutoff_value = str(getattr(config, "late_entry_cutoff_time", "") or "").strip()
        if not cutoff_value:
            return False
        try:
            cutoff = datetime.strptime(cutoff_value, "%H:%M").time()
        except ValueError:
            return False

        cycle_timestamp = context.data.get("cycle_timestamp")
        if isinstance(cycle_timestamp, str):
            try:
                now = datetime.fromisoformat(cycle_timestamp)
            except ValueError:
                now = datetime.now()
        else:
            now = datetime.now()
        return now.time() > cutoff

    def _strict_entry_rejection_reason(self, setup_assessment: Dict[str, Any], config: ScalpingConfig) -> Optional[str]:
        tag = str(setup_assessment.get("tag", "C") or "C")
        if bool(getattr(config, "strict_a_plus_only", False)):
            if tag == "A+":
                return None
            missing = ", ".join(setup_assessment.get("missing_requirements", []))
            if missing:
                return f"Strict A+ filter rejected setup ({tag}): {missing}"
            return f"Strict A+ filter rejected setup ({tag})"
        if tag == "C":
            rr = float(setup_assessment.get("rr_ratio", 0.0) or 0.0)
            min_rr = float(getattr(config, "strict_b_rr_ratio", 1.1) or 1.1)
            # Allow C-tag if R:R is acceptable — timeframe data may be sparse
            # but the signal has valid risk/reward from live option pricing
            if rr >= min_rr:
                return None
            missing = ", ".join(setup_assessment.get("missing_requirements", []))
            if missing:
                return f"Setup quality too weak ({tag}): {missing}"
            return "Setup quality too weak (C)"
        return None

    def _assess_trade_setup(
        self,
        signal_payload: Dict[str, Any],
        symbol: str,
        option_type: str,
        context: BotContext,
        micro_momentum: Dict[str, Any],
        liquidity_vacuum: Dict[str, Any],
        volatility_burst: Dict[str, Any],
        confirmation_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        config = context.data.get("config", ScalpingConfig())
        direction = _option_direction(option_type)
        rr_ratio = self._rr_ratio(signal_payload)
        structure_info = (
            dict((context.data.get("market_structure", {}) or {}).get(symbol, {}) or {})
            if isinstance(context.data.get("market_structure", {}), dict)
            else {}
        )
        timeframes = dict(structure_info.get("timeframes", {}) or {})
        one_minute = dict(timeframes.get("1m", {}) or {})
        three_minute = dict(timeframes.get("3m", {}) or {})
        five_minute = dict(timeframes.get("5m", {}) or {})

        one_minute_aligned = (
            bool(one_minute.get("available"))
            and str(one_minute.get("trend", "neutral")) == direction
            and float(one_minute.get("momentum_points", 0.0) or 0.0) > 0
        )
        three_minute_breakout = (
            bool(three_minute.get("available"))
            and str(three_minute.get("breakout", "")) == direction
        )
        five_minute_aligned = (
            bool(five_minute.get("available"))
            and str(five_minute.get("trend", "neutral")) == direction
        )
        three_tf_aligned = one_minute_aligned and three_minute_breakout and five_minute_aligned

        synthetic_burst = self._synthetic_volatility_burst(symbol, context)
        trigger_types: List[str] = []
        if bool(volatility_burst.get("active")):
            trigger_types.append("volatility_burst")
        if bool(liquidity_vacuum.get("active")):
            trigger_types.append("liquidity_vacuum")
        if bool(synthetic_burst.get("active")):
            trigger_types.append("synthetic_volatility_burst")
        trigger_active = bool(trigger_types)

        micro_score = float(micro_momentum.get("score", 0.0) or 0.0)
        micro_aligned = micro_score > 0 and one_minute_aligned
        confirmation_status = str(confirmation_state.get("status", "") or "").lower()
        live_confirmed = confirmation_status == "confirmed"

        missing_requirements: List[str] = []
        if rr_ratio < float(getattr(config, "strict_a_plus_rr_ratio", 1.3) or 1.3):
            missing_requirements.append(f"R:R<{float(getattr(config, 'strict_a_plus_rr_ratio', 1.3) or 1.3):.1f}")
        if not five_minute_aligned:
            missing_requirements.append("5m trend misaligned")
        if not three_minute_breakout:
            missing_requirements.append("3m breakout missing")
        if not one_minute_aligned:
            missing_requirements.append("1m momentum missing")
        if not trigger_active:
            missing_requirements.append("entry trigger missing")

        b_missing_requirements: List[str] = []
        if rr_ratio < float(getattr(config, "strict_b_rr_ratio", 1.1) or 1.1):
            b_missing_requirements.append(f"R:R<{float(getattr(config, 'strict_b_rr_ratio', 1.1) or 1.1):.1f}")
        if not three_minute_breakout:
            b_missing_requirements.append("3m breakout missing")
        if not one_minute_aligned:
            b_missing_requirements.append("1m momentum missing")

        supportive_count = sum(
            1 for flag in (five_minute_aligned, three_minute_breakout, one_minute_aligned, trigger_active) if flag
        )
        strict_mode = bool(getattr(config, "strict_a_plus_only", False))
        if rr_ratio >= float(getattr(config, "strict_a_plus_rr_ratio", 1.3) or 1.3) and supportive_count == 4:
            tag = "A+"
            strict_pass = True
            active_missing_requirements: List[str] = []
        elif not b_missing_requirements:
            tag = "B"
            strict_pass = not strict_mode
            active_missing_requirements = missing_requirements if strict_mode else []
        else:
            tag = "C"
            strict_pass = False
            active_missing_requirements = missing_requirements if strict_mode else b_missing_requirements

        return {
            "tag": tag,
            "strict_pass": strict_pass,
            "rr_ratio": round(rr_ratio, 4),
            "timeframe_alignment": {
                "1m_trend": one_minute.get("trend", "neutral"),
                "1m_momentum_points": float(one_minute.get("momentum_points", 0.0) or 0.0),
                "1m_aligned": one_minute_aligned,
                "3m_trend": three_minute.get("trend", "neutral"),
                "3m_breakout": three_minute.get("breakout"),
                "3m_breakout_aligned": three_minute_breakout,
                "5m_trend": five_minute.get("trend", "neutral"),
                "5m_aligned": five_minute_aligned,
                "three_tf_aligned": three_tf_aligned,
            },
            "entry_trigger": {
                "active": trigger_active,
                "types": trigger_types,
                "live_volatility_burst": bool(volatility_burst.get("active")),
                "live_liquidity_vacuum": bool(liquidity_vacuum.get("active")),
                "synthetic_volatility_burst": bool(synthetic_burst.get("active")),
            },
            "micro_momentum": {
                "score": round(micro_score, 4),
                "timing": str(micro_momentum.get("timing", "unknown") or "unknown"),
                "aligned": micro_aligned,
                "live_confirmed": live_confirmed,
            },
            "supportive_count": supportive_count,
            "reasons": [
                f"setup_tag={tag}",
                f"rr={rr_ratio:.2f}",
                f"3tf_aligned={three_tf_aligned}",
                f"trigger_active={trigger_active}",
                f"live_confirmed={live_confirmed}",
            ],
            "missing_requirements": active_missing_requirements,
            "a_plus_missing_requirements": missing_requirements,
            "b_missing_requirements": b_missing_requirements,
        }

    def _synthetic_volatility_burst(self, symbol: str, context: BotContext) -> Dict[str, Any]:
        candles = context.data.get(f"candles_{symbol}", [])
        if not isinstance(candles, list) or len(candles) < 3:
            return {"active": False, "average_abs_return": 0.0}
        recent = candles[-3:]
        returns: List[float] = []
        previous_close: Optional[float] = None
        for candle in recent:
            close = float(candle.get("close", 0.0) or 0.0)
            if previous_close and previous_close > 0:
                returns.append(abs((close - previous_close) / previous_close))
            previous_close = close
        average_abs_return = sum(returns) / len(returns) if returns else 0.0
        threshold = float(getattr(context.data.get("config", ScalpingConfig()), "volatility_burst_vol_threshold", 0.012) or 0.012)
        return {
            "active": average_abs_return >= threshold,
            "average_abs_return": round(average_abs_return, 6),
        }

    def _rr_ratio(self, signal: Dict[str, Any]) -> float:
        entry = float(signal.get("entry", signal.get("premium", 0.0)) or 0.0)
        sl = float(signal.get("sl", signal.get("stop_loss", 0.0)) or 0.0)
        target = float(signal.get("target", signal.get("t1", 0.0)) or 0.0)
        if entry <= 0 or sl <= 0 or target <= 0:
            return 0.0
        risk = abs(entry - sl)
        reward = abs(target - entry)
        if risk <= 0:
            return 0.0
        return reward / risk

    def compute_position_multiplier(
        self,
        signal: Dict[str, Any],
        context: BotContext,
        *,
        setup_assessment: Optional[Dict[str, Any]] = None,
        confirmation_state: Optional[Dict[str, Any]] = None,
    ) -> float:
        confirmation_state = confirmation_state or {}
        setup_assessment = setup_assessment or {}
        tag = str(setup_assessment.get("tag", "") or "")
        rr_ratio = float(setup_assessment.get("rr_ratio", 0.0) or 0.0)
        live_confirmed = bool((setup_assessment.get("micro_momentum", {}) or {}).get("live_confirmed"))

        if tag == "A+":
            base = float(context.data.get("config", ScalpingConfig()).strict_a_plus_size_fraction or 0.65)
            if rr_ratio >= 1.6:
                base = min(0.7, base + 0.05)
            if live_confirmed or str(confirmation_state.get("status", "")).lower() == "confirmed":
                base = min(0.7, base + 0.02)
        elif tag == "B":
            base = float(context.data.get("config", ScalpingConfig()).strict_b_size_fraction or 0.35)
            if rr_ratio >= 1.3:
                base = min(0.4, base + 0.03)
        elif tag == "C":
            # C-tag with valid R:R gets reduced size (25% of normal)
            if rr_ratio >= float(context.data.get("config", ScalpingConfig()).strict_b_rr_ratio or 1.1):
                base = 0.25
            else:
                base = 0.0
        else:
            confidence = float(signal.get("confidence", signal.get("quality_score", signal.get("score", 0))) or 0)
            if confidence <= 1.0:
                confidence *= 100.0
            base = confidence / 100.0

        spread_pct = float(signal.get("spread_pct", 0) or 0)
        vix = float(context.data.get("vix", context.data.get("current_vix", 15.0)) or 15.0)
        learn_prob = float(signal.get("learn_prob", 1.0) or 1.0)
        volatility_surface = context.data.get("volatility_surface", {})
        dealer_pressure = context.data.get("dealer_pressure", {})
        symbol = str(signal.get("symbol", ""))
        surface_info = volatility_surface.get(symbol, {}) if isinstance(volatility_surface, dict) else {}
        dealer_info = dealer_pressure.get(symbol, {}) if isinstance(dealer_pressure, dict) else {}

        if spread_pct > 0.5:
            base *= 0.5
        if vix > 25:
            base *= 0.7
        if learn_prob < 0.5:
            base *= 0.6
        base *= float(surface_info.get("size_scale", 1.0) or 1.0)

        gamma_regime = str(dealer_info.get("gamma_regime", "neutral"))
        pinning_score = float(dealer_info.get("pinning_score", 0.0) or 0.0)
        extreme_pin_threshold = float(getattr(context.data.get("config", ScalpingConfig()), "dealer_extreme_pinning_score", 0.85) or 0.85)
        if gamma_regime == "short":
            base *= self._bounded_scale(context.data.get("config", ScalpingConfig()).dealer_short_gamma_boost)
        elif gamma_regime == "long" and pinning_score >= extreme_pin_threshold:
            base *= self._bounded_scale(context.data.get("config", ScalpingConfig()).dealer_long_gamma_penalty)

        return max(0.0, min(1.0, base))

    def _bounded_scale(self, value: float) -> float:
        return max(0.8, min(1.1, float(value or 1.0)))

    def _check_entry_conditions(
        self,
        symbol: str,
        structure_breaks: List,
        momentum_signals: List,
        trap_signals: List,
        config: ScalpingConfig,
    ) -> List[str]:
        """Check entry conditions for a symbol."""
        conditions = []

        # Condition 1: Structure breakout
        if config.require_structure_break:
            symbol_breaks = [b for b in structure_breaks if hasattr(b, 'symbol') and b.symbol == symbol]
            if symbol_breaks:
                conditions.append("structure_break")

        # Condition 2: Futures momentum
        if config.require_futures_confirm:
            symbol_momentum = [m for m in momentum_signals if m.symbol == symbol]
            strong_momentum = [m for m in symbol_momentum if m.strength >= 0.7]
            if strong_momentum:
                conditions.append("futures_momentum")
            elif symbol_momentum and any(
                getattr(m, "signal_type", "") == "gamma_zone"
                and getattr(m, "strength", 0) >= 0.8
                for m in symbol_momentum
            ):
                # Gamma zone with high strength + structure break = momentum proxy
                if "structure_break" in conditions:
                    conditions.append("futures_momentum")

        # Condition 3: Volume burst
        if config.require_volume_burst:
            volume_signals = [m for m in momentum_signals
                           if m.symbol == symbol and m.signal_type == "volume_spike"]
            if volume_signals:
                conditions.append("volume_burst")
            elif any(
                getattr(m, "signal_type", "") == "gamma_zone"
                and getattr(m, "strength", 0) >= 0.9
                for m in momentum_signals
                if m.symbol == symbol
            ):
                # High-strength gamma zone implies volume activity at ATM strikes
                conditions.append("volume_burst")

        # Condition 4: Trap confirmation (optional)
        if config.require_trap_confirm:
            symbol_traps = [t for t in trap_signals if t.symbol == symbol and t.confidence >= 0.6]
            if symbol_traps:
                conditions.append("trap_confirmed")
        else:
            # Give partial credit if trap signals align
            symbol_traps = [t for t in trap_signals if t.symbol == symbol]
            if symbol_traps:
                conditions.append("trap_alignment")

        return conditions

    def _augment_replay_entry_conditions(
        self,
        signal_payload: Dict[str, Any],
        conditions: List[str],
    ) -> List[str]:
        """Use recorded journal approvals as entry context during historical replay."""
        source = str(signal_payload.get("source", "") or "").strip().lower()
        status = str(signal_payload.get("status", "") or "").strip().upper()
        action = str(signal_payload.get("action", "") or "").strip().lower()
        entry_ready = str(signal_payload.get("entry_ready", "") or "").strip().upper() == "Y"
        selected = str(signal_payload.get("selected", "") or "").strip().upper() == "Y"

        augmented = list(conditions)
        if source != "replay_journal" and status != "APPROVED" and not entry_ready and not selected:
            return augmented

        if status == "APPROVED" or entry_ready:
            augmented.append("historical_signal_approved")
        if action in {"take", "buy", "enter"} or selected:
            augmented.append("historical_entry_ready")

        deduped: List[str] = []
        seen = set()
        for condition in augmented:
            if condition not in seen:
                deduped.append(condition)
                seen.add(condition)
        return deduped

    def _validate_replay_entry_conditions(
        self,
        signal_payload: Dict[str, Any],
        conditions: List[str],
        config: ScalpingConfig,
    ) -> Optional[str]:
        if not bool(getattr(config, "replay_require_market_conditions", True)):
            return None
        if not _is_replay_journal_signal_payload(signal_payload):
            return None

        market_conditions = [condition for condition in conditions if condition not in _HISTORICAL_ENTRY_CONDITIONS]
        if market_conditions:
            return None
        return "Replay journal approval requires at least 1 live market condition"

    async def _create_entry_order(
        self,
        signal: EntrySignal,
        symbol: str,
        config: ScalpingConfig,
        multiplier: float,
        replay_mode: bool,
        metadata: Dict[str, Any],
        current_time: Optional[datetime] = None,
    ) -> Order:
        """Create entry order."""
        import uuid

        idx_config = self._get_index_config(symbol)
        lot_size = idx_config.lot_size if idx_config else 25
        quantity = signal.lots * lot_size
        simulate_order = self.dry_run or replay_mode

        order = Order(
            order_id=f"ENT_{uuid.uuid4().hex[:8]}",
            symbol=signal.symbol,
            strike=signal.strike,
            option_type=signal.direction,
            order_type="market",
            side="buy",
            quantity=quantity,
            price=_round_price_for_symbol(signal.symbol, signal.premium),
            status="pending" if not simulate_order else "simulated",
            reason=f"Entry: {', '.join(signal.conditions_met)}",
            metadata={
                **metadata,
                "multiplier": multiplier,
                "conditions_met": list(signal.conditions_met),
                "confidence": signal.confidence,
                "lots": signal.lots,
            },
        )

        if simulate_order:
            order.fill_price = self._simulate_entry_fill_price(signal.symbol, signal.premium, metadata)
            order.fill_time = current_time or signal.timestamp or datetime.now()
            order.status = "simulated"

        return order

    def _simulate_entry_fill_price(self, symbol: str, premium: float, metadata: Dict[str, Any]) -> float:
        fill_quote = metadata.get("fill_quote", {}) if isinstance(metadata.get("fill_quote"), dict) else {}
        ask = float(fill_quote.get("ask", metadata.get("ask", 0)) or 0)
        bid = float(fill_quote.get("bid", metadata.get("bid", 0)) or 0)
        spread = float(fill_quote.get("spread", metadata.get("spread", 0)) or 0)
        if ask > 0:
            return _round_price_for_symbol(symbol, ask)
        if bid > 0 and spread > 0:
            return _round_price_for_symbol(symbol, bid + spread)
        if spread > 0:
            return _round_price_for_symbol(symbol, premium + (spread / 2))
        return _round_price_for_symbol(symbol, premium)

    def _validate_fill_conditions(
        self,
        signal_payload: Dict[str, Any],
        context: BotContext,
        config: ScalpingConfig,
    ) -> Tuple[bool, List[str], Dict[str, float]]:
        current_quote = self._lookup_current_quote(signal_payload, context)
        if not current_quote:
            return True, [], {}

        premium = float(signal_payload.get("premium", signal_payload.get("entry", 0)) or 0)
        reference_ask = float(signal_payload.get("ask", current_quote.get("ask", 0)) or 0)
        reference_spread_pct = float(signal_payload.get("spread_pct", 0) or 0)
        current_bid = float(current_quote.get("bid", 0) or 0)
        current_ask = float(current_quote.get("ask", 0) or 0)
        current_spread = max(0.0, current_ask - current_bid) if current_bid > 0 and current_ask > 0 else float(current_quote.get("spread", 0) or 0)
        current_spread_pct = (
            (current_spread / max((current_ask + current_bid) / 2, 0.01)) * 100
            if current_bid > 0 and current_ask > 0
            else float(current_quote.get("spread_pct", 0) or 0)
        )
        reference_entry = reference_ask if reference_ask > 0 else premium
        slippage_pct = ((current_ask - premium) / premium * 100) if premium > 0 and current_ask > 0 else 0.0
        ask_drift_pct = ((current_ask - reference_entry) / reference_entry * 100) if reference_entry > 0 and current_ask > 0 else 0.0
        spread_widen_ratio = (current_spread_pct / reference_spread_pct) if reference_spread_pct > 0 else 1.0

        reasons: List[str] = []
        if slippage_pct > config.max_entry_slippage_pct:
            reasons.append(f"Entry slippage too high: {slippage_pct:.2f}%")
        if ask_drift_pct > config.max_bid_ask_drift_pct:
            reasons.append(f"Bid/ask drift too high: {ask_drift_pct:.2f}%")
        if reference_spread_pct > 0 and spread_widen_ratio > config.max_spread_widening_ratio:
            reasons.append(f"Spread widened during entry: {spread_widen_ratio:.2f}x")

        return (not reasons), reasons, {
            "bid": current_bid,
            "ask": current_ask,
            "spread": current_spread,
            "spread_pct": current_spread_pct,
            "slippage_pct": slippage_pct,
            "ask_drift_pct": ask_drift_pct,
        }

    def _lookup_current_quote(self, signal_payload: Dict[str, Any], context: BotContext) -> Optional[Dict[str, float]]:
        symbol = str(signal_payload.get("symbol", ""))
        strike = int(float(signal_payload.get("strike", 0) or 0))
        option_type = str(signal_payload.get("option_type", signal_payload.get("side", "CE"))).upper()
        option_chains = context.data.get("option_chains", {})
        chain = option_chains.get(symbol)
        if not chain:
            return None
        for opt in getattr(chain, "options", []):
            if int(getattr(opt, "strike", 0) or 0) == strike and str(getattr(opt, "option_type", "")).upper() == option_type:
                return {
                    "bid": float(getattr(opt, "bid", 0) or 0),
                    "ask": float(getattr(opt, "ask", 0) or 0),
                    "spread": float(getattr(opt, "spread", 0) or 0),
                    "spread_pct": float(getattr(opt, "spread_pct", 0) or 0),
                }
        return None

    def _symbol_momentum_strength(self, symbol: str, momentum_signals: List) -> float:
        strengths = [float(getattr(m, "strength", 0) or 0) for m in momentum_signals if getattr(m, "symbol", None) == symbol]
        return max(strengths) if strengths else 0.0

    def _get_index_config(self, symbol: str) -> Optional[IndexConfig]:
        """Get index configuration for a symbol."""
        return _resolve_index_config(symbol)

    async def _validate_entry_with_debate(
        self, signal: EntrySignal, symbol: str, context: BotContext, config: ScalpingConfig
    ) -> tuple:
        """
        Validate entry signal with LLM Debate.

        Returns:
            (should_proceed, reason, debate_result)
        """
        if not HAS_DEBATE:
            return True, "Debate not available", None

        # Get market context
        spot_data = context.data.get("spot_data", {})
        spot = spot_data.get(symbol)
        spot_price = spot.ltp if spot else 0

        # Get risk context
        positions = context.data.get("positions", [])
        open_positions = len([p for p in positions if p.status != "closed"])

        # Calculate risk amount
        idx_config = self._get_index_config(symbol)
        lot_size = idx_config.lot_size if idx_config else 25
        risk_amount = signal.lots * lot_size * signal.premium * 0.3  # 30% max loss

        # Get market indicators
        option_chains = context.data.get("option_chains", {})
        chain = option_chains.get(symbol)
        pcr = chain.pcr if chain else 1.0
        vix = context.data.get("vix", 15.0)

        # Get momentum info
        momentum_signals = context.data.get("momentum_signals", [])
        symbol_momentum = [s for s in momentum_signals if s.symbol == symbol]
        momentum = "bullish" if any(s.price_move > 0 for s in symbol_momentum) else "bearish"

        # Get structure info
        structures = context.data.get("market_structure", {})
        structure = structures.get(symbol, {})
        structure_break = structure.get("break") is not None

        try:
            should_proceed, reason, result = await debate_entry_decision(
                index=symbol,
                spot_price=spot_price,
                direction=signal.direction,
                strike=signal.strike,
                option_type=signal.direction,
                premium=signal.premium,
                stop_loss=signal.premium * 0.7,
                target=signal.premium + config.first_target_points,
                risk_amount=risk_amount,
                signal_strength=int(signal.confidence * 100),
                momentum=momentum,
                structure_break=structure_break,
                pcr=pcr,
                vix=vix,
                capital_used_pct=(open_positions / config.max_positions * 100) if config.max_positions > 0 else 0,
                open_positions=open_positions,
            )
            return should_proceed, reason, result
        except Exception as e:
            # On error, allow trade but log it
            return True, f"Debate error: {e}", None


class ExitAgent(BaseBot):
    """
    Agent 9: Exit Agent

    Exit strategy:
    1. Partial exit: Sell 50-60% at +₹3 to +₹5
    2. Move SL to entry (risk-free runner)
    3. Trail runner using candle HL / VWAP
    4. Target ₹8-₹15 for runner (rare: ₹80+)

    Uses LLM Debate for large position exits and borderline decisions.
    """

    BOT_TYPE = "exit"
    REQUIRES_LLM = False  # DISABLED - debate causes latency in execution path

    def __init__(self, dry_run: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.dry_run = dry_run
        self._thesis_support_loss_streaks: Dict[str, int] = {}

    def get_description(self) -> str:
        return "Exit management with partial profit booking (debate-validated)"

    async def execute(self, context: BotContext) -> BotResult:
        """Manage exits for open positions."""
        config = context.data.get("config", ScalpingConfig())
        positions = context.data.get("positions", [])
        option_chains = context.data.get("option_chains", {})
        spot_data = context.data.get("spot_data", {})
        replay_mode = bool(context.data.get("replay_mode"))
        cycle_now = _context_now(context)

        # Batch-prefetch live quotes for all open positions (single API call)
        # Results are cached in context so PositionManager reuses them
        context.data.pop("_position_ltp_cache", None)  # Clear stale cache from prior cycle
        self._position_ltp_cache = _batch_fetch_position_ltp(positions, context.data)

        exit_orders = []
        position_updates = []
        debate_results = []
        active_position_ids = set()

        for pos in positions:
            if pos.status == "closed":
                self._thesis_support_loss_streaks.pop(pos.position_id, None)
                continue
            active_position_ids.add(pos.position_id)

            # Get current price
            current_price = self._get_current_price(pos, option_chains)
            if current_price == 0:
                continue

            # Sanity check: reject obviously bad prices (>80% drop from entry in one tick)
            # This catches stale/corrupt chain data returning delta/spread as LTP
            if current_price < pos.entry_price * 0.20:
                print(f"[ExitAgent] Bad price for {pos.symbol} {pos.strike}{pos.option_type}: "
                      f"got {current_price:.2f}, entry was {pos.entry_price:.2f} — skipping")
                continue

            pos.current_price = current_price
            pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity

            # Check initial SL hit (before partial exit)
            if pos.sl_price > 0 and current_price <= pos.sl_price and not pos.partial_exit_done:
                import uuid as _uuid
                sl_order = Order(
                    order_id=f"SL_{_uuid.uuid4().hex[:8]}",
                    symbol=pos.symbol,
                    strike=pos.strike,
                    option_type=pos.option_type,
                    order_type="market",
                    side="sell",
                    quantity=pos.quantity,
                    price=_round_price_for_symbol(pos.symbol, current_price),
                    status="simulated" if self.dry_run else "pending",
                    reason=f"SL hit: {current_price:.2f} <= {pos.sl_price:.2f}",
                )
                if self.dry_run:
                    sl_order.fill_price = _round_price_for_symbol(pos.symbol, current_price)
                    sl_order.fill_time = cycle_now
                exit_orders.append(sl_order)
                self._simulate_exit_fill(sl_order, option_chains, fill_time=cycle_now)
                position_updates.append({
                    "position_id": pos.position_id,
                    "action": "sl_hit",
                    "qty": sl_order.quantity,
                    "price": current_price,
                })
                continue

            time_stop_order = self._check_time_stop(pos, current_price, config, cycle_now)
            if time_stop_order:
                exit_orders.append(time_stop_order)
                self._simulate_exit_fill(time_stop_order, option_chains, fill_time=cycle_now)
                position_updates.append({
                    "position_id": pos.position_id,
                    "action": "time_stop",
                    "qty": time_stop_order.quantity,
                    "price": current_price,
                })
                continue

            reversal_order = self._check_momentum_reversal_exit(pos, current_price, context)
            if reversal_order:
                exit_orders.append(reversal_order)
                self._simulate_exit_fill(reversal_order, option_chains, fill_time=cycle_now)
                position_updates.append({
                    "position_id": pos.position_id,
                    "action": "momentum_reversal",
                    "qty": reversal_order.quantity,
                    "price": current_price,
                })
                continue

            spread_exit_order = self._check_spread_widening_exit(pos, current_price, option_chains, config)
            if spread_exit_order:
                exit_orders.append(spread_exit_order)
                self._simulate_exit_fill(spread_exit_order, option_chains, fill_time=cycle_now)
                position_updates.append({
                    "position_id": pos.position_id,
                    "action": "spread_exit",
                    "qty": spread_exit_order.quantity,
                    "price": current_price,
                })
                continue

            thesis_exit_order = self._check_thesis_invalidation_exit(pos, current_price, context, config, replay_mode)
            if thesis_exit_order:
                exit_orders.append(thesis_exit_order)
                self._simulate_exit_fill(thesis_exit_order, option_chains, fill_time=cycle_now)
                position_updates.append({
                    "position_id": pos.position_id,
                    "action": "thesis_invalidated",
                    "qty": thesis_exit_order.quantity,
                    "price": current_price,
                })
                continue

            # Check for partial exit
            if not pos.partial_exit_done:
                partial_order = self._check_partial_exit(pos, current_price, config)
                if partial_order:
                    # Validate large exits with debate
                    if abs(pos.unrealized_pnl) > 1000 and HAS_DEBATE:
                        should_exit, reason, result = await self._validate_exit_with_debate(
                            pos, current_price, "partial_exit_target_hit", context
                        )
                        if result:
                            debate_results.append({"position": pos.position_id, "result": reason})
                        if not should_exit:
                            continue  # Skip this exit, debate recommends holding

                    exit_orders.append(partial_order)
                    self._simulate_exit_fill(partial_order, option_chains, fill_time=cycle_now)
                    position_updates.append({
                        "position_id": pos.position_id,
                        "action": "partial_exit",
                        "qty": partial_order.quantity,
                        "price": current_price,
                    })

            # Check runner stop loss
            if pos.partial_exit_done and pos.runner_qty > 0:
                sl_order = self._check_runner_stop(pos, current_price, config)
                if sl_order:
                    exit_orders.append(sl_order)
                    self._simulate_exit_fill(sl_order, option_chains, fill_time=cycle_now)
                    position_updates.append({
                        "position_id": pos.position_id,
                        "action": "runner_stopped",
                        "qty": sl_order.quantity,
                        "price": current_price,
                    })

            # Check runner target
            if pos.runner_qty > 0:
                target_order = self._check_runner_target(pos, current_price, config)
                if target_order:
                    exit_orders.append(target_order)
                    self._simulate_exit_fill(target_order, option_chains, fill_time=cycle_now)
                    position_updates.append({
                        "position_id": pos.position_id,
                        "action": "runner_target_hit",
                        "qty": target_order.quantity,
                        "price": current_price,
                    })

            # Update trail stop
            self._update_trail_stop(pos, current_price, context, config)

        stale_position_ids = set(self._thesis_support_loss_streaks.keys()) - active_position_ids
        for position_id in stale_position_ids:
            self._thesis_support_loss_streaks.pop(position_id, None)

        context.data["exit_orders"] = exit_orders
        context.data["position_updates"] = position_updates
        context.data["exit_debate_results"] = debate_results
        _sync_dashboard_state(context)

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "exit_orders": len(exit_orders),
                "position_updates": position_updates,
                "debate_validated": len(debate_results),
            },
            metrics={
                "exits_triggered": len(exit_orders),
                "debate_validations": len(debate_results),
            },
        )

    def _check_thesis_invalidation_exit(
        self,
        pos: Position,
        current_price: float,
        context: BotContext,
        config: ScalpingConfig,
        replay_mode: bool,
    ) -> Optional[Order]:
        if replay_mode:
            return None

        # Don't invalidate thesis when entry pipeline was skipped (risk blocked,
        # max positions, etc.) — strike_selections will be stale/empty
        if context.data.get("risk_blocked_new_entries") or context.data.get("trade_disabled"):
            return None

        support = self._current_thesis_support(pos, context)
        if support["supported"]:
            self._thesis_support_loss_streaks[pos.position_id] = 0
            return None

        streak = self._thesis_support_loss_streaks.get(pos.position_id, 0) + 1
        self._thesis_support_loss_streaks[pos.position_id] = streak
        required_cycles = max(1, int(getattr(config, "thesis_invalidation_cycles", 3) or 3))
        if streak < required_cycles:
            return None

        import uuid

        missing = ", ".join(support["missing_stages"])
        return Order(
            order_id=f"THESIS_{uuid.uuid4().hex[:8]}",
            symbol=pos.symbol,
            strike=pos.strike,
            option_type=pos.option_type,
            order_type="market",
            side="sell",
            quantity=pos.quantity,
            price=_round_price_for_symbol(pos.symbol, current_price),
            status="pending" if not self.dry_run else "simulated",
            reason=f"Thesis invalidated after {streak} cycles ({missing})",
            metadata={
                "thesis_support": support,
                "thesis_loss_streak": streak,
            },
        )

    def _current_thesis_support(self, pos: Position, context: BotContext) -> Dict[str, Any]:
        strike_supported = _stage_has_signal_support(
            context.data.get("strike_selections", {}),
            pos.symbol,
            pos.strike,
            pos.option_type,
        )
        quality_supported = _stage_has_signal_support(
            context.data.get("quality_filtered_signals", []),
            pos.symbol,
            pos.strike,
            pos.option_type,
        )
        liquidity_supported = _stage_has_signal_support(
            context.data.get("liquidity_filtered_selections", []),
            pos.symbol,
            pos.strike,
            pos.option_type,
        )
        missing = [
            stage_name
            for stage_name, supported in (
                ("strike", strike_supported),
                ("quality", quality_supported),
                ("liquidity", liquidity_supported),
            )
            if not supported
        ]
        return {
            "strike": strike_supported,
            "quality": quality_supported,
            "liquidity": liquidity_supported,
            "supported": bool(strike_supported or quality_supported or liquidity_supported),
            "missing_stages": missing,
        }

    def _simulate_exit_fill(self, order: Order, chains: Dict[str, Any], fill_time: Optional[datetime] = None) -> None:
        if order.status not in {"simulated", "filled"} and not self.dry_run:
            return
        fill_price = order.price
        for chain in chains.values():
            for opt in chain.options:
                if opt.strike == order.strike and opt.option_type == order.option_type:
                    bid = float(getattr(opt, "bid", 0) or 0)
                    ltp = float(getattr(opt, "ltp", 0) or 0)
                    chain_price = bid if bid > 0 else ltp
                    if chain_price > 0:
                        fill_price = chain_price
                    break
        # Fallback to direct broker quote if chain didn't have the strike
        if fill_price <= 0 or fill_price < order.price * 0.20:
            live_ltp = _fetch_live_option_ltp(order.symbol, order.strike, order.option_type)
            if live_ltp > 0:
                fill_price = live_ltp
        order.fill_price = _round_price_for_symbol(order.symbol, fill_price)
        order.fill_time = fill_time or datetime.now()
        order.status = "simulated"

    def _get_current_price(self, pos: Position, chains: Dict) -> float:
        """Get current price — batch cache first, chain second, single quote last."""
        # 1. Check batch-prefetched cache (populated once per cycle)
        cached = getattr(self, "_position_ltp_cache", {})
        if cached.get(pos.position_id, 0) > 0:
            return _round_price_for_symbol(pos.symbol, cached[pos.position_id])
        # 2. Check option chain
        for symbol, chain in chains.items():
            for opt in chain.options:
                if opt.strike == pos.strike and opt.option_type == pos.option_type:
                    ltp = float(getattr(opt, "ltp", 0) or 0)
                    if ltp > 0:
                        return _round_price_for_symbol(pos.symbol, ltp)
        # 3. Single broker quote fallback
        live_ltp = _fetch_live_option_ltp(pos.symbol, pos.strike, pos.option_type)
        if live_ltp > 0:
            return _round_price_for_symbol(pos.symbol, live_ltp)
        return 0

    def _check_partial_exit(
        self, pos: Position, current_price: float, config: ScalpingConfig
    ) -> Optional[Order]:
        """Check if partial exit should be triggered."""
        profit = current_price - pos.entry_price
        target_points = max(pos.target_price - pos.entry_price, config.first_target_points)

        if profit >= target_points:
            import uuid

            partial_qty = int(pos.quantity * config.partial_exit_pct)

            return Order(
                order_id=f"PART_{uuid.uuid4().hex[:8]}",
                symbol=pos.symbol,
                strike=pos.strike,
                option_type=pos.option_type,
                order_type="market",
                side="sell",
                quantity=partial_qty,
                price=_round_price_for_symbol(pos.symbol, current_price),
                status="pending" if not self.dry_run else "simulated",
                reason=f"Partial exit at +₹{profit:.2f}",
            )

        return None

    def _check_time_stop(
        self,
        pos: Position,
        current_price: float,
        config: ScalpingConfig,
        current_time: Optional[datetime] = None,
    ) -> Optional[Order]:
        effective_now = current_time or datetime.now()
        age_minutes = (effective_now - pos.entry_time).total_seconds() / 60 if pos.entry_time else 0
        if age_minutes < config.exit_time_stop_minutes:
            return None
        # Don't time-stop a profitable position — let SL/target/trail handle it
        if current_price >= pos.entry_price:
            return None

        import uuid

        return Order(
            order_id=f"TIME_{uuid.uuid4().hex[:8]}",
            symbol=pos.symbol,
            strike=pos.strike,
            option_type=pos.option_type,
            order_type="market",
            side="sell",
            quantity=pos.quantity,
            price=_round_price_for_symbol(pos.symbol, current_price),
            status="pending" if not self.dry_run else "simulated",
            reason=f"Time stop after {age_minutes:.1f}m (price {current_price:.2f} < entry {pos.entry_price:.2f})",
        )

    def _check_momentum_reversal_exit(
        self, pos: Position, current_price: float, context: BotContext
    ) -> Optional[Order]:
        # Only consider reversal exits for futures_surge signals (not gamma_zone)
        # and only when there's a genuine directional move against the position
        momentum_signals = context.data.get("momentum_signals", [])
        symbol_momentum = [m for m in momentum_signals if getattr(m, "symbol", None) == pos.symbol]
        reversal = False
        for signal in symbol_momentum:
            signal_type = str(getattr(signal, "signal_type", "")).lower()
            if signal_type != "futures_surge":
                continue
            price_move = float(getattr(signal, "price_move", 0) or 0)
            strength = float(getattr(signal, "strength", 0) or 0)
            if strength < 0.8:
                continue
            if pos.option_type == "CE" and price_move < -30:
                reversal = True
            if pos.option_type == "PE" and price_move > 30:
                reversal = True

        if not reversal:
            return None

        import uuid

        return Order(
            order_id=f"REV_{uuid.uuid4().hex[:8]}",
            symbol=pos.symbol,
            strike=pos.strike,
            option_type=pos.option_type,
            order_type="market",
            side="sell",
            quantity=pos.quantity,
            price=_round_price_for_symbol(pos.symbol, current_price),
            status="pending" if not self.dry_run else "simulated",
            reason="Momentum reversal exit",
        )

    def _check_spread_widening_exit(
        self, pos: Position, current_price: float, chains: Dict[str, Any], config: ScalpingConfig
    ) -> Optional[Order]:
        quote = self._find_option_quote(pos, chains)
        if quote is None:
            return None
        spread_pct = float(getattr(quote, "spread_pct", 0) or 0)
        if spread_pct < config.exit_spread_widening_pct:
            return None

        import uuid

        return Order(
            order_id=f"SPD_{uuid.uuid4().hex[:8]}",
            symbol=pos.symbol,
            strike=pos.strike,
            option_type=pos.option_type,
            order_type="market",
            side="sell",
            quantity=pos.quantity,
            price=_round_price_for_symbol(pos.symbol, current_price),
            status="pending" if not self.dry_run else "simulated",
            reason=f"Spread widening exit ({spread_pct:.1f}%)",
        )

    def _check_runner_stop(
        self, pos: Position, current_price: float, config: ScalpingConfig
    ) -> Optional[Order]:
        """Check if runner stop loss hit."""
        if pos.trail_stop > 0 and current_price <= pos.trail_stop:
            import uuid

            return Order(
                order_id=f"SL_{uuid.uuid4().hex[:8]}",
                symbol=pos.symbol,
                strike=pos.strike,
                option_type=pos.option_type,
                order_type="market",
                side="sell",
                quantity=pos.runner_qty,
                price=_round_price_for_symbol(pos.symbol, current_price),
                status="pending" if not self.dry_run else "simulated",
                reason=f"Runner SL hit at ₹{current_price:.2f}",
            )

        return None

    def _check_runner_target(
        self, pos: Position, current_price: float, config: ScalpingConfig
    ) -> Optional[Order]:
        """Check if runner target hit."""
        profit = current_price - pos.entry_price
        runner_target = max(config.runner_target_max, (pos.target_price - pos.entry_price) * 2)

        # Check for moon shot (rare explosive move)
        if profit >= config.runner_moon_target:
            import uuid

            return Order(
                order_id=f"MOON_{uuid.uuid4().hex[:8]}",
                symbol=pos.symbol,
                strike=pos.strike,
                option_type=pos.option_type,
                order_type="market",
                side="sell",
                quantity=pos.runner_qty,
                price=_round_price_for_symbol(pos.symbol, current_price),
                status="pending" if not self.dry_run else "simulated",
                reason=f"Moon shot exit at +₹{profit:.2f}!",
            )

        # Normal runner target
        if profit >= runner_target:
            import uuid

            return Order(
                order_id=f"TGT_{uuid.uuid4().hex[:8]}",
                symbol=pos.symbol,
                strike=pos.strike,
                option_type=pos.option_type,
                order_type="market",
                side="sell",
                quantity=pos.runner_qty,
                price=_round_price_for_symbol(pos.symbol, current_price),
                status="pending" if not self.dry_run else "simulated",
                reason=f"Runner target at +₹{profit:.2f}",
            )

        return None

    def _update_trail_stop(
        self,
        pos: Position,
        current_price: float,
        context: BotContext,
        config: ScalpingConfig,
    ):
        """Update trailing stop for runner."""
        if not pos.partial_exit_done or pos.runner_qty == 0:
            profit = current_price - pos.entry_price
            if profit >= config.profit_lock_trigger_points:
                locked_stop = pos.entry_price + config.profit_lock_buffer_points
                pos.trail_stop = _round_price_for_symbol(pos.symbol, max(pos.trail_stop, locked_stop, pos.sl_price))
            return

        if config.trail_method == "candle_hl":
            # Trail using previous candle low
            candles = context.data.get(f"candles_{pos.symbol}", [])
            if candles and len(candles) >= 2:
                prev_low = candles[-2].get("low", 0)
                if prev_low > pos.trail_stop:
                    pos.trail_stop = _round_price_for_symbol(pos.symbol, prev_low)

        elif config.trail_method == "vwap":
            # Trail using VWAP
            spot_data = context.data.get("spot_data", {})
            spot = spot_data.get(pos.symbol)
            if spot and hasattr(spot, 'vwap'):
                vwap_stop = spot.vwap - 20  # 20 points below VWAP
                if vwap_stop > pos.trail_stop:
                    pos.trail_stop = _round_price_for_symbol(pos.symbol, vwap_stop)

        else:  # ATR-based
            # Simple ATR trailing
            profit = current_price - pos.entry_price
            if profit > 5:
                new_stop = pos.entry_price + (profit * 0.5)
                if new_stop > pos.trail_stop:
                    pos.trail_stop = _round_price_for_symbol(pos.symbol, new_stop)

        profit = current_price - pos.entry_price
        if profit >= config.profit_lock_trigger_points:
            locked_stop = pos.entry_price + config.profit_lock_buffer_points
            if locked_stop > pos.trail_stop:
                pos.trail_stop = _round_price_for_symbol(pos.symbol, locked_stop)

    def _find_option_quote(self, pos: Position, chains: Dict[str, Any]) -> Optional[Any]:
        for chain in chains.values():
            for opt in getattr(chain, "options", []):
                if opt.strike == pos.strike and opt.option_type == pos.option_type:
                    return opt
        return None

    async def _validate_exit_with_debate(
        self, pos: Position, current_price: float, exit_reason: str, context: BotContext
    ) -> tuple:
        """
        Validate exit decision with LLM Debate.

        Returns:
            (should_exit, reason, debate_result)
        """
        if not HAS_DEBATE:
            return True, "Debate not available", None

        # Get spot price
        spot_data = context.data.get("spot_data", {})
        index = self._get_index_from_symbol(pos.symbol)
        spot = spot_data.get(index)
        spot_price = spot.ltp if spot else 0

        # Get momentum
        momentum_signals = context.data.get("momentum_signals", [])
        symbol_momentum = [s for s in momentum_signals if s.symbol == index]
        momentum = "neutral"
        if symbol_momentum:
            momentum = "bullish" if any(s.price_move > 0 for s in symbol_momentum) else "bearish"

        # Calculate time in trade
        time_in_trade = str(_context_now(context) - pos.entry_time) if pos.entry_time else "0m"

        try:
            should_exit, reason, result = await debate_exit_decision(
                index=index,
                entry_price=pos.entry_price,
                current_price=current_price,
                spot_price=spot_price,
                unrealized_pnl=pos.unrealized_pnl,
                time_in_trade=time_in_trade,
                exit_reason=exit_reason,
                momentum=momentum,
                volume_spike=any(s.signal_type == "volume_spike" for s in symbol_momentum),
            )
            return should_exit, reason, result
        except Exception as e:
            # On error, proceed with exit
            return True, f"Debate error: {e}", None

    def _get_index_from_symbol(self, symbol: str) -> str:
        """Extract index from option symbol."""
        if "NIFTY" in symbol and "BANK" not in symbol:
            return "NIFTY50"
        elif "BANKNIFTY" in symbol:
            return "BANKNIFTY"
        elif "SENSEX" in symbol:
            return "SENSEX"
        elif "FINNIFTY" in symbol:
            return "FINNIFTY"
        else:
            return symbol


class PositionManagerAgent(BaseBot):
    """
    Agent 10: Position Manager Agent

    Manages:
    - Position tracking
    - P&L calculation
    - Position status updates
    - Trade logging
    """

    BOT_TYPE = "position_manager"
    REQUIRES_LLM = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._positions: Dict[str, Position] = {}
        self._trade_log: List[Dict] = []
        self._trade_records: Dict[str, Dict[str, Any]] = {}
        self._processed_order_ids: set = set()

    def get_description(self) -> str:
        return "Position tracking and P&L management"

    async def execute(self, context: BotContext) -> BotResult:
        """Update and manage positions."""
        config = context.data.get("config", ScalpingConfig())
        pending_orders = context.data.get("pending_orders", [])
        exit_orders = context.data.get("exit_orders", [])
        position_updates = context.data.get("position_updates", [])
        option_chains = context.data.get("option_chains", {})

        # Process new entry orders (skip already-processed ones)
        processed_order_ids = {pos.position_id for pos in self._positions.values()}
        for order in pending_orders:
            if order.status in ["filled", "simulated"] and order.order_id not in self._processed_order_ids:
                self._create_position(order, context)
                self._processed_order_ids.add(order.order_id)

        # Process exit orders (skip already-processed ones)
        for order in exit_orders:
            if order.status in ["filled", "simulated"] and order.order_id not in self._processed_order_ids:
                self._process_exit(order, position_updates)
                self._processed_order_ids.add(order.order_id)

        # Update all positions
        total_unrealized, total_realized = self._refresh_context_state(context, config)

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                "open_positions": len([p for p in self._positions.values() if p.status != "closed"]),
                "total_unrealized_pnl": round(total_unrealized, 2),
                "total_realized_pnl": round(total_realized, 2),
            },
            metrics={
                "positions_count": len(self._positions),
                "unrealized_pnl": total_unrealized,
                "realized_pnl": total_realized,
            },
        )

    def _refresh_context_state(self, context: BotContext, config: ScalpingConfig) -> Tuple[float, float]:
        option_chains = context.data.get("option_chains", {})
        # Use batch LTP cache populated by ExitAgent earlier in this cycle
        self._context_ltp_cache = context.data.get("_position_ltp_cache", {})
        total_unrealized = 0
        total_realized = 0

        for pos_id, pos in self._positions.items():
            if pos.status != "closed":
                current_price = self._get_current_price(pos, option_chains)
                if current_price <= 0 or current_price < pos.entry_price * 0.20:
                    current_price = pos.current_price or pos.entry_price
                pos.current_price = current_price
                pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity
                total_unrealized += pos.unrealized_pnl
                self._sync_trade_record_from_position(pos)

            total_realized += pos.realized_pnl

        # Store positions in context
        context.data["positions"] = list(self._positions.values())
        context.data["executed_trades"] = list(self._trade_records.values())
        context.data["recent_trades"] = self._trade_log[-50:]
        capital_state = self._build_capital_state(config, total_unrealized)
        context.data["capital_state"] = capital_state
        context.data["daily_pnl"] = capital_state["daily_pnl"]
        context.data["initial_capital"] = config.total_capital
        context.data["learning_feedback"] = self._build_learning_feedback()
        _sync_dashboard_state(context)
        return total_unrealized, total_realized

    def flatten_open_positions(self, context: BotContext, reason: str = "Replay completed") -> List[Order]:
        option_chains = context.data.get("option_chains", {})
        flatten_time = _context_now(context)
        flatten_orders: List[Order] = []

        for pos in list(self._positions.values()):
            if pos.status == "closed":
                continue
            current_price = self._get_current_price(pos, option_chains)
            if current_price <= 0:
                current_price = pos.current_price or pos.entry_price
            order = Order(
                order_id=f"FLAT_{pos.position_id}",
                symbol=pos.symbol,
                strike=pos.strike,
                option_type=pos.option_type,
                order_type="market",
                side="sell",
                quantity=pos.quantity,
                price=_round_price_for_symbol(pos.symbol, current_price),
                status="simulated",
                reason=reason,
                metadata={"flatten_reason": reason},
            )
            fill_price = current_price
            for chain in option_chains.values():
                for opt in getattr(chain, "options", []):
                    if opt.strike == pos.strike and opt.option_type == pos.option_type:
                        fill_price = float(getattr(opt, "bid", 0) or getattr(opt, "ltp", 0) or current_price)
                        break
            order.fill_price = _round_price_for_symbol(pos.symbol, fill_price)
            order.fill_time = flatten_time
            flatten_orders.append(order)
            self._process_exit(order, updates=[])

        if flatten_orders:
            self._refresh_context_state(context, context.data.get("config", ScalpingConfig()))
        return flatten_orders

    def _create_position(self, order: Order, context: BotContext):
        """Create a new position from an entry order."""
        import uuid

        # Get lot size
        config = context.data.get("config", ScalpingConfig())
        idx_config = _resolve_index_config(order.symbol)
        lot_size = idx_config.lot_size if idx_config else 25
        lots = order.quantity // lot_size
        entry_price = _round_price_for_symbol(order.symbol, order.fill_price or order.price)
        # Use signal's pre-calculated SL/target if available; otherwise fallback
        signal_sl = float(order.metadata.get("sl", 0) or 0)
        signal_target = float(order.metadata.get("t1", 0) or 0)
        stop_scale = float(order.metadata.get("stop_scale", 1.0) or 1.0)
        if signal_sl > 0:
            sl_price = _round_price_for_symbol(order.symbol, signal_sl)
        else:
            sl_distance_multiplier = 0.25 * max(0.5, min(1.0, stop_scale))
            sl_price = _round_price_for_symbol(order.symbol, entry_price * (1.0 - sl_distance_multiplier))
        if signal_target > 0:
            target_price = _round_price_for_symbol(order.symbol, signal_target)
        else:
            target_offset = max(config.first_target_points, entry_price * 0.35)
            target_price = _round_price_for_symbol(
                order.symbol,
                entry_price + (target_offset * float(order.metadata.get("target_scale", 1.0) or 1.0)),
            )

        position = Position(
            position_id=f"POS_{uuid.uuid4().hex[:8]}",
            symbol=order.symbol,
            strike=order.strike,
            option_type=order.option_type,
            entry_price=entry_price,
            entry_time=order.fill_time or _context_now(context),
            quantity=order.quantity,
            lots=lots,
            lot_size=lot_size,
            direction="long",
            status="open",
            expiry=str(order.metadata.get("expiry", "")),
            sl_price=sl_price,
            target_price=target_price,
        )

        self._positions[position.position_id] = position
        timestamp = _normalize_timestamp(order.fill_time or _context_now(context))
        trade_record = {
            "trade_id": position.position_id,
            "symbol": order.symbol,
            "index": _index_name(order.symbol),
            "strike": order.strike,
            "option_type": order.option_type,
            "direction": position.direction,
            "entry_time": timestamp,
            "entry_price": position.entry_price,
            "quantity": order.quantity,
            "lots": lots,
            "status": "open",
            "exit_time": None,
            "exit_price": None,
            "initial_sl": position.sl_price,
            "current_sl": position.sl_price,
            "sl_moves": [],
            "partial_exits": [],
            "remaining_qty": order.quantity,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "pnl_pct": 0.0,
            "entry_signals": dict(order.metadata),
            "regime": order.metadata.get("regime", "unknown"),
            "entry_conditions": list(order.metadata.get("conditions_met", [])),
            "entry_condition_count": int(order.metadata.get("condition_count", 0) or 0),
            "spread_pct": float(order.metadata.get("spread_pct", 0) or 0),
            "momentum_strength": float(order.metadata.get("momentum_strength", 0) or 0),
            "outcome": "open",
            "decision_packet": self._build_entry_decision_packet(order, context, position, timestamp),
            "agent_decisions": [],
        }
        trade_record["agent_decisions"].append(
            {
                "phase": "entry",
                "timestamp": timestamp,
                "reason": order.reason,
                "decision_packet": dict(trade_record["decision_packet"]),
            }
        )
        self._trade_records[position.position_id] = trade_record

        # Log trade
        self._trade_log.append({
            "type": "entry",
            "position_id": position.position_id,
            "symbol": order.symbol,
            "strike": order.strike,
            "option_type": order.option_type,
            "price": position.entry_price,
            "quantity": order.quantity,
            "timestamp": timestamp,
            "pnl": 0.0,
        })

    def _process_exit(self, order: Order, updates: List[Dict]):
        """Process an exit order."""
        # Find the matching position
        for pos in self._positions.values():
            if (pos.strike == order.strike and
                pos.option_type == order.option_type and
                pos.status != "closed"):

                exit_price = _round_price_for_symbol(pos.symbol, order.fill_price or order.price)
                pnl = (exit_price - pos.entry_price) * order.quantity

                # Check if partial or full exit
                if "PART" in order.order_id:
                    pos.partial_exit_done = True
                    pos.partial_exit_qty = order.quantity
                    pos.runner_qty = pos.quantity - order.quantity
                    pos.quantity = pos.runner_qty
                    pos.realized_pnl += pnl

                    # Move SL to entry
                    pos.trail_stop = pos.entry_price
                    trade = self._trade_records.get(pos.position_id)
                    if trade is not None:
                        trade["status"] = "partial"
                        trade["remaining_qty"] = pos.runner_qty
                        trade["realized_pnl"] = pos.realized_pnl
                        trade["current_sl"] = pos.entry_price
                        trade["partial_exits"].append(
                            {
                                "time": _normalize_timestamp(order.fill_time or datetime.now()),
                                "quantity": order.quantity,
                                "price": exit_price,
                                "pnl": pnl,
                            }
                        )
                        trade["sl_moves"].append(
                            {
                                "time": _normalize_timestamp(order.fill_time or datetime.now()),
                                "old_sl": pos.sl_price,
                                "new_sl": pos.entry_price,
                                "reason": "move_to_entry_after_partial",
                            }
                        )
                        trade.setdefault("agent_decisions", []).append(
                            self._build_exit_decision_packet(order, pos, exit_price, pnl, partial=True)
                        )

                else:
                    pos.realized_pnl += pnl
                    pos.status = "closed"
                    trade = self._trade_records.get(pos.position_id)
                    if trade is not None:
                        trade["status"] = "closed"
                        trade["exit_price"] = exit_price
                        trade["exit_time"] = _normalize_timestamp(order.fill_time or datetime.now())
                        trade["remaining_qty"] = 0
                        trade["realized_pnl"] = pos.realized_pnl
                        entry_value = max(trade["entry_price"] * max(trade["quantity"], 1), 1e-9)
                        trade["pnl_pct"] = (trade["realized_pnl"] / entry_value) * 100
                        trade["outcome"] = "win" if trade["realized_pnl"] > 0 else "loss" if trade["realized_pnl"] < 0 else "flat"
                        trade.setdefault("agent_decisions", []).append(
                            self._build_exit_decision_packet(order, pos, exit_price, pnl, partial=False)
                        )

                # Log trade
                self._trade_log.append({
                    "type": "exit",
                    "position_id": pos.position_id,
                    "order_type": order.order_id.split("_")[0],
                    "price": exit_price,
                    "quantity": order.quantity,
                    "pnl": pnl,
                    "timestamp": _normalize_timestamp(order.fill_time or datetime.now()),
                })

                break

    def _sync_trade_record_from_position(self, pos: Position) -> None:
        trade = self._trade_records.get(pos.position_id)
        if trade is None:
            return
        trade["current_price"] = pos.current_price
        trade["unrealized_pnl"] = pos.unrealized_pnl
        entry = float(trade.get("entry_price", 0) or 0)
        trade["pnl_pct"] = round(((pos.current_price - entry) / entry) * 100, 2) if entry > 0 and pos.current_price > 0 else 0.0
        trade["current_sl"] = pos.trail_stop or pos.sl_price
        trade["target_price"] = pos.target_price
        trade["remaining_qty"] = pos.quantity if pos.status != "closed" else 0
        trade["status"] = "partial" if pos.partial_exit_done and pos.status != "closed" else pos.status

    def _build_entry_decision_packet(
        self,
        order: Order,
        context: BotContext,
        position: Position,
        timestamp: str,
    ) -> Dict[str, Any]:
        symbol = order.symbol
        regime_info = (
            dict((context.data.get("market_regimes", {}) or {}).get(symbol, {}) or {})
            if isinstance(context.data.get("market_regimes", {}), dict)
            else {}
        )
        structure_info = (
            dict((context.data.get("market_structure", {}) or {}).get(symbol, {}) or {})
            if isinstance(context.data.get("market_structure", {}), dict)
            else {}
        )
        stage_support = {
            "strike": _stage_has_signal_support(context.data.get("strike_selections", {}), symbol, order.strike, order.option_type),
            "quality": _stage_has_signal_support(context.data.get("quality_filtered_signals", []), symbol, order.strike, order.option_type),
            "liquidity": _stage_has_signal_support(context.data.get("liquidity_filtered_selections", []), symbol, order.strike, order.option_type),
        }
        return {
            "phase": "entry",
            "signal_key": _signal_key(symbol, order.strike, order.option_type),
            "timestamp": timestamp,
            "cycle_timestamp": context.data.get("cycle_timestamp"),
            "engine_mode": context.data.get("engine_mode", "unknown"),
            "index": _index_name(symbol),
            "symbol": symbol,
            "strike": order.strike,
            "option_type": order.option_type,
            "entry_price": position.entry_price,
            "initial_sl": position.sl_price,
            "initial_target": position.target_price,
            "conditions_met": list(order.metadata.get("conditions_met", [])),
            "condition_count": int(order.metadata.get("condition_count", 0) or 0),
            "quality_grade": order.metadata.get("quality_grade"),
            "quality_score": float(order.metadata.get("quality_score", 0) or 0),
            "confidence": float(order.metadata.get("confidence", 0) or 0),
            "setup_tag": order.metadata.get("setup_tag"),
            "strict_filter_pass": bool(order.metadata.get("strict_filter_pass")),
            "setup_reasons": list(order.metadata.get("setup_reasons", []) or []),
            "rr_ratio": float(order.metadata.get("rr_ratio", 0) or 0),
            "regime": regime_info,
            "structure": structure_info,
            "stage_support": stage_support,
            "spread_pct": float(order.metadata.get("spread_pct", 0) or 0),
            "momentum_strength": float(order.metadata.get("momentum_strength", 0) or 0),
            "timeframe_alignment": dict(order.metadata.get("timeframe_alignment", {}) or {}),
            "micro_momentum": dict(order.metadata.get("micro_momentum", {}) or {}),
            "entry_trigger": dict(order.metadata.get("entry_trigger", {}) or {}),
            "entry_confirmation": dict(order.metadata.get("entry_confirmation", {}) or {}),
            "queue_risk": dict(order.metadata.get("queue_risk", {}) or {}),
            "liquidity_vacuum": dict(order.metadata.get("liquidity_vacuum", {}) or {}),
            "volatility_burst": dict(order.metadata.get("volatility_burst", {}) or {}),
            "fill_quote": dict(order.metadata.get("fill_quote", {}) or {}),
            "raw_signal": dict(order.metadata),
        }

    def _build_exit_decision_packet(
        self,
        order: Order,
        pos: Position,
        exit_price: float,
        pnl: float,
        *,
        partial: bool,
    ) -> Dict[str, Any]:
        metadata = dict(order.metadata or {})
        return {
            "phase": "exit",
            "timestamp": _normalize_timestamp(order.fill_time or datetime.now()),
            "order_id": order.order_id,
            "reason": order.reason,
            "partial": partial,
            "symbol": pos.symbol,
            "strike": pos.strike,
            "option_type": pos.option_type,
            "exit_price": exit_price,
            "quantity": order.quantity,
            "pnl": pnl,
            "metadata": metadata,
        }

    def _build_capital_state(self, config: ScalpingConfig, total_unrealized: float) -> Dict[str, float]:
        realized = sum(float(trade.get("realized_pnl", 0) or 0) for trade in self._trade_records.values())
        used_capital = sum(
            pos.entry_price * pos.quantity
            for pos in self._positions.values()
            if pos.status != "closed"
        )
        total_pnl = realized + total_unrealized
        available_capital = config.total_capital + realized - used_capital
        return {
            "initial_capital": config.total_capital,
            "available_capital": available_capital,
            "used_capital": used_capital,
            "realized_pnl": realized,
            "unrealized_pnl": total_unrealized,
            "total_pnl": total_pnl,
            "daily_pnl": total_pnl,
            "daily_loss_limit": config.total_capital * (config.daily_loss_limit_pct / 100),
            "risk_per_trade": config.total_capital * (config.risk_per_trade_pct / 100),
            "risk_used_pct": (used_capital / config.total_capital * 100) if config.total_capital > 0 else 0,
        }

    def _build_learning_feedback(self) -> Dict[str, Any]:
        closed = [trade for trade in self._trade_records.values() if trade.get("status") == "closed"]
        recent = closed[-50:]
        if not recent:
            return {"closed_trades": 0, "adaptive_weights": {}}

        wins = [trade for trade in recent if float(trade.get("realized_pnl", 0) or 0) > 0]
        losses = [trade for trade in recent if float(trade.get("realized_pnl", 0) or 0) < 0]

        def _avg(rows: List[Dict[str, Any]], field: str) -> float:
            values = [float(row.get(field, 0) or 0) for row in rows]
            return sum(values) / len(values) if values else 0.0

        adaptive_weights: Dict[str, float] = {}
        if _avg(losses, "spread_pct") > _avg(wins, "spread_pct"):
            adaptive_weights["liquidity"] = 1.15
        if _avg(wins, "momentum_strength") > _avg(losses, "momentum_strength"):
            adaptive_weights["momentum"] = 1.10
        if _avg(losses, "entry_condition_count") < _avg(wins, "entry_condition_count"):
            adaptive_weights["confidence"] = 1.05
            adaptive_weights["risk"] = 1.05

        return {
            "closed_trades": len(recent),
            "wins": len(wins),
            "losses": len(losses),
            "average_spread_pct": _avg(recent, "spread_pct"),
            "average_momentum_strength": _avg(recent, "momentum_strength"),
            "adaptive_weights": adaptive_weights,
            "recent_trade_features": [
                {
                    "trade_id": trade.get("trade_id"),
                    "regime": trade.get("regime", "unknown"),
                    "entry_conditions": trade.get("entry_conditions", []),
                    "spread_pct": trade.get("spread_pct", 0),
                    "momentum_strength": trade.get("momentum_strength", 0),
                    "outcome": trade.get("outcome", "open"),
                }
                for trade in recent[-20:]
            ],
        }

    def _get_current_price(self, pos: Position, chains: Dict) -> float:
        """Get current price — batch cache first, chain second, single quote last."""
        # 1. Check batch-prefetched cache (populated by ExitAgent earlier in cycle)
        cached = self._context_ltp_cache
        if cached.get(pos.position_id, 0) > 0:
            return _round_price_for_symbol(pos.symbol, cached[pos.position_id])
        # 2. Check option chain
        for symbol, chain in chains.items():
            for opt in chain.options:
                if opt.strike == pos.strike and opt.option_type == pos.option_type:
                    ltp = float(getattr(opt, "ltp", 0) or 0)
                    if ltp > 0:
                        return _round_price_for_symbol(pos.symbol, ltp)
        # 3. Single broker quote fallback
        live_ltp = _fetch_live_option_ltp(pos.symbol, pos.strike, pos.option_type)
        if live_ltp > 0:
            return _round_price_for_symbol(pos.symbol, live_ltp)
        return pos.current_price


class RiskGuardianAgent(BaseBot):
    """
    Agent 11: Risk Guardian Agent

    Enforces:
    - Risk per trade ≤5% capital
    - Daily loss limit ≤10%
    - Disable during low liquidity
    - Disable during high spread
    - Trading hours enforcement
    """

    BOT_TYPE = "risk_guardian"
    REQUIRES_LLM = False  # DISABLED - debate causes latency in execution path

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._daily_pnl = 0
        self._trades_today = 0
        self._start_capital = 0

    def get_description(self) -> str:
        return "Capital protection and risk management"

    async def execute(self, context: BotContext) -> BotResult:
        """Check all risk parameters."""
        config = context.data.get("config", ScalpingConfig())
        positions = context.data.get("positions", [])
        option_chains = context.data.get("option_chains", {})
        pending_orders = context.data.get("pending_orders", [])
        replay_mode = bool(context.data.get("replay_mode"))
        recent_trades = context.data.get("recent_trades", [])

        if self._start_capital == 0:
            self._start_capital = config.total_capital

        risk_checks = []
        breaches = []
        blocked_orders = []

        # Calculate current P&L
        realized_pnl = sum(p.realized_pnl for p in positions)
        unrealized_pnl = sum(p.unrealized_pnl for p in positions if p.status != "closed")
        total_pnl = realized_pnl + unrealized_pnl

        # Check 1: Daily loss limit
        daily_loss_pct = -total_pnl / config.total_capital * 100 if total_pnl < 0 else 0
        daily_check = {
            "check": "daily_loss",
            "current": daily_loss_pct,
            "limit": config.daily_loss_limit_pct,
            "passed": daily_loss_pct < config.daily_loss_limit_pct,
        }
        risk_checks.append(daily_check)
        if not daily_check["passed"]:
            breaches.append(f"Daily loss limit: {daily_loss_pct:.1f}% > {config.daily_loss_limit_pct}%")

        # Check 2: Risk per trade
        for order in pending_orders:
            trade_risk = order.quantity * order.price * 0.3  # Assume 30% max loss
            trade_risk_pct = trade_risk / config.total_capital * 100

            if trade_risk_pct > config.risk_per_trade_pct:
                breaches.append(f"Trade risk too high: {trade_risk_pct:.1f}%")
                blocked_orders.append(order.order_id)

        # Check 3: Spread check — strict on expiry, lenient on non-expiry
        from datetime import date as _date
        _is_expiry = _date.today().weekday() in (3, 4)
        # On non-expiry, only block if 80%+ options are wide (whole chain is illiquid)
        # On expiry, block if 50%+ are wide (standard threshold)
        _spread_block_ratio = 0.5 if _is_expiry else 0.8
        for symbol, chain in option_chains.items():
            wide_spreads = [opt for opt in chain.options if opt.spread > config.max_spread_to_trade]
            if len(wide_spreads) > len(chain.options) * _spread_block_ratio:
                if config.disable_high_spread:
                    breaches.append(f"{symbol}: Wide spreads detected")

        # Check 4: Trading hours
        cycle_timestamp = context.data.get("cycle_timestamp")
        if isinstance(cycle_timestamp, str):
            try:
                now = datetime.fromisoformat(cycle_timestamp)
            except ValueError:
                now = datetime.now()
        else:
            now = datetime.now()
        trading_start = datetime.strptime(config.trading_hours[0], "%H:%M").time()
        trading_end = datetime.strptime(config.trading_hours[1], "%H:%M").time()

        if not replay_mode and not (trading_start <= now.time() <= trading_end):
            breaches.append("Outside trading hours")

        # Check 5: No-trade zones
        if not replay_mode:
            for zone_start, zone_end in config.no_trade_zones:
                zone_s = datetime.strptime(zone_start, "%H:%M").time()
                zone_e = datetime.strptime(zone_end, "%H:%M").time()
                if zone_s <= now.time() <= zone_e:
                    breaches.append(f"In no-trade zone: {zone_start}-{zone_end}")

        # Check 6: Max positions
        open_positions = len([p for p in positions if p.status != "closed"])
        if open_positions >= config.max_positions:
            breaches.append(f"Max positions reached: {open_positions}/{config.max_positions}")

        # Check 7: Max exposure per symbol/index
        exposure_by_symbol: Dict[str, float] = {}
        total_capital = max(config.total_capital, 1.0)
        for pos in positions:
            if pos.status == "closed":
                continue
            exposure_by_symbol[pos.symbol] = exposure_by_symbol.get(pos.symbol, 0.0) + (pos.entry_price * pos.quantity)
        for symbol, exposure in exposure_by_symbol.items():
            exposure_pct = exposure / total_capital * 100
            if exposure_pct > config.max_symbol_exposure_pct:
                breaches.append(f"Exposure too high on {symbol}: {exposure_pct:.1f}%")

        # Check 8: Consecutive losses pause
        consecutive_losses = 0
        exit_pnls = [float(trade.get("pnl", 0) or 0) for trade in recent_trades if trade.get("type") == "exit"]
        for pnl in reversed(exit_pnls):
            if pnl < 0:
                consecutive_losses += 1
            else:
                break
        if consecutive_losses >= config.max_consecutive_losses:
            breaches.append(f"Consecutive losses pause: {consecutive_losses}")

        context.data["risk_checks"] = risk_checks
        context.data["risk_breaches"] = breaches
        context.data["blocked_orders"] = blocked_orders

        # Per-index breach tracking: only disable trading for indices with breaches,
        # not the entire pipeline.  Global trade_disabled only set for non-index-specific
        # breaches (consecutive losses, daily loss limit, etc.).
        index_breaches = set()
        global_breaches = []
        for b in breaches:
            # Index-specific breaches contain the symbol (e.g. "NSE:NIFTYBANK-INDEX: Wide spreads")
            matched_idx = False
            for symbol, chain in option_chains.items():
                if symbol in b:
                    index_breaches.add(symbol)
                    matched_idx = True
                    break
            if not matched_idx:
                global_breaches.append(b)

        context.data["risk_blocked_indices"] = list(index_breaches)
        context.data["trade_disabled"] = bool(context.data.get("trade_disabled") or global_breaches)
        if global_breaches:
            context.data["trade_disabled_reason"] = "; ".join(global_breaches[:3])
        elif index_breaches and not context.data.get("trade_disabled"):
            context.data["trade_disabled_reason"] = f"Partial: {', '.join(index_breaches)} blocked"

        # Use LLM Debate for risk validation on pending orders
        if pending_orders and HAS_DEBATE:
            total_risk = sum(o.quantity * o.price * 0.3 for o in pending_orders)
            correlation_risk = "high" if len([p for p in positions if p.status != "closed"]) > 2 else "low"

            is_allowed, reason, debate_result = await self._validate_risk_with_debate(
                config=config,
                used_capital=sum(p.entry_price * p.quantity for p in positions if p.status != "closed"),
                daily_pnl=total_pnl,
                proposed_action=f"Execute {len(pending_orders)} new orders",
                risk_amount=total_risk,
                open_positions=open_positions,
                correlation_risk=correlation_risk,
            )
            context.data["risk_debate"] = {
                "allowed": is_allowed,
                "reason": reason,
                "result": debate_result.__dict__ if debate_result and hasattr(debate_result, '__dict__') else None
            }
            if not is_allowed:
                breaches.append(f"Debate rejected: {reason}")
                blocked_orders.extend([o.order_id for o in pending_orders])

        # Emit risk event if breaches
        if breaches:
            await self._emit_event("risk_breach", {
                "breaches": breaches,
                "action": "halt" if len(breaches) >= 2 else "caution",
            })

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.BLOCKED if len(breaches) >= 2 else BotStatus.SUCCESS,
            output={
                "risk_checks": risk_checks,
                "breaches": breaches,
                "blocked_orders": blocked_orders,
                "trading_allowed": len(breaches) < 2,
            },
            metrics={
                "daily_pnl": total_pnl,
                "daily_pnl_pct": total_pnl / config.total_capital * 100,
                "breach_count": len(breaches),
            },
            warnings=breaches,
        )

    async def _validate_risk_with_debate(
        self,
        config: ScalpingConfig,
        used_capital: float,
        daily_pnl: float,
        proposed_action: str,
        risk_amount: float,
        open_positions: int,
        correlation_risk: str = "low",
    ) -> tuple:
        """
        Validate risk with LLM Debate.

        Returns:
            (is_allowed, reason, debate_result)
        """
        if not HAS_DEBATE:
            return True, "Debate not available", None

        try:
            is_allowed, reason, result = await debate_risk_check(
                capital=config.total_capital,
                used_capital=used_capital,
                daily_pnl=daily_pnl,
                daily_loss_limit=config.total_capital * config.daily_loss_limit_pct / 100,
                proposed_action=proposed_action,
                risk_amount=risk_amount,
                open_positions=open_positions,
                correlation_risk=correlation_risk,
                concentration_risk="high" if open_positions >= config.max_positions - 1 else "low",
            )
            return is_allowed, reason, result
        except Exception as e:
            # On error, apply basic risk rules
            if risk_amount > config.total_capital * config.risk_per_trade_pct / 100:
                return False, f"Risk too high (error: {e})", None
            return True, f"Debate error: {e}, basic checks passed", None

"""
Data Layer Agents - Real-time market data feeds.

Agents:
1. DataFeedAgent - Spot price, VWAP, volume
2. OptionChainAgent - Option chain with Greeks, OI, volume
3. FuturesAgent - Futures price, basis, momentum

Uses shared MarketDataAdapter for data with cross-process caching.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
from typing import Any, Dict, List, Optional
import re
import sys
import os
from pathlib import Path

# Use local base module
from ..base import BaseBot, BotContext, BotResult, BotStatus

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Try to use shared MarketDataAdapter (preferred)
try:
    from shared_project_engine.market.adapter import MarketDataAdapter
    HAS_ADAPTER = True
except ImportError:
    HAS_ADAPTER = False
    MarketDataAdapter = None

# Fallback to legacy fyers_data
try:
    from integrations.fyers_data import FyersDataProvider, get_fyers_data
    HAS_FYERS = True
except ImportError:
    HAS_FYERS = False

try:
    from shared_project_engine.auth import FyersClient
    HAS_CLIENT = True
except ImportError:
    HAS_CLIENT = False

try:
    from shared_project_engine.indices.config import canonicalize_index_name
    HAS_INDEX_CONFIG = True
except ImportError:
    HAS_INDEX_CONFIG = False

# Singleton adapter instance
_market_adapter: Optional["MarketDataAdapter"] = None


def _coerce_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            pass
    return datetime.now()


def _replay_payload(context: BotContext) -> Optional[Dict[str, Any]]:
    if not context.data.get("replay_mode"):
        return None
    payload = context.data.get("replay_payload")
    return payload if isinstance(payload, dict) else None


def _mark_data_source(obj: Any, source: str) -> Any:
    try:
        setattr(obj, "_data_source", source)
    except Exception:
        pass
    return obj


def _data_source(obj: Any, default: str = "unknown") -> str:
    return str(getattr(obj, "_data_source", default))


def _infer_strike_from_symbol(symbol: str, spot_price: float = 0.0) -> int:
    if not symbol:
        return 0
    match = re.search(r"(\\d+)(CE|PE)$", str(symbol))
    if not match:
        return 0
    digits = match.group(1)
    candidates: List[int] = []
    for width in (4, 5, 6):
        if len(digits) >= width:
            try:
                candidates.append(int(digits[-width:]))
            except ValueError:
                continue
    if not candidates:
        return 0
    if spot_price and spot_price > 0:
        return min(candidates, key=lambda value: abs(value - spot_price))
    return candidates[-1]


def _infer_index_name(spot_symbol: str) -> str:
    if not spot_symbol:
        return ""
    core = spot_symbol.split(":")[-1]
    core = core.split("-")[0]
    if HAS_INDEX_CONFIG:
        try:
            return canonicalize_index_name(core)
        except Exception:
            pass
    if core == "NIFTYBANK":
        return "BANKNIFTY"
    return core


def _normalize_depth_levels(raw_levels: Any, fallback_price: float, fallback_qty: int, descending: bool) -> List[Dict[str, float]]:
    levels: List[Dict[str, float]] = []
    if isinstance(raw_levels, list):
        for item in raw_levels[:5]:
            if isinstance(item, dict):
                price = float(item.get("price", item.get("p", fallback_price)) or fallback_price)
                qty = float(item.get("qty", item.get("quantity", item.get("q", fallback_qty))) or fallback_qty)
                levels.append({"price": price, "qty": qty})
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                levels.append({"price": float(item[0]), "qty": float(item[1])})

    if levels:
        return levels[:5]

    step = max(fallback_price * 0.001, 0.05)
    synthetic_levels = []
    for idx in range(5):
        price = fallback_price - (idx * step) if descending else fallback_price + (idx * step)
        qty = max(1.0, float(fallback_qty) * max(0.35, 1.0 - (idx * 0.15)))
        synthetic_levels.append({"price": round(price, 2), "qty": round(qty, 2)})
    return synthetic_levels


def _build_depth_snapshot(
    raw_opt: Any,
    bid: float,
    ask: float,
    bid_qty: int,
    ask_qty: int,
) -> Dict[str, Any]:
    bid_levels = []
    ask_levels = []
    if isinstance(raw_opt, dict):
        depth = raw_opt.get("depth", {})
        bid_levels = raw_opt.get("bids") or raw_opt.get("bidDepth") or depth.get("bids") or depth.get("buy") or []
        ask_levels = raw_opt.get("asks") or raw_opt.get("askDepth") or depth.get("asks") or depth.get("sell") or []

    top_bid_levels = _normalize_depth_levels(bid_levels, bid, bid_qty, descending=True)
    top_ask_levels = _normalize_depth_levels(ask_levels, ask, ask_qty, descending=False)

    total_bid_qty = sum(level["qty"] for level in top_bid_levels)
    total_ask_qty = sum(level["qty"] for level in top_ask_levels)
    total_depth = total_bid_qty + total_ask_qty
    imbalance = ((total_bid_qty - total_ask_qty) / total_depth) if total_depth > 0 else 0.0
    pressure = "BUY" if imbalance > 0.1 else "SELL" if imbalance < -0.1 else "neutral"

    return {
        "top_bid_levels": top_bid_levels,
        "top_ask_levels": top_ask_levels,
        "order_book_imbalance": imbalance,
        "order_book_pressure": pressure,
    }


class _DataQualityMixin:
    """Shared data-quality bookkeeping for data layer agents."""

    def _update_data_quality(self, context: BotContext, layer: str, sources: Dict[str, str]) -> None:
        data_sources = context.data.get("data_sources", {})
        if not isinstance(data_sources, dict):
            data_sources = {}
        data_sources[layer] = dict(sources)
        context.data["data_sources"] = data_sources

        live_sources = [source for source in sources.values() if source and source != "replay"]
        synthetic_used = any(source == "sample" for source in live_sources)
        if synthetic_used:
            context.data["synthetic_fallback_used"] = True
            if not context.data.get("replay_mode"):
                context.data["trade_disabled"] = True
                context.data["trade_disabled_reason"] = "synthetic_fallback_data"


def get_market_adapter() -> Optional["MarketDataAdapter"]:
    """Get or create the shared market adapter instance."""
    global _market_adapter
    if _market_adapter is not None:
        return _market_adapter

    if not HAS_ADAPTER:
        return None

    try:
        # Use env file from project root
        env_file = os.environ.get("AUTH_ENV_FILE")
        if not env_file:
            # Try to find it relative to scalping module
            # Path: engines/scalping/scalping/agents/data_agents.py -> project root
            current = Path(__file__).resolve().parent
            # Go up until we find .env file
            for _ in range(6):  # Max 6 levels
                candidate = current / ".env"
                if candidate.exists():
                    env_file = str(candidate)
                    break
                current = current.parent
            else:
                # Fallback to hardcoded path
                env_file = str(Path(__file__).parent.parent.parent.parent.parent / ".env")

        _market_adapter = MarketDataAdapter(env_file=env_file)
        return _market_adapter
    except Exception as e:
        print(f"[DataAgents] Failed to create MarketDataAdapter: {e}")
        return None


@dataclass
class SpotData:
    """Real-time spot data."""
    symbol: str
    ltp: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: int
    vwap: float
    change_pct: float
    timestamp: datetime


@dataclass
class OptionData:
    """Single option contract data."""
    symbol: str
    strike: int
    option_type: str  # CE or PE
    ltp: float
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    volume: int
    oi: int
    oi_change: int
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float
    spread: float
    spread_pct: float
    expiry: str = ""
    top_bid_levels: List[Dict[str, float]] = field(default_factory=list)
    top_ask_levels: List[Dict[str, float]] = field(default_factory=list)
    order_book_imbalance: float = 0.0
    order_book_pressure: str = "neutral"


@dataclass
class OptionChainData:
    """Complete option chain snapshot."""
    underlying: str
    spot_price: float
    atm_strike: int
    pcr: float  # Put-Call Ratio
    max_pain: int
    total_ce_oi: int
    total_pe_oi: int
    options: List[OptionData]
    timestamp: datetime


@dataclass
class FuturesData:
    """Futures data with basis."""
    symbol: str
    ltp: float
    open: float
    high: float
    low: float
    volume: int
    oi: int
    spot_price: float
    basis: float  # Futures - Spot
    basis_pct: float
    timestamp: datetime


class DataFeedAgent(_DataQualityMixin, BaseBot):
    """
    Agent 1: Data Feed Agent

    Provides real-time spot data with:
    - LTP, OHLC, Volume
    - VWAP calculation
    - Price change tracking
    """

    BOT_TYPE = "data_feed"
    REQUIRES_LLM = False

    def __init__(self, symbols: List[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.symbols = symbols or [
            "NSE:NIFTY50-INDEX",
            "NSE:NIFTYBANK-INDEX",
            "BSE:SENSEX-INDEX",
        ]
        self._vwap_data: Dict[str, List] = {}
        self._replay_candles: Dict[str, List[Dict[str, Any]]] = {}
        self._last_tick_seen: Dict[str, datetime] = {}
        self._last_tick_signature: Dict[str, tuple] = {}

    def get_description(self) -> str:
        return f"Real-time spot data for {len(self.symbols)} indices"

    async def execute(self, context: BotContext) -> BotResult:
        """Fetch spot data for all symbols."""
        replay_payload = _replay_payload(context)
        if replay_payload is not None:
            replay_spot = self._build_replay_spot_data(replay_payload)
            if not replay_spot:
                context.data["spot_data"] = {}
                return BotResult(
                    bot_id=self.bot_id,
                    bot_type=self.BOT_TYPE,
                    status=BotStatus.SKIPPED,
                    output={"message": "Replay payload missing spot data"},
                    warnings=["Replay payload missing spot data"],
                )

            context.data["spot_data"] = replay_spot
            context.data["vix"] = replay_payload.get("vix", context.data.get("vix", 15.0))
            self._update_candle_context(context, replay_spot)
            self._update_tick_heartbeat(context, replay_spot)
            self._update_data_quality(context, "spot", {symbol: "replay" for symbol in replay_spot})

            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SUCCESS,
                output=replay_spot,
                metrics={
                    "symbols_fetched": len(replay_spot),
                    "replay_mode": True,
                },
            )

        spot_data = {}
        spot_sources: Dict[str, str] = {}

        for symbol in self.symbols:
            data = await self._fetch_spot(symbol)
            if data:
                spot_data[symbol] = data
                spot_sources[symbol] = _data_source(data)

        if not spot_data:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.FAILED,
                errors=["No spot data available"],
            )

        # Store in context for other agents
        context.data["spot_data"] = spot_data
        self._update_candle_context(context, spot_data)
        self._update_tick_heartbeat(context, spot_data)
        self._update_data_quality(context, "spot", spot_sources)

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output=spot_data,
            metrics={
                "symbols_fetched": len(spot_data),
            },
        )

    def _build_replay_spot_data(self, payload: Dict[str, Any]) -> Dict[str, SpotData]:
        replay_spot = payload.get("spot_data", {})
        if not isinstance(replay_spot, dict):
            return {}

        spot_data: Dict[str, SpotData] = {}
        for symbol, row in replay_spot.items():
            if not isinstance(row, dict):
                continue
            timestamp = _coerce_timestamp(row.get("timestamp"))
            ltp = float(row.get("ltp", 0) or 0)
            if ltp <= 0:
                continue
            prev_close = float(row.get("prev_close", ltp) or ltp)
            change_pct = float(row.get("change_pct", 0) or 0)
            if not change_pct and prev_close > 0:
                change_pct = ((ltp - prev_close) / prev_close) * 100
            spot_data[symbol] = _mark_data_source(SpotData(
                symbol=symbol,
                ltp=ltp,
                open=float(row.get("open", ltp) or ltp),
                high=float(row.get("high", ltp) or ltp),
                low=float(row.get("low", ltp) or ltp),
                prev_close=prev_close,
                volume=int(float(row.get("volume", 0) or 0)),
                vwap=float(row.get("vwap", ltp) or ltp),
                change_pct=change_pct,
                timestamp=timestamp,
            ), "replay")
        return spot_data

    def _update_candle_context(self, context: BotContext, spot_data: Dict[str, SpotData]) -> None:
        candle_map = context.data.get("candles_1m", {})
        if not isinstance(candle_map, dict):
            candle_map = {}

        for symbol, spot in spot_data.items():
            timestamp = _coerce_timestamp(getattr(spot, "timestamp", None))
            minute_key = timestamp.replace(second=0, microsecond=0)
            candles = self._replay_candles.setdefault(symbol, [])
            source = _data_source(spot)
            current_price = float(getattr(spot, "ltp", 0) or 0)

            if not candles or candles[-1]["timestamp"] != minute_key.isoformat():
                if source == "replay":
                    candle_high = max(
                        current_price,
                        float(getattr(spot, "high", current_price) or current_price),
                    )
                    candle_low = min(
                        current_price,
                        float(getattr(spot, "low", current_price) or current_price),
                    )
                    candle_open = float(getattr(spot, "open", current_price) or current_price)
                else:
                    # Live quote snapshots expose session high/low, not minute-bar highs/lows.
                    # Build intraminute candles from observed LTPs so structure stays meaningful.
                    candle_open = current_price
                    candle_high = current_price
                    candle_low = current_price
                candles.append(
                    {
                        "open": candle_open,
                        "high": candle_high,
                        "low": candle_low,
                        "close": current_price,
                        "timestamp": minute_key.isoformat(),
                    }
                )
            else:
                if source == "replay":
                    high_candidate = max(
                        current_price,
                        float(getattr(spot, "high", current_price) or current_price),
                    )
                    low_candidate = min(
                        current_price,
                        float(getattr(spot, "low", current_price) or current_price),
                    )
                else:
                    high_candidate = current_price
                    low_candidate = current_price

                candles[-1]["high"] = max(candles[-1]["high"], high_candidate)
                candles[-1]["low"] = min(candles[-1]["low"], low_candidate)
                candles[-1]["close"] = current_price

            candles[:] = candles[-120:]
            candle_map[symbol] = list(candles)
            context.data[f"candles_{symbol}"] = list(candles)

        context.data["candles_1m"] = candle_map

    def _update_tick_heartbeat(self, context: BotContext, spot_data: Dict[str, SpotData]) -> None:
        heartbeat = context.data.get("tick_heartbeat", {})
        if not isinstance(heartbeat, dict):
            heartbeat = {}

        now = datetime.now()
        for symbol, spot in spot_data.items():
            signature = (
                round(float(getattr(spot, "ltp", 0) or 0), 4),
                round(float(getattr(spot, "high", 0) or 0), 4),
                round(float(getattr(spot, "low", 0) or 0), 4),
                int(getattr(spot, "volume", 0) or 0),
            )
            if self._last_tick_signature.get(symbol) != signature:
                self._last_tick_signature[symbol] = signature
                self._last_tick_seen[symbol] = now

            last_tick = self._last_tick_seen.get(symbol, now)
            heartbeat[symbol] = {
                "last_tick": last_tick.isoformat(),
                "age_seconds": max(0.0, (now - last_tick).total_seconds()),
                "source": _data_source(spot),
            }

        context.data["tick_heartbeat"] = heartbeat

    async def _fetch_spot(self, symbol: str) -> Optional[SpotData]:
        """Fetch spot data for a symbol using shared MarketDataAdapter."""
        # Try shared MarketDataAdapter first (preferred)
        adapter = get_market_adapter()
        if adapter:
            try:
                quote = adapter.get_quote(symbol)
                ltp = float(quote.get("ltp", 0))

                if ltp > 0:
                    prev_close = float(quote.get("prev_close", ltp))
                    change_pct = ((ltp - prev_close) / prev_close * 100) if prev_close > 0 else 0
                    volume = int(quote.get("volume", 0))
                    vwap = self._calculate_vwap(symbol, ltp, volume)

                    return _mark_data_source(SpotData(
                        symbol=symbol,
                        ltp=ltp,
                        open=float(quote.get("open", ltp)),
                        high=float(quote.get("high", ltp)),
                        low=float(quote.get("low", ltp)),
                        prev_close=prev_close,
                        volume=volume,
                        vwap=vwap,
                        change_pct=change_pct,
                        timestamp=datetime.now(),
                    ), "adapter")
            except Exception as e:
                print(f"[DataFeedAgent] Adapter error for {symbol}: {e}")

        # Fallback to legacy FyersDataProvider
        if HAS_FYERS:
            try:
                provider = get_fyers_data()
                quote = provider.get_quote(symbol)

                if quote:
                    vwap = self._calculate_vwap(symbol, quote.ltp, quote.volume)
                    return _mark_data_source(SpotData(
                        symbol=symbol,
                        ltp=quote.ltp,
                        open=quote.open,
                        high=quote.high,
                        low=quote.low,
                        prev_close=quote.prev_close,
                        volume=quote.volume,
                        vwap=vwap,
                        change_pct=quote.change_pct,
                        timestamp=datetime.now(),
                    ), "fyers")
            except Exception:
                pass

        # Final fallback: sample data for testing
        return self._generate_sample_spot(symbol)

    def _calculate_vwap(self, symbol: str, price: float, volume: int) -> float:
        """Calculate running VWAP."""
        if symbol not in self._vwap_data:
            self._vwap_data[symbol] = []

        self._vwap_data[symbol].append((price, volume))

        # Keep last 100 ticks
        if len(self._vwap_data[symbol]) > 100:
            self._vwap_data[symbol] = self._vwap_data[symbol][-100:]

        total_pv = sum(p * v for p, v in self._vwap_data[symbol])
        total_v = sum(v for _, v in self._vwap_data[symbol])

        return total_pv / total_v if total_v > 0 else price

    def _generate_sample_spot(self, symbol: str) -> SpotData:
        """Generate sample data for testing."""
        import random

        base_prices = {
            "NSE:NIFTY50-INDEX": 22500,
            "NSE:NIFTYBANK-INDEX": 48000,
            "BSE:SENSEX-INDEX": 74000,
        }
        base = base_prices.get(symbol, 22500)
        ltp = base + random.gauss(0, base * 0.005)

        return _mark_data_source(SpotData(
            symbol=symbol,
            ltp=ltp,
            open=ltp - random.uniform(-50, 50),
            high=ltp + random.uniform(0, 100),
            low=ltp - random.uniform(0, 100),
            prev_close=ltp - random.uniform(-100, 100),
            volume=random.randint(1000000, 5000000),
            vwap=ltp - random.uniform(-20, 20),
            change_pct=random.uniform(-1, 1),
            timestamp=datetime.now(),
        ), "sample")


class OptionChainAgent(_DataQualityMixin, BaseBot):
    """
    Agent 2: Option Chain Agent

    Provides complete option chain with:
    - All strikes with Greeks
    - OI, Volume, Bid/Ask
    - PCR calculation
    - Max Pain calculation
    """

    BOT_TYPE = "option_chain"
    REQUIRES_LLM = False

    def __init__(self, symbols: List[str] = None, strike_count: int = 25, **kwargs):
        super().__init__(**kwargs)
        self.symbols = symbols or ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"]
        self.strike_count = strike_count

    def get_description(self) -> str:
        return f"Option chain data for {len(self.symbols)} indices"

    async def execute(self, context: BotContext) -> BotResult:
        """Fetch option chains for all symbols."""
        replay_payload = _replay_payload(context)
        if replay_payload is not None:
            chains = self._build_replay_chains(replay_payload, context)
            if not chains:
                context.data["option_chains"] = {}
                return BotResult(
                    bot_id=self.bot_id,
                    bot_type=self.BOT_TYPE,
                    status=BotStatus.SKIPPED,
                    output={"message": "Replay payload missing option data"},
                    warnings=["Replay payload missing option data"],
                )

            context.data["option_chains"] = chains
            self._update_data_quality(context, "option_chain", {symbol: "replay" for symbol in chains})
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SUCCESS,
                output={
                    symbol: {
                        "atm": chain.atm_strike,
                        "pcr": chain.pcr,
                        "max_pain": chain.max_pain,
                        "options_count": len(chain.options),
                        "pressure": self._summarize_order_book_pressure(chain),
                    }
                    for symbol, chain in chains.items()
                },
                metrics={
                    "chains_fetched": len(chains),
                    "replay_mode": True,
                },
            )

        chains = {}
        chain_sources: Dict[str, str] = {}
        spot_data = context.data.get("spot_data", {})

        for symbol in self.symbols:
            spot = spot_data.get(symbol)
            spot_price = spot.ltp if spot else 22500

            chain = await self._fetch_chain(symbol, spot_price)
            if chain:
                chains[symbol] = chain
                chain_sources[symbol] = _data_source(chain)

        if not chains:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.FAILED,
                errors=["No option chain data"],
            )

        context.data["option_chains"] = chains
        self._update_data_quality(context, "option_chain", chain_sources)

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                symbol: {
                    "atm": chain.atm_strike,
                    "pcr": chain.pcr,
                    "max_pain": chain.max_pain,
                    "options_count": len(chain.options),
                    "pressure": self._summarize_order_book_pressure(chain),
                }
                for symbol, chain in chains.items()
            },
            metrics={
                "chains_fetched": len(chains),
            },
        )

    def _build_replay_chains(
        self,
        payload: Dict[str, Any],
        context: BotContext,
    ) -> Dict[str, OptionChainData]:
        option_rows = payload.get("option_rows", {})
        spot_data = context.data.get("spot_data", {})
        chains: Dict[str, OptionChainData] = {}

        for symbol, rows in option_rows.items():
            if not isinstance(rows, list) or not rows:
                continue

            spot = spot_data.get(symbol)
            spot_price = spot.ltp if spot else float(rows[0].get("spot", 0) or 0)
            timestamp = _coerce_timestamp(rows[0].get("timestamp", payload.get("timestamp")))

            options: List[OptionData] = []
            total_ce_oi = 0
            total_pe_oi = 0
            for row in rows:
                opt_type = str(row.get("side", row.get("option_type", ""))).upper()
                if opt_type not in {"CE", "PE"}:
                    continue
                strike = int(float(row.get("strike", row.get("strike_price", 0)) or 0))
                if strike <= 0:
                    strike = _infer_strike_from_symbol(str(row.get("symbol", "")), spot_price)
                ltp = float(row.get("entry", row.get("ltp", 0)) or 0)
                bid = float(row.get("bid", ltp) or ltp)
                ask = float(row.get("ask", ltp) or ltp)
                spread = max(0.0, ask - bid)
                volume = int(float(row.get("volume", 0) or 0))
                oi = int(float(row.get("oi", 0) or 0))
                depth_snapshot = _build_depth_snapshot(row, bid, ask, max(volume, 1), max(volume, 1))
                if opt_type == "CE":
                    total_ce_oi += oi
                else:
                    total_pe_oi += oi
                options.append(
                    OptionData(
                        symbol=str(row.get("symbol", f"{symbol}{strike}{opt_type}")),
                        strike=strike,
                        option_type=opt_type,
                        ltp=ltp,
                        bid=bid,
                        ask=ask,
                        bid_qty=max(volume, 1),
                        ask_qty=max(volume, 1),
                        volume=volume,
                        oi=oi,
                        oi_change=int(float(row.get("oi_chg", 0) or 0)),
                        delta=float(row.get("delta", 0) or 0),
                        gamma=float(row.get("gamma", 0) or 0),
                        theta=float(row.get("theta", 0) or 0),
                        vega=float(row.get("vega", 0) or 0),
                        iv=float(row.get("iv", 0) or 0),
                        spread=spread,
                        spread_pct=float(row.get("spread_pct", (spread / ltp * 100) if ltp > 0 else 0) or 0),
                        expiry=str(row.get("expiry", row.get("date", "")) or ""),
                        top_bid_levels=depth_snapshot["top_bid_levels"],
                        top_ask_levels=depth_snapshot["top_ask_levels"],
                        order_book_imbalance=depth_snapshot["order_book_imbalance"],
                        order_book_pressure=depth_snapshot["order_book_pressure"],
                    )
                )

            if not options:
                continue

            chains[symbol] = _mark_data_source(OptionChainData(
                underlying=symbol,
                spot_price=spot_price,
                atm_strike=round(spot_price / 100) * 100 if spot_price else 0,
                pcr=(total_pe_oi / total_ce_oi) if total_ce_oi > 0 else 1.0,
                max_pain=round(spot_price / 100) * 100 if spot_price else 0,
                total_ce_oi=total_ce_oi,
                total_pe_oi=total_pe_oi,
                options=options,
                timestamp=timestamp,
            ), "replay")

        return chains

    async def _fetch_chain(self, symbol: str, spot_price: float) -> Optional[OptionChainData]:
        """Fetch option chain for a symbol using shared MarketDataAdapter."""
        # Try shared MarketDataAdapter first
        adapter = get_market_adapter()
        if adapter:
            try:
                chain_data = adapter.get_option_chain_snapshot(symbol, strike_count=self.strike_count)

                # Parse the option chain response
                if chain_data and not chain_data.get("_cache_hit") is None:
                    parsed = self._parse_option_chain(chain_data, symbol, spot_price)
                    if parsed:
                        return _mark_data_source(parsed, "adapter")
            except Exception as e:
                print(f"[OptionChainAgent] Adapter error for {symbol}: {e}")

        # Fallback to direct FyersClient
        if HAS_CLIENT:
            try:
                client = FyersClient()
                result = client.option_chain(symbol, self.strike_count)

                if result.get("success"):
                    parsed = self._parse_option_chain(result.get("data", {}), symbol, spot_price)
                    if parsed:
                        return _mark_data_source(parsed, "fyers")
            except Exception:
                pass

        # Final fallback: sample data
        return self._generate_sample_chain(symbol, spot_price)

    def _parse_option_chain(self, data: Dict[str, Any], symbol: str, spot_price: float) -> Optional[OptionChainData]:
        """Parse option chain response from adapter."""
        try:
            if isinstance(data.get("data"), dict):
                data = data["data"]

            # Handle different response formats
            options_list = data.get("optionsChain", data.get("options", []))
            if not options_list:
                return None

            # Determine strike interval
            if "BANKNIFTY" in symbol:
                strike_interval = 100
            elif "SENSEX" in symbol:
                strike_interval = 100
            else:
                strike_interval = 50

            atm_strike = round(spot_price / strike_interval) * strike_interval

            options = []
            total_ce_oi = 0
            total_pe_oi = 0

            for opt in options_list:
                if not isinstance(opt, dict):
                    continue

                strike = int(opt.get("strikePrice", opt.get("strike", opt.get("strike_price", 0))))
                opt_type = str(opt.get("option_type", opt.get("type", ""))).upper()

                if opt_type not in ("CE", "PE"):
                    # Try to parse from symbol
                    opt_symbol = str(opt.get("symbol", ""))
                    if "CE" in opt_symbol:
                        opt_type = "CE"
                    elif "PE" in opt_symbol:
                        opt_type = "PE"
                    else:
                        continue
                if strike <= 0:
                    strike = _infer_strike_from_symbol(opt.get("symbol", ""), spot_price)

                ltp = float(opt.get("ltp", opt.get("last_price", 0)))
                oi = int(opt.get("oi", opt.get("openInterest", 0)))
                volume = int(opt.get("volume", opt.get("tradedVolume", 0)))

                if opt_type == "CE":
                    total_ce_oi += oi
                else:
                    total_pe_oi += oi

                bid = float(opt.get("bid", opt.get("bidPrice", ltp * 0.99)))
                ask = float(opt.get("ask", opt.get("askPrice", ltp * 1.01)))
                spread = ask - bid
                bid_qty = int(opt.get("bidQty", 0))
                ask_qty = int(opt.get("askQty", 0))
                if bid_qty <= 0 or ask_qty <= 0:
                    proxy_depth = self._estimate_depth_proxy(volume=volume, oi=oi)
                    if bid_qty <= 0:
                        bid_qty = proxy_depth
                    if ask_qty <= 0:
                        ask_qty = proxy_depth
                depth_snapshot = _build_depth_snapshot(opt, bid, ask, bid_qty, ask_qty)

                delta = float(opt.get("delta", 0) or 0)
                gamma = float(opt.get("gamma", 0) or 0)
                if abs(delta) < 1e-6:
                    delta = self._estimate_live_delta(
                        spot_price=spot_price,
                        strike=strike,
                        option_type=opt_type,
                        premium=ltp,
                        atm_strike=atm_strike,
                        strike_interval=strike_interval,
                    )
                if gamma <= 0:
                    gamma = self._estimate_live_gamma(
                        strike=strike,
                        atm_strike=atm_strike,
                        strike_interval=strike_interval,
                    )

                options.append(OptionData(
                    symbol=opt.get("symbol", f"{symbol}{strike}{opt_type}"),
                    strike=strike,
                    option_type=opt_type,
                    ltp=ltp,
                    bid=bid,
                    ask=ask,
                    bid_qty=bid_qty,
                    ask_qty=ask_qty,
                    volume=volume,
                    oi=oi,
                    oi_change=int(opt.get("oiChange", opt.get("changeinOpenInterest", 0))),
                    delta=delta,
                    gamma=gamma,
                    theta=float(opt.get("theta", 0)),
                    vega=float(opt.get("vega", 0)),
                    iv=float(opt.get("iv", opt.get("impliedVolatility", 0))),
                    spread=spread,
                    spread_pct=(spread / ltp * 100) if ltp > 0 else 0,
                    expiry=str(opt.get("expiry", opt.get("expiryDate", "")) or ""),
                    top_bid_levels=depth_snapshot["top_bid_levels"],
                    top_ask_levels=depth_snapshot["top_ask_levels"],
                    order_book_imbalance=depth_snapshot["order_book_imbalance"],
                    order_book_pressure=depth_snapshot["order_book_pressure"],
                ))

            if not options:
                return None

            pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

            return OptionChainData(
                underlying=symbol,
                spot_price=spot_price,
                atm_strike=atm_strike,
                pcr=round(pcr, 3),
                max_pain=atm_strike,  # Would need calculation
                total_ce_oi=total_ce_oi,
                total_pe_oi=total_pe_oi,
                options=options,
                timestamp=datetime.now(),
            )
        except Exception as e:
            print(f"[OptionChainAgent] Parse error: {e}")
            return None

    def _estimate_live_delta(
        self,
        *,
        spot_price: float,
        strike: int,
        option_type: str,
        premium: float,
        atm_strike: int,
        strike_interval: int,
    ) -> float:
        """Approximate delta when the live chain omits Greeks."""
        if spot_price <= 0 or strike_interval <= 0:
            return 0.0

        steps_from_atm = abs(strike - atm_strike) / max(strike_interval, 1)
        premium_anchor = max(spot_price * 0.0015, 1.0)
        premium_hint = min(1.0, max(0.0, premium / premium_anchor))
        delta_abs = 0.06 + (0.34 * math.exp(-steps_from_atm / 8.0)) + (0.08 * premium_hint)

        if strike == atm_strike:
            delta_abs = max(delta_abs, 0.48)

        delta_abs = max(0.05, min(0.6, delta_abs))
        return delta_abs if option_type == "CE" else -delta_abs

    def _estimate_live_gamma(
        self,
        *,
        strike: int,
        atm_strike: int,
        strike_interval: int,
    ) -> float:
        if strike_interval <= 0:
            return 0.0
        steps_from_atm = abs(strike - atm_strike) / max(strike_interval, 1)
        return round(max(0.001, 0.12 * math.exp(-steps_from_atm / 2.5)), 6)

    def _estimate_depth_proxy(self, *, volume: int, oi: int) -> int:
        liquidity_anchor = max(float(volume or 0), float(oi or 0), 1.0)
        proxy = max(100, min(5000, int(round(math.sqrt(liquidity_anchor)))))
        return proxy

    def _generate_sample_chain(self, symbol: str, spot_price: float) -> OptionChainData:
        """Generate sample option chain."""
        import random
        import math

        # Determine strike interval
        if "BANKNIFTY" in symbol:
            strike_interval = 100
        elif "SENSEX" in symbol:
            strike_interval = 100
        else:
            strike_interval = 50

        atm_strike = round(spot_price / strike_interval) * strike_interval

        options = []
        total_ce_oi = 0
        total_pe_oi = 0

        for i in range(-self.strike_count, self.strike_count + 1):
            strike = atm_strike + (i * strike_interval)

            for opt_type in ["CE", "PE"]:
                # Calculate theoretical premium using absolute OTM distance
                # This produces realistic premiums: ~20-25 at 100 OTM, ~10-15 at 200 OTM
                points_otm = abs(strike - spot_price)
                base_premium = max(5, 40 * math.exp(-points_otm / 200))
                premium = base_premium + random.gauss(0, base_premium * 0.1)

                # Calculate Greeks - delta in 0.15-0.25 range for far OTM
                # ATM delta ~0.5, decays with OTM distance
                if opt_type == "CE":
                    delta = 0.5 * math.exp(-points_otm / 400) if strike > spot_price else 0.6
                else:
                    delta = -0.5 * math.exp(-points_otm / 400) if strike < spot_price else -0.6

                oi = random.randint(10000, 500000)
                volume = random.randint(1000, 100000)

                if opt_type == "CE":
                    total_ce_oi += oi
                else:
                    total_pe_oi += oi

                bid = premium - random.uniform(0.1, 0.5)
                ask = premium + random.uniform(0.1, 0.5)
                spread = ask - bid
                bid_qty = random.randint(100, 5000)
                ask_qty = random.randint(100, 5000)
                depth_snapshot = _build_depth_snapshot({}, bid, ask, bid_qty, ask_qty)

                options.append(OptionData(
                    symbol=f"{symbol}{strike}{opt_type}",
                    strike=strike,
                    option_type=opt_type,
                    ltp=premium,
                    bid=bid,
                    ask=ask,
                    bid_qty=bid_qty,
                    ask_qty=ask_qty,
                    volume=volume,
                    oi=oi,
                    oi_change=random.randint(-10000, 10000),
                    delta=delta,
                    gamma=0.01 * math.exp(-points_otm / spot_price * 5),
                    theta=-premium * 0.05,
                    vega=premium * 0.1,
                    iv=0.15 + random.uniform(-0.03, 0.03),
                    spread=spread,
                    spread_pct=(spread / premium * 100) if premium > 0 else 0,
                    expiry="",
                    top_bid_levels=depth_snapshot["top_bid_levels"],
                    top_ask_levels=depth_snapshot["top_ask_levels"],
                    order_book_imbalance=depth_snapshot["order_book_imbalance"],
                    order_book_pressure=depth_snapshot["order_book_pressure"],
                ))

        pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

        return _mark_data_source(OptionChainData(
            underlying=symbol,
            spot_price=spot_price,
            atm_strike=atm_strike,
            pcr=round(pcr, 3),
            max_pain=atm_strike,  # Simplified
            total_ce_oi=total_ce_oi,
            total_pe_oi=total_pe_oi,
            options=options,
            timestamp=datetime.now(),
        ), "sample")

    def _summarize_order_book_pressure(self, chain: OptionChainData) -> Dict[str, Any]:
        imbalances = [float(getattr(opt, "order_book_imbalance", 0) or 0) for opt in chain.options[:10]]
        avg_imbalance = sum(imbalances) / len(imbalances) if imbalances else 0.0
        pressure = "BUY" if avg_imbalance > 0.1 else "SELL" if avg_imbalance < -0.1 else "neutral"
        return {
            "avg_imbalance": round(avg_imbalance, 4),
            "pressure": pressure,
        }


class FuturesAgent(_DataQualityMixin, BaseBot):
    """
    Agent 3: Futures Agent

    Provides futures data with:
    - Price and volume
    - Basis calculation (Futures - Spot)
    - Momentum detection
    """

    BOT_TYPE = "futures"
    REQUIRES_LLM = False

    def __init__(self, symbols: Dict[str, str] = None, **kwargs):
        super().__init__(**kwargs)
        self.symbols = symbols or {
            "NSE:NIFTY50-INDEX": "NSE:NIFTY-FUT",
            "NSE:NIFTYBANK-INDEX": "NSE:BANKNIFTY-FUT",
            "BSE:SENSEX-INDEX": "BSE:SENSEX-FUT",
        }
        self._price_history: Dict[str, List] = {}

    def get_description(self) -> str:
        return f"Futures data for {len(self.symbols)} indices"

    async def execute(self, context: BotContext) -> BotResult:
        """Fetch futures data."""
        replay_payload = _replay_payload(context)
        if replay_payload is not None:
            futures_data = self._build_replay_futures(replay_payload)
            if not futures_data:
                context.data["futures_data"] = {}
                context.data["futures_momentum"] = {}
                return BotResult(
                    bot_id=self.bot_id,
                    bot_type=self.BOT_TYPE,
                    status=BotStatus.SKIPPED,
                    output={"message": "Replay payload missing futures data"},
                    warnings=["Replay payload missing futures data"],
                )

            context.data["futures_data"] = futures_data
            momentum = self._calculate_momentum(futures_data)
            context.data["futures_momentum"] = momentum
            self._update_data_quality(context, "futures", {symbol: "replay" for symbol in futures_data})
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SUCCESS,
                output={
                    symbol: {
                        "ltp": data.ltp,
                        "basis": data.basis,
                        "basis_pct": data.basis_pct,
                    }
                    for symbol, data in futures_data.items()
                },
                metrics={
                    "futures_fetched": len(futures_data),
                    "replay_mode": True,
                },
            )

        futures_data = {}
        futures_sources: Dict[str, str] = {}
        spot_data = context.data.get("spot_data", {})

        for spot_symbol, fut_symbol in self.symbols.items():
            spot = spot_data.get(spot_symbol)
            spot_price = spot.ltp if spot else 22500

            index_name = _infer_index_name(spot_symbol)
            data = await self._fetch_futures(index_name, fut_symbol, spot_price)
            if data:
                futures_data[spot_symbol] = data
                futures_sources[spot_symbol] = _data_source(data)

        if not futures_data:
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.FAILED,
                errors=["No futures data"],
            )

        context.data["futures_data"] = futures_data
        self._update_data_quality(context, "futures", futures_sources)

        # Calculate momentum
        momentum = self._calculate_momentum(futures_data)
        context.data["futures_momentum"] = momentum

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={
                symbol: {
                    "ltp": data.ltp,
                    "basis": data.basis,
                    "basis_pct": data.basis_pct,
                }
                for symbol, data in futures_data.items()
            },
            metrics={
                "futures_fetched": len(futures_data),
            },
        )

    def _build_replay_futures(self, payload: Dict[str, Any]) -> Dict[str, FuturesData]:
        replay_futures = payload.get("futures_data", {})
        if not isinstance(replay_futures, dict):
            return {}

        futures_data: Dict[str, FuturesData] = {}
        for symbol, row in replay_futures.items():
            if not isinstance(row, dict):
                continue
            ltp = float(row.get("ltp", 0) or 0)
            spot_price = float(row.get("spot_price", 0) or 0)
            if ltp <= 0:
                continue
            futures_data[symbol] = _mark_data_source(FuturesData(
                symbol=str(row.get("symbol", symbol)),
                ltp=ltp,
                open=float(row.get("open", ltp) or ltp),
                high=float(row.get("high", ltp) or ltp),
                low=float(row.get("low", ltp) or ltp),
                volume=int(float(row.get("volume", 0) or 0)),
                oi=int(float(row.get("oi", 0) or 0)),
                spot_price=spot_price,
                basis=float(row.get("basis", ltp - spot_price) or 0),
                basis_pct=float(row.get("basis_pct", ((ltp - spot_price) / spot_price * 100) if spot_price > 0 else 0) or 0),
                timestamp=_coerce_timestamp(row.get("timestamp", payload.get("timestamp"))),
            ), "replay")
        return futures_data

    async def _fetch_futures(self, index_name: str, symbol: str, spot_price: float) -> Optional[FuturesData]:
        """Fetch futures data using shared MarketDataAdapter."""
        # Try shared MarketDataAdapter first
        adapter = get_market_adapter()
        if adapter:
            try:
                future_symbol = ""
                ltp = 0.0
                if index_name:
                    future_symbol, ltp = adapter.resolve_future_quote(index_name)
                if ltp <= 0 and symbol:
                    future_symbol = symbol
                    ltp = adapter.get_quote_ltp(symbol)
                if ltp > 0:
                    quote = adapter.get_quote(future_symbol) if future_symbol else {}
                    basis = ltp - spot_price
                    return _mark_data_source(FuturesData(
                        symbol=future_symbol or symbol,
                        ltp=ltp,
                        open=float(quote.get("open", ltp)),
                        high=float(quote.get("high", ltp)),
                        low=float(quote.get("low", ltp)),
                        volume=int(quote.get("volume", 0)),
                        oi=int(quote.get("oi", 0) or 0),  # Would need separate call
                        spot_price=spot_price,
                        basis=basis,
                        basis_pct=(basis / spot_price * 100) if spot_price > 0 else 0,
                        timestamp=datetime.now(),
                    ), "adapter")
            except Exception as e:
                print(f"[FuturesAgent] Adapter error for {symbol}: {e}")

        # Fallback to legacy FyersDataProvider
        if HAS_FYERS:
            try:
                provider = get_fyers_data()
                future_symbol = symbol
                quote = provider.get_quote(future_symbol) if future_symbol else None

                if quote and getattr(quote, "ltp", 0) > 0:
                    basis = quote.ltp - spot_price
                    return _mark_data_source(FuturesData(
                        symbol=future_symbol,
                        ltp=quote.ltp,
                        open=quote.open,
                        high=quote.high,
                        low=quote.low,
                        volume=quote.volume,
                        oi=0,
                        spot_price=spot_price,
                        basis=basis,
                        basis_pct=(basis / spot_price * 100) if spot_price > 0 else 0,
                        timestamp=datetime.now(),
                    ), "fyers")
            except Exception:
                pass

        # No valid futures quote
        return None

    def _generate_sample_futures(self, symbol: str, spot_price: float) -> FuturesData:
        """Generate sample futures data."""
        import random

        # Futures typically trade at a premium
        premium = spot_price * random.uniform(0.0005, 0.002)
        ltp = spot_price + premium

        return _mark_data_source(FuturesData(
            symbol=symbol,
            ltp=ltp,
            open=ltp - random.uniform(-30, 30),
            high=ltp + random.uniform(0, 50),
            low=ltp - random.uniform(0, 50),
            volume=random.randint(100000, 500000),
            oi=random.randint(1000000, 5000000),
            spot_price=spot_price,
            basis=premium,
            basis_pct=(premium / spot_price * 100),
            timestamp=datetime.now(),
        ), "sample")

    def _calculate_momentum(self, futures_data: Dict[str, FuturesData]) -> Dict[str, Dict]:
        """Calculate momentum for each futures contract."""
        momentum = {}

        for symbol, data in futures_data.items():
            # Track price history
            if symbol not in self._price_history:
                self._price_history[symbol] = []

            self._price_history[symbol].append({
                "price": data.ltp,
                "time": datetime.now(),
            })

            # Keep last 60 ticks (1 minute at 1 tick/sec)
            if len(self._price_history[symbol]) > 60:
                self._price_history[symbol] = self._price_history[symbol][-60:]

            history = self._price_history[symbol]

            if len(history) >= 10:
                # Calculate momentum over last 10 ticks
                price_change = history[-1]["price"] - history[-10]["price"]
                time_delta = (history[-1]["time"] - history[-10]["time"]).total_seconds()

                momentum[symbol] = {
                    "price_change": price_change,
                    "time_seconds": time_delta,
                    "momentum_per_sec": price_change / time_delta if time_delta > 0 else 0,
                    "is_strong": abs(price_change) > 20,  # Strong if >20 points
                }
            else:
                momentum[symbol] = {
                    "price_change": 0,
                    "time_seconds": 0,
                    "momentum_per_sec": 0,
                    "is_strong": False,
                }

        return momentum

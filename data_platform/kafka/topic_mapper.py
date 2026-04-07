"""
Topic mapping for ClawWorker data platform.

── Market data topics ───────────────────────────────────────────────────────
Index ticks:    market.<index>.ticks          e.g. market.nifty50.ticks
Index candles:  market.<index>.candles.<res>  e.g. market.nifty50.candles.1m
Stock ticks:    market.<index>.<exchange>.<symbol>.ticks
Stock candles:  market.<index>.<exchange>.<symbol>.candles.<res>

── Trading topics (per strategy) ────────────────────────────────────────────
Signals:   trading.<strategy>.signals.raw
           trading.<strategy>.signals.validated
           trading.<strategy>.signals.sized
           trading.<strategy>.signals.risk_validated

Orders:    trading.<strategy>.orders.commands
           trading.<strategy>.orders.events

Positions: trading.<strategy>.positions.events
Execution: trading.<strategy>.execution.events

── System topics ─────────────────────────────────────────────────────────────
system.heartbeat
system.audit.logs

All <...> segments are lower-cased and stripped of special characters so
topic names stay Kafka-safe (letters, digits, dots, underscores, hyphens).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_UNSAFE = re.compile(r"[^a-z0-9._\-]")


def _safe(segment: str) -> str:
    return _UNSAFE.sub("_", segment.lower().strip())


# ── Supported strategies ──────────────────────────────────────────────────────
STRATEGY_SCALPING = "scalping"
STRATEGY_FYERSNIFTY = "fyersn7"
STRATEGY_CLAWWORK = "clawwork"

ALL_STRATEGIES = (STRATEGY_SCALPING, STRATEGY_FYERSNIFTY, STRATEGY_CLAWWORK)


@dataclass(frozen=True)
class KafkaTopicMapper:
    # Market data — raw
    quotes: str = "market.quotes"
    option_chain: str = "market.optionchain"
    vix: str = "market.vix"
    futures: str = "market.futures"

    # Market data — validated / rejected
    validated_prefix: str = "market.validated"
    rejected: str = "market.rejected"
    validated_ticks: str = "market.validated.ticks"
    validated_candles: str = "market.validated.candles"
    validated_candles_3m: str = "market.validated.candles.3m"
    validated_candles_5m: str = "market.validated.candles.5m"
    validated_candles_15m: str = "market.validated.candles.15m"
    validated_candles_30m: str = "market.validated.candles.30m"
    validated_options: str = "market.validated.options"
    rejected_ticks: str = "market.rejected.ticks"
    rejected_candles: str = "market.rejected.candles"
    rejected_options: str = "market.rejected.options"

    # System
    heartbeat: str = "system.heartbeat"
    audit_logs: str = "system.audit.logs"

    @classmethod
    def from_topics_dict(cls, topics: dict[str, str]) -> "KafkaTopicMapper":
        return cls(
            quotes=topics.get("market_quotes", "market.quotes"),
            option_chain=topics.get("market_option_chain", "market.optionchain"),
            vix=topics.get("market_vix", "market.vix"),
            futures=topics.get("market_futures", "market.futures"),
            validated_prefix=topics.get("market_validated", "market.validated"),
            rejected=topics.get("market_rejected", "market.rejected"),
            validated_ticks=topics.get("market_validated_ticks", "market.validated.ticks"),
            validated_candles=topics.get("market_validated_candles", "market.validated.candles"),
            validated_candles_3m=topics.get("market_validated_candles_3m", "market.validated.candles.3m"),
            validated_candles_5m=topics.get("market_validated_candles_5m", "market.validated.candles.5m"),
            validated_candles_15m=topics.get("market_validated_candles_15m", "market.validated.candles.15m"),
            validated_candles_30m=topics.get("market_validated_candles_30m", "market.validated.candles.30m"),
            validated_options=topics.get("market_validated_options", "market.validated.options"),
            rejected_ticks=topics.get("market_rejected_ticks", "market.rejected.ticks"),
            rejected_candles=topics.get("market_rejected_candles", "market.rejected.candles"),
            rejected_options=topics.get("market_rejected_options", "market.rejected.options"),
        )

    # ── Market data helpers ───────────────────────────────────────────────────

    def candles_topic(self, resolution: str) -> str:
        _map = {
            "1m": self.validated_candles,
            "3m": self.validated_candles_3m,
            "5m": self.validated_candles_5m,
            "15m": self.validated_candles_15m,
            "30m": self.validated_candles_30m,
        }
        return _map.get(resolution, self.validated_candles)

    def raw_topic(self, stream: str) -> str:
        if stream == "quote":
            return self.quotes
        if stream == "option_chain":
            return self.option_chain
        if stream == "vix":
            return self.vix
        if stream == "futures":
            return self.futures
        raise ValueError(f"unknown stream: {stream}")

    def validated_topic(self, stream: str) -> str:
        return f"{self.validated_prefix}.{stream}"

    def validated_contract_topic(self, payload_type: str) -> str:
        if payload_type == "tick":
            return self.validated_ticks
        if payload_type == "candle":
            return self.validated_candles
        if payload_type == "option_chain":
            return self.validated_options
        raise ValueError(f"unknown payload type: {payload_type}")

    def rejected_contract_topic(self, payload_type: str) -> str:
        if payload_type == "tick":
            return self.rejected_ticks
        if payload_type == "candle":
            return self.rejected_candles
        if payload_type == "option_chain":
            return self.rejected_options
        return self.rejected

    @staticmethod
    def symbol_ticks_topic(index: str, fyers_symbol: str | None = None) -> str:
        idx = _safe(index)
        if not fyers_symbol:
            return f"market.{idx}.ticks"
        exchange, raw_sym = _split_fyers_symbol(fyers_symbol)
        return f"market.{idx}.{_safe(exchange)}.{_safe(raw_sym)}.ticks"

    @staticmethod
    def symbol_candles_topic(index: str, resolution: str, fyers_symbol: str | None = None) -> str:
        idx = _safe(index)
        res = _safe(resolution)
        if not fyers_symbol:
            return f"market.{idx}.candles.{res}"
        exchange, raw_sym = _split_fyers_symbol(fyers_symbol)
        return f"market.{idx}.{_safe(exchange)}.{_safe(raw_sym)}.candles.{res}"

    @staticmethod
    def all_symbol_topics(
        index: str,
        fyers_symbol: str | None = None,
        resolutions: tuple[str, ...] = ("1m", "3m", "5m", "15m", "30m"),
    ) -> list[str]:
        topics = [KafkaTopicMapper.symbol_ticks_topic(index, fyers_symbol)]
        for res in resolutions:
            topics.append(KafkaTopicMapper.symbol_candles_topic(index, res, fyers_symbol))
        return topics

    # ── Trading signal / order pipeline topics ────────────────────────────────

    @staticmethod
    def signals_raw(strategy: str) -> str:
        return f"trading.{_safe(strategy)}.signals.raw"

    @staticmethod
    def signals_validated(strategy: str) -> str:
        return f"trading.{_safe(strategy)}.signals.validated"

    @staticmethod
    def signals_sized(strategy: str) -> str:
        return f"trading.{_safe(strategy)}.signals.sized"

    @staticmethod
    def signals_risk_validated(strategy: str) -> str:
        return f"trading.{_safe(strategy)}.signals.risk_validated"

    @staticmethod
    def orders_commands(strategy: str) -> str:
        return f"trading.{_safe(strategy)}.orders.commands"

    @staticmethod
    def orders_events(strategy: str) -> str:
        return f"trading.{_safe(strategy)}.orders.events"

    @staticmethod
    def positions_events(strategy: str) -> str:
        return f"trading.{_safe(strategy)}.positions.events"

    @staticmethod
    def execution_events(strategy: str) -> str:
        return f"trading.{_safe(strategy)}.execution.events"

    @staticmethod
    def all_trading_topics(strategy: str) -> list[str]:
        """Return all pipeline topics for a given strategy."""
        return [
            KafkaTopicMapper.signals_raw(strategy),
            KafkaTopicMapper.signals_validated(strategy),
            KafkaTopicMapper.signals_sized(strategy),
            KafkaTopicMapper.signals_risk_validated(strategy),
            KafkaTopicMapper.orders_commands(strategy),
            KafkaTopicMapper.orders_events(strategy),
            KafkaTopicMapper.positions_events(strategy),
            KafkaTopicMapper.execution_events(strategy),
        ]


def _split_fyers_symbol(fyers_symbol: str) -> tuple[str, str]:
    """
    Split "NSE:RELIANCE-EQ" → ("NSE", "RELIANCE")
    Split "NSE:NIFTY50-INDEX" → ("NSE", "NIFTY50")
    """
    if ":" in fyers_symbol:
        exchange, rest = fyers_symbol.split(":", 1)
    else:
        exchange, rest = "NSE", fyers_symbol
    sym = rest.split("-")[0] if "-" in rest else rest
    return exchange, sym

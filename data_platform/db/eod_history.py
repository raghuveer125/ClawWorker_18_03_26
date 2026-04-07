"""
PostgreSQL-backed 30-day EOD (End-of-Day) OHLCV history.

Schema:
    market_eod_history (
        symbol      TEXT         -- Fyers symbol e.g. NSE:RELIANCE-EQ
        trade_date  DATE         -- IST calendar date e.g. 2026-03-30
        index_name  TEXT         -- canonical index e.g. NIFTY50 ( for stocks not tracked per-index)
        open        NUMERIC
        high        NUMERIC
        low         NUMERIC
        close       NUMERIC
        volume      BIGINT
        source      TEXT         -- 'kafka_flush' | 'fyers_history'
        created_at  TIMESTAMPTZ
        updated_at  TIMESTAMPTZ
        PRIMARY KEY (symbol, trade_date)
    )

Retention: rows older than 30 calendar days are pruned on each flush.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Sequence

import asyncpg


@dataclass(frozen=True)
class EODRecord:
    symbol: str
    trade_date: date
    index_name: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str = "kafka_flush"


_CREATE_EOD = """
CREATE TABLE IF NOT EXISTS market_eod_history (
    symbol      TEXT    NOT NULL,
    trade_date  DATE    NOT NULL,
    index_name  TEXT    NOT NULL DEFAULT '',
    open        NUMERIC NOT NULL,
    high        NUMERIC NOT NULL,
    low         NUMERIC NOT NULL,
    close       NUMERIC NOT NULL,
    volume      BIGINT  NOT NULL DEFAULT 0,
    source      TEXT    NOT NULL DEFAULT 'kafka_flush',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, trade_date)
)
"""

_CREATE_IDX_DATE  = "CREATE INDEX IF NOT EXISTS idx_eod_trade_date  ON market_eod_history (trade_date)"
_CREATE_IDX_INDEX = "CREATE INDEX IF NOT EXISTS idx_eod_index_name  ON market_eod_history (index_name)"


@dataclass(frozen=True)
class EODHistoryConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "clawworker"
    user: str = "clawworker"
    password: str = "clawworker"
    retention_days: int = 30


class PostgresEODRepository:
    def __init__(self, config: EODHistoryConfig) -> None:
        self._cfg = config

    async def ensure_schema(self) -> None:
        conn = await self._connect()
        try:
            await conn.execute(_CREATE_EOD)
            await conn.execute(_CREATE_IDX_DATE)
            await conn.execute(_CREATE_IDX_INDEX)
        finally:
            await conn.close()

    async def upsert_many(self, records: Sequence[EODRecord]) -> int:
        if not records:
            return 0
        conn = await self._connect()
        try:
            async with conn.transaction():
                count = 0
                for r in records:
                    await conn.execute(
                        """
                        INSERT INTO market_eod_history
                            (symbol, trade_date, index_name, open, high, low, close, volume, source, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                        ON CONFLICT (symbol, trade_date) DO UPDATE SET
                            index_name = EXCLUDED.index_name,
                            open       = EXCLUDED.open,
                            high       = EXCLUDED.high,
                            low        = EXCLUDED.low,
                            close      = EXCLUDED.close,
                            volume     = EXCLUDED.volume,
                            source     = EXCLUDED.source,
                            updated_at = NOW()
                        """,
                        r.symbol, r.trade_date, r.index_name,
                        r.open, r.high, r.low, r.close,
                        r.volume, r.source,
                    )
                    count += 1
            return count
        finally:
            await conn.close()

    async def prune_old(self) -> int:
        conn = await self._connect()
        try:
            result = await conn.execute(
                """
                DELETE FROM market_eod_history
                WHERE trade_date < CURRENT_DATE - $1::INTEGER
                """,
                self._cfg.retention_days,
            )
            deleted = int(result.split()[-1]) if result else 0
            return deleted
        finally:
            await conn.close()

    async def list_for_symbol(self, symbol: str, days: int = 30) -> list[EODRecord]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                """
                SELECT symbol, trade_date, index_name, open, high, low, close, volume, source
                FROM market_eod_history
                WHERE symbol = $1
                  AND trade_date >= CURRENT_DATE - $2::INTEGER
                ORDER BY trade_date ASC
                """,
                symbol, days,
            )
        finally:
            await conn.close()
        return [_row_to_record(r) for r in rows]

    async def list_for_index(self, index_name: str, days: int = 30) -> list[EODRecord]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                """
                SELECT symbol, trade_date, index_name, open, high, low, close, volume, source
                FROM market_eod_history
                WHERE index_name = $1
                  AND trade_date >= CURRENT_DATE - $2::INTEGER
                ORDER BY trade_date ASC, symbol ASC
                """,
                index_name, days,
            )
        finally:
            await conn.close()
        return [_row_to_record(r) for r in rows]

    async def list_for_date(self, trade_date: date) -> list[EODRecord]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                """
                SELECT symbol, trade_date, index_name, open, high, low, close, volume, source
                FROM market_eod_history
                WHERE trade_date = $1
                ORDER BY index_name, symbol
                """,
                trade_date,
            )
        finally:
            await conn.close()
        return [_row_to_record(r) for r in rows]

    async def summary(self) -> dict[str, object]:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*)                    AS total_rows,
                    COUNT(DISTINCT symbol)      AS unique_symbols,
                    COUNT(DISTINCT trade_date)  AS trading_days,
                    MIN(trade_date)             AS earliest_date,
                    MAX(trade_date)             AS latest_date
                FROM market_eod_history
                """
            )
        finally:
            await conn.close()
        return {
            "total_rows":     int(row["total_rows"]),
            "unique_symbols": int(row["unique_symbols"]),
            "trading_days":   int(row["trading_days"]),
            "earliest_date":  str(row["earliest_date"]) if row["earliest_date"] else None,
            "latest_date":    str(row["latest_date"])   if row["latest_date"]   else None,
        }

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(
            host=self._cfg.host,
            port=self._cfg.port,
            database=self._cfg.database,
            user=self._cfg.user,
            password=self._cfg.password,
        )


def _row_to_record(row: asyncpg.Record) -> EODRecord:
    return EODRecord(
        symbol=str(row["symbol"]),
        trade_date=row["trade_date"],
        index_name=str(row["index_name"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row["volume"]),
        source=str(row["source"]),
    )


def sync_ensure_eod_schema(config: EODHistoryConfig) -> None:
    asyncio.run(PostgresEODRepository(config).ensure_schema())


def sync_upsert_eod(config: EODHistoryConfig, records: Sequence[EODRecord]) -> int:
    return asyncio.run(PostgresEODRepository(config).upsert_many(records))


def sync_prune_eod(config: EODHistoryConfig) -> int:
    return asyncio.run(PostgresEODRepository(config).prune_old())


def sync_eod_summary(config: EODHistoryConfig) -> dict[str, object]:
    return asyncio.run(PostgresEODRepository(config).summary())


def sync_eod_for_symbol(config: EODHistoryConfig, symbol: str, days: int = 30) -> list[EODRecord]:
    return asyncio.run(PostgresEODRepository(config).list_for_symbol(symbol, days))


def sync_eod_for_index(config: EODHistoryConfig, index_name: str, days: int = 30) -> list[EODRecord]:
    return asyncio.run(PostgresEODRepository(config).list_for_index(index_name, days))


def sync_eod_for_date(config: EODHistoryConfig, trade_date: date) -> list[EODRecord]:
    return asyncio.run(PostgresEODRepository(config).list_for_date(trade_date))

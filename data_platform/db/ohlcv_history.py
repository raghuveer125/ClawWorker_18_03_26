"""
market_ohlcv_history — multi-resolution OHLCV candle store.

Schema:
    market_ohlcv_history (
        symbol      TEXT          -- Fyers symbol e.g. NSE:RELIANCE-EQ or NSE:NIFTY50-INDEX
        resolution  TEXT          -- '1m','3m','5m','10m','15m','30m','1h','4h','1d'
        ts          TIMESTAMPTZ   -- candle open timestamp (UTC), PK component
        open        NUMERIC
        high        NUMERIC
        low         NUMERIC
        close       NUMERIC
        volume      BIGINT
        index_name  TEXT          -- owning index e.g. NIFTY50 (empty for index-level symbols)
        source      TEXT          -- 'fyers_history' | 'kafka_flush'
        created_at  TIMESTAMPTZ
        updated_at  TIMESTAMPTZ
        PRIMARY KEY (symbol, resolution, ts)
    )

Indexes:
    idx_ohlcv_sym_res_ts  ON (symbol, resolution, ts DESC)
    idx_ohlcv_index_res   ON (index_name, resolution, ts DESC)
    idx_ohlcv_ts          ON (ts DESC)

Retention: rows older than 30 calendar days are pruned on request.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Optional, Sequence

import asyncpg


@dataclass(frozen=True)
class OHLCVRecord:
    symbol: str
    resolution: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    index_name: str = ""
    source: str = "fyers_history"


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS market_ohlcv_history (
    symbol      TEXT        NOT NULL,
    resolution  TEXT        NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    open        NUMERIC     NOT NULL,
    high        NUMERIC     NOT NULL,
    low         NUMERIC     NOT NULL,
    close       NUMERIC     NOT NULL,
    volume      BIGINT      NOT NULL DEFAULT 0,
    index_name  TEXT        NOT NULL DEFAULT '',
    source      TEXT        NOT NULL DEFAULT 'fyers_history',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, resolution, ts)
)
"""

_CREATE_IDX_SYM_RES_TS = """
CREATE INDEX IF NOT EXISTS idx_ohlcv_sym_res_ts
    ON market_ohlcv_history (symbol, resolution, ts DESC)
"""

_CREATE_IDX_INDEX_RES = """
CREATE INDEX IF NOT EXISTS idx_ohlcv_index_res
    ON market_ohlcv_history (index_name, resolution, ts DESC)
"""

_CREATE_IDX_TS = """
CREATE INDEX IF NOT EXISTS idx_ohlcv_ts
    ON market_ohlcv_history (ts DESC)
"""

_UPSERT = """
INSERT INTO market_ohlcv_history
    (symbol, resolution, ts, open, high, low, close, volume, index_name, source, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
ON CONFLICT (symbol, resolution, ts) DO UPDATE SET
    open       = EXCLUDED.open,
    high       = EXCLUDED.high,
    low        = EXCLUDED.low,
    close      = EXCLUDED.close,
    volume     = EXCLUDED.volume,
    index_name = EXCLUDED.index_name,
    source     = EXCLUDED.source,
    updated_at = NOW()
"""

_PRUNE = """
DELETE FROM market_ohlcv_history
WHERE ts < NOW() - ($1 || ' days')::INTERVAL
"""

_LIST_FOR_SYMBOL = """
SELECT symbol, resolution, ts, open, high, low, close, volume, index_name, source
FROM market_ohlcv_history
WHERE symbol = $1 AND resolution = $2
  AND ts >= NOW() - ($3 || ' days')::INTERVAL
ORDER BY ts ASC
"""

_LIST_FOR_INDEX = """
SELECT symbol, resolution, ts, open, high, low, close, volume, index_name, source
FROM market_ohlcv_history
WHERE index_name = $1 AND resolution = $2
  AND ts >= NOW() - ($3 || ' days')::INTERVAL
ORDER BY ts ASC, symbol ASC
"""

_LATEST_TS_ALL = """
SELECT symbol, resolution, MAX(ts) AS latest_ts
FROM market_ohlcv_history
GROUP BY symbol, resolution
"""

_SUMMARY = """
SELECT
    COUNT(*)                    AS total_rows,
    COUNT(DISTINCT symbol)      AS unique_symbols,
    COUNT(DISTINCT resolution)  AS resolutions,
    MIN(ts)                     AS earliest_ts,
    MAX(ts)                     AS latest_ts
FROM market_ohlcv_history
"""

_RESOLUTION_COUNTS = """
SELECT resolution, COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols
FROM market_ohlcv_history
GROUP BY resolution
ORDER BY resolution
"""


@dataclass(frozen=True)
class OHLCVHistoryConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "clawworker"
    user: str = "clawworker"
    password: str = "clawworker"
    retention_days: int = 30

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "OHLCVHistoryConfig":
        import os
        source = env or dict(os.environ)
        return cls(
            host=source.get("CLAWWORKER_DB_HOST", source.get("DB_HOST", "localhost")),
            port=int(source.get("CLAWWORKER_DB_PORT", source.get("DB_PORT", "5432"))),
            database=source.get("CLAWWORKER_DB_NAME", source.get("DB_NAME", "clawworker")),
            user=source.get("CLAWWORKER_DB_USER", source.get("DB_USER", "clawworker")),
            password=source.get("CLAWWORKER_DB_PASSWORD", source.get("DB_PASSWORD", "clawworker")),
        )


class PostgresOHLCVRepository:
    def __init__(
        self,
        config: OHLCVHistoryConfig,
        pool: Optional[asyncpg.Pool] = None,
    ) -> None:
        self._cfg = config
        self._pool = pool

    @asynccontextmanager
    async def _get_conn(self) -> AsyncIterator[asyncpg.Connection]:
        """Yield a connection from the pool (preferred) or a direct connection."""
        if self._pool is not None:
            async with self._pool.acquire() as conn:
                yield conn
        else:
            conn = await self._connect()
            try:
                yield conn
            finally:
                await conn.close()

    async def ensure_schema(self) -> None:
        async with self._get_conn() as conn:
            await conn.execute(_CREATE_TABLE)
            await conn.execute(_CREATE_IDX_SYM_RES_TS)
            await conn.execute(_CREATE_IDX_INDEX_RES)
            await conn.execute(_CREATE_IDX_TS)

    async def upsert_many(self, records: Sequence[OHLCVRecord]) -> int:
        if not records:
            return 0
        async with self._get_conn() as conn:
            async with conn.transaction():
                for r in records:
                    ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
                    await conn.execute(
                        _UPSERT,
                        r.symbol, r.resolution, ts,
                        r.open, r.high, r.low, r.close,
                        r.volume, r.index_name, r.source,
                    )
            return len(records)

    async def prune_old(self, retention_days: int | None = None) -> int:
        days = retention_days if retention_days is not None else self._cfg.retention_days
        async with self._get_conn() as conn:
            result = await conn.execute(_PRUNE, str(days))
            return int(result.split()[-1]) if result else 0

    async def list_for_symbol(
        self,
        symbol: str,
        resolution: str,
        days: int = 30,
    ) -> list[OHLCVRecord]:
        async with self._get_conn() as conn:
            rows = await conn.fetch(_LIST_FOR_SYMBOL, symbol, resolution, str(days))
        return [_row_to_record(r) for r in rows]

    async def list_for_index(
        self,
        index_name: str,
        resolution: str,
        days: int = 30,
    ) -> list[OHLCVRecord]:
        async with self._get_conn() as conn:
            rows = await conn.fetch(_LIST_FOR_INDEX, index_name, resolution, str(days))
        return [_row_to_record(r) for r in rows]

    async def latest_timestamps(self) -> dict[tuple[str, str], datetime]:
        """Return {(symbol, resolution): latest_ts} for all rows in the table."""
        async with self._get_conn() as conn:
            rows = await conn.fetch(_LATEST_TS_ALL)
        return {(str(r["symbol"]), str(r["resolution"])): r["latest_ts"] for r in rows}

    async def summary(self) -> dict[str, object]:
        async with self._get_conn() as conn:
            row = await conn.fetchrow(_SUMMARY)
            res_rows = await conn.fetch(_RESOLUTION_COUNTS)
        return {
            "total_rows":      int(row["total_rows"]),
            "unique_symbols":  int(row["unique_symbols"]),
            "resolutions":     int(row["resolutions"]),
            "earliest_ts":     row["earliest_ts"].isoformat() if row["earliest_ts"] else None,
            "latest_ts":       row["latest_ts"].isoformat()   if row["latest_ts"]   else None,
            "by_resolution": [
                {"resolution": r["resolution"], "rows": int(r["rows"]), "symbols": int(r["symbols"])}
                for r in res_rows
            ],
        }

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(
            host=self._cfg.host,
            port=self._cfg.port,
            database=self._cfg.database,
            user=self._cfg.user,
            password=self._cfg.password,
        )


def _row_to_record(row: asyncpg.Record) -> OHLCVRecord:
    return OHLCVRecord(
        symbol=str(row["symbol"]),
        resolution=str(row["resolution"]),
        ts=row["ts"],
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row["volume"]),
        index_name=str(row["index_name"]),
        source=str(row["source"]),
    )


def _run(coro):
    try:
        asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


def sync_ensure_ohlcv_schema(config: OHLCVHistoryConfig) -> None:
    _run(PostgresOHLCVRepository(config).ensure_schema())


def sync_upsert_ohlcv(config: OHLCVHistoryConfig, records: Sequence[OHLCVRecord]) -> int:
    return _run(PostgresOHLCVRepository(config).upsert_many(records))


def sync_prune_ohlcv(config: OHLCVHistoryConfig, retention_days: int | None = None) -> int:
    return _run(PostgresOHLCVRepository(config).prune_old(retention_days))


def sync_ohlcv_latest_timestamps(config: OHLCVHistoryConfig) -> dict[tuple[str, str], datetime]:
    """Return {(symbol, resolution): latest_ts} — used to skip already-fetched ranges."""
    return _run(PostgresOHLCVRepository(config).latest_timestamps())


def sync_ohlcv_summary(config: OHLCVHistoryConfig) -> dict[str, object]:
    return _run(PostgresOHLCVRepository(config).summary())


def sync_ohlcv_for_symbol(
    config: OHLCVHistoryConfig,
    symbol: str,
    resolution: str,
    days: int = 30,
) -> list[OHLCVRecord]:
    return _run(PostgresOHLCVRepository(config).list_for_symbol(symbol, resolution, days))


def sync_ohlcv_for_index(
    config: OHLCVHistoryConfig,
    index_name: str,
    resolution: str,
    days: int = 30,
) -> list[OHLCVRecord]:
    return _run(PostgresOHLCVRepository(config).list_for_index(index_name, resolution, days))

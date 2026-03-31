"""
trades — per-strategy trade recording table.

Replaces scattered JSONL/CSV files across ClawWork, scalping engine, and fyersN7.

Schema:
    trades (
        trade_id        TEXT PRIMARY KEY      -- UUID
        strategy        TEXT NOT NULL         -- 'scalping' | 'fyersn7' | 'clawwork'
        bot_name        TEXT NOT NULL         -- e.g. 'ict_sniper', 'ensemble', 'scalping_engine'
        index_name      TEXT NOT NULL         -- NIFTY50 | BANKNIFTY | SENSEX | FINNIFTY
        option_type     TEXT                  -- CE | PE | NULL for futures
        strike          NUMERIC               -- option strike price
        entry_price     NUMERIC NOT NULL
        exit_price      NUMERIC
        entry_time      TIMESTAMPTZ NOT NULL
        exit_time       TIMESTAMPTZ
        quantity        INT NOT NULL
        pnl             NUMERIC
        pnl_pct         NUMERIC
        outcome         TEXT                  -- WIN | LOSS | BREAKEVEN | OPEN
        mode            TEXT NOT NULL         -- paper | live
        signal_source   TEXT                  -- signal that triggered the trade
        market_snapshot JSONB                 -- market conditions at entry
        reasoning       TEXT                  -- bot reasoning / LLM output
        lessons         JSONB                 -- lessons_learned array
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional, Sequence

import asyncpg


@dataclass
class TradeRecord:
    trade_id: str
    strategy: str
    bot_name: str
    index_name: str
    entry_price: float
    quantity: int
    mode: str
    entry_time: datetime
    option_type: str = ""
    strike: float | None = None
    exit_price: float | None = None
    exit_time: datetime | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    outcome: str = "OPEN"
    signal_source: str = ""
    market_snapshot: dict[str, Any] | None = None
    reasoning: str = ""
    lessons: list[str] | None = None


@dataclass(frozen=True)
class TradesConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "clawworker"
    user: str = "clawworker"
    password: str = "clawworker"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "TradesConfig":
        import os
        src = env or dict(os.environ)
        return cls(
            host=src.get("CLAWWORKER_DB_HOST", "localhost"),
            port=int(src.get("CLAWWORKER_DB_PORT", "5432")),
            database=src.get("CLAWWORKER_DB_NAME", "clawworker"),
            user=src.get("CLAWWORKER_DB_USER", "clawworker"),
            password=src.get("CLAWWORKER_DB_PASSWORD", "clawworker"),
        )


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id        TEXT        PRIMARY KEY,
    strategy        TEXT        NOT NULL,
    bot_name        TEXT        NOT NULL DEFAULT '',
    index_name      TEXT        NOT NULL,
    option_type     TEXT        NOT NULL DEFAULT '',
    strike          NUMERIC,
    entry_price     NUMERIC     NOT NULL,
    exit_price      NUMERIC,
    entry_time      TIMESTAMPTZ NOT NULL,
    exit_time       TIMESTAMPTZ,
    quantity        INT         NOT NULL DEFAULT 1,
    pnl             NUMERIC,
    pnl_pct         NUMERIC,
    outcome         TEXT        NOT NULL DEFAULT 'OPEN',
    mode            TEXT        NOT NULL DEFAULT 'paper',
    signal_source   TEXT        NOT NULL DEFAULT '',
    market_snapshot JSONB,
    reasoning       TEXT        NOT NULL DEFAULT '',
    lessons         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_CREATE_IDX_STRATEGY = """
CREATE INDEX IF NOT EXISTS idx_trades_strategy_entry
    ON trades (strategy, entry_time DESC)
"""

_CREATE_IDX_INDEX = """
CREATE INDEX IF NOT EXISTS idx_trades_index_entry
    ON trades (index_name, entry_time DESC)
"""

_CREATE_IDX_OUTCOME = """
CREATE INDEX IF NOT EXISTS idx_trades_outcome
    ON trades (strategy, outcome, entry_time DESC)
"""

_INSERT = """
INSERT INTO trades (
    trade_id, strategy, bot_name, index_name, option_type, strike,
    entry_price, exit_price, entry_time, exit_time, quantity,
    pnl, pnl_pct, outcome, mode, signal_source,
    market_snapshot, reasoning, lessons
) VALUES (
    $1, $2, $3, $4, $5, $6,
    $7, $8, $9, $10, $11,
    $12, $13, $14, $15, $16,
    $17, $18, $19
)
ON CONFLICT (trade_id) DO NOTHING
"""

_UPDATE_EXIT = """
UPDATE trades SET
    exit_price  = $2,
    exit_time   = $3,
    pnl         = $4,
    pnl_pct     = $5,
    outcome     = $6,
    lessons     = $7,
    updated_at  = NOW()
WHERE trade_id = $1
"""

_LIST_FOR_STRATEGY = """
SELECT * FROM trades
WHERE strategy = $1
  AND entry_time >= NOW() - ($2 || ' days')::INTERVAL
ORDER BY entry_time DESC
"""

_SUMMARY = """
SELECT
    strategy,
    COUNT(*)                                        AS total_trades,
    COUNT(*) FILTER (WHERE outcome = 'WIN')         AS wins,
    COUNT(*) FILTER (WHERE outcome = 'LOSS')        AS losses,
    COUNT(*) FILTER (WHERE outcome = 'OPEN')        AS open_trades,
    ROUND(SUM(pnl)::NUMERIC, 2)                     AS total_pnl,
    ROUND(AVG(pnl_pct)::NUMERIC, 4)                 AS avg_pnl_pct
FROM trades
GROUP BY strategy
ORDER BY strategy
"""


class PostgresTradesRepository:
    def __init__(
        self,
        config: TradesConfig,
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
            await conn.execute(_CREATE_IDX_STRATEGY)
            await conn.execute(_CREATE_IDX_INDEX)
            await conn.execute(_CREATE_IDX_OUTCOME)

    async def insert(self, record: TradeRecord) -> None:
        import json
        async with self._get_conn() as conn:
            snapshot_json = json.dumps(record.market_snapshot) if record.market_snapshot else None
            lessons_json = json.dumps(record.lessons) if record.lessons else None
            entry_time = record.entry_time if record.entry_time.tzinfo else record.entry_time.replace(tzinfo=timezone.utc)
            await conn.execute(
                _INSERT,
                record.trade_id, record.strategy, record.bot_name, record.index_name,
                record.option_type, record.strike,
                record.entry_price, record.exit_price, entry_time, record.exit_time,
                record.quantity, record.pnl, record.pnl_pct, record.outcome, record.mode,
                record.signal_source, snapshot_json, record.reasoning, lessons_json,
            )

    async def update_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_time: datetime,
        pnl: float,
        pnl_pct: float,
        outcome: str,
        lessons: list[str] | None = None,
    ) -> None:
        import json
        async with self._get_conn() as conn:
            etime = exit_time if exit_time.tzinfo else exit_time.replace(tzinfo=timezone.utc)
            await conn.execute(
                _UPDATE_EXIT,
                trade_id, exit_price, etime, pnl, pnl_pct, outcome,
                json.dumps(lessons) if lessons else None,
            )

    async def list_for_strategy(self, strategy: str, days: int = 30) -> list[dict[str, Any]]:
        async with self._get_conn() as conn:
            rows = await conn.fetch(_LIST_FOR_STRATEGY, strategy, str(days))
        return [dict(r) for r in rows]

    async def summary(self) -> list[dict[str, Any]]:
        async with self._get_conn() as conn:
            rows = await conn.fetch(_SUMMARY)
        return [dict(r) for r in rows]

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(
            host=self._cfg.host,
            port=self._cfg.port,
            database=self._cfg.database,
            user=self._cfg.user,
            password=self._cfg.password,
        )


def _run(coro):
    try:
        asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


def sync_ensure_trades_schema(config: TradesConfig) -> None:
    _run(PostgresTradesRepository(config).ensure_schema())


def sync_insert_trade(config: TradesConfig, record: TradeRecord) -> None:
    _run(PostgresTradesRepository(config).insert(record))


def sync_update_trade_exit(
    config: TradesConfig,
    trade_id: str,
    exit_price: float,
    exit_time: datetime,
    pnl: float,
    pnl_pct: float,
    outcome: str,
    lessons: list[str] | None = None,
) -> None:
    _run(PostgresTradesRepository(config).update_exit(
        trade_id, exit_price, exit_time, pnl, pnl_pct, outcome, lessons
    ))


def sync_trades_for_strategy(config: TradesConfig, strategy: str, days: int = 30) -> list[dict[str, Any]]:
    return _run(PostgresTradesRepository(config).list_for_strategy(strategy, days))


def sync_trades_summary(config: TradesConfig) -> list[dict[str, Any]]:
    return _run(PostgresTradesRepository(config).summary())

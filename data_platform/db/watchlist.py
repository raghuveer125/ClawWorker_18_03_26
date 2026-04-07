"""
PostgreSQL-backed watchlist storage for live symbol management.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable, Sequence

import re

import asyncpg

# Whitelist of allowed table names for watchlist storage.
_ALLOWED_TABLE_NAMES: frozenset[str] = frozenset({
    "watchlist_symbols",
    "watchlist_symbols_dev",
    "watchlist_symbols_staging",
    "watchlist_symbols_test",
})

# Pattern: only lowercase letters, digits, and underscores (1-63 chars).
_VALID_TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _validate_table_name(name: str) -> str:
    """Validate a table name against the whitelist and safe-character pattern.

    Raises ValueError if the name is not allowed, preventing SQL injection
    through dynamic table identifiers.
    """
    if name in _ALLOWED_TABLE_NAMES:
        return name
    if not _VALID_TABLE_RE.match(name):
        raise ValueError(
            f"Invalid table name '{name}': must match {_VALID_TABLE_RE.pattern}"
        )
    raise ValueError(
        f"Table name '{name}' is not in the allowed whitelist: "
        f"{sorted(_ALLOWED_TABLE_NAMES)}"
    )


@dataclass(frozen=True)
class WatchlistConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "clawworker"
    user: str = "clawworker"
    password: str = "clawworker"
    table: str = "watchlist_symbols"


class PostgresWatchlistRepository:
    def __init__(self, config: WatchlistConfig) -> None:
        self._cfg = config
        self._table = _validate_table_name(config.table)

    async def ensure_schema(self) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    symbol TEXT PRIMARY KEY,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        finally:
            await conn.close()

    async def list_active_symbols(self) -> tuple[str, ...]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT symbol
                FROM {self._table}
                WHERE is_active = TRUE
                ORDER BY symbol
                """
            )
        finally:
            await conn.close()
        return tuple(str(row["symbol"]) for row in rows)

    async def add_symbols(self, symbols: Sequence[str]) -> int:
        cleaned = tuple(_clean_symbols(symbols))
        if not cleaned:
            return 0
        conn = await self._connect()
        try:
            inserted = 0
            for symbol in cleaned:
                result = await conn.execute(
                    f"""
                    INSERT INTO {self._table} (symbol, is_active)
                    VALUES ($1, TRUE)
                    ON CONFLICT (symbol)
                    DO UPDATE SET is_active = TRUE
                    """,
                    symbol,
                )
                if result.startswith("INSERT"):
                    inserted += 1
            return inserted
        finally:
            await conn.close()

    async def deactivate_symbol(self, symbol: str) -> bool:
        cleaned = _clean_symbol(symbol)
        if not cleaned:
            return False
        conn = await self._connect()
        try:
            result = await conn.execute(
                f"""
                UPDATE {self._table}
                SET is_active = FALSE
                WHERE symbol = $1
                """,
                cleaned,
            )
            return result.endswith("1")
        finally:
            await conn.close()

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(
            host=self._cfg.host,
            port=self._cfg.port,
            database=self._cfg.database,
            user=self._cfg.user,
            password=self._cfg.password,
        )


def watchlist_from_sources(
    env_watchlist: str,
    db_symbols: Sequence[str] | None = None,
) -> tuple[str, ...]:
    cleaned_db = tuple(_clean_symbols(db_symbols or ()))
    if cleaned_db:
        return cleaned_db
    env_items = tuple(_clean_symbols(env_watchlist.split(",")))
    if env_items:
        return env_items
    return ("NSE:NIFTY50-INDEX",)


def sync_list_active_symbols(config: WatchlistConfig) -> tuple[str, ...]:
    return asyncio.run(PostgresWatchlistRepository(config).list_active_symbols())


def sync_ensure_schema(config: WatchlistConfig) -> None:
    asyncio.run(PostgresWatchlistRepository(config).ensure_schema())


def sync_add_symbols(config: WatchlistConfig, symbols: Sequence[str]) -> int:
    return asyncio.run(PostgresWatchlistRepository(config).add_symbols(symbols))


def sync_deactivate_symbol(config: WatchlistConfig, symbol: str) -> bool:
    return asyncio.run(PostgresWatchlistRepository(config).deactivate_symbol(symbol))


def _clean_symbol(symbol: str) -> str:
    return str(symbol or "").strip()


def _clean_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        cleaned = _clean_symbol(raw)
        if not cleaned or cleaned in seen:
            continue
        result.append(cleaned)
        seen.add(cleaned)
    return tuple(result)

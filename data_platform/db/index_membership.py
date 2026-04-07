"""
PostgreSQL-backed index membership storage.

Schema (normalized, no duplicate symbols):

    symbols (fyers_symbol PK, symbol, company_name, exchange, is_active, updated_at)
    index_symbol_map (index_name, fyers_symbol FK -> symbols, sort_order, is_active, updated_at)
        PRIMARY KEY (index_name, fyers_symbol)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

import asyncpg


@dataclass(frozen=True)
class IndexMembershipConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "clawworker"
    user: str = "clawworker"
    password: str = "clawworker"


@dataclass(frozen=True)
class IndexMember:
    index_name: str
    symbol: str
    fyers_symbol: str
    company_name: str
    source_name: str
    source_url: str
    as_of: date | None = None
    sort_order: int = 0
    is_active: bool = True


_CREATE_SYMBOLS = """
CREATE TABLE IF NOT EXISTS symbols (
    fyers_symbol  TEXT PRIMARY KEY,
    symbol        TEXT NOT NULL,
    company_name  TEXT NOT NULL,
    exchange      TEXT NOT NULL DEFAULT 'NSE',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_CREATE_INDEX_MAP = """
CREATE TABLE IF NOT EXISTS index_symbol_map (
    index_name    TEXT NOT NULL,
    fyers_symbol  TEXT NOT NULL REFERENCES symbols(fyers_symbol) ON DELETE CASCADE,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (index_name, fyers_symbol)
)
"""


class PostgresIndexMembershipRepository:
    def __init__(self, config: IndexMembershipConfig) -> None:
        self._cfg = config

    async def ensure_schema(self) -> None:
        conn = await self._connect()
        try:
            await conn.execute(_CREATE_SYMBOLS)
            await conn.execute(_CREATE_INDEX_MAP)
        finally:
            await conn.close()

    async def replace_index_members(self, index_name: str, members: Sequence[IndexMember]) -> int:
        canonical = canonical_index_name(index_name)
        normalized = tuple(_clean_members(members))
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE index_symbol_map
                    SET is_active = FALSE, updated_at = NOW()
                    WHERE index_name = $1
                    """,
                    canonical,
                )
                for member in normalized:
                    exchange = member.fyers_symbol.split(":")[0] if ":" in member.fyers_symbol else "NSE"
                    await conn.execute(
                        """
                        INSERT INTO symbols (fyers_symbol, symbol, company_name, exchange, is_active, updated_at)
                        VALUES ($1, $2, $3, $4, TRUE, NOW())
                        ON CONFLICT (fyers_symbol) DO UPDATE SET
                            symbol       = EXCLUDED.symbol,
                            company_name = EXCLUDED.company_name,
                            exchange     = EXCLUDED.exchange,
                            is_active    = TRUE,
                            updated_at   = NOW()
                        """,
                        member.fyers_symbol,
                        member.symbol,
                        member.company_name,
                        exchange,
                    )
                    await conn.execute(
                        """
                        INSERT INTO index_symbol_map (index_name, fyers_symbol, sort_order, is_active, updated_at)
                        VALUES ($1, $2, $3, TRUE, NOW())
                        ON CONFLICT (index_name, fyers_symbol) DO UPDATE SET
                            sort_order = EXCLUDED.sort_order,
                            is_active  = TRUE,
                            updated_at = NOW()
                        """,
                        canonical,
                        member.fyers_symbol,
                        member.sort_order,
                    )
        finally:
            await conn.close()
        return len(normalized)

    async def list_indices(self) -> tuple[str, ...]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                """
                SELECT DISTINCT index_name
                FROM index_symbol_map
                WHERE is_active = TRUE
                ORDER BY index_name
                """
            )
        finally:
            await conn.close()
        return tuple(str(r["index_name"]) for r in rows)

    async def list_members(self, index_name: str) -> tuple[IndexMember, ...]:
        canonical = canonical_index_name(index_name)
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                """
                SELECT m.index_name, s.symbol, s.fyers_symbol, s.company_name, m.sort_order, m.is_active
                FROM index_symbol_map m
                JOIN symbols s ON s.fyers_symbol = m.fyers_symbol
                WHERE m.index_name = $1 AND m.is_active = TRUE AND s.is_active = TRUE
                ORDER BY m.sort_order, s.symbol
                """,
                canonical,
            )
        finally:
            await conn.close()
        return tuple(
            IndexMember(
                index_name=str(r["index_name"]),
                symbol=str(r["symbol"]),
                fyers_symbol=str(r["fyers_symbol"]),
                company_name=str(r["company_name"]),
                source_name="",
                source_url="",
                as_of=None,
                sort_order=int(r["sort_order"]),
                is_active=bool(r["is_active"]),
            )
            for r in rows
        )

    async def list_all_symbols(self) -> tuple[str, ...]:
        """Return all unique active fyers_symbols across all indices."""
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                """
                SELECT fyers_symbol FROM symbols
                WHERE is_active = TRUE
                ORDER BY fyers_symbol
                """
            )
        finally:
            await conn.close()
        return tuple(str(r["fyers_symbol"]) for r in rows)

    async def list_indices_for_symbol(self, fyers_symbol: str) -> tuple[str, ...]:
        """Return all index names a given symbol belongs to."""
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                """
                SELECT index_name FROM index_symbol_map
                WHERE fyers_symbol = $1 AND is_active = TRUE
                ORDER BY index_name
                """,
                fyers_symbol,
            )
        finally:
            await conn.close()
        return tuple(str(r["index_name"]) for r in rows)

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(
            host=self._cfg.host,
            port=self._cfg.port,
            database=self._cfg.database,
            user=self._cfg.user,
            password=self._cfg.password,
        )


def canonical_index_name(name: str) -> str:
    compact = "".join(ch for ch in str(name or "").upper() if ch.isalnum())
    aliases = {
        "NIFTY":      "NIFTY50",
        "NIFTY50":    "NIFTY50",
        "BANKNIFTY":  "BANKNIFTY",
        "NIFTYBANK":  "BANKNIFTY",
        "FINNIFTY":   "FINNIFTY",
        "MIDCPNIFTY": "MIDCPNIFTY",
        "SENSEX":     "SENSEX",
        "BSESENSEX":  "SENSEX",
        "BSE30":      "SENSEX",
    }
    return aliases.get(compact, compact)


def fyers_symbol_for_equity(symbol: str, exchange: str = "NSE") -> str:
    cleaned = str(symbol or "").strip().upper()
    if not cleaned:
        return ""
    return f"{exchange}:{cleaned}-EQ"


def sync_ensure_index_schema(config: IndexMembershipConfig) -> None:
    asyncio.run(PostgresIndexMembershipRepository(config).ensure_schema())


def sync_replace_index_members(
    config: IndexMembershipConfig,
    index_name: str,
    members: Sequence[IndexMember],
) -> int:
    return asyncio.run(
        PostgresIndexMembershipRepository(config).replace_index_members(index_name, members)
    )


def sync_list_indices(config: IndexMembershipConfig) -> tuple[str, ...]:
    return asyncio.run(PostgresIndexMembershipRepository(config).list_indices())


def sync_list_members(config: IndexMembershipConfig, index_name: str) -> tuple[IndexMember, ...]:
    return asyncio.run(PostgresIndexMembershipRepository(config).list_members(index_name))


def sync_list_all_symbols(config: IndexMembershipConfig) -> tuple[str, ...]:
    return asyncio.run(PostgresIndexMembershipRepository(config).list_all_symbols())


def sync_list_indices_for_symbol(config: IndexMembershipConfig, fyers_symbol: str) -> tuple[str, ...]:
    return asyncio.run(PostgresIndexMembershipRepository(config).list_indices_for_symbol(fyers_symbol))


def _clean_members(members: Iterable[IndexMember]) -> tuple[IndexMember, ...]:
    result: list[IndexMember] = []
    seen: set[str] = set()
    for item in members:
        canonical = canonical_index_name(item.index_name)
        symbol = str(item.symbol or "").strip().upper()
        fyers_symbol = str(item.fyers_symbol or "").strip()
        company_name = str(item.company_name or "").strip()
        if not canonical or not symbol or not fyers_symbol or not company_name:
            continue
        if fyers_symbol in seen:
            continue
        seen.add(fyers_symbol)
        result.append(
            IndexMember(
                index_name=canonical,
                symbol=symbol,
                fyers_symbol=fyers_symbol,
                company_name=company_name,
                source_name=str(item.source_name or "").strip(),
                source_url=str(item.source_url or "").strip(),
                as_of=item.as_of,
                sort_order=int(item.sort_order),
                is_active=bool(item.is_active),
            )
        )
    return tuple(result)

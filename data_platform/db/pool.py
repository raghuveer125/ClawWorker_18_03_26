"""
Shared asyncpg connection pool for all data_platform DB modules.

Usage:
    pool = await get_pool(config)
    async with pool.acquire() as conn:
        await conn.execute(...)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import asyncpg

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DBPoolConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "clawworker"
    user: str = "clawworker"
    password: str = "clawworker"
    min_size: int = 2
    max_size: int = 10

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "DBPoolConfig":
        import os
        src = env or dict(os.environ)
        return cls(
            host=src.get("CLAWWORKER_DB_HOST", "localhost"),
            port=int(src.get("CLAWWORKER_DB_PORT", "5432")),
            database=src.get("CLAWWORKER_DB_NAME", "clawworker"),
            user=src.get("CLAWWORKER_DB_USER", "clawworker"),
            password=src.get("CLAWWORKER_DB_PASSWORD", "clawworker"),
            min_size=int(src.get("CLAWWORKER_DB_POOL_MIN", "2")),
            max_size=int(src.get("CLAWWORKER_DB_POOL_MAX", "10")),
        )


_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


async def get_pool(config: Optional[DBPoolConfig] = None) -> asyncpg.Pool:
    """Get or create the shared connection pool."""
    global _pool
    if _pool is not None and not _pool._closed:
        return _pool
    async with _pool_lock:
        if _pool is not None and not _pool._closed:
            return _pool
        cfg = config or DBPoolConfig.from_env()
        _pool = await asyncpg.create_pool(
            host=cfg.host,
            port=cfg.port,
            database=cfg.database,
            user=cfg.user,
            password=cfg.password,
            min_size=cfg.min_size,
            max_size=cfg.max_size,
        )
        _log.info("DB pool created (%s@%s:%d/%s, %d-%d conns)",
                   cfg.user, cfg.host, cfg.port, cfg.database, cfg.min_size, cfg.max_size)
        return _pool


async def close_pool() -> None:
    """Close the shared pool (call on shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        _log.info("DB pool closed")

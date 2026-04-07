"""
strategy_config — per-strategy capital and risk parameters in DB.

Replaces hardcoded RiskConfig / ScalpingConfig values scattered across:
  - engines/scalping/scalping/config.py  (total_capital, risk_per_trade_pct, etc.)
  - ClawWork/livebench/trading/auto_trader.py  (RiskConfig)
  - ClawWork/livebench/bots/capital_allocator.py  (sleeve allocations)

Schema:
    strategy_config (
        strategy        TEXT PRIMARY KEY      -- 'scalping' | 'fyersn7' | 'clawwork'
        total_capital   NUMERIC NOT NULL      -- INR allocated to this strategy
        risk_per_trade_pct  NUMERIC NOT NULL  -- % of capital at risk per trade
        daily_loss_limit_pct NUMERIC NOT NULL -- % daily stop-loss
        max_positions   INT NOT NULL          -- max concurrent open positions
        enabled         BOOLEAN NOT NULL      -- runtime on/off switch
        params          JSONB                 -- strategy-specific overrides (lot size, OTM distance, etc.)
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Optional

import asyncpg


@dataclass
class StrategyConfig:
    strategy: str
    total_capital: float
    risk_per_trade_pct: float
    daily_loss_limit_pct: float
    max_positions: int
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)


# Sensible defaults per strategy — used for seeding on first run
_DEFAULTS: list[StrategyConfig] = [
    StrategyConfig(
        strategy="scalping",
        total_capital=100000.0,
        risk_per_trade_pct=5.0,
        daily_loss_limit_pct=10.0,
        max_positions=3,
        enabled=True,
        params={
            "entry_lots": 4,
            "partial_exit_pct": 0.55,
            "first_target_points": 4.0,
            "indices": ["NIFTY50", "BANKNIFTY", "SENSEX"],
        },
    ),
    StrategyConfig(
        strategy="fyersn7",
        total_capital=100000.0,
        risk_per_trade_pct=5.0,
        daily_loss_limit_pct=10.0,
        max_positions=2,
        enabled=True,
        params={
            "target_points": 25,
            "stop_loss_points": 15,
            "indices": ["NIFTY50", "BANKNIFTY", "SENSEX", "FINNIFTY"],
        },
    ),
    StrategyConfig(
        strategy="clawwork",
        total_capital=100000.0,
        risk_per_trade_pct=5.0,
        daily_loss_limit_pct=10.0,
        max_positions=1,
        enabled=True,
        params={
            "kelly_fraction": 0.25,
            "sleeve_allocations": {
                "trend": 0.35,
                "mean_reversion": 0.35,
                "momentum": 0.20,
                "event": 0.10,
            },
        },
    ),
]


@dataclass(frozen=True)
class StrategyConfigDBConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "clawworker"
    user: str = "clawworker"
    password: str = "clawworker"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "StrategyConfigDBConfig":
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
CREATE TABLE IF NOT EXISTS strategy_config (
    strategy             TEXT    PRIMARY KEY,
    total_capital        NUMERIC NOT NULL,
    risk_per_trade_pct   NUMERIC NOT NULL DEFAULT 5.0,
    daily_loss_limit_pct NUMERIC NOT NULL DEFAULT 10.0,
    max_positions        INT     NOT NULL DEFAULT 1,
    enabled              BOOLEAN NOT NULL DEFAULT TRUE,
    params               JSONB,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_UPSERT = """
INSERT INTO strategy_config
    (strategy, total_capital, risk_per_trade_pct, daily_loss_limit_pct,
     max_positions, enabled, params, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
ON CONFLICT (strategy) DO UPDATE SET
    total_capital        = EXCLUDED.total_capital,
    risk_per_trade_pct   = EXCLUDED.risk_per_trade_pct,
    daily_loss_limit_pct = EXCLUDED.daily_loss_limit_pct,
    max_positions        = EXCLUDED.max_positions,
    enabled              = EXCLUDED.enabled,
    params               = EXCLUDED.params,
    updated_at           = NOW()
"""

_SELECT_ALL = "SELECT * FROM strategy_config ORDER BY strategy"
_SELECT_ONE = "SELECT * FROM strategy_config WHERE strategy = $1"


class PostgresStrategyConfigRepository:
    def __init__(
        self,
        config: StrategyConfigDBConfig,
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

    async def seed_defaults(self, overwrite: bool = False) -> int:
        async with self._get_conn() as conn:
            seeded = 0
            for sc in _DEFAULTS:
                if not overwrite:
                    existing = await conn.fetchrow(_SELECT_ONE, sc.strategy)
                    if existing:
                        continue
                await self._upsert_one(conn, sc)
                seeded += 1
            return seeded

    async def upsert(self, config: StrategyConfig) -> None:
        async with self._get_conn() as conn:
            await self._upsert_one(conn, config)

    async def load(self, strategy: str) -> StrategyConfig | None:
        async with self._get_conn() as conn:
            row = await conn.fetchrow(_SELECT_ONE, strategy)
        return _row_to_config(row) if row else None

    async def load_all(self) -> list[StrategyConfig]:
        async with self._get_conn() as conn:
            rows = await conn.fetch(_SELECT_ALL)
        return [_row_to_config(r) for r in rows]

    async def set_enabled(self, strategy: str, enabled: bool) -> None:
        async with self._get_conn() as conn:
            await conn.execute(
                "UPDATE strategy_config SET enabled = $2, updated_at = NOW() WHERE strategy = $1",
                strategy, enabled,
            )

    async def _upsert_one(self, conn: asyncpg.Connection, sc: StrategyConfig) -> None:
        import json
        await conn.execute(
            _UPSERT,
            sc.strategy, sc.total_capital, sc.risk_per_trade_pct,
            sc.daily_loss_limit_pct, sc.max_positions, sc.enabled,
            json.dumps(sc.params) if sc.params else None,
        )

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(
            host=self._cfg.host,
            port=self._cfg.port,
            database=self._cfg.database,
            user=self._cfg.user,
            password=self._cfg.password,
        )


def _row_to_config(row: asyncpg.Record) -> StrategyConfig:
    import json
    params_raw = row["params"]
    params = json.loads(params_raw) if isinstance(params_raw, str) else (params_raw or {})
    return StrategyConfig(
        strategy=str(row["strategy"]),
        total_capital=float(row["total_capital"]),
        risk_per_trade_pct=float(row["risk_per_trade_pct"]),
        daily_loss_limit_pct=float(row["daily_loss_limit_pct"]),
        max_positions=int(row["max_positions"]),
        enabled=bool(row["enabled"]),
        params=params,
    )


def _run(coro):
    try:
        asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


def sync_ensure_strategy_config_schema(config: StrategyConfigDBConfig) -> None:
    _run(PostgresStrategyConfigRepository(config).ensure_schema())


def sync_seed_strategy_defaults(config: StrategyConfigDBConfig, overwrite: bool = False) -> int:
    return _run(PostgresStrategyConfigRepository(config).seed_defaults(overwrite))


def sync_load_strategy_config(config: StrategyConfigDBConfig, strategy: str) -> StrategyConfig | None:
    return _run(PostgresStrategyConfigRepository(config).load(strategy))


def sync_load_all_strategy_configs(config: StrategyConfigDBConfig) -> list[StrategyConfig]:
    return _run(PostgresStrategyConfigRepository(config).load_all())


def sync_upsert_strategy_config(config: StrategyConfigDBConfig, sc: StrategyConfig) -> None:
    _run(PostgresStrategyConfigRepository(config).upsert(sc))


def sync_set_strategy_enabled(config: StrategyConfigDBConfig, strategy: str, enabled: bool) -> None:
    _run(PostgresStrategyConfigRepository(config).set_enabled(strategy, enabled))

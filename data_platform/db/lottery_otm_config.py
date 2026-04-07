"""
lottery_otm_config — DB-driven configuration for the LotteryOTM selector.

Each row stores one parameter for one index (e.g. NIFTY50.delta_min = 0.05).
All thresholds and AI scoring weights live here — no hardcoded values in code.

Adding a new index:   seed_defaults() with the new index name.
Changing a threshold: UPDATE lottery_otm_config SET value = '...' WHERE ...
Disabling an index:   SET is_active = false on all rows for that index.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Sequence

import asyncpg

# ── Supported indices ──────────────────────────────────────────────────────
SUPPORTED_INDICES = ("NIFTY50", "BANKNIFTY", "SENSEX")

# ── Default parameter values (used for initial seed) ──────────────────────
_DEFAULTS: dict[str, dict[str, tuple[str, str]]] = {
    # param_name: (value, description)
    "delta_min":         ("0.05",  "Minimum absolute delta — filters out too-deep OTM strikes"),
    "delta_max":         ("0.20",  "Maximum absolute delta — filters out near-ATM strikes"),
    "price_min":         ("3.0",   "Minimum option LTP in ₹ — filters illiquid/zero-priced strikes"),
    "price_max":         ("80.0",  "Maximum option LTP in ₹ — filters expensive strikes"),
    "min_oi":            ("5000",  "Minimum open interest — liquidity gate"),
    "min_volume":        ("500",   "Minimum daily volume — activity gate"),
    "top_n":             ("3",     "Number of CE + PE strikes to select per index"),
    "gamma_weight":      ("0.40",  "AI scorer: weight for gamma/price ratio"),
    "liquidity_weight":  ("0.30",  "AI scorer: weight for OI+volume liquidity score"),
    "momentum_weight":   ("0.20",  "AI scorer: weight for momentum direction alignment"),
    "theta_penalty":     ("0.10",  "AI scorer: penalty for theta decay per ₹ of premium"),
}

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS lottery_otm_config (
    index_name   TEXT        NOT NULL,
    param_name   TEXT        NOT NULL,
    value        TEXT        NOT NULL,
    description  TEXT        NOT NULL DEFAULT '',
    is_active    BOOLEAN     NOT NULL DEFAULT true,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (index_name, param_name)
)
"""

_UPSERT = """
INSERT INTO lottery_otm_config
    (index_name, param_name, value, description, is_active, updated_at)
VALUES ($1, $2, $3, $4, $5, NOW())
ON CONFLICT (index_name, param_name) DO UPDATE SET
    value       = EXCLUDED.value,
    description = EXCLUDED.description,
    is_active   = EXCLUDED.is_active,
    updated_at  = NOW()
"""

_LIST_ALL = """
SELECT index_name, param_name, value, description, is_active
FROM lottery_otm_config
ORDER BY index_name, param_name
"""

_LIST_FOR_INDEX = """
SELECT param_name, value, description, is_active
FROM lottery_otm_config
WHERE index_name = $1 AND is_active = true
ORDER BY param_name
"""

_SET_VALUE = """
UPDATE lottery_otm_config
SET value = $3, updated_at = NOW()
WHERE index_name = $1 AND param_name = $2
"""


@dataclass(frozen=True)
class LotteryOTMParams:
    """Resolved, typed config for one index."""
    index_name: str
    delta_min: float
    delta_max: float
    price_min: float
    price_max: float
    min_oi: int
    min_volume: int
    top_n: int
    gamma_weight: float
    liquidity_weight: float
    momentum_weight: float
    theta_penalty: float

    @classmethod
    def from_row_dict(cls, index_name: str, rows: dict[str, str]) -> "LotteryOTMParams":
        def f(k: str, default: str) -> float:
            return float(rows.get(k, _DEFAULTS[k][0]) if k in rows else default)
        def i(k: str, default: str) -> int:
            return int(float(rows.get(k, _DEFAULTS[k][0]) if k in rows else default))
        return cls(
            index_name=index_name,
            delta_min=f("delta_min", _DEFAULTS["delta_min"][0]),
            delta_max=f("delta_max", _DEFAULTS["delta_max"][0]),
            price_min=f("price_min", _DEFAULTS["price_min"][0]),
            price_max=f("price_max", _DEFAULTS["price_max"][0]),
            min_oi=i("min_oi", _DEFAULTS["min_oi"][0]),
            min_volume=i("min_volume", _DEFAULTS["min_volume"][0]),
            top_n=i("top_n", _DEFAULTS["top_n"][0]),
            gamma_weight=f("gamma_weight", _DEFAULTS["gamma_weight"][0]),
            liquidity_weight=f("liquidity_weight", _DEFAULTS["liquidity_weight"][0]),
            momentum_weight=f("momentum_weight", _DEFAULTS["momentum_weight"][0]),
            theta_penalty=f("theta_penalty", _DEFAULTS["theta_penalty"][0]),
        )


@dataclass(frozen=True)
class LotteryOTMConfigEntry:
    index_name: str
    param_name: str
    value: str
    description: str
    is_active: bool


@dataclass(frozen=True)
class LotteryOTMConfigStore:
    host: str = "localhost"
    port: int = 5432
    database: str = "clawworker"
    user: str = "clawworker"
    password: str = "clawworker"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "LotteryOTMConfigStore":
        import os
        src = env or dict(os.environ)
        return cls(
            host=src.get("CLAWWORKER_DB_HOST", "localhost"),
            port=int(src.get("CLAWWORKER_DB_PORT", "5432")),
            database=src.get("CLAWWORKER_DB_NAME", "clawworker"),
            user=src.get("CLAWWORKER_DB_USER", "clawworker"),
            password=src.get("CLAWWORKER_DB_PASSWORD", "clawworker"),
        )


class PostgresLotteryOTMConfigRepository:
    def __init__(self, config: LotteryOTMConfigStore) -> None:
        self._cfg = config

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(
            host=self._cfg.host,
            port=self._cfg.port,
            database=self._cfg.database,
            user=self._cfg.user,
            password=self._cfg.password,
        )

    async def ensure_schema(self) -> None:
        conn = await self._connect()
        try:
            await conn.execute(_CREATE_TABLE)
        finally:
            await conn.close()

    async def seed_defaults(self, indices: Sequence[str] = SUPPORTED_INDICES) -> int:
        conn = await self._connect()
        count = 0
        try:
            async with conn.transaction():
                for index in indices:
                    for param, (value, desc) in _DEFAULTS.items():
                        await conn.execute(_UPSERT, index, param, value, desc, True)
                        count += 1
        finally:
            await conn.close()
        return count

    async def list_all(self) -> list[LotteryOTMConfigEntry]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(_LIST_ALL)
            return [
                LotteryOTMConfigEntry(
                    index_name=r["index_name"],
                    param_name=r["param_name"],
                    value=r["value"],
                    description=r["description"],
                    is_active=r["is_active"],
                )
                for r in rows
            ]
        finally:
            await conn.close()

    async def load_params(self, index_name: str) -> LotteryOTMParams:
        conn = await self._connect()
        try:
            rows = await conn.fetch(_LIST_FOR_INDEX, index_name)
            row_dict = {r["param_name"]: r["value"] for r in rows}
            return LotteryOTMParams.from_row_dict(index_name, row_dict)
        finally:
            await conn.close()

    async def set_value(self, index_name: str, param_name: str, value: str) -> None:
        conn = await self._connect()
        try:
            await conn.execute(_SET_VALUE, index_name, param_name, value)
        finally:
            await conn.close()


# ── Sync wrappers ──────────────────────────────────────────────────────────

def _run(coro):
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


def sync_ensure_lottery_schema(config: LotteryOTMConfigStore) -> None:
    _run(PostgresLotteryOTMConfigRepository(config).ensure_schema())


def sync_seed_lottery_defaults(
    config: LotteryOTMConfigStore,
    indices: Sequence[str] = SUPPORTED_INDICES,
) -> int:
    return _run(PostgresLotteryOTMConfigRepository(config).seed_defaults(indices))


def sync_list_lottery_config(config: LotteryOTMConfigStore) -> list[LotteryOTMConfigEntry]:
    return _run(PostgresLotteryOTMConfigRepository(config).list_all())


def sync_load_lottery_params(config: LotteryOTMConfigStore, index_name: str) -> LotteryOTMParams:
    return _run(PostgresLotteryOTMConfigRepository(config).load_params(index_name))


def sync_set_lottery_param(
    config: LotteryOTMConfigStore,
    index_name: str,
    param_name: str,
    value: str,
) -> None:
    _run(PostgresLotteryOTMConfigRepository(config).set_value(index_name, param_name, value))

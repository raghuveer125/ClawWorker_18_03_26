"""
market_field_schema — single DB table that owns all field definitions
for every stream type (tick, candle, quote, option_chain, futures, vix).

Each row describes one field:
  - stream_type     : which payload type this field belongs to
  - field_name      : canonical name used everywhere in the pipeline
  - field_type      : "float" | "int" | "str" | "bool"
  - is_required     : whether the field must be present for validation to pass
  - broker_aliases  : JSON array of alternative names the broker may send
                      e.g. ["lp", "last_price", "lastTradedPrice"] for ltp
  - description     : human-readable note
  - is_active       : false = soft-deleted, ignored by all consumers
  - sort_order      : display / iteration order within a stream_type

Adding a new field:  INSERT a row.
Removing a field:    SET is_active = false.
Adding an alias:     UPDATE broker_aliases.
No code change needed.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Sequence

import asyncpg


@dataclass(frozen=True)
class FieldDefinition:
    stream_type: str
    field_name: str
    field_type: str
    is_required: bool
    broker_aliases: tuple[str, ...]
    description: str
    is_active: bool = True
    sort_order: int = 0


_CREATE_FIELD_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_field_schema (
    stream_type     TEXT    NOT NULL,
    field_name      TEXT    NOT NULL,
    field_type      TEXT    NOT NULL DEFAULT 'str',
    is_required     BOOLEAN NOT NULL DEFAULT false,
    broker_aliases  JSONB   NOT NULL DEFAULT '[]',
    description     TEXT    NOT NULL DEFAULT '',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    sort_order      INT     NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stream_type, field_name)
)
"""

_UPSERT_FIELD = """
INSERT INTO market_field_schema
    (stream_type, field_name, field_type, is_required, broker_aliases, description, is_active, sort_order, updated_at)
VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, NOW())
ON CONFLICT (stream_type, field_name) DO UPDATE SET
    field_type     = EXCLUDED.field_type,
    is_required    = EXCLUDED.is_required,
    broker_aliases = EXCLUDED.broker_aliases,
    description    = EXCLUDED.description,
    is_active      = EXCLUDED.is_active,
    sort_order     = EXCLUDED.sort_order,
    updated_at     = NOW()
"""

_LIST_ACTIVE = """
SELECT stream_type, field_name, field_type, is_required, broker_aliases, description, is_active, sort_order
FROM market_field_schema
WHERE is_active = true
ORDER BY stream_type, sort_order, field_name
"""

_LIST_BY_STREAM = """
SELECT stream_type, field_name, field_type, is_required, broker_aliases, description, is_active, sort_order
FROM market_field_schema
WHERE stream_type = $1 AND is_active = true
ORDER BY sort_order, field_name
"""

_DEACTIVATE_FIELD = """
UPDATE market_field_schema SET is_active = false, updated_at = NOW()
WHERE stream_type = $1 AND field_name = $2
"""


def _row_to_def(row: asyncpg.Record) -> FieldDefinition:
    aliases_raw = row["broker_aliases"]
    if isinstance(aliases_raw, str):
        aliases = tuple(json.loads(aliases_raw))
    elif isinstance(aliases_raw, list):
        aliases = tuple(aliases_raw)
    else:
        aliases = ()
    return FieldDefinition(
        stream_type=row["stream_type"],
        field_name=row["field_name"],
        field_type=row["field_type"],
        is_required=row["is_required"],
        broker_aliases=aliases,
        description=row["description"],
        is_active=row["is_active"],
        sort_order=row["sort_order"],
    )


@dataclass(frozen=True)
class FieldSchemaConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "clawworker"
    user: str = "clawworker"
    password: str = "clawworker"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "FieldSchemaConfig":
        import os
        source = env or dict(os.environ)
        return cls(
            host=source.get("CLAWWORKER_DB_HOST", "localhost"),
            port=int(source.get("CLAWWORKER_DB_PORT", "5432")),
            database=source.get("CLAWWORKER_DB_NAME", "clawworker"),
            user=source.get("CLAWWORKER_DB_USER", "clawworker"),
            password=source.get("CLAWWORKER_DB_PASSWORD", "clawworker"),
        )


class PostgresFieldSchemaRepository:
    def __init__(self, config: FieldSchemaConfig) -> None:
        self._config = config

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(
            host=self._config.host,
            port=self._config.port,
            database=self._config.database,
            user=self._config.user,
            password=self._config.password,
        )

    async def ensure_schema(self) -> None:
        conn = await self._connect()
        try:
            await conn.execute(_CREATE_FIELD_SCHEMA)
        finally:
            await conn.close()

    async def upsert(self, field: FieldDefinition) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                _UPSERT_FIELD,
                field.stream_type,
                field.field_name,
                field.field_type,
                field.is_required,
                json.dumps(list(field.broker_aliases)),
                field.description,
                field.is_active,
                field.sort_order,
            )
        finally:
            await conn.close()

    async def upsert_many(self, fields: Sequence[FieldDefinition]) -> int:
        conn = await self._connect()
        try:
            rows = [
                (
                    f.stream_type,
                    f.field_name,
                    f.field_type,
                    f.is_required,
                    json.dumps(list(f.broker_aliases)),
                    f.description,
                    f.is_active,
                    f.sort_order,
                )
                for f in fields
            ]
            async with conn.transaction():
                for row in rows:
                    await conn.execute(_UPSERT_FIELD, *row)
            return len(rows)
        finally:
            await conn.close()

    async def list_active(self) -> list[FieldDefinition]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(_LIST_ACTIVE)
            return [_row_to_def(r) for r in rows]
        finally:
            await conn.close()

    async def list_for_stream(self, stream_type: str) -> list[FieldDefinition]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(_LIST_BY_STREAM, stream_type)
            return [_row_to_def(r) for r in rows]
        finally:
            await conn.close()

    async def deactivate(self, stream_type: str, field_name: str) -> None:
        conn = await self._connect()
        try:
            await conn.execute(_DEACTIVATE_FIELD, stream_type, field_name)
        finally:
            await conn.close()


def _run(coro):
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


def sync_ensure_field_schema(config: FieldSchemaConfig) -> None:
    _run(PostgresFieldSchemaRepository(config).ensure_schema())


def sync_upsert_field(config: FieldSchemaConfig, field: FieldDefinition) -> None:
    _run(PostgresFieldSchemaRepository(config).upsert(field))


def sync_upsert_fields(config: FieldSchemaConfig, fields: Sequence[FieldDefinition]) -> int:
    return _run(PostgresFieldSchemaRepository(config).upsert_many(fields))


def sync_list_active_fields(config: FieldSchemaConfig) -> list[FieldDefinition]:
    return _run(PostgresFieldSchemaRepository(config).list_active())


def sync_list_fields_for_stream(config: FieldSchemaConfig, stream_type: str) -> list[FieldDefinition]:
    return _run(PostgresFieldSchemaRepository(config).list_for_stream(stream_type))


def sync_deactivate_field(config: FieldSchemaConfig, stream_type: str, field_name: str) -> None:
    _run(PostgresFieldSchemaRepository(config).deactivate(stream_type, field_name))


def build_validation_schema(fields: Sequence[FieldDefinition]) -> dict[str, dict]:
    """
    Convert a list of FieldDefinition rows into the dict shape that
    ValidationService._schema_for_payload_type() returns:
        { "required": (...,), "types": { field_name: (type, ...) } }
    Grouped by stream_type.
    """
    _TYPE_MAP: dict[str, tuple[type, ...]] = {
        "float": (int, float),
        "int": (int,),
        "str": (str,),
        "bool": (bool,),
    }
    by_stream: dict[str, dict] = {}
    for f in fields:
        if f.stream_type not in by_stream:
            by_stream[f.stream_type] = {"required": [], "types": {}}
        if f.is_required:
            by_stream[f.stream_type]["required"].append(f.field_name)
        by_stream[f.stream_type]["types"][f.field_name] = _TYPE_MAP.get(f.field_type, (str,))
    for v in by_stream.values():
        v["required"] = tuple(v["required"])
    return by_stream


def build_alias_map(fields: Sequence[FieldDefinition]) -> dict[str, dict[str, str]]:
    """
    Convert FieldDefinition rows into a broker-alias lookup:
        { stream_type: { alias: canonical_field_name } }
    Used by TickNormalizer and _normalize_ws_tick to map raw broker keys.
    """
    by_stream: dict[str, dict[str, str]] = {}
    for f in fields:
        if f.stream_type not in by_stream:
            by_stream[f.stream_type] = {}
        for alias in f.broker_aliases:
            by_stream[f.stream_type][alias] = f.field_name
    return by_stream

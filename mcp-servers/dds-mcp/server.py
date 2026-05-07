"""DDS MCP server — read-only inspection tools for the DDS2 PostgreSQL + Redis stack.

Exposes a small set of tools for Claude Code to query the local dev DB / cache
without spawning bash + psql + redis-cli pipelines on every debug session.

All SQL is parameterized; tables are validated against information_schema;
project_id and is_deleted filters are applied automatically when the columns
exist on the queried table.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg
import redis.asyncio as aioredis
from mcp.server.fastmcp import FastMCP

# 5434 — read-only host port for MCP, mapped in docker-compose.yml (db service)
DB_DSN = os.environ.get(
    "DDS_DB_DSN",
    "postgresql://dds:dds_secret@127.0.0.1:5434/dds_db",
)
REDIS_URL = os.environ.get(
    "DDS_REDIS_URL",
    "redis://:dds_redis_secret@127.0.0.1:6379/0",
)

mcp = FastMCP("dds")

_pool: asyncpg.Pool | None = None
_redis: aioredis.Redis | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=3, timeout=10)
    return _pool


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"unserializable: {type(obj).__name__}")


def _dump(obj: Any) -> str:
    return json.dumps(obj, default=_json_default, ensure_ascii=False, indent=2)


async def _table_exists(table: str) -> bool:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        return bool(
            await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = $1
                )
                """,
                table,
            )
        )


async def _has_column(table: str, column: str) -> bool:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        return bool(
            await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = $1
                      AND column_name = $2
                )
                """,
                table,
                column,
            )
        )


_SQL_FORBIDDEN = (
    ";",
    "--",
    "/*",
    "*/",
    " drop ",
    " delete ",
    " insert ",
    " update ",
    " truncate ",
    " alter ",
    " grant ",
    " revoke ",
)


def _check_no_dml(fragment: str, field: str) -> None:
    """Reject obvious DML/DDL tokens in a user-supplied SQL fragment."""
    lowered = f" {fragment.lower()} "
    for token in _SQL_FORBIDDEN:
        if token in lowered:
            raise ValueError(f"forbidden token {token!r} in {field}")


# ---------------------------- TOOLS ----------------------------


@mcp.tool()
async def list_projects(limit: int = 50) -> str:
    """List active projects with id, name, slug, owner_id, created_at."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, slug, owner_id, created_at, tax_rate, vat_rate
            FROM projects
            WHERE is_deleted = false
            ORDER BY id DESC
            LIMIT $1
            """,
            min(max(int(limit), 1), 500),
        )
    return _dump([dict(r) for r in rows])


@mcp.tool()
async def db_schema(table: str) -> str:
    """Show columns of a public-schema table + flags whether it has project_id / is_deleted."""
    if not await _table_exists(table):
        raise ValueError(f"unknown table: {table!r}")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position
            """,
            table,
        )
    columns = [dict(r) for r in rows]
    return _dump(
        {
            "table": table,
            "auto_project_id_filter": any(c["column_name"] == "project_id" for c in columns),
            "auto_is_deleted_filter": any(c["column_name"] == "is_deleted" for c in columns),
            "columns": columns,
        }
    )


@mcp.tool()
async def query_project(
    project_id: int,
    table: str,
    columns: str = "*",
    where: str | None = None,
    order_by: str | None = None,
    limit: int = 50,
) -> str:
    """Read-only SELECT with auto project_id and is_deleted filters.

    Args:
        project_id: tenant scope (required even if the table has no project_id —
            you'll get an explicit "no project_id column" notice in that case).
        table: public-schema table name (validated against information_schema).
        columns: comma-separated columns or '*'.
        where: extra predicates (no AND prefix). DML tokens rejected.
        order_by: e.g. 'created_at DESC'. DML tokens rejected.
        limit: capped at 500.
    """
    if not await _table_exists(table):
        raise ValueError(f"unknown table: {table!r}")

    has_project_id = await _has_column(table, "project_id")
    has_is_deleted = await _has_column(table, "is_deleted")

    parts = [f"SELECT {columns}", f"FROM {table}", "WHERE 1=1"]
    params: list[Any] = []

    if has_project_id:
        params.append(int(project_id))
        parts.append(f"AND project_id = ${len(params)}")

    if has_is_deleted:
        parts.append("AND is_deleted = false")

    if where:
        _check_no_dml(where, "where")
        parts.append(f"AND ({where})")

    if order_by:
        _check_no_dml(order_by, "order_by")
        parts.append(f"ORDER BY {order_by}")

    capped = min(max(int(limit), 1), 500)
    parts.append(f"LIMIT {capped}")
    sql = "\n".join(parts)

    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    return _dump(
        {
            "sql": sql,
            "params": params,
            "row_count": len(rows),
            "auto_project_id_filter": has_project_id,
            "auto_is_deleted_filter": has_is_deleted,
            "rows": [dict(r) for r in rows],
        }
    )


@mcp.tool()
async def inspect_cache(prefix: str, limit: int = 50) -> str:
    """List Redis keys matching prefix + their TTL and type. Read-only."""
    if not prefix or len(prefix) < 2:
        raise ValueError("prefix must be at least 2 chars to avoid full scan")
    r = await _get_redis()
    pattern = prefix if prefix.endswith("*") else f"{prefix}*"
    keys: list[str] = []
    cursor: int = 0
    capped = min(max(int(limit), 1), 200)
    while True:
        cursor, batch = await r.scan(cursor=cursor, match=pattern, count=200)
        keys.extend(batch)
        if cursor == 0 or len(keys) >= capped:
            break
    keys = keys[:capped]

    out = []
    for k in keys:
        try:
            ttl = await r.ttl(k)
            ktype = await r.type(k)
            out.append({"key": k, "ttl_seconds": ttl, "type": ktype})
        except Exception as exc:
            out.append({"key": k, "error": str(exc)})
    return _dump({"pattern": pattern, "count": len(out), "keys": out})


@mcp.tool()
async def sync_status(project_id: int, service: str | None = None, limit: int = 10) -> str:
    """Last sync_log entries for a project (joined via integration_keys.project_id).

    Args:
        project_id: tenant scope.
        service: optional service filter (e.g. 'wb', 'ozon').
        limit: capped at 100.
    """
    pool = await _get_pool()
    capped = min(max(int(limit), 1), 100)
    async with pool.acquire() as conn:
        if service:
            rows = await conn.fetch(
                """
                SELECT sl.id, sl.integration_id, sl.service, sl.sync_type,
                       sl.status, sl.started_at, sl.finished_at,
                       sl.rows_fetched, sl.rows_inserted, sl.error_msg
                FROM sync_log sl
                JOIN integration_keys ik ON ik.id = sl.integration_id
                WHERE ik.project_id = $1 AND sl.service = $2
                ORDER BY sl.started_at DESC
                LIMIT $3
                """,
                int(project_id),
                service,
                capped,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT sl.id, sl.integration_id, sl.service, sl.sync_type,
                       sl.status, sl.started_at, sl.finished_at,
                       sl.rows_fetched, sl.rows_inserted, sl.error_msg
                FROM sync_log sl
                JOIN integration_keys ik ON ik.id = sl.integration_id
                WHERE ik.project_id = $1
                ORDER BY sl.started_at DESC
                LIMIT $2
                """,
                int(project_id),
                capped,
            )
    return _dump([dict(r) for r in rows])


@mcp.tool()
async def migration_status() -> str:
    """Current Alembic head(s) — useful for validating migration state."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT version_num FROM alembic_version")
    return _dump({"current_heads": [r["version_num"] for r in rows]})


@mcp.tool()
async def list_tables(pattern: str = "%") -> str:
    """List tables in public schema (optional ILIKE pattern, e.g. 'wb_%')."""
    _check_no_dml(pattern, "pattern")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT table_name,
                   (SELECT COUNT(*) FROM information_schema.columns c
                      WHERE c.table_schema = 'public' AND c.table_name = t.table_name) AS column_count
            FROM information_schema.tables t
            WHERE table_schema = 'public' AND table_name ILIKE $1
            ORDER BY table_name
            """,
            pattern,
        )
    return _dump({"pattern": pattern, "count": len(rows), "tables": [dict(r) for r in rows]})


if __name__ == "__main__":
    mcp.run(transport="stdio")

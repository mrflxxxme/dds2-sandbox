# dds-mcp

Read-only MCP server для быстрой инспекции DDS2 (PostgreSQL + Redis) из Claude Code.
Заменяет рутинные `bash + psql + redis-cli` цепочки в типовой отладке.

## Tools

| Tool | Что делает |
|---|---|
| `list_projects(limit)` | Активные проекты (id, name, slug, owner, tax_rate, vat_rate) |
| `list_tables(pattern)` | Таблицы public-схемы по ILIKE (`'wb_%'`, `'%cost%'`) |
| `db_schema(table)` | Колонки таблицы + флаги «есть project_id», «есть is_deleted» |
| `query_project(project_id, table, columns, where, order_by, limit)` | Безопасный SELECT с авто-фильтрами `project_id` и `is_deleted=false` |
| `inspect_cache(prefix, limit)` | Redis SCAN по prefix + TTL/type каждого ключа |
| `sync_status(project_id, service, limit)` | Последние записи `sync_log` через JOIN `integration_keys` |
| `migration_status()` | Текущие alembic heads |

## Безопасность (defense-in-depth)

1. **PG роль `readonly_agent`** — `GRANT SELECT` only, `INSERT/UPDATE/DELETE` блокируются на уровне Postgres (создаётся `postgres/init-readonly-user.sql`)
2. **Хост-порт `127.0.0.1:5434`** — read-only mapping `db` (не PgBouncer), localhost-only
3. **DML/DDL token rejection** в user-supplied фрагментах (`where`, `order_by`, `pattern`)
4. **`information_schema` валидация** имени таблицы перед SELECT
5. **Параметризованные значения** (asyncpg `$1, $2, ...`)
6. **Redis read-only** операции (`SCAN`, `TTL`, `TYPE`, `decode_responses=True`)

## Установка

```bash
cd mcp-servers/dds-mcp
uv sync   # или: pip install -e .
```

## Регистрация в Claude Code

В `.mcp.json` добавлен блок `dds`:

```json
"dds": {
  "command": "uv",
  "args": ["--directory", "mcp-servers/dds-mcp", "run", "python", "server.py"]
}
```

Перезапустите Claude Code (`claude` → новая сессия) — tools появятся как `mcp__dds__*`.

## Env

По умолчанию читает `dds:dds_secret@127.0.0.1:5434/dds_db` и `redis://:dds_redis_secret@127.0.0.1:6379/0`.
Переопределить через переменные:

```bash
export DDS_DB_DSN="postgresql://user:pass@host:port/db"
export DDS_REDIS_URL="redis://:pass@host:port/0"
```

## Примеры использования (изнутри Claude Code)

```
# Топ проектов
mcp__dds__list_projects()

# Что вообще есть про WB
mcp__dds__list_tables(pattern="wb_%")

# Схема таблицы перед запросом
mcp__dds__db_schema(table="wb_funnel_daily")

# Прочитать данные проекта 15 за последний день
mcp__dds__query_project(
  project_id=15,
  table="wb_funnel_daily",
  columns="date, nm_id, orders_count, adv_sum",
  where="date >= CURRENT_DATE - INTERVAL '7 days'",
  order_by="date DESC, nm_id",
  limit=100
)

# Cache-кеи отчёта
mcp__dds__inspect_cache(prefix="reports:opiu:", limit=20)

# Последние WB-синки проекта
mcp__dds__sync_status(project_id=15, service="wb", limit=5)
```

## TODO (по мере использования)

- [ ] Доменные tools: `cost_trace_fifo(sku_id)`, `wb_stock_diff(date_from, date_to)`
- [ ] Read-only DB role (сейчас подключается под полным `dds` user, изоляция только через port 5434)
- [ ] `audit_log` query helper

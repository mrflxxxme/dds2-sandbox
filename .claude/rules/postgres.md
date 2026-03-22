---
paths:
  - "**/*.py"
  - "migrations/**"
---
# PostgreSQL правила DDS2

## Типы данных
- `bigint` для ID
- `text` для строк (не varchar(255) без причины)
- `timestamptz` для дат (не timestamp без tz)
- `Numeric(18,2)` для денег (НИКОГДА Float)
- `boolean` для флагов

## Multi-tenancy
- КАЖДАЯ таблица имеет `project_id` FK
- КАЖДЫЙ запрос фильтрует по `project_id`
- `project_id` ПЕРВЫЙ в composite index

## Soft Delete
- SoftDeleteMixin: `is_deleted`, `deleted_at`
- КАЖДЫЙ запрос: `.where(Model.is_deleted == False)`
- Partial index: `WHERE is_deleted = false`

## Индексы
- FK колонки ВСЕГДА проиндексированы
- Composite: equality колонки первыми, потом range
- Partial indexes для soft delete
- Covering indexes (`INCLUDE`) где возможно

## Запросы
- Параметризованный SQL (`:param` binding)
- `.limit()` для `.scalars().all()`
- JOIN или batch вместо N+1
- Cursor pagination (`WHERE id > :last`) вместо OFFSET
- Batch inserts (не по одному в цикле)
- Короткие транзакции (не держать lock при API вызовах)

## Миграции Alembic
- Использовать `DATABASE_URL_SYNC` (прямое подключение)
- Миграции последовательно (не параллельно)
- Downgrade определён
- Данные не теряются

## PgBouncer
- Mode: transaction
- `prepared_statement_cache_size=0`
- Pool: min=5, default=20, max=50
- Statement timeout через event listener (НЕ server_settings)

## Анти-паттерны
- `SELECT *` в production коде
- `OFFSET` для пагинации больших таблиц
- Float для денег
- `timestamp` без timezone
- INSERT в цикле (использовать batch)
- Lock + external API call (deadlock risk)

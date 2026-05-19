---
paths:
  - "backend/**/*.py"
  - "migrations/versions/*.py"
  - "tests/**/*.py"
---

# Backend правила DDS2 (Python + PostgreSQL + Security)

## Архитектура слоёв
`routers/` (HTTP, валидация) → `services/` (логика) → `models/` (ORM). Порядок нового модуля: Model → Migration → Schema → Service → Router → Test.

## Типы данных PostgreSQL
- `bigint` ID, `text` строки, `timestamptz` даты, `Numeric(18,2)` деньги, `boolean` флаги

## Multi-tenancy + Soft Delete
- КАЖДЫЙ запрос фильтрует `project_id` + `.where(Model.is_deleted == False)` для SoftDeleteMixin
- Удаление → `soft_delete()`, не `db.delete()`
- Дочерние без project_id → проверять parent.project_id

## Запросы
- `:param` binding (НИКОГДА f-string в `text()`)
- `.limit()` для `.scalars().all()`
- Batch inserts, JOIN вместо N+1
- `ilike()` с экранированием `%`/`_`

## Кэш (Redis)
- `@cached(prefix, ttl=300)` — ключ MUST содержать project_id
- `invalidate_cache(prefix)` после мутаций — суффикс `:*` добавляется сам

## PgBouncer
- `prepared_statement_cache_size=0`, `DATABASE_URL_SYNC` для Alembic/ETL

## Безопасность
- API-ключи шифруются `utils/crypto.py` (legacy_fallback — не менять)
- Upload → проверять `MAX_UPLOAD_SIZE_MB` ПЕРЕД обработкой
- scheduler jobs: `except asyncio.CancelledError: raise` ПЕРЕД `except Exception`
- Мутации настроек → через `project_settings_service`

## Тестирование (pytest, asyncio_mode=auto)
```bash
make test / test-fast / test-changed / test-unit
docker compose exec backend pytest tests/ -x --tb=short
```
Обязательно: happy path, edge cases, multi-tenancy изоляция, soft delete фильтрация.

## Анти-паттерны
- `SELECT *`, `.scalars().all()` без `.limit()`, Float для денег
- `datetime.utcnow()` → `from backend.utils.time import utcnow`
- `except Exception` без CancelledError в scheduler
- Логика в роутере, сервис >500 строк, функция >50 строк

---
paths:
  - "**/*"
---

# DDS Learnings (накопленные решения)

## Ошибки и решения
- Alembic "multiple heads": `alembic merge heads -m "merge"` затем `alembic upgrade head`
- FastAPI 422: отсутствует Pydantic валидатор, проверь типы полей схемы
- Next.js hydration mismatch: убедись что сервер и клиент рендерят одинаковое начальное состояние
- Docker "port already in use": `docker compose down` затем `lsof -i :8000`
- PgBouncer prepared statements: `prepared_statement_cache_size=0`, используй `DATABASE_URL_SYNC` для Alembic
- Redis ConnectionError: проверь `REDIS_URL` в .env и что redis контейнер запущен
- ilike injection (P4): ВСЕГДА экранируй `%` и `_` в пользовательском вводе для ILIKE
- Case-sensitive JOINs (P26): коды WB могут отличаться регистром — нормализуй перед JOIN

## Паттерны которые работают
- Новый API endpoint: schema → service → router → test (в этом порядке)
- Новая страница: скопируй ближайшую существующую, модифицируй
- DB миграция: тестируй `upgrade head && downgrade -1 && upgrade head`
- Кэш: после мутации вызывай `invalidate_cache(prefix)` — суффикс `:*` добавляется автоматически
- Отчёты с датами (P25): привязывай rolling period к дате запроса, не к "сегодня"

## Антипаттерны (не повторять)
- `SELECT *` в продакшн запросах — всегда указывай колонки
- Пропуск type annotations на возвращаемых значениях сервисов
- Создание миграции без проверки `alembic heads`
- `.scalars().all()` без `.limit()` на больших таблицах
- `except Exception` без `asyncio.CancelledError` в scheduler jobs

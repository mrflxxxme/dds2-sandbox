# AGENTS.md — DDS Project Guide

## Обзор
**DDS** — система управленческого учёта (ДДС): импорт выписок, категоризация, отчёты, кэшфлоу.
**Стек:** FastAPI + PostgreSQL + Redis + MinIO + Next.js 15

## Структура
```
backend/
  main.py, config.py, database.py, auth.py, cache.py, storage.py, exceptions.py
  models/        — ORM (auth, refs, transactions, planning, cost, customs, integrations, wb_finance, fx_rates, tax, enums)
  schemas/       — Pydantic (auth, common, cost, imports, integrations, planning, refs, reports, tax, transactions)
  routers/       — HTTP (auth, projects, import_txn, refs, reports, planning, cost, integrations, funnel, ws)
  services/      — Logic (reports/, planning/, cost/, funnel/, transactions, refs, integrations, opiu, wb_bdr, wb_finance_sync, fx, tax)
  etl/parsers/   — Bank parsers (vtb.py, wb.py, helpers.py)
  utils/         — crypto, file_validation, queries, time, telegram
  seeds/         — default_categories.py

frontend-react/src/
  app/p/[slug]/  — pages: dashboard, import, txn, inbox, reports, dds, planning, orders, cost, funnel, trends, refs, settings, team
  lib/api.ts     — 80+ API methods, JWT auth + refresh
  lib/utils.ts   — formatNumber, formatDate, exportToExcel
  components/    — DataTable, FormModal, PageHeader, TabLayout, Toast
  types/api.ts   — TypeScript interfaces

tests/           — 16+ files, 249 tests
```

## ⚠️ CRITICAL Rules

### RLS / Multi-tenancy
- Каждая DB-сессия **MUST** `SET LOCAL app.project_id`
- HTTP: через `Depends(get_current_project)` → `project.id`
- Background tasks / scheduler: `get_db_with_rls(project_id)`
- Без `project_id` → данные **утекают** между проектами
- Cache key без `project_id` → утечка кэша между проектами

### Crypto
- Шифрование API-ключей: **только** `backend/utils/crypto.py` (`encrypt` / `decrypt`)
- Есть `legacy_fallback` — не менять без data-migration
- Любое изменение крипто → Alembic migration скрипт **ОБЯЗАТЕЛЕН**

### Background Tasks / Scheduler
- `sync_log`: **ВСЕГДА** обновлять в `finally` → никогда не оставлять `RUNNING`
- `asyncio.wait_for(timeout=600)` для всех фоновых задач
- При старте: stale cleanup — `RUNNING` > 10 min → `STALE`
- File lock (`/tmp/.dds_scheduler.lock`) — один scheduler на все workers

## Антипаттерны

### Backend
- Бизнес-логика в роутере → выносить в `service`
- `datetime.utcnow()` / `datetime.now(tz)` → `from backend.utils.time import utcnow`
- `Float` для денег → `Numeric(18, 2)`
- f-string в SQL → параметризованный `:param` binding
- Запрос без `project_id` → `project_select(Model, project_id)`
- Без `is_deleted` → `project_select_active(Model, project_id)`
- List без пагинации → `limit/offset`
- Мутация без инвалидации → `await invalidate_cache("reports:*")`
- `ilike(f"%{input}%")` → экранировать `%`/`_`
- Сервис > 400 строк → разбить
- `db.delete(model)` → `model.soft_delete()` (наследовать `SoftDeleteMixin`)
- Новая модель без timestamps → наследовать `TimestampMixin`
- Generic `raise Exception` → `raise HTTPException` с кодом из `_status_to_code()`
- Ошибка Redis/MinIO → graceful degradation (warning, не crash)

### Frontend
- Сырые числа/даты → `formatNumber()` / `formatDate()`
- Таблица без Excel → кнопка с `exportToExcel()`
- Без loading/error states → обязательно
- Inline типы → `types/api.ts`
- Прямые `fetch()` → методы `api.ts` (кроме FormData upload)
- `<any>` в типах → описать конкретный интерфейс
- Новый endpoint → **ВСЕГДА** добавить метод в `api.ts` + тип в `types/api.ts`
- Upload (FormData) → отдельный `fetch` с `Authorization` + `X-Project-Id` headers

## Conventions

### Архитектура
- Router = HTTP only, Service = logic, Model = ORM only
- SQLAlchemy async, Models/Schemas split по доменам
- Новая модель → Model + Schema + Router + Service + тест
- Порядок: Model → Alembic migration → Schema → Service → Router

### Middleware stack (порядок важен!)
```
CORS → RateLimit → RequestID → SlowQuery → ErrorHandler
```

### Error handling
- `HTTPException` → unified `{error: {code, message, details}}`
- Generic 500 → `"Внутренняя ошибка сервера"` (без стектрейса клиенту)
- Rate limit / Redis / MinIO → **fail-open** (пропускаем, не падаем)

### API versioning
- Все эндпоинты: `/api/v1/...`
- Новые поля в response → `Optional` с дефолтом
- Удаление полей → **НИКОГДА** без версионирования

### Тестирование
- `@cached(ttl=300)` для отчётов, инвалидация при мутации
- TDD: RED → GREEN → REFACTOR
- Тесты: `docker compose exec backend pytest tests/ -x --tb=short`

### Git & Deploy
- Коммиты на русском: `feat:` / `fix:` / `infra:` / `refactor:` / `test:`
- Push в `dev` → staging → проверка → `main`
- Hot-reload: `.py` / `.tsx` — автоматически
- Docker rebuild: только Dockerfile, docker-compose, package.json, requirements

## Кэширование
Cached: balance, balance_daily, dds_month, income_daily, dashboard, opiu, wb_bdr, cashflow
Invalidation: импорт → `reports:*`, WB sync → opiu/wb_bdr/dashboard, категоризация → balance/dashboard/dds_month

## WB API
- Rate limits: `asyncio.Semaphore`, отдельные semaphore для Stats и Adv API
- 429 → нормальный лимит (`RateLimitError`, respect `Retry-After`), **НЕ** Circuit Breaker
- Circuit Breaker → **только** для 500-504
- Partial data → сохранять уже загруженные дни при ошибках

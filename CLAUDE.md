# CLAUDE.md — DDS (управленческий учёт для e-commerce / Wildberries)

## Стек
FastAPI + PostgreSQL 15 + PgBouncer + Redis + MinIO + Next.js 15 (React 19)

## Команды
```bash
docker compose up -d                              # Запуск
docker compose exec backend pytest tests/ -x      # Тесты
docker compose logs backend --tail=50             # Логи
bash scripts/check_conventions.sh                 # Проверка конвенций
```

## Железные правила (нарушение = баг)
1. **project_id** — КАЖДЫЙ запрос к БД MUST фильтровать по `project_id`
2. **is_deleted** — КАЖДЫЙ запрос к SoftDeleteMixin моделям MUST `.where(Model.is_deleted == False)`
3. **soft_delete** — удаление моделей с SoftDeleteMixin: `model.soft_delete()` (НИКОГДА `db.delete()` для них)
4. **datetime** — ТОЛЬКО `from backend.utils.time import utcnow` (НИКОГДА `datetime.utcnow()`)
5. **деньги** — ТОЛЬКО `Numeric(18, 2)` (НИКОГДА Float)
6. **SQL** — ТОЛЬКО параметризованный `:param` binding (НИКОГДА f-string в `text()`)
7. **кэш** — `invalidate_cache(prefix)` после мутаций, ключ MUST содержать project_id
8. **логика** — бизнес-логика в `services/` (НИКОГДА в `routers/`)

## Архитектура backend
```
routers/ (HTTP only) → services/ (логика) → models/ (ORM)
schemas/ — Pydantic request/response
etl/ — импорт выписок (парсеры VTB, WB)
integrations/ — внешние API (WB)
scheduler/jobs/ — фоновые задачи (ТОЛЬКО в worker container)
```

### Порядок создания нового модуля
Model → Alembic migration → Schema → Service → Router → Test

### Agent TDD — процесс разработки
**Подробно:** `docs/AGENT_DEVELOPMENT.md`

#### Фичи и кросс-доменные изменения (полный цикл)
```
Фаза 0: Человек описывает задачу своими словами
         → Агент читает DOMAIN_*.md, анализирует код
         → Агент задаёт уточняющие вопросы (только неясное)
         → Агент формирует ТЗ, показывает на подтверждение
         → Человек: "ок" или правки
Фаза 1: Model → Migration → Schema (последовательно, один агент)
Фаза 2: Backend ‖ Frontend (параллельно, агент оркестрирует сам)
Фаза 3: pytest + vitest + check_conventions → коммит
```
- Агент НЕ пишет код, пока человек не подтвердил ТЗ
- Backend и Frontend — 0 пересечений файлов, всегда параллелятся
- Alembic миграции — ТОЛЬКО последовательно
- Один файл — ТОЛЬКО один агент

#### Баги и мелкие изменения (быстрый цикл)
Без ТЗ — сразу анализ → фикс → тесты → коммит

### PgBouncer
- `prepared_statement_cache_size=0` ОБЯЗАТЕЛЕН в DATABASE_URL
- `DATABASE_URL_SYNC` → напрямую к PostgreSQL (для Alembic/ETL)
- Statement timeout через event listener (НЕ server_settings)

### Кэш (Redis)
- `@cached(prefix="...", ttl=300)` для отчётов
- `invalidate_cache(prefix)` сам добавляет `:*` — НЕ передавать wildcard
- При ошибке Redis → graceful degradation (warning, не crash)
- НИКОГДА не сбрасывать все ключи разом (worker starvation)

### Crypto (API-ключи WB)
- Шифрование: `backend/utils/crypto.py` (encrypt/decrypt)
- Есть legacy_fallback — НЕ менять без data-migration

### WB API
- Rate limits: asyncio.Semaphore, respect Retry-After
- Circuit Breaker: ТОЛЬКО для 500-504 (НЕ для 429)
- Partial data: сохранять уже загруженные дни при ошибках
- sync_log: ВСЕГДА обновлять в finally (НИКОГДА не оставлять RUNNING)

### WB Finance deductions (БДР/ОПИУ)
- `ad_deduction` — отдельная статья, НЕ включать в to_pay
- `loan_deduction` — финансовая операция, НЕ включать в операционную прибыль
- Только `other_deduction` → операционные расходы
- Изменение типов удержаний → обновить ОБА: wb_bdr_service.py И opiu_service.py

### AI Multi-Agent система
```
services/ai/orchestrator.py — классификация интента (Haiku), маршрутизация к 1-2 агентам
services/ai/agents/ — 7 специализированных агентов (analyst, financier, marketer, advertiser, supply_manager, logistics, logistician)
services/ai/agents/base.py — базовый класс с tool execution loop (до 5 раундов)
services/ai/synthesizer.py — объединение ответов нескольких агентов
services/ai/memory.py — Obsidian-style память (авто-инсайты в BrandNote)
services/ai/tools/ — 19 инструментов (finance 6, marketing 5, logistics 8)
services/ai/prompts/ — системные промпты для каждого агента + orchestrator + synthesizer
services/ai/llm_client.py — клиент Anthropic API (Sonnet для чата, Haiku для классификации/дайджестов)
```
- Подробнее: `backend/DOMAIN_AI.md`

## Архитектура frontend
```
src/app/(main)/p/[slug]/ — основное приложение (22+ страниц: dds, import, txn, inbox, reports, planning, cost, funnel, trends, refs, settings, opiu, orders, plan-fact, team, monitoring, bulk-cost, container-loader, order-geography, warehouse/*)
src/app/(tma)/tma/[slug]/ — Telegram Mini App (dashboard, capital, chat, funnel, pnl, pulse, warehouse)
src/lib/api/ — модульный API клиент (client.ts + 13 доменных файлов, JWT auth + auto-refresh)
src/lib/utils.ts — formatNumber, formatDate, exportToExcel
src/components/ — DataTable, FormModal, PageHeader, PageGuard, TabLayout, Toast
src/types/api.ts — TypeScript интерфейсы
```

### Правила frontend
- Типы → `types/api.ts` (НИКОГДА inline / any)
- API → методы `api.ts` (НИКОГДА прямой fetch, кроме FormData upload)
- Числа → `formatNumber()`, даты → `formatDate()`
- Таблицы → кнопка Excel export
- ОБЯЗАТЕЛЬНО: loading, error, empty states
- Новый endpoint → тип в api.ts + метод в api.ts

## Домены — читай DOMAIN_*.md перед работой с модулем
| Домен | Контекст | Ключевые файлы |
|-------|----------|----------------|
| Транзакции | `backend/DOMAIN_TRANSACTIONS.md` | etl/, services/transactions_service.py |
| Отчёты | `backend/DOMAIN_REPORTS.md` | services/reports/, opiu_service.py, wb_bdr_service.py |
| Планирование | `backend/DOMAIN_PLANNING.md` | services/planning/, routers/planning.py |
| Себестоимость | `backend/DOMAIN_COST.md` | services/cost/, etl/cost_parsers.py, etl/cost_parser_helpers.py |
| Склад | `backend/DOMAIN_WAREHOUSE.md` | services/warehouse_*, services/fbo_supply_service.py |
| WB Интеграция | `backend/DOMAIN_WB.md` | integrations/, services/funnel/, scheduler/jobs/ |
| Сборка/Логистика | `backend/DOMAIN_ASSEMBLY.md` | services/assembly_service.py, routers/assembly.py |
| AI Агенты | `backend/DOMAIN_AI.md` | services/ai/orchestrator.py, services/ai/agents/, services/ai/memory.py |
| Telegram | `backend/DOMAIN_TELEGRAM.md` | integrations/telegram_bot.py, services/telegram_service.py, routers/telegram_webhook.py |
| Фронтенд | `frontend-react/DOMAIN_FRONTEND.md` | src/app/(main)/, src/app/(tma)/, src/lib/api.ts |

## Перед началом задачи
Следуй процессу Agent TDD из `docs/AGENT_DEVELOPMENT.md`:
- **Фича** → уточни неясное, покажи ТЗ, жди подтверждения, потом кодь
- **Баг/мелочь** → сразу делай

## Git
- Коммиты: `feat:` / `fix:` / `infra:` / `refactor:` / `test:` (русский или англ.)
- Ветки: `dev` → проверка → merge в `main` → production auto-deploy
- НИКОГДА не деплоить через SSH — только CI/CD
- Перед коммитом: тесты + check_conventions.sh

## Среды
| Среда | Ветка | URL |
|-------|-------|-----|
| Local | dev | http://localhost:3000 |
| Production | main | https://app.vyatkin-wb.ru |

## При баге или новом модуле — обнови правила
1. **Новая модель с SoftDeleteMixin** → добавь в `SOFT_MODELS` в `scripts/check_conventions.sh`
2. **Новый отчёт с кэшем** → добавь prefix в `invalidate_project_reports()` в `backend/cache.py`
3. **Новый домен/модуль** → создай `backend/DOMAIN_*.md`, добавь строку в таблицу доменов выше
4. **Найден новый антипаттерн** → добавь check в `scripts/check_conventions.sh` + строку в Антипаттерны ниже
5. **Урок из бага** → запиши в `memory/project_known_bugs.md`
6. **Исправлен баг из known_bugs** → обнови `memory/project_known_bugs.md` — перенеси в «Исправленные» с номером коммита

## Антипаттерны (НЕ ДЕЛАТЬ)
- Запрос без `project_id` или `is_deleted` фильтра
- `db.delete()` на моделях с SoftDeleteMixin (вместо `soft_delete()`)
- f-string в SQL `text()`
- Float для денег
- `datetime.utcnow()` / `datetime.now()`
- Бизнес-логика в роутере
- `.scalars().all()` без `.limit()`
- `ilike(f"%{input}%")` без экранирования `%`/`_` и без `escape="\\"`
- Мутация без `invalidate_cache()`
- Сервис > 500 строк без разбиения
- `asyncio.get_event_loop()` вместо `asyncio.get_running_loop()`
- `print()` в backend коде вместо `logging.getLogger()`
- `except Exception` без `except asyncio.CancelledError` в scheduler jobs (CancelledError — BaseException, не Exception)

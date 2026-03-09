# AGENTS.md — Guide for AI Agents

> **Прочитай этот файл ПОЛНОСТЬЮ перед тем как вносить изменения в проект.**
> После каждого изменения — обнови соответствующие секции этого файла.

---

## Обзор проекта

**DDS** — система управленческого учёта (ДДС). Позволяет импортировать банковские выписки, категоризировать операции, строить отчёты, планировать кэшфлоу.

**Стек:** FastAPI (Python) + PostgreSQL + Redis + MinIO + Next.js 15 (React)

---

## Структура проекта

```
dds_app/
├── backend/                    # FastAPI backend (Python)
│   ├── main.py                 # Entry point, lifespan, middleware, router registration
│   ├── config.py               # Pydantic Settings (.env)
│   ├── database.py             # SQLAlchemy async/sync engines, get_db
│   ├── auth.py                 # JWT: hash, verify, create_token, get_current_user
│   ├── cache.py                # Redis: @cached decorator, invalidation
│   ├── storage.py              # MinIO: upload/download files
│   ├── exceptions.py           # Unified error handling
│   ├── project_context.py      # FastAPI dependency: resolve project_id from X-Project-Id header
│   ├── scheduler.py            # APScheduler: WB funnel sync, backfill, anomaly check
│   ├── models/                 # ORM models (33+ таблиц, split по доменам)
│   │   ├── __init__.py         # Re-export всех моделей
│   │   ├── auth.py             # User, Project, ProjectMember, ProjectInvite
│   │   ├── refs.py             # Account, CounterpartyCategory, Override, OpeningBalance, CategoryRef
│   │   ├── transactions.py     # Transaction, ImportLog, CategoryChangeLog
│   │   ├── planning.py         # Order, PlannedPayment, PlannedIncome, WbPayout, LeadTime, PaymentFactLink
│   │   ├── cost.py             # CostOrder, CostOrderItem, Nomenclature, DutyRule
│   │   ├── customs.py          # CustomsTopup, CustomsAlloc, CustomsDT
│   │   ├── integrations.py     # IntegrationKey, SyncLog, WbFunnelDaily, WbCostOverride
│   │   ├── wb_finance.py       # WbFinanceRow, WbFinanceSyncLog
│   │   ├── fx_rates.py         # FxRate
│   │   ├── tax.py              # TaxRate
│   │   ├── enums.py            # DutyBasis, EventType2, TransactionStatus, PurposeTag
│   │   └── mixins.py           # SoftDeleteMixin, TimestampMixin
│   ├── schemas/                # Pydantic request/response schemas (split по доменам)
│   │   ├── __init__.py         # Re-export всех схем
│   │   ├── auth.py, common.py, cost.py, imports.py
│   │   ├── integrations.py, planning.py, refs.py
│   │   └── reports.py, tax.py, transactions.py
│   ├── routers/                # HTTP endpoints
│   │   ├── auth.py             # /api/v1/auth/* — login, register, profile, change_password, refresh
│   │   ├── projects.py         # /api/v1/projects/* — CRUD projects, members, invites
│   │   ├── import_txn.py       # /api/v1/import/*, /api/v1/transactions/*
│   │   ├── refs.py             # /api/v1/refs/* — accounts, categories, overrides
│   │   ├── reports.py          # /api/v1/reports/* — DDS month, balance, FX, customs, dashboard
│   │   ├── planning.py         # /api/v1/planning/* — orders, payments, cashflow
│   │   ├── cost.py             # /api/v1/cost/* — orders, nomenclature, duty rules
│   │   ├── integrations.py     # /api/v1/integrations/* — WB API keys, sync
│   │   ├── funnel.py           # /api/v1/funnel/* — воронка продаж, тренды, анализ дня
│   │   └── ws.py               # WebSocket broadcast
│   ├── services/               # Business logic layer
│   │   ├── reports/            # Reports (decomposed package)
│   │   │   ├── balance.py      # Balance, balance daily
│   │   │   ├── dds.py          # DDS month, PnL
│   │   │   ├── dashboard.py    # Dashboard summary (KPIs, charts)
│   │   │   └── queries.py      # FX control, customs, income daily, filtered txn
│   │   ├── planning/           # Planning (decomposed package)
│   │   │   ├── crud.py         # Orders, lead times, payments, incomes CRUD
│   │   │   ├── customs.py      # Customs topup/alloc/DT, FTS PDF parsing
│   │   │   ├── fact_links.py   # Payment ↔ transaction matching
│   │   │   ├── cashflow.py     # Daily cashflow, order summary
│   │   │   └── wb.py           # WB payouts, reconciliation, forecast
│   │   ├── cost/               # Cost calculation (decomposed package)
│   │   │   ├── helpers.py      # Shared utils (safe_float, _order_no_to_int)
│   │   │   ├── nomenclature.py # Nomenclature CRUD, Excel upload
│   │   │   ├── duty.py         # Duty rules CRUD
│   │   │   ├── orders.py       # Cost orders CRUD with aggregation
│   │   │   ├── items.py        # Cost order items, Excel upload, recalculation
│   │   │   └── plan_gen.py     # Payment plan generation
│   │   ├── transactions_service.py # Transaction search, category assignment
│   │   ├── refs_service.py     # Accounts, CP categories, overrides
│   │   ├── integrations_service.py # WB sync, nomenclature sync
│   │   ├── wb_bdr_service.py   # WB БДР report
│   │   ├── wb_finance_sync.py  # WB finance streaming sync
│   │   ├── fx_service.py       # FX rates
│   │   ├── tax_service.py      # Tax rates
│   │   ├── cost_history_service.py # Cost history report
│   │   └── funnel/             # Воронка продаж (decomposed package)
│   │       ├── sync.py         # WB funnel data sync
│   │       ├── query.py        # Data queries, filters, summary
│   │       └── analysis.py     # Day analysis, trends
│   ├── integrations/
│   │   └── wb_api.py           # WB Content/Statistics API client
│   ├── utils/                  # Shared utilities
│   │   ├── crypto.py           # Fernet encryption for API keys
│   │   ├── file_validation.py  # File extension/size validation
│   │   ├── queries.py          # project_select() — tenant-safe SQL helpers
│   │   ├── time.py             # utcnow() — unified datetime (see Anti-patterns)
│   │   └── telegram.py         # Telegram alert notifications
│   ├── seeds/                  # Seed data for new projects
│   │   └── default_categories.py  # 28 default category_ref entries
│   └── etl/                    # ETL pipeline
│       ├── parsers.py          # 7 bank statement parsers (VTB RUB/CNY/Multi, WB Main/Payout/Multi/Cabinet)
│       ├── cost_parsers.py     # Excel normalizers for cost orders
│       ├── master_logic.py     # Categorization, txn_id, cp_key generation
│       └── service.py          # Orchestrator: parse → enrich → load
│
├── frontend-react/             # Next.js 15 frontend (TypeScript)
│   ├── src/
│   │   ├── app/                # App Router pages
│   │   │   ├── login/page.tsx          # Login page
│   │   │   ├── register/page.tsx       # Registration page
│   │   │   ├── projects/page.tsx       # Project list / selection
│   │   │   ├── profile/page.tsx        # User profile
│   │   │   └── p/[slug]/              # Project-scoped pages
│   │   │       ├── layout.tsx          # Sidebar + header layout
│   │   │       ├── page.tsx            # Dashboard (balances, charts, summary)
│   │   │       ├── import/page.tsx     # Bank statement import
│   │   │       ├── txn/page.tsx        # Операции (all transactions)
│   │   │       ├── inbox/page.tsx      # INBOX — unassigned transactions
│   │   │       ├── reports/page.tsx    # Reports (DDS, Balance, FX, Customs)
│   │   │       ├── dds/page.tsx        # DDS P&L report
│   │   │       ├── planning/page.tsx   # Planning (7 tabs)
│   │   │       ├── orders/page.tsx     # Orders management
│   │   │       ├── cost/page.tsx       # Cost calculation (3 tabs)
│   │   │       ├── funnel/page.tsx     # Воронка продаж WB
│   │   │       ├── trends/page.tsx     # Product trends
│   │   │       ├── refs/page.tsx       # References (5 tabs)
│   │   │       ├── settings/page.tsx   # Project settings
│   │   │       └── team/page.tsx       # Team management
│   │   ├── lib/
│   │   │   ├── api.ts          # API client (80+ methods, JWT auth + refresh)
│   │   │   └── utils.ts        # formatNumber, formatDate, exportToExcel
│   │   ├── components/         # Shared UI: DataTable, FormModal, PageHeader, TabLayout, Toast
│   │   └── types/
│   │       └── api.ts          # TypeScript interfaces
│   ├── next.config.mjs         # API rewrite: /api/* → backend:8000
│   └── Dockerfile              # Multi-stage build (standalone)
│
├── tests/                      # Backend tests (13 files, 2300+ lines)
│   ├── conftest.py             # Fixtures: test DB, test user, auth headers
│   ├── test_api_*.py           # Integration tests per module
│   ├── test_master_logic.py    # ETL categorization unit tests
│   ├── test_parsers.py         # Bank statement parser tests
│   └── test_scheduler.py       # Scheduler resilience tests
│
├── migrations/                 # Alembic migrations
├── scripts/                    # Backup/restore scripts
├── nginx/                      # Nginx config
├── docker-compose.yml          # 7 services: db, redis, minio, backend, frontend-react, nginx, db-backup
├── .env                        # Environment variables (secrets!)
└── AGENTS.md                   # ← THIS FILE
```

---

## Auth Flow

```
1. User → POST /api/v1/auth/login {username, password}
2. Backend → verify bcrypt hash → issue JWT (HS256, 8h expiry)
3. Frontend → stores JWT in localStorage ('dds_token')
4. All API calls → Authorization: Bearer <token>
5. Backend → get_current_user() dependency verifies token on each request
```

**Ключевые файлы:** `backend/auth.py`, `backend/routers/auth.py`, `frontend-react/src/lib/api.ts`

---

## Модель данных (ключевые таблицы)

| Группа | Таблицы | Router |
|--------|---------|--------|
| Auth | `users` | `auth.py` |
| Projects | `projects`, `project_members`, `project_invites` | `projects.py` |
| Transactions | `transactions`, `import_log`, `category_change_log` | `import_txn.py` |
| References | `accounts`, `counterparty_categories`, `overrides`, `opening_balances`, `category_ref` | `refs.py` |
| Reports | (reads from transactions + opening_balances) | `reports.py` |
| Planning | `orders`, `planned_payments`, `planned_incomes`, `lead_time`, `customs_*`, `wb_payouts`, `payment_fact_links` | `planning.py` |
| Cost | `cost_orders`, `cost_order_items`, `nomenclature`, `duty_rules` | `cost.py` |
| Integrations | `integration_keys`, `sync_log`, `wb_funnel_daily`, `wb_cost_override` | `integrations.py` |
| WB Finance | `wb_finance_row`, `wb_finance_sync_log` | `reports.py` |
| FX & Tax | `fx_rates`, `tax_rates` | `reports.py` |

---

## Как добавить новую страницу

### 1. Backend (если нужен новый API)

```
backend/routers/my_feature.py  — новый роутер
backend/models/my_feature.py   — новые ORM модели + re-export в models/__init__.py
backend/schemas/my_feature.py  — Pydantic schemas + re-export в schemas/__init__.py
backend/services/my_feature_service.py — бизнес-логика
backend/main.py                — зарегистрировать router: app.include_router(...)
```

### 2. Frontend React

```
frontend-react/src/app/p/[slug]/my_feature/page.tsx  — страница
frontend-react/src/lib/api.ts                        — новые методы в ApiClient
frontend-react/src/app/p/[slug]/layout.tsx            — добавить в sidebar nav
```

### 3. Документация

```
AGENTS.md  — обновить структуру, таблицу моделей, список страниц
```

---

## 🧠 Правило коммуникации

> **Если задача/фича сформулирована неоднозначно — СНАЧАЛА задай уточняющие вопросы, ПОТОМ пиши код.**

**Когда спрашивать:**
- Неясно, где именно должен появиться UI-элемент (на какой странице, в каком месте)
- Неясно поведение (dropdown vs отдельный блок, inline vs модалка)
- Задача может быть реализована несколькими способами и выбор неочевиден
- Нет информации о формате данных, фильтрах, сортировке
- Фича затрагивает несколько модулей и непонятен приоритет

**Формат вопросов:**
1. Краткий пронумерованный список
2. Без лишнего текста — только суть
3. Если можно предложить варианты — предложи (A/B)

**Когда НЕ спрашивать:**
- Задача очевидна и однозначна
- Баг-фикс с чётким описанием
- Рефакторинг по конкретному правилу из AGENTS.md

---

## ⛔ ЗАПРЕЩЕНО — антипаттерны

> **Агент ОБЯЗАН проверять эти правила при КАЖДОМ изменении кода.**
> Нарушение любого из них — баг, который нужно исправить ДО коммита.

### Backend

| ❌ НЕЛЬЗЯ | ✅ ПРАВИЛЬНО |
|-----------|-------------|
| Бизнес-логика в роутере | Роутер вызывает `service`-функцию: `return await my_service.create_item(db, data)` |
| `datetime.utcnow()` или `datetime.now(timezone.utc)` | `from backend.utils.time import utcnow` — единый helper, naive UTC, совместим с asyncpg. **Оба** варианта напрямую ЗАПРЕЩЕНЫ (deprecated / breaks asyncpg). |
| `Column(Integer, ...)` | `Mapped[int] = mapped_column(Integer, ...)` — новый SQLAlchemy стиль |
| `Float` для денег | `Numeric(18, 2)` — точные вычисления |
| f-string в SQL: `f"WHERE id={x}"` | Параметризованный SQL: `text("WHERE id = :x"), {"x": x}` |
| Запрос без `project_id` | `from backend.utils.queries import project_select` → `project_select(Model, project_id)` — невозможно забыть |
| Запрос без `is_deleted` фильтра | `from backend.utils.queries import project_select_active` → `project_select_active(Model, project_id)` для моделей с SoftDeleteMixin |
| Новая модель в `models.py` (монолит) | Новый файл `models/feature.py` + re-export в `models/__init__.py` |
| Новая схема в `schemas.py` (монолит) | Новый файл `schemas/feature.py` + re-export в `schemas/__init__.py` |
| List-эндпоинт без пагинации | `limit: int = 100, offset: int = 0` + `.limit(limit).offset(offset)` |
| Мутация без инвалидации кэша | `await invalidate_cache("reports:*")` после INSERT/UPDATE/DELETE |
| `print()` для дебага | `logger = logging.getLogger("dds.module")` + `logger.info(...)` |
| Inline стили в React | CSS классы из `globals.css`: `glass-card`, `data-table`, `btn-*` |
| **Сервис > 400 строк** | Разбить по ответственности: CRUD, парсеры, генерация |  
| **Seed/init данные в `main.py`** | Выносить в `backend/seeds/` + import |
| **Cache key без `project_id`** | Ключ ОБЯЗАН содержать `project_id` — иначе утечка между проектами |
| **`ilike(f"%{user_input}%")`** | Экранировать `%`/`_`: `s.replace("%", r"\%").replace("_", r"\_")` перед подстановкой в LIKE |

### Frontend

| ❌ НЕЛЬЗЯ | ✅ ПРАВИЛЬНО |
|-----------|-------------|
| Сырые числа: `{item.amount}` | `{formatNumber(item.amount)}` из `lib/utils.ts` |
| Сырые даты: `{item.date}` | `{formatDate(item.date)}` из `lib/utils.ts` |
| Таблица без Excel экспорта | Кнопка «📥 Excel» с `exportToExcel()` |
| Компонент без loading/error | Обязательно: `if (loading)`, `if (error)`, пустое состояние |
| Inline типы | Типы в `types/api.ts`, импорт оттуда |
| Data fetch без `useCallback` | `const loadData = useCallback(async () => {...}, [deps])` |

---

## ✅ ОБЯЗАТЕЛЬНО — при каждом изменении

### 🔴🟢 TDD — Test-Driven Development

> **ОБЯЗАТЕЛЬНО: сначала тест, потом код.**
> Это правило применяется ко ВСЕМ изменениям: новые фичи, рефакторинг, баг-фиксы.

**Порядок работы:**
1. **RED** — напиши falling test, который описывает желаемое поведение
2. **GREEN** — напиши минимальный код, чтобы тест прошёл
3. **REFACTOR** — улучши код, не ломая тесты

**Что тестировать:**
- Новый парсер → `tests/test_parser_<name>.py` — unit-тест на DataFrame output
- Новый эндпоинт → `tests/test_api_<module>.py` — integration test с БД
- Новый сервис → `tests/test_<service>.py` — unit-тест с mock DB
- Баг-фикс → тест воспроизводящий баг ДО исправления кода

**Запуск тестов:**
```bash
# Локально (в контейнере)
docker compose exec backend pytest tests/ -x --tb=short
# Один файл
docker compose exec backend pytest tests/test_parser_vtb.py -v
```

### Backend — новый эндпоинт

1. **Тест-first** — `tests/test_api_feature.py` с falling тестом ДО написания кода
2. **Schema-first** — определи Pydantic request/response ДО написания кода
3. **Service layer** — вся логика в `services/feature_service.py`
4. **Тонкий роутер** — только HTTP, валидация, вызов service
5. **Модель** — `Mapped[]`, `project_id`, `SoftDeleteMixin` для критичных сущностей
6. **Пагинация** — `limit/offset` для list-эндпоинтов
7. **Документация** — обновить этот файл + `docs/MODULES.md`

### Frontend — новая страница

1. **Типы** — интерфейсы в `types/api.ts`
2. **API методы** — в `lib/api.ts` класс `ApiClient`
3. **Страница** — `'use client'`, loading/error/empty states
4. **Форматирование** — `formatNumber()`, `formatDate()`, Excel export
5. **Стили** — только CSS классы из `globals.css`
6. **Sidebar** — добавить в `layout.tsx`

---

## Conventions

### Backend
- **Router** — ТОЛЬКО HTTP layer (валидация, auth). Бизнес-логику → в service
- **Service** — бизнес-логика, координация моделей, logging
- **SQLAlchemy** — async по умолчанию (`AsyncSession`), sync только для ETL
- **Models** — split по доменам в `models/*.py`, re-export в `models/__init__.py`
- **Schemas** — split по доменам в `schemas/*.py`, re-export в `schemas/__init__.py`
- **Errors** — через `HTTPException`, unified формат из `exceptions.py`
- **Cache** — `@cached(ttl=300)` для тяжёлых отчётов, инвалидация при мутации
- **Performance** — `SlowRequestMiddleware` логирует запросы >500ms (`🐢 SLOW` в логах)
- **TDD** — СНАЧАЛА тест, потом код. `docker compose exec backend pytest tests/ -x --tb=short` перед каждым коммитом
- **TESTING=1** — env переменная, отключает rate limiter (устанавливается автоматически в conftest)
- **Параметры SQL** — ТОЛЬКО `:param` binding, НИКОГДА f-string

### Frontend
- **Pages** — `'use client'` directive, каждая — самостоятельный компонент
- **API** — все вызовы через `api.methodName()` из `lib/api.ts`
- **Стили** — CSS variables в `globals.css`, классы: `glass-card`, `data-table`, `btn-*`, `badge-*`
- **Таблицы** — всегда с кнопкой «📥 Excel» (`exportToExcel()`)
- **Форматирование** — `formatNumber()` для чисел, `formatDate()` для дат

---

## WB (Wildberries) интеграция

### API клиент (`backend/integrations/wb_api.py`)
- `WBApiClient(api_key)` — HTTP client с retry logic
- `get_sales(date_from)` — продажи (Statistics API)
- `get_orders(date_from)` — заказы (Statistics API)
- `get_finance_report(date_from, date_to)` — финансовый отчёт
- `get_cards_list(limit=100)` — **карточки товаров** (Content API, cursor pagination)
- `parse_wb_cards_to_nomenclature(cards)` — маппинг WB карточек → Nomenclature

### Маппинг WB → Nomenclature
| WB поле | Nomenclature поле |
|---------|------------------|
| `nmID` | `article_wb` |
| `brand` | `brand` |
| `subjectName` | `subject` |
| `vendorCode` | `article_seller` |
| `sizes[].skus[]` | `barcode` (одна строка на баркод) |
| `dimensions.L×W×H/1000` | `volume_l` |

### Endpoints
- `POST /api/v1/integrations/keys` — добавить API ключ (Fernet encryption)
- `POST /api/v1/integrations/wb/sync` — синхронизация продаж/заказов/финансов
- `POST /api/v1/integrations/wb/sync_nomenclature` — **синхронизация номенклатуры из WB Content API**
- `GET /api/v1/integrations/sync_log` — лог синхронизаций

### UI
- Кнопка **«🔄 Синхронизация WB»** на вкладке Номенклатура (`cost/page.tsx`)
- Результат: loading spinner → сообщение (inserted/updated counts)

---

## Безопасность (текущее состояние)

### ✅ Исправлено
- [x] SECRET_KEY — auto-генерируется при отсутствии, валидация длины ≥32
- [x] Default admin — случайный пароль, выводится в логи при первом запуске
- [x] CORS — включает :3000, restricted methods/headers
- [x] Docker ports — DB/Redis/MinIO закрыты снаружи
- [x] Redis — требует пароль
- [x] Rate limiting — Redis-based на login/register (10/мин)
- [x] Password validation — минимум 6 символов
- [x] File upload — валидация расширения (.xlsx/.xls/.csv/.pdf) и размера (50MB)
- [x] Filename sanitization — path traversal protection
- [x] Register toggle — можно отключить через `REGISTER_ENABLED=false`
- [x] Security logging — login attempts, admin creation
- [x] Refresh tokens — access + refresh token flow (фронтенд поддерживает auto-refresh)

### ⚠️ TODO (архитектурные)
- [ ] Seed endpoint — `POST /api/v1/seed` всё ещё в `main.py` (hardcoded счета) — вынести в `seeds/` или удалить
- [ ] Project-level data isolation (project_id FK + middleware)
- [ ] JWT в HttpOnly cookies вместо localStorage
- [ ] Token revocation (Redis blacklist)
- [ ] Admin role-based access

---

*Последнее обновление: 2026-03-08 — актуализация структуры, моделей, безопасности, добавлены services/utils/funnel*

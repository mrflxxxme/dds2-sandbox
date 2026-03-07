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
│   ├── models/                 # ORM models (28 таблиц, split по доменам)
│   ├── schemas.py              # Pydantic request/response schemas
│   ├── auth.py                 # JWT: hash, verify, create_token, get_current_user
│   ├── cache.py                # Redis: @cached decorator, invalidation
│   ├── storage.py              # MinIO: upload/download files
│   ├── exceptions.py           # Unified error handling
│   ├── routers/                # HTTP endpoints
│   │   ├── auth.py             # /api/v1/auth/* — login, register, profile, change_password
│   │   ├── projects.py         # /api/v1/projects/* — CRUD projects, members, invites
│   │   ├── import_txn.py       # /api/v1/import/*, /api/v1/transactions/*
│   │   ├── refs.py             # /api/v1/refs/* — accounts, categories, overrides
│   │   ├── reports.py          # /api/v1/reports/* — DDS month, balance, FX, customs
│   │   ├── planning.py         # /api/v1/planning/* — orders, payments, cashflow
│   │   ├── cost.py             # /api/v1/cost/* — orders, nomenclature, duty rules
│   │   └── integrations.py     # /api/v1/integrations/* — WB API keys, sync sales/nomenclature
│   ├── integrations/
│   │   └── wb_api.py           # WB Content/Statistics API client (cards, sales, orders)
│   ├── seeds/                  # Seed data for new projects
│   │   └── default_categories.py  # 28 default category_ref entries
│   └── etl/                    # ETL pipeline
│       ├── parsers.py          # 5 bank statement parsers
│       ├── cost_parsers.py     # Excel normalizers for cost orders (дивандек/ковры)
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
│   │   │       ├── page.tsx            # Dashboard (balances, accounts)
│   │   │       ├── import/page.tsx     # Bank statement import
│   │   │       ├── txn/page.tsx        # Операции (all transactions)
│   │   │       ├── inbox/page.tsx      # INBOX — unassigned transactions
│   │   │       ├── reports/page.tsx    # Reports (DDS, Balance, FX, Customs)
│   │   │       ├── planning/page.tsx   # Planning (7 tabs)
│   │   │       ├── cost/page.tsx       # Cost calculation (3 tabs)
│   │   │       ├── refs/page.tsx       # References (5 tabs)
│   │   │       ├── settings/page.tsx   # Project settings
│   │   │       └── team/page.tsx       # Team management
│   │   ├── lib/
│   │   │   ├── api.ts          # API client (50+ methods, JWT auth)
│   │   │   └── utils.ts        # formatNumber, formatDate, exportToExcel
│   │   └── components/         # Shared UI components
│   ├── next.config.mjs         # API rewrite: /api/* → backend:8000
│   └── Dockerfile              # Multi-stage build (standalone)
│
├── frontend/                   # Streamlit frontend (LEGACY, будет удалён)
│
├── docker-compose.yml          # db, redis, minio, backend, frontend, frontend-react
├── .env                        # Environment variables (secrets!)
├── ARCHITECTURE.md             # Technical architecture doc
├── README.md                   # User-facing README
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
| Integrations | `integration_keys`, `sync_log`, `wb_payouts` | `integrations.py` |

---

## Как добавить новую страницу

### 1. Backend (если нужен новый API)

```
backend/routers/my_feature.py  — новый роутер
backend/models.py              — новые ORM модели (если нужны)
backend/schemas.py             — Pydantic schemas
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
| `datetime.now(timezone.utc)` для `DateTime` колонок | `datetime.utcnow()` — asyncpg НЕ принимает offset-aware datetime в `TIMESTAMP WITHOUT TIME ZONE` колонки. Если нужен aware → колонка должна быть `DateTime(timezone=True)` |
| `Column(Integer, ...)` | `Mapped[int] = mapped_column(Integer, ...)` — новый SQLAlchemy стиль |
| `Float` для денег | `Numeric(18, 2)` — точные вычисления |
| f-string в SQL: `f"WHERE id={x}"` | Параметризованный SQL: `text("WHERE id = :x"), {"x": x}` |
| Запрос без `project_id` | `select(Model).where(Model.project_id == project_id)` |
| Запрос без `is_deleted` фильтра | `.where(Model.is_deleted == False)` для моделей с SoftDeleteMixin |
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
- [x] Seed endpoint removed — убран публичный `/api/seed_defaults`

### ⚠️ TODO (архитектурные)
- [ ] Project-level data isolation (project_id FK + middleware)
- [ ] JWT в HttpOnly cookies вместо localStorage
- [ ] Refresh tokens + token revocation (Redis blacklist)
- [ ] Admin role-based access

---

*Последнее обновление: 2026-03-07 — добавлено правило TDD (test-first), VTB_MULTI парсер*

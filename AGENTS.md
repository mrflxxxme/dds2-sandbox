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
│   ├── models.py               # ORM models (28 таблиц)
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
│   │   └── integrations.py     # /api/v1/integrations/* — WB API (planned)
│   └── etl/                    # ETL pipeline
│       ├── parsers.py          # 5 bank statement parsers
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

## Conventions

### Backend
- **Router** — ТОЛЬКО HTTP layer (валидация, auth). Бизнес-логику → в service
- **SQLAlchemy** — async по умолчанию (`AsyncSession`), sync только для ETL
- **Errors** — через `HTTPException`, unified формат из `exceptions.py`
- **Cache** — `@cached(ttl=300)` для тяжёлых отчётов
- **Параметры SQL** — ТОЛЬКО `:param` binding, НИКОГДА f-string

### Frontend
- **Pages** — `'use client'` directive, каждая — самостоятельный компонент
- **API** — все вызовы через `api.methodName()` из `lib/api.ts`
- **Стили** — CSS variables в `globals.css`, классы: `glass-card`, `data-table`, `btn-*`, `badge-*`
- **Таблицы** — всегда с кнопкой "📥 Excel" (`exportToExcel()`)
- **Форматирование** — `formatNumber()` для чисел, `formatDate()` для дат

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

*Последнее обновление: 2026-03-01*

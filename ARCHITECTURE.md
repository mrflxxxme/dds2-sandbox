# DDS Architecture

> Система управленческого учёта (ДДС).
> Стек: **FastAPI + PostgreSQL + Redis + MinIO + React (planned)**.

---

## High-Level

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Frontend   │────▶│   Backend    │────▶│  PostgreSQL  │
│  (React/SL)  │     │  (FastAPI)   │──┬─▶│    Redis     │
│  :8501       │     │  :8000       │  └─▶│    MinIO     │
└──────────────┘     └──────┬───────┘     └──────────────┘
                            │
                    ┌───────▼────────┐
                    │ External APIs  │
                    │ (Wildberries)  │
                    └────────────────┘
```

---

## Слои Backend

```
routers/          ← HTTP-слой: валидация, авторизация, response
  ├── auth.py
  ├── import_txn.py
  ├── refs.py
  ├── reports.py
  ├── planning.py
  ├── cost.py
  └── integrations.py    ← PLANNED: WB API, другие маркетплейсы

services/         ← PLANNED: бизнес-логика (сейчас частично в routers)

etl/              ← ETL-pipeline: парсинг → обогащение → загрузка
  ├── parsers.py         ← 5 форматов банковских выписок
  ├── master_logic.py    ← Категоризация, txn_id, cp_key
  └── service.py         ← Оркестратор

integrations/     ← PLANNED: внешние API
  └── wb_api.py          ← Wildberries Statistics/Marketplace API

models.py         ← SQLAlchemy ORM (24 таблицы)
schemas.py        ← Pydantic request/response модели
cache.py          ← Redis: @cached, invalidate_cache
storage.py        ← MinIO: upload/download файлов
auth.py           ← JWT: hash, verify, get_current_user
config.py         ← Pydantic Settings (.env)
database.py       ← Async/Sync engines, Base, get_db
```

### Правило слоёв

```
Router → Service → Model/DB
  ↓         ↓
Schema    Cache/Storage/Integration
```

- **Router** — HTTP, валидация, авторизация. НЕ содержит бизнес-логику.
- **Service** — бизнес-логика, координация нескольких моделей.
- **Model** — ORM-маппинг, без логики.
- **Schema** — Pydantic: request/response контракты.
- **Cache** — Redis-кэш для тяжёлых запросов.
- **Storage** — MinIO для файлов.
- **Integration** — внешние API (WB, банки).

---

## Модули (границы)

### 1. Auth
- **Таблицы:** `users`
- **Файлы:** `auth.py`, `routers/auth.py`
- **API:** `/api/auth/login`, `/api/auth/change_password`

### 2. Import & Transactions
- **Таблицы:** `transactions`, `import_log`
- **Файлы:** `routers/import_txn.py`, `etl/*`
- **API:** `/api/import/upload`, `/api/transactions/search`, `/api/transactions/inbox`

### 3. References
- **Таблицы:** `accounts`, `counterparty_categories`, `overrides`, `opening_balances`, `category_ref`
- **Файлы:** `routers/refs.py`
- **API:** `/api/refs/*`

### 4. Reports
- **Таблицы:** `transactions` (read-only), `opening_balances`
- **Файлы:** `routers/reports.py`
- **API:** `/api/reports/*`
- **Кэш:** Redis с TTL=300s, инвалидация при импорте

### 5. Planning
- **Таблицы:** `orders`, `planned_payments`, `planned_incomes`, `lead_time`, `customs_topup`, `customs_alloc`, `customs_dt`, `wb_payouts`, `payment_fact_links`
- **Файлы:** `routers/planning.py`
- **API:** `/api/planning/*`

### 6. Cost (Себестоимость)
- **Таблицы:** `nomenclature`, `duty_rules`, `cost_orders`, `cost_order_items`
- **Файлы:** `routers/cost.py`
- **API:** `/api/cost/*`

### 7. Integrations (PLANNED)
- **Таблицы:** `integration_keys` (PLANNED), `wb_payouts`, `wb_sync_log` (PLANNED)
- **Файлы:** `routers/integrations.py`, `integrations/wb_api.py` (PLANNED)
- **API:** `/api/integrations/*` (PLANNED)
- **Описание:** пользователь добавляет API-ключ WB → система периодически тянет данные (продажи, выплаты, остатки) → загружает в БД

---

## Модель данных (24 таблицы)

```
Auth:           users
References:     accounts, counterparty_categories, overrides,
                opening_balances, category_ref
Transactions:   transactions, category_change_log, import_log
Customs:        customs_topup, customs_alloc, customs_dt
Planning:       orders, lead_time, planned_payments,
                planned_incomes, payment_fact_links
WB:             wb_payouts
Cost:           nomenclature, duty_rules, cost_orders, cost_order_items
Integrations:   integration_keys (PLANNED), wb_sync_log (PLANNED)
```

---

## Внешние интеграции (PLANNED)

### Wildberries API
```
Пользователь
  → Настройки → "Добавить WB API ключ"
  → POST /api/integrations/wb/connect {api_key: "..."}
  → Ключ шифруется и сохраняется в integration_keys
  → Периодический sync (или ручная кнопка):
      GET /api/integrations/wb/sync
      → wb_api.py: тянет данные из WB API
      → Записывает в wb_payouts / planned_incomes
      → Обновляет wb_sync_log
```

**WB API endpoints для интеграции:**
- `/api/v1/supplier/incomes` — поставки
- `/api/v1/supplier/orders` — заказы
- `/api/v1/supplier/sales` — продажи
- `/api/v2/finance/report` — финансовый отчёт (детализация выплат)

---

## Infrastructure

```
docker-compose.yml
  ├── db        (PostgreSQL 15, pgdata volume)
  ├── redis     (Redis 7, кэш отчётов)
  ├── minio     (MinIO, хранение файлов)
  ├── backend   (FastAPI, зависит от db+redis)
  └── frontend  (Streamlit → React)

Config:     .env → backend/config.py (Pydantic Settings)
Migrations: Alembic → migrations/versions/
Deploy:     deploy.sh (file mapping → git push → docker rebuild)
```

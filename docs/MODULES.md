# DDS — Документация по модулям

> Система управления финансами товарного бизнеса (ВЭД)

## Архитектура

```
┌─────────────────┐     ┌──────────────┐     ┌────────────┐
│  Frontend       │────▶│  Backend     │────▶│ PostgreSQL │
│  Next.js (3000) │     │  FastAPI     │     │  (5432)    │
│                 │     │  (8000)      │────▶│  Redis     │
└─────────────────┘     └──────────────┘     └────────────┘
```

- **Frontend**: `frontend-react/` — Next.js 14, React, TypeScript
- **Backend**: `backend/` — FastAPI, SQLAlchemy, Pydantic
- **БД**: PostgreSQL (Docker)
- **Кэш**: Redis (Docker)
- **Запуск**: `docker compose up -d --build`

---

## Frontend — Страницы

Все страницы проекта: `frontend-react/src/app/p/[slug]/<module>/page.tsx`

| Модуль | Путь | Описание |
|--------|------|----------|
| **Дашборд** | `page.tsx` | Главная страница проекта, общая статистика |
| **Импорт выписок** | `import/page.tsx` | Загрузка банковских выписок (XML/CSV) |
| **Операции** | `txn/page.tsx` | Просмотр всех транзакций из выписок |
| **INBOX** | `inbox/page.tsx` | Неразнесённые транзакции |
| **Отчёты** | `reports/page.tsx` | Финансовые отчёты и аналитика |
| **Планирование** | `planning/page.tsx` | Заказы (PlanOrders) + Платежи (PlanPayments) |
| **Себестоимость** | `cost/page.tsx` | Заказы, номенклатура, пошлины, файлы |
| **Справочники** | `refs/page.tsx` | Контрагенты, счета, категории |
| **Настройки** | `settings/page.tsx` | Настройки проекта |
| **Команда** | `team/page.tsx` | Управление командой |

### Ключевые файлы frontend

| Файл | Назначение |
|------|-----------|
| `src/lib/api.ts` | **API клиент** — все функции для запросов к backend |
| `src/app/p/[slug]/layout.tsx` | **Layout** — боковая навигация, переключатель проектов |
| `src/app/globals.css` | **Стили** — тёмная тема, glassmorphism, все UI-компоненты |
| `src/app/layout.tsx` | Корневой layout (шрифты, метаданные) |
| `src/app/login/page.tsx` | Страница авторизации |

---

## Backend — Роутеры

Все роутеры: `backend/routers/*.py`

| Роутер | Prefix | Описание |
|--------|--------|----------|
| `auth.py` | `/api/auth` | Авторизация (JWT), регистрация, смена пароля |
| `projects.py` | `/api/projects` | CRUD проектов, переключение проектов |
| `import_txn.py` | `/api` | Импорт выписок, парсинг XML/CSV, список транзакций |
| `planning.py` | `/api` | Планирование: заказы, платежи, привязка транзакций |
| `cost.py` | `/api` | Себестоимость: заказы, номенклатура, пошлины |
| `refs.py` | `/api` | Справочники: контрагенты, счета, категории |
| `reports.py` | `/api` | Отчёты: P&L, баланс, аналитика |
| `integrations.py` | `/api` | WB интеграция, синхронизация номенклатуры |

### Ключевые файлы backend

| Файл | Назначение |
|------|-----------|
| `main.py` | Точка входа FastAPI, подключение роутеров, CORS |
| `models.py` | **SQLAlchemy модели** — все таблицы БД |
| `schemas.py` | **Pydantic схемы** — валидация запросов/ответов |
| `database.py` | Подключение к PostgreSQL |
| `config.py` | Переменные окружения (DATABASE_URL, JWT_SECRET и т.д.) |
| `auth.py` | JWT утилиты (create_token, verify_token) |
| `cache.py` | Redis кэширование |
| `middleware.py` | Middleware (CORS, project context) |
| `project_context.py` | Контекст текущего проекта пользователя |
| `storage.py` | Работа с файлами (загрузка/скачивание) |
| `exceptions.py` | Кастомные исключения |
| `etl/` | ETL-скрипты для обработки данных |
| `integrations/` | Внешние интеграции (WB API) |

---

## Основные API эндпоинты

### Авторизация (`auth.py`)
- `POST /api/auth/login` — вход
- `POST /api/auth/register` — регистрация
- `GET /api/auth/me` — текущий пользователь

### Импорт (`import_txn.py`)
- `POST /api/import_bank_statement` — загрузка выписки
- `GET /api/bank_transactions` — список транзакций
- `GET /api/accounts_list` — список банковских счетов
- `GET /api/candidate_transactions?account=...` — кандидаты для привязки

### Планирование (`planning.py`)
- `GET /api/planning_orders` — заказы в планировании
- `GET /api/planning_payments` — платежи
- `POST /api/planning_payment` — создать/обновить платёж
- `POST /api/fact_link` — привязать транзакцию к платежу
- `POST /api/sync_plan_payments` — синхронизация факта

### Себестоимость (`cost.py`)
- `GET /api/orders` — список заказов
- `POST /api/order` — создать/обновить заказ
- `GET /api/order/{id}/items` — товары заказа
- `GET /api/nomenclature` — номенклатура
- `GET /api/duties` — правила пошлин
- `POST /api/duty` — создать/обновить правило пошлины

### Справочники (`refs.py`)
- `GET /api/counterparties` — контрагенты
- `GET /api/accounts` — банковские счета
- `GET /api/categories` — категории

### Отчёты (`reports.py`)
- `GET /api/reports/pnl` — отчёт P&L
- `GET /api/reports/balance` — баланс
- `GET /api/reports/cashflow` — денежный поток

### Интеграции (`integrations.py`)
- `POST /api/wb/sync_nomenclature` — синхронизация с WB
- `GET /api/wb/nomenclature` — номенклатура WB

---

## Модели БД (основные таблицы)

| Таблица | Описание |
|---------|----------|
| `users` | Пользователи системы |
| `projects` | Проекты (мультитенант) |
| `bank_accounts` | Банковские счета |
| `bank_transactions` | Транзакции из выписок |
| `counterparties` | Контрагенты |
| `orders` | Заказы (ВЭД) |
| `order_items` | Товары в заказе (номенклатура) |
| `planning_payments` | Плановые платежи |
| `fact_links` | Привязки транзакций к платежам |
| `duties` | Правила пошлин по категориям |
| `categories` | Категории товаров |

---

## Docker

```yaml
# docker-compose.yml
services:
  db:          # PostgreSQL :5432
  redis:       # Redis :6379
  backend:     # FastAPI :8000
  frontend-react:  # Next.js :3000
```

Запуск: `docker compose up -d --build`
Только frontend: `docker compose up -d --build frontend-react`
Логи: `docker compose logs -f frontend-react`

---

*Последнее обновление: 2026-03-01*

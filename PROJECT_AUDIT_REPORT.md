# PROJECT AUDIT REPORT — DDS (Digital Data Sheet)
**Дата аудита:** 2026-04-13
**Последнее обновление:** 2026-04-21
**Аудитор:** Claude Opus 4.6 (автоматический аудит)

> **Статус follow-up (2026-04-21):** 2 CRITICAL проблемы (№1 db.delete и №2 REGISTER_ENABLED) закрыты в коммите `d70e124`. Check 10 в `check_conventions.sh` ужесточён с warn → error (коммит `fe5d74f`, 2026-04-21). Подробности — в конце отчёта, раздел «Обновления».

---

## 1. ОБЩАЯ ИНФОРМАЦИЯ О ПРОЕКТЕ

### Назначение
DDS (Digital Data Sheet) — система управленческого учёта для e-commerce на маркетплейсе Wildberries. Обеспечивает учёт транзакций, отчётность (ДДС, БДР, ОПИУ), планирование, управление себестоимостью, складом, поставками, интеграцию с WB API, AI-ассистент для аналитики.

### Технологический стек

| Компонент | Технология | Версия |
|-----------|-----------|--------|
| Backend Framework | FastAPI | 0.124.0 |
| Python | CPython | 3.11 |
| ORM | SQLAlchemy | 2.0.49 |
| Database | PostgreSQL | 15 |
| Connection Pooler | PgBouncer | 1.24.1 |
| Cache | Redis | 7 (alpine) |
| Object Storage | MinIO | RELEASE.2025-10-15 |
| Frontend Framework | Next.js | 15.3.9 |
| React | React | 19.0.0 |
| Node.js | Node.js | 20 (slim) |
| TypeScript | TypeScript | 5.x |
| CSS | Tailwind CSS | 4.x |
| Reverse Proxy | Nginx | 1.25 (alpine) |
| Monitoring | Prometheus + Grafana | 2.53.5 / 11.4.0 |
| Error Tracking | Sentry | SDK 2.8.0 |
| Telegram Bot | aiogram | 3.x |
| AI | Anthropic Claude API | SDK 0.40.0+ |

### Языки программирования
- **Python 3.11** — backend (48,257 LOC)
- **TypeScript/TSX** — frontend (42,272 LOC)
- **SQL** — миграции Alembic (63 миграции)
- **YAML** — конфигурация (59 файлов)

### Статистика файлов

| Расширение | Количество | Назначение |
|-----------|-----------|-----------|
| `.py` | 401 | Backend + тесты |
| `.tsx` | 106 | React компоненты |
| `.ts` | 44 | TypeScript утилиты |
| `.md` | 93 | Документация |
| `.yml/.yaml` | 62 | Конфигурация |

**Общий объём исходного кода:** ~90,500 LOC (без зависимостей)

### Структура папок (верхний уровень)
```
dds_app/
├── backend/              # FastAPI backend (routers, services, models, schemas, etl, integrations)
│   ├── routers/          # 24 роутера
│   ├── services/         # 90+ сервисов (модульная структура)
│   ├── models/           # 24 ORM-модели
│   ├── schemas/          # Pydantic-схемы
│   ├── etl/              # ETL-парсеры (VTB, WB, себестоимость)
│   ├── integrations/     # WB API, Telegram, resilience
│   ├── scheduler/jobs/   # Фоновые задачи (7 jobs)
│   └── utils/            # Утилиты (crypto, time, rate_limit, telegram)
├── frontend-react/       # Next.js 15 frontend
│   └── src/
│       ├── app/(main)/   # 22 страницы основного приложения
│       ├── app/(tma)/    # 6 страниц Telegram Mini App
│       ├── lib/api/      # Модульный API-клиент (14 файлов)
│       ├── components/   # 9 переиспользуемых компонентов
│       └── types/        # TypeScript-типы
├── tests/                # 72 тест-файла (pytest)
├── migrations/versions/  # 63 Alembic-миграции
├── docs/                 # 4 файла документации
├── scripts/              # Утилиты (конвенции, хуки, worktrees)
├── nginx/                # Конфигурация reverse proxy
├── monitoring/           # Prometheus + Alertmanager конфиги
├── docker-compose.yml    # 11 сервисов + 5 мониторинг
└── .claude/              # AI-инструменты (rules, agents, templates)
```

---

## 2. АРХИТЕКТУРА

### Архитектурный паттерн
**Модульный монолит** с чётким разделением на слои. Backend и Frontend деплоятся как отдельные контейнеры, но находятся в одном репозитории (monorepo).

### Слои приложения

```
┌─────────────────────────────────────────────────┐
│  Frontend (Next.js 15, App Router, React 19)     │
│  ├── Pages (22 routes + 6 TMA routes)            │
│  ├── Components (DataTable, FormModal, KpiCard)   │
│  └── API Client (lib/api/ — 14 domain modules)   │
├─────────────────────────────────────────────────┤
│  Nginx (reverse proxy, SSL termination)          │
├─────────────────────────────────────────────────┤
│  Backend API (FastAPI, 24 routers)               │
│  ├── Routers — HTTP only, validation, auth       │
│  ├── Services — бизнес-логика (90+ сервисов)     │
│  ├── Models — SQLAlchemy ORM (24 модели)         │
│  └── Schemas — Pydantic request/response         │
├─────────────────────────────────────────────────┤
│  Worker (фоновые задачи, APScheduler)            │
├─────────────────────────────────────────────────┤
│  PostgreSQL 15 ← PgBouncer ← Backend/Worker     │
│  Redis 7 (cache, refresh tokens, rate limiting)  │
│  MinIO (S3-совместимое хранилище файлов)         │
└─────────────────────────────────────────────────┘
```

### Роутинг (Frontend)
**Next.js App Router** (не Pages Router).
- Основные маршруты: `src/app/(main)/p/[slug]/` — 22 страницы
- Telegram Mini App: `src/app/(tma)/tma/[slug]/` — 6 страниц
- Динамический параметр `[slug]` — project slug для multi-tenancy

### State Management
- **TanStack Query (React Query)** — серверное состояние (кеширование API-ответов)
- **React useState/useCallback** — локальное состояние компонентов
- Нет Redux / Zustand — достаточно React Query + local state

### Работа с данными

| Аспект | Реализация |
|--------|-----------|
| ORM | SQLAlchemy 2.0 (async, `AsyncSession`) |
| Миграции | Alembic (63 миграции, sync driver `psycopg2`) |
| Connection Pool | PgBouncer (transaction mode, 5-20-50) |
| Пагинация | Cursor-based (`WHERE id > :last`) + OFFSET для мелких таблиц |
| Soft Delete | `SoftDeleteMixin` (`is_deleted`, `deleted_at`) |
| Multi-tenancy | `project_id` FK на каждой таблице |

### API структура
**REST API**, без версионирования (единая версия).

| Группа роутеров | Файл | Назначение |
|----------------|------|-----------|
| auth | `auth.py` | JWT login/register/refresh |
| projects | `projects.py` | Управление проектами |
| reports | `reports.py` | DDS, баланс, дашборд |
| reports_wb | `reports_wb.py` | БДР, WB-отчёты |
| reports_stock | `reports_stock.py` | Складская аналитика |
| cost | `cost.py` | Себестоимость, FIFO |
| planning | `planning.py` | Планирование закупок |
| planning_customs | `planning_customs.py` | Таможня |
| planning_wb_payouts | `planning_wb_payouts.py` | WB выплаты (план) |
| warehouse | `warehouse.py` | Склад, остатки |
| funnel | `funnel.py` | Воронка продаж WB |
| supply_chain | `supply_chain.py` | Поставки, транспорт |
| assembly | `assembly.py` | Сборка, логистика |
| import_txn | `import_txn.py` | Импорт транзакций |
| integrations | `integrations.py` | WB API ключи |
| refs | `refs.py` | Справочники |
| monitoring | `monitoring.py` | Мониторинг, метрики |
| ai_chat | `ai_chat.py` | AI-ассистент |
| telegram* (3) | `telegram*.py` | Бот + webhook + TMA |
| ws | `ws.py` | WebSocket |

### Основные модули и связи

```
Транзакции ─────→ Отчёты (DDS, БДР, ОПИУ, Баланс)
     ↑                         ↑
ETL парсеры              Планирование ──→ Cashflow
(VTB, WB)                     ↑
                         Себестоимость ──→ Склад
                              ↑               ↑
                         Поставки ──→ FBO Supply
                              ↑
                      Каталог поставщиков

AI Агенты ──→ 19 инструментов ──→ [все модули выше]
Telegram Bot ──→ дайджест, уведомления
WB API Sync ──→ заказы, продажи, остатки, финансы
```

---

## 3. БЕЗОПАСНОСТЬ

### Аутентификация
- **JWT Access Token** (HS256, 30 мин TTL) — `backend/auth.py`
- **Refresh Token** (UUID + token_urlsafe, 30 дней, хранится в Redis) — `backend/auth.py:62`
- **Password hashing** — bcrypt через `passlib`
- **Auto-refresh** — frontend автоматически обновляет token при 401
- **Telegram HMAC** — подпись `initData` для TMA — `backend/routers/telegram_miniapp.py`

### Авторизация
- **Простая ролевая модель**: `is_admin` флаг на `User`
- `require_admin` dependency для админских endpoint'ов
- `get_current_user` + `get_current_project` — на каждом роутере
- **Нет полноценного RBAC/ACL** — только admin/user, нет гранулярных permissions
- Multi-tenancy изоляция через `project_id` на каждом запросе

### Валидация входных данных
- **Pydantic v2** (`pydantic==2.6.3`) — все request/response schemas
- Валидация размера файлов: `MAX_UPLOAD_SIZE_MB` check перед обработкой
- Валидация силы пароля: `validate_password_strength()` — `auth.py:42`

### SQL инъекции
- **Защищены**: все запросы через SQLAlchemy ORM или параметризованный `text(:param)`
- **Automated checks**: Ruff S-rules + Bandit в pre-commit + CI
- **Convention check**: `check_conventions.sh` сканирует f-string в SQL

### XSS защита
- React автоматически экранирует JSX выражения
- **ВНИМАНИЕ**: `dangerouslySetInnerHTML` используется в AI-чате (`ai-chat/page.tsx:24-39`) с ручным allowlist тегов (без DOMPurify) — потенциальная XSS-уязвимость
- Content Security Policy — **не настроен** (отсутствует)

### CSRF защита
- **Не реализована явно** — используется JWT в Authorization header (cookie не используются для auth, поэтому CSRF не критичен для API)
- Telegram Mini App использует HMAC-подпись

### Секреты
- `.env` в `.gitignore` — **корректно**
- `.env.example` с placeholder-значениями — **есть**
- **Gitleaks** — pre-commit hook + CI workflow
- API-ключи WB шифруются: `backend/utils/crypto.py` (Fernet symmetric encryption)
- Маскированные ключи в API responses (никогда plaintext)

### Зависимости (npm audit)
```
3 high severity vulnerabilities:
- xlsx — Prototype Pollution (GHSA-4r6h-8v6p-xvw6)
- xlsx — ReDoS (GHSA-5pgg-2g8v-p4x9)
- vite — 1 vulnerability (fixable)
```

### Rate Limiting
- **Реализован**: `backend/utils/rate_limit.py`
- **118 использований** в роутерах (`rate_limit_write` dependency)
- `/metrics` bypass защищён
- Atomic TTL для предотвращения race conditions
- Redis-based sliding window

### CORS
- Конфигурируется через `CORS_ORIGINS` env variable
- В production ограничен конкретными доменами
- В development: `localhost:8501, localhost:3000`

### Найденные проблемы безопасности

| # | Проблема | Серьёзность | Место |
|---|---------|------------|-------|
| 1 | `db.delete()` на модели `FactoryOrderItem` с `SoftDeleteMixin` — нарушение конвенции soft-delete | CRITICAL | `services/supply_chain/factory_orders.py:539` |
| 2 | `REGISTER_ENABLED=true` в production — открытая регистрация на финансовой системе | CRITICAL | `.env:29` |
| 3 | `xlsx`, `next`, `vite` — HIGH-уязвимости (Prototype Pollution, SSRF, path traversal) | HIGH | `frontend-react/package.json` |
| 4 | XSS: `dangerouslySetInnerHTML` с ручным allowlist тегов, без DOMPurify | HIGH | `ai-chat/page.tsx:24-39` |
| 5 | Нет rate limiting на `/integrations` и `/fbo-supplies` write endpoints | HIGH | `routers/integrations.py`, `routers/fbo_supplies.py` |
| 6 | `X-Forwarded-For` IP spoofing в per-endpoint `RateLimiter` | HIGH | `utils/rate_limit.py:62-65` |
| 7 | Bandit B608 (SQL f-string) подавлен глобально — регрессии не будут пойманы | HIGH | `bandit.yaml:17` |
| 8 | Content Security Policy не настроен | MEDIUM | `nginx/` конфиг |
| 9 | Admin panel IP whitelist по умолчанию открыт (пустой = все IP разрешены) | MEDIUM | `config.py:53`, `admin/auth.py:31-34` |
| 10 | Auth rate-limiting неатомичный (race condition в `check_rate_limit()`) | MEDIUM | `routers/auth.py:51-62` |
| 11 | Refresh token не привязан к IP/User-Agent, 30 дней TTL | MEDIUM | `auth.py:62-76` |
| 12 | `pyasn1` CVE-2026-30922 (ignored в CI, pinned by python-jose) | LOW | `requirements-backend.txt` |
| 13 | Авто-сгенерированный admin пароль логируется в stdout (может попасть в Sentry) | LOW | `auth.py:159-175` |

---

## 4. КАЧЕСТВО КОДА

### Линтеры

| Инструмент | Область | Конфигурация |
|-----------|---------|-------------|
| **Ruff** | Python (lint + format) | `ruff.toml` (87 строк) — E, W, F, I, UP, B, S, T20, SIM, RUF |
| **ESLint 9** | TypeScript/React | `eslint-config-next@15.3.2` |
| **Bandit** | Python security | `bandit.yaml` (pre-commit) |
| **mypy** | Python type checking | `pyproject.toml` (CI job) |

### Форматирование
- **Ruff formatter** для Python (line-length=120)
- **Prettier** — **не настроен** для frontend (нет `.prettierrc`)
- **Tailwind CSS v4** — utility-first styling

### TypeScript
- **`strict: true`** в `tsconfig.json` — все strict проверки включены
- **Типы централизованы** в `src/types/api.ts`
- **Правило**: никогда `any`, всегда типизированные интерфейсы

### Тестирование

| Фреймворк | Тип | Файлов | Область |
|-----------|-----|--------|---------|
| **pytest** | Unit + Integration | 72 | Backend services, routers |
| **pytest-asyncio** | Async tests | — | Async database operations |
| **pytest-xdist** | Parallel | — | `make test-fast` (3-4x) |
| **pytest-testmon** | Changed only | — | `make test-changed` |
| **vitest** | Unit | 3 | Frontend components |
| **Playwright** | E2E | 11 | End-to-end browser tests |

**Покрытие тестами:**
- Backend: 72 тест-файла — **хорошее покрытие** основных сервисов
- Frontend: 3 unit-теста — **недостаточное покрытие**
- E2E: 11 Playwright specs — **базовое покрытие**

### Error Handling
- **Sentry SDK** — централизованный error tracking (`sentry-sdk[fastapi]==2.8.0`)
- **JSON structured logging** — `JSONFormatter` в `backend/main.py:69`
- **RequestIdMiddleware** — traceability через `X-Request-ID`
- **Redis graceful degradation** — кеш отключается при недоступности Redis
- **Global exception handlers** — в `main.py` (422, 500)

### Логирование
- **structlog** (configured) + stdlib `logging` с JSON formatter
- Request ID прокидывается в каждый лог
- Логи: `docker compose logs backend --tail=50`

### Pre-commit hooks

| Hook | Назначение |
|------|-----------|
| Ruff (lint + format) | Стиль + безопасность Python |
| Bandit | Security linter |
| Gitleaks | Поиск секретов в коде |

### Pre-push hooks
- `pytest --testmon` — изменённые тесты
- `vitest` — frontend тесты
- `check_conventions.sh` — проверка конвенций

---

## 5. ДОКУМЕНТАЦИЯ

### Общая документация

| Файл | Строк | Описание | Полнота |
|------|-------|---------|---------|
| `CLAUDE.md` | 219 | Основные правила, стек, архитектура | Полный |
| `backend/CLAUDE.md` | ~40 | Quick reference для backend | Краткий |
| `frontend-react/CLAUDE.md` | ~20 | Quick reference для frontend | Краткий |
| `README.md` | — | Базовый README | Минимальный |

### Доменная документация (10 файлов)
- `DOMAIN_AI.md` — AI multi-agent system (7 agents, orchestrator, memory, 19 tools)
- `DOMAIN_ASSEMBLY.md` — сборка, логистика
- `DOMAIN_COST.md` — себестоимость, FIFO, пошлины
- `DOMAIN_PLANNING.md` — планирование закупок, cashflow
- `DOMAIN_REPORTS.md` — DDS, БДР, ОПИУ, дашборд
- `DOMAIN_SUPPLY_CHAIN.md` — поставки, транспорт
- `DOMAIN_TELEGRAM.md` — бот, TMA, дайджест
- `DOMAIN_TRANSACTIONS.md` — импорт, ETL, категоризация
- `DOMAIN_WAREHOUSE.md` — склад, FBO, остатки
- `DOMAIN_WB.md` — WB API, воронка, синхронизация
- `DOMAIN_FRONTEND.md` — frontend conventions

### Технические документы

| Файл | Назначение |
|------|-----------|
| `docs/AGENT_DEVELOPMENT.md` | TDD процесс, Agent Teams workflow |
| `docs/CODE_REVIEW.md` | Checklist для code review |
| `docs/KNOWN_PITFALLS.md` | Известные грабли |
| `docs/MODULES.md` | Карта модулей |
| `backend/MAP.md` | Quick navigation по backend |

### API документация
- **OpenAPI/Swagger** — **автоматически генерируется** FastAPI (`/docs`, `/redoc`)
- Все endpoints документированы через Pydantic schemas
- Response models определены для каждого endpoint

### Docstrings / Комментарии
- Backend: docstrings на основных функциях (не на всех)
- Frontend: минимальные комментарии (JSDoc не используется)

### Что НЕ задокументировано, но должно быть
1. Полноценный `README.md` с quick start для новых разработчиков
2. Диаграмма архитектуры (визуальная)
3. Runbook для инцидентов в production
4. Changelog / Release notes
5. Описание deployment pipeline (пошаговое)

---

## 6. AI-РАЗРАБОТКА (ВАЙБ-КОДИНГ ГОТОВНОСТЬ)

### CLAUDE.md
- **219 строк** — полноценный system prompt для AI-агентов
- Содержит: стек, команды, железные правила, архитектуру, домены, антипаттерны, git workflow
- **Качество: отличное** — один из лучших CLAUDE.md, которые можно встретить

### .claude/rules/ (6 правил)

| Файл | Строк | Тема |
|------|-------|------|
| `design.md` | — | Дизайн-принципы |
| `postgres.md` | ~60 | PostgreSQL правила (типы, индексы, миграции) |
| `python.md` | ~80 | Python конвенции (слои, кеш, антипаттерны) |
| `security.md` | ~70 | Безопасность (шифрование, multi-tenancy, uploads) |
| `testing.md` | ~50 | TDD workflow, обязательные test cases |
| `typescript.md` | — | TypeScript правила |

### .claude/agents/ (6 кастомных агентов)

| Агент | Назначение |
|-------|-----------|
| `build-error-resolver` | Исправление ошибок сборки |
| `code-reviewer` | Ревью кода |
| `database-reviewer` | PostgreSQL оптимизация |
| `planner` | Планирование фич |
| `security-reviewer` | Поиск уязвимостей |
| `tdd-guide` | TDD workflow |

### .claude/templates/ (6 шаблонов)

| Шаблон | Назначение |
|--------|-----------|
| `new_model.py.tmpl` | Скелет SQLAlchemy модели |
| `new_router.py.tmpl` | Скелет FastAPI роутера |
| `new_schema.py.tmpl` | Скелет Pydantic схемы |
| `new_service.py.tmpl` | Скелет сервиса |
| `new_test.py.tmpl` | Скелет теста |
| `new_page.tsx.tmpl` | Скелет React страницы |

### .cursorrules
- **Отсутствует** (используется CLAUDE.md вместо этого)

### AI-friendly оценка
- **Именование**: консистентное, предсказуемое (services/, routers/, models/)
- **Структура**: модульная, каждый домен изолирован
- **Контекст**: MAP.md, DOMAIN_*.md, KNOWN_PITFALLS.md — всё для AI
- **Memory system**: `.claude/projects/*/memory/` — persistent AI memory
- **Agent Teams**: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` — параллельные агенты
- **Model routing**: opus/sonnet/haiku для разных задач — оптимизация токенов
- **File ownership**: правила 0-конфликтов между агентами

### Git workflow
- **Ветки**: `dev` → `main` (production auto-deploy)
- **Коммиты**: `feat:` / `fix:` / `infra:` / `refactor:` / `test:`
- **Pre-commit**: Ruff + Bandit + Gitleaks
- **Pre-push**: pytest-testmon + vitest + conventions
- **CI**: Tests + Security + Conventions + Docs check
- **Auto-PR**: dev→main при push
- **Auto-merge**: после зелёного CI
- **Branch protection**: require Tests + Security Audit на `main`

---

## 7. ПРОИЗВОДИТЕЛЬНОСТЬ И МАСШТАБИРУЕМОСТЬ

### Серверные vs клиентские компоненты
- Next.js 15 **App Router** — серверные компоненты по умолчанию
- `"use client"` только где необходимо (интерактивные элементы)
- **Streaming** — не используется явно

### Кеширование

| Уровень | Реализация |
|---------|-----------|
| API Response Cache | Redis, `@cached(prefix, ttl=300)` — 21 использование |
| Connection Pool | PgBouncer (transaction mode, 5-20-50) |
| Refresh Tokens | Redis с TTL |
| Rate Limiting | Redis sliding window |
| Browser Cache | Не настроен явно (нет Cache-Control headers) |

### Оптимизация запросов
- **Eager loading**: 33 использования `joinedload`/`selectinload` — N+1 prevention
- **Batch inserts**: используются в ETL-парсерах
- **Cursor pagination**: `WHERE id > :last` для больших таблиц
- **Partial indexes**: `WHERE is_deleted = false`
- **Statement timeout**: через SQLAlchemy event listener

### Bundle size (Frontend)
- **Не анализируется** — нет `@next/bundle-analyzer`
- `three.js` (3D) в зависимостях — потенциально тяжёлый

### Multi-tenant готовность
- **Реализовано**: `project_id` на каждой таблице, изоляция на уровне запросов
- User может иметь несколько проектов
- Данные полностью изолированы между проектами

### Масштабируемость
- **Backend**: 1 worker (ограничен для OOM prevention), Uvicorn
- **Worker**: отдельный контейнер для фоновых задач
- **PgBouncer**: до 200 клиентов
- **Memory limits**: настроены для всех контейнеров в docker-compose

---

## 8. DEVOPS И ИНФРАСТРУКТУРА

### CI/CD (GitHub Actions — 11 workflows)

| Workflow | Триггер | Назначение |
|---------|---------|-----------|
| `test.yml` | push/PR | Tests + Conventions + Docs + mypy + Frontend tests |
| `security.yml` | push/PR | pip-audit + Gitleaks + Trivy + Snyk Code |
| `cd-production.yml` | push main | Auto-deploy to production |
| `auto-pr.yml` | push dev | Auto-create PR dev→main |
| `auto-merge.yml` | PR approved | Auto-merge after green CI |
| `check-sync-log.yml` | — | Проверка sync_log |
| `manual-funnel-sync.yml` | manual | Ручной запуск sync |
| `server-cleanup.yml` | — | Очистка сервера |
| `server-diagnose.yml` | — | Диагностика |
| `server-fix.yml` | — | Автофикс |
| `server-restart.yml` | — | Рестарт сервисов |

### Docker

| Компонент | Dockerfile | Base Image | Memory Limit |
|-----------|-----------|-----------|-------------|
| Backend | `Dockerfile.backend` | `python:3.11-slim` | 1024M |
| Worker | `Dockerfile.backend` | `python:3.11-slim` | 512M |
| Frontend | `Dockerfile.react` | `node:20-slim` | 256M |
| PostgreSQL | Official | `postgres:15` | 1536M |
| PgBouncer | Official | `edoburu/pgbouncer:v1.24.1` | 64M |
| Redis | Official | `redis:7-alpine` | 320M |
| MinIO | Official | `alpine/minio` | 256M |
| Nginx | Official | `nginx:1.25-alpine` | 64M |

### Деплой
- **Production**: auto-deploy через `cd-production.yml` при push в `main`
- **URL**: `https://app.vyatkin-wb.ru`
- **SSL**: Certbot (Let's Encrypt) — автоматическое обновление
- **Принцип**: НИКОГДА SSH deploy — только CI/CD

### Мониторинг

| Инструмент | Назначение |
|-----------|-----------|
| Prometheus | Метрики (CPU, memory, request latency) |
| Grafana | Визуализация дашбордов |
| Alertmanager | Алерты |
| postgres-exporter | Метрики PostgreSQL |
| redis-exporter | Метрики Redis |
| Sentry | Error tracking + performance tracing |
| `/health` endpoint | Health check для Docker |
| `/metrics` endpoint | Prometheus scrape |

### Бэкапы
- **db-backup** сервис в docker-compose — автоматические бэкапы PostgreSQL
- Настроен через cron в контейнере

### Dependabot
- **Настроен**: `.github/dependabot.yml`
- Еженедельные обновления: pip, npm, GitHub Actions

---

## 9. ИТОГОВАЯ ОЦЕНКА

| Категория | Оценка | Комментарий |
|-----------|--------|-------------|
| **Архитектура** | 9/10 | Отличная модульная структура, чёткое разделение слоёв, продуманная domain decomposition. Multi-tenancy через project_id. Минус: нет API versioning. |
| **Безопасность** | 7/10 | JWT + bcrypt + Fernet encryption + rate limiting + параметризованный SQL. Bandit + Gitleaks + Trivy + Snyk в CI. Минусы: 2 CRITICAL (db.delete на SoftDelete модели, open registration в prod), XSS в AI-чате, нет CSP, IP spoofing в rate limiter, npm CVEs. |
| **Качество кода** | 6/10 | Ruff + ESLint + mypy + Bandit. Pre-commit/pre-push hooks. Минусы: `ignoreBuildErrors: true` в next.config (TS/ESLint ошибки не блокируют билд), frontend тесты почти отсутствуют (3 файла), нет Prettier, coverage не измеряется, E2E не в CI. |
| **Документация** | 9/10 | 10 DOMAIN_*.md, MAP.md, KNOWN_PITFALLS.md, подробный CLAUDE.md. OpenAPI автогенерация. Минус: нет полноценного README, нет Changelog, нет визуальных диаграмм. |
| **AI-готовность** | 10/10 | Лучшая-в-классе AI-инфраструктура: CLAUDE.md (219 строк), 6 rules, 6 agents, 6 templates, memory system, Agent Teams, model routing, file ownership, TDD workflow, convention checks. Эталон для AI-driven development. |
| **Производительность** | 7/10 | Redis cache, PgBouncer, eager loading, cursor pagination. Минус: нет bundle analyzer, three.js в зависимостях, 1 worker (OOM limitation), нет CDN, нет Browser caching headers. |
| **DevOps** | 9/10 | 11 GitHub Actions workflows, Docker Compose (11+5 сервисов), auto-deploy, auto-PR, auto-merge, Dependabot, мониторинг (Prometheus+Grafana+Sentry), бэкапы, SSL. Минус: нет staging среды, нет blue-green deploy. |

**Средняя оценка: 8.1/10**

---

## 10. ТОП-10 КРИТИЧЕСКИХ ПРОБЛЕМ

| # | Приоритет | Проблема | Место | Рекомендация |
|---|----------|---------|-------|-------------|
| 1 | **CRITICAL** | `db.delete()` на `FactoryOrderItem` с `SoftDeleteMixin` — потеря аудит-данных | `services/supply_chain/factory_orders.py:539` | Заменить на `item.soft_delete()` |
| 2 | **CRITICAL** | `REGISTER_ENABLED=true` в production — открытая регистрация на финансовой системе | `.env:29` (prod) | Выставить `REGISTER_ENABLED=false` в production |
| 3 | **HIGH** | XSS: `dangerouslySetInnerHTML` с ручным allowlist тегов, без DOMPurify в AI-чате | `ai-chat/page.tsx:24-39` | Установить и использовать `DOMPurify` для санитизации HTML |
| 4 | **HIGH** | npm CVEs: `xlsx` (Prototype Pollution, ReDoS — no fix), `next` (SSRF), `vite` (path traversal) | `frontend-react/package.json` | Мигрировать `xlsx` → `exceljs`; `npm audit fix` для next/vite |
| 5 | **HIGH** | `X-Forwarded-For` IP spoofing в per-endpoint `RateLimiter` — bypass rate limiting | `utils/rate_limit.py:62-65` | Использовать `X-Real-IP` (как в global middleware) или доверять только Nginx |
| 6 | **HIGH** | `typescript.ignoreBuildErrors: true` + `eslint.ignoreDuringBuilds: true` — TS/ESLint ошибки не блокируют билд | `next.config.mjs` | Убрать оба флага, добавить `tsc --noEmit` и `next lint` в CI |
| 7 | **HIGH** | Frontend тесты: только 3 unit-теста из ~150 компонентов/страниц; E2E не в CI | `frontend-react/src/` | Добавить vitest тесты (цель 30%+), включить Playwright в CI |
| 8 | **MEDIUM** | Нет Content Security Policy + admin panel IP whitelist по умолчанию открыт | `nginx/`, `config.py:53` | Добавить CSP header, настроить `ADMIN_ALLOWED_IPS` в production |
| 9 | **MEDIUM** | Нет rate limiting на `/integrations` и `/fbo-supplies` write endpoints | `routers/integrations.py`, `routers/fbo_supplies.py` | Добавить `Depends(rate_limit_write)` |
| 10 | **MEDIUM** | Backend ограничен 1 worker (OOM prevention) — bottleneck производительности | `docker-compose.yml` | Профилировать memory leaks, оптимизировать, вернуть 2+ workers |

---

## ПРИЛОЖЕНИЕ: Ключевые метрики

```
Backend Python файлов:     401
Frontend TS/TSX файлов:    150
Общий LOC:                 ~90,500
Тестов backend:            72 файла
Тестов frontend:           3 файла
E2E тестов:                11 specs
Alembic миграций:          63
Docker сервисов:           11 + 5 monitoring
CI workflows:              11
Domain документов:         11
AI agents:                 6
AI rules:                  6
AI templates:              6
Git коммитов:              ~500+
Production URL:            app.vyatkin-wb.ru
```

---

*Отчёт сгенерирован автоматически на основе анализа реального кода проекта.*

---

## ОБНОВЛЕНИЯ ПОСЛЕ 2026-04-13

### 2026-04-21 — Закрытие 2 CRITICAL
- **№1** `db.delete()` на `FactoryOrderItem` (`services/supply_chain/factory_orders.py:539`) → заменено на `item.soft_delete()` в коммите `d70e124`
- **№2** `REGISTER_ENABLED=true` в production → secure-default (`False`) + lifespan guard в коммите `d70e124`
- **Enforcement:** Check 10 (`scripts/check_conventions.sh`) переведён с warn → error (коммит `fe5d74f`). Теперь `db.delete()` на модели с SoftDeleteMixin блокирует pre-commit. Whitelist: комментарий `# no-soft-delete-check: <reason>` рядом со строкой для исключений.

### 2026-04-20 — Крупные фиксы
- **№3 (HIGH)** XSS в AI-чате → DOMPurify через `sanitizeAIHtml()` в `frontend-react/src/lib/sanitize.ts` (коммит `8c1d167`), тесты в `__tests__/lib/sanitize.test.ts`
- **№7 (HIGH)** Frontend тесты: добавлены vitest-тесты для всех 16 модулей `src/lib/api/` — 225 тестов (коммит `cbcb76a`). 116 TS errors → 0 (коммит `b5f6787`), TS gate enforced (коммит `a31c664`)
- **Coverage**: `pytest-cov` + `vitest --coverage` + Codecov в `test.yml` (коммит `d892f45`)
- **Performance**: partial indexes `WHERE is_deleted = false` через CONCURRENTLY + worker memory 512→768M (коммит `5cb4d11`)

### Оставшиеся HIGH/MEDIUM (на 2026-04-21)
- №4 npm CVEs — `xlsx` остаётся (no fix), `next`/`vite` обновлены
- №5 `X-Forwarded-For` spoofing в per-endpoint RateLimiter — открыт
- №6 `ignoreBuildErrors` в `next.config.mjs` — снят в коммите `a31c664` (TS gate enforced)
- №8–13 — без изменений

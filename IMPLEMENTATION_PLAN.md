# План внедрения улучшений DDS — Апрель 2026

> Источник: DDS_AI_Development_Recommendations_2026.docx + анализ текущей архитектуры
> Приоритеты: скорость разработки, правильность кода, безопасность

---

## ФАЗА 1: Быстрые победы (1-2 дня)

### 1.1 Agent Teams ✅ DONE (2026-04-05)
- **Что:** Включить экспериментальную фичу Claude Code для координации агентов
- **Как:** `export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` + настроить file ownership rules
- **Даст:** 40% быстрее на фичах, 2-4x на рефакторинге. Каждый агент в чистом контексте = точнее код
- **Сделано:**
  - `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` в `.claude/settings.json` → `env`
  - File Ownership Rules задокументированы в `CLAUDE.md` (Backend/Frontend/Shared/Infra зоны)
  - Правила координации: sequential migrations, 1 file = 1 agent, backend ‖ frontend

### 1.2 MCP: Playwright ✅ DONE (2026-04-05)
- **Что:** `npx @playwright/mcp --headless` — E2E тестирование через агента
- **Как:** Добавить в `.mcp.json` → mcpServers
- **Даст:** Агент сам пишет и запускает E2E тесты. Сейчас 0 E2E тестов
- **Сделано:** Добавлен `playwright` MCP server в `.mcp.json`. Также установлены Homebrew + Node.js v25.9 + tmux 3.6a на хост

### 1.3 MCP: PostgreSQL (read-only) ✅ DONE (2026-04-05)
- **Что:** MCP сервер для прямого доступа агента к БД
- **Как:** Создать read-only пользователя, подключить MCP
- **Даст:** Агент видит реальные данные/схему, не работает вслепую
- **Сделано:**
  - Порт `127.0.0.1:5433:5432` проброшен в docker-compose.yml (localhost only)
  - SQL скрипт `postgres/init-readonly-user.sql` (user: readonly_agent, SELECT only)
  - Init скрипт в docker-entrypoint-initdb.d (авто-создание при первом запуске)
  - MCP `@modelcontextprotocol/server-postgres` добавлен в `.mcp.json`
- **Активация:** `docker compose down && docker compose up -d`, затем для существующей БД: `docker compose exec db psql -U dds -d dds_db -f /etc/postgresql/init-readonly-user.sql`

### 1.4 MCP: GitHub ✅ DONE (2026-04-05)
- **Что:** Official GitHub MCP server
- **Как:** `npx @modelcontextprotocol/server-github` + scope: repo
- **Даст:** Issues, PR, code search доступны агенту без переключения
- **Сделано:** MCP server добавлен в `.mcp.json`
- **Активация:** Добавить `export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...` в `~/.zprofile`

### 1.5 Magic bytes validation (безопасность) ✅ ALREADY DONE
- **Что:** Проверка содержимого файла при upload, не только размера
- **Статус:** Уже реализовано в `backend/utils/file_validation.py`
- **Покрытие:** Все 6 upload-эндпоинтов используют `validate_file_content()`
- **Форматы:** .xlsx (PK/ZIP), .xls (OLE2/HTML/XML/BIFF), .pdf (%PDF), .csv (text encoding)

### 1.6 MinIO credentials из env (безопасность) ✅ DONE (2026-04-05)
- **Что:** Убрать хардкодные дефолтные пароли MinIO
- **Даст:** Закрыта P0 дыра — если контейнер торчит наружу, данные не утекут
- **Сделано:**
  - docker-compose.yml: убраны `:-minioadmin` дефолты (6 мест), теперь ОБЯЗАТЕЛЬНЫ из .env
  - .env: сгенерированы безопасные ключи (token_urlsafe)
  - config.py: валидатор ужесточён — `ValueError` вместо `UserWarning` при `minioadmin`
  - .env.example: уже содержал плейсхолдеры
- **Активация:** `docker compose down && docker compose up -d` (MinIO пересоздастся с новыми кредами)

---

## ФАЗА 2: Фундамент качества (2 недели)

### 2.1 Backend тесты: 34 сервиса → 60%+ покрытие
- **Что:** Сгенерировать тесты для ВСЕХ сервисов через TDD субагента
- **Как:** Для каждого сервиса: happy path + project_id isolation + is_deleted + edge cases
- **Даст:** С 16% до 60%+ покрытие. Регрессии ловятся ДО прода
- **Усилия:** 1-2 недели (с Agent Teams — быстрее)
- **Приоритет внутри:** сначала финансовые (opiu, bdr, cost), потом остальные

### 2.2 mypy --strict на services/ и models/ ✅ DONE (2026-04-05 setup, 2026-04-06 cleanup)
- **Что:** Статическая проверка типов в CI
- **Как:** Добавить mypy в pre-commit + CI, начать с `# type: ignore` на legacy
- **Даст:** Float не просочится в Decimal расчёты. Ruff это НЕ ловит
- **Усилия:** 1 день setup + постепенное исправление
- **Файлы:** `pyproject.toml` / `mypy.ini`, `.pre-commit-config.yaml`, `.github/workflows/test.yml`
- **Сделано:**
  - Phase 1: strict на services/models, 132 файла, 0 ошибок, CI job
  - Phase 2 (2026-04-06): снят suppress с 37 из 52 модулей. Осталось 15 сложных. mypy чисто на 151 файле

### 2.3 Vitest для frontend
- **Что:** Базовые тесты для критичных компонентов
- **Как:** vitest + React Testing Library, тесты на: render, loading, error, empty states
- **Даст:** 73 страницы без единого теста → 40%+ покрытие критичных путей
- **Усилия:** 2-3 дня setup + генерация тестов
- **Файлы:** `frontend-react/vitest.config.ts`, `frontend-react/src/__tests__/`

### 2.4 project_id на 7 недостающих моделей (безопасность)
- **Что:** Добавить project_id FK + миграции для моделей без изоляции
- **Как:** Найти 7 моделей → добавить колонку → миграция → обновить запросы
- **Даст:** Полная tenant isolation. Закрыта P0 дыра cross-tenant access
- **Усилия:** 2-3 дня
- **Файлы:** `backend/models/`, `migrations/versions/`, соответствующие сервисы

### 2.5 Hooks: shell → Agent type
- **Что:** Апгрейд iron rules проверок с grep-паттернов на AI-анализ
- **Как:** PostToolUse (Edit) → Agent hook проверяет project_id, is_deleted, soft_delete с ПОНИМАНИЕМ кода
- **Даст:** Сейчас grep не ловит `is_deleted` пропущенный в сложном JOIN. Agent hook поймёт
- **Усилия:** 2-3 часа
- **Файлы:** `.claude/settings.json`

---

## ФАЗА 3: Зрелость (3-4 недели)

### 3.1 E2E тесты Playwright ✅ DONE (2026-04-06)
- **Что:** 10-15 критичных user flows
- **Как:** С Playwright MCP: агент пишет тест → запускает → видит результат → итерирует
- **Даст:** Визуальные и интеграционные баги ловятся автоматически
- **Сделано:** 11 spec-файлов, 73 E2E теста (auth, dashboard, import, navigation, planning, reports, settings, smoke, team, transactions, warehouse)

### 3.2 Snyk Code в CI
- **Что:** Глубокий SAST поверх Bandit + Trivy
- **Как:** Добавить snyk в `.github/workflows/security.yml`
- **Даст:** Bandit ловит паттерны. Snyk понимает data flow через FastAPI (цепочки injection)
- **Усилия:** 2-3 часа

### 3.3 Per-project CircuitBreaker (безопасность)
- **Что:** Заменить глобальный CircuitBreaker на per-project
- **Как:** Рефакторинг `backend/integrations/resilience.py` — breaker per project_id
- **Даст:** Один клиент с проблемным WB API не блокирует ВСЕХ клиентов
- **Усилия:** 2-3 дня
- **Файлы:** `backend/integrations/resilience.py`, `backend/integrations/wb_api.py`

### 3.4 Рефакторинг монолитов (5 файлов)
- **Что:** Разбить файлы >800 строк на модули
- **Как:** С Agent Teams — один teammate пишет тесты, другой рефакторит
- **Цели:**
  - `assembly_service.py` (1121 строк) → assembly_crud.py + assembly_logistics.py + assembly_status.py
  - `fbo_supply_service.py` (882) → fbo_crud.py + fbo_cost.py + fbo_sync.py
  - `warehouse_stock_service.py` (852) → stock_queries.py + stock_reports.py
  - `executor.py` (29K) → разбить 19 tools по доменным файлам (уже есть tools/)
- **Даст:** Агенты быстрее читают, меньше конфликтов при параллелке
- **Усилия:** 1-2 недели

### 3.5 Load testing
- **Что:** Базовые нагрузочные тесты
- **Как:** locust или k6, сценарии: concurrent imports, report generation, WB sync
- **Даст:** Baseline метрики + найдены race conditions и bottleneck'и
- **Усилия:** 2-3 дня

---

## СВОДНАЯ ТАБЛИЦА

| # | Что | Скорость | Качество | Безопасность | Усилия |
|---|-----|----------|----------|-------------|--------|
| **ФАЗА 1** |
| 1.1 | Agent Teams | +++ | ++ | — | 1-2ч |
| 1.2 | Playwright MCP | + | +++ | — | 30м |
| 1.3 | PostgreSQL MCP | ++ | + | — | 30м |
| 1.4 | GitHub MCP | + | — | — | 30м |
| 1.5 | Magic bytes | — | — | +++ | 2ч |
| 1.6 | MinIO creds | — | — | +++ | 1ч |
| **ФАЗА 2** |
| 2.1 | Backend тесты 60%+ | — | +++ | + | 1-2нед |
| 2.2 | mypy strict | — | +++ | ++ | 1д |
| 2.3 | Vitest frontend | — | +++ | — | 2-3д |
| 2.4 | project_id x7 | — | — | +++ | 2-3д |
| 2.5 | Agent hooks | + | ++ | ++ | 2-3ч |
| **ФАЗА 3** |
| 3.1 | E2E Playwright | — | +++ | — | 1нед |
| 3.2 | Snyk Code | — | — | +++ | 2-3ч |
| 3.3 | Per-project CB | — | — | ++ | 2-3д |
| 3.4 | Рефакторинг 5 файлов | ++ | ++ | — | 1-2нед |
| 3.5 | Load testing | — | ++ | + | 2-3д |

## ОЖИДАЕМЫЙ РЕЗУЛЬТАТ (через 8 недель)

| Метрика | Сейчас | Цель |
|---------|--------|------|
| Backend test coverage | 16% → ~50% | 60%+ |
| Frontend test coverage | 0% → базовый | 40%+ |
| E2E сценариев | 0 → 73 | 10-15 ✅ |
| Скорость фич (backend+frontend) | baseline | 2-3x (Agent Teams + MCP) |
| Скорость рефакторинга | baseline | 2-4x |
| MCP серверов | 1 | 4-5 |
| Модели с project_id | 23/30 | 30/30 |
| Type checking (mypy) | нет → strict (15 suppress) | strict на services/ |
| Upload security | size only | size + magic bytes |
| SAST | Bandit (patterns) | + Snyk (data flow) |
| CircuitBreaker | глобальный → per-project ✅ | per-project |
| Файлы >500 строк (нарушение) | 5 | 0 |

### 3.6 Rate limiting на write endpoints ✅ DONE (2026-04-06)
- **Что:** Rate limiting на POST/PUT/DELETE эндпоинтах (кроме auth — уже было)
- **Как:** Reusable `RateLimiter` dependency в `backend/utils/rate_limit.py`, Redis sliding window
- **Даст:** Защита от brute-force/скриптовых атак на мутирующие эндпоинты
- **Сделано:**
  - `backend/utils/rate_limit.py` — `RateLimiter` class с graceful degradation
  - Import endpoints: 5/min, обычные write: 30/min
  - 6 роутеров: import_txn, cost, planning, warehouse, assembly, supply_chain (71 endpoint)
  - 10 unit тестов в `tests/test_rate_limit.py`, все проходят

---

## НЕ ВНЕДРЯЕМ (и почему)

| Рекомендация | Причина отказа |
|-------------|---------------|
| Qodo/CodiumAI | Claude TDD субагент делает то же. Лишний инструмент = overhead |
| KMS/Vault | Fernet работает, один сервер. KMS окупится при масштабировании |
| Sentry MCP | Sentry уже интегрирован через DSN, MCP — удобство, не необходимость |

---

## КАК ИСПОЛЬЗОВАТЬ ЭТОТ ПЛАН

В новой сессии Claude Code:
```
Открой IMPLEMENTATION_PLAN.md — это план внедрения улучшений.
Начни с Фазы 1, пункт 1.1 (Agent Teams).
```

Каждый пункт содержит: что делать, как, какие файлы, сколько займёт.

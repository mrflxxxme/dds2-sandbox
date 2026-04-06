# CLAUDE.md — DDS (управленческий учёт для e-commerce / Wildberries)

## Стек
FastAPI + PostgreSQL 15 + PgBouncer + Redis + MinIO + Next.js 15 (React 19)

## Команды
```bash
docker compose up -d                              # Запуск
docker compose exec backend pytest tests/ -x      # Тесты
docker compose logs backend --tail=50             # Логи
bash scripts/check_conventions.sh                 # Проверка конвенций
make test-fast                                    # Параллельные тесты (xdist)
make test-changed                                 # Только изменённые тесты (testmon)
make test-unit                                    # Только unit-тесты
cd frontend-react && npx vitest run              # Frontend тесты (39)
```

## Железные правила (нарушение = баг)
Подробности в `.claude/rules/` — здесь краткая сводка:
1. **project_id** — КАЖДЫЙ запрос к БД фильтрует по `project_id`
2. **is_deleted** — `.where(Model.is_deleted == False)` для SoftDeleteMixin
3. **soft_delete** — `model.soft_delete()` (НИКОГДА `db.delete()`)
4. **datetime** — `from backend.utils.time import utcnow`
5. **деньги** — `Numeric(18, 2)` (НИКОГДА Float)
6. **SQL** — параметризованный `:param` (НИКОГДА f-string)
7. **кэш** — `invalidate_cache(prefix)` после мутаций
8. **логика** — бизнес-логика в `services/` (НИКОГДА в `routers/`)

## Архитектура backend
```
routers/ (HTTP only) → services/ (логика) → models/ (ORM)
schemas/ — Pydantic request/response
etl/ — импорт выписок (парсеры VTB, WB)
integrations/ — внешние API (WB)
scheduler/jobs/ — фоновые задачи (ТОЛЬКО в worker container)
```

### Рефакторнутые пакеты (разбиты из монолитов)
- `services/assembly/` — crud.py + status.py + analytics.py (было assembly_service.py)
- `services/fbo_supply/` — service.py + sync.py + mappers.py (было fbo_supply_service.py)
- `services/ai/tools/` — 7 domain modules + common.py (было executor.py)
- `services/reports/queries/` — control.py + income.py + filters.py (было queries.py)
- `services/warehouse_need_service.py` — расчёт потребности (выделен из warehouse_stock_service.py)

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

### Agent Teams (координация параллельных агентов)
Включено: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` в `.claude/settings.json`

#### File Ownership Rules (0 конфликтов)
| Зона | Файлы | Владелец |
|------|-------|----------|
| Backend | `backend/`, `migrations/`, `docker/`, `tests/` | Backend teammate |
| Frontend | `src/`, `frontend-react/`, `next.config.*` | Frontend teammate |
| Shared (sequential only) | `models/`, `schemas/`, `CLAUDE.md`, `.claude/` | Lead agent |
| Infra | `docker-compose.yml`, `.github/`, `scripts/` | Lead agent |

#### Правила координации
- **Alembic миграции** — ТОЛЬКО lead agent, последовательно
- **Один файл = один агент** — никогда двое в одном файле
- **Backend ‖ Frontend** — всегда параллельно (0 пересечений)
- **Model → Migration → Schema** — последовательно (lead), потом параллелятся
- **Рефакторинг** — один teammate пишет тесты, другой рефакторит (разные файлы)

### Технические детали
Подробности в `.claude/rules/` и DOMAIN файлах. Ключевое:
- **PgBouncer**: `prepared_statement_cache_size=0`, `DATABASE_URL_SYNC` для Alembic/ETL
- **Кэш**: `invalidate_cache(prefix)` добавляет `:*` сам — НЕ передавать wildcard
- **Crypto**: `backend/utils/crypto.py`, legacy_fallback — НЕ менять без data-migration
- **WB API**: sync_log ВСЕГДА обновлять в finally, deductions → см. `DOMAIN_WB.md`

## Архитектура frontend
```
src/app/(main)/p/[slug]/ — основное приложение (23+ страниц: dds, import, txn, inbox, reports, planning, cost, funnel, trends, refs, settings, opiu, orders, plan-fact, team, monitoring, bulk-cost, container-loader, order-geography, warehouse/*, supply-chain)
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

## Домены — DOMAIN_*.md для сложных задач, gotchas ниже для быстрых
| Домен | Gotchas | Ключевые файлы |
|-------|---------|----------------|
| Транзакции | deduplicate by txn_id; VTB/WB парсеры | etl/, transactions_service |
| Отчёты | кэш 300s; invalidate_project_reports(); ОПИУ+БДР синхронизировать | reports/, opiu_service, wb_bdr_service |
| Планирование | plan_items привязаны к category; cashflow = txn+plan | planning/, routers/planning |
| Себестоимость | FIFO; duty per container; cost_price = себест + пошлина + логистика | cost/, cost_parsers, cost_parser_helpers |
| Склад | FBO vs FBS; stock_date ≠ report_date; остатки WB daily sync | warehouse_*, fbo_supply_service |
| WB API | Semaphore; Retry-After; partial save; sync_log в finally | integrations/, funnel/, scheduler/jobs/ |
| Сборка | assembly/ — crud+status+analytics (рефакторнут) | assembly/, routers/assembly |
| AI Агенты | orchestrator→agents→synthesizer; 22 tools в 7 модулях (ai/tools/); memory=BrandNote | services/ai/ |
| Telegram | polling с прокси на проде; HMAC auth для TMA | telegram_bot, telegram_service |
| Поставки | FactoryOrder→CostOrder→Warehouse; VehicleStatus; split_to_vehicles | supply_chain/, cost.py |
| Фронтенд | types/api.ts; formatNumber(); loading+error+empty states | src/app/, src/lib/api/ |

## Быстрая навигация (для агентов)
- **Карта backend** → `backend/MAP.md` (типовые паттерны, импорты, где что лежит)
- **Шаблоны** → `.claude/templates/` (скелеты service, router, test, model, schema, page)
- **Грабли** → `docs/KNOWN_PITFALLS.md` (ошибки которые агенты повторяют)
- **Worktrees** → `scripts/worktree-start.sh` / `worktree-finish.sh` (параллельная работа)
- **Runbooks** → `.claude/runbooks/common-scenarios.md` (пошаговые сценарии: endpoint, страница, баг, миграция)

## Перед началом задачи
Следуй процессу Agent TDD из `docs/AGENT_DEVELOPMENT.md`:
- **Фича** → уточни неясное, покажи ТЗ, жди подтверждения, потом кодь
- **Баг/мелочь** → сразу делай

## Git
- Коммиты: `feat:` / `fix:` / `infra:` / `refactor:` / `test:` (русский или англ.)
- Ветки: `dev` → проверка → merge в `main` → production auto-deploy
- НИКОГДА не деплоить через SSH — только CI/CD
- Перед коммитом: тесты + check_conventions.sh

## Безопасность и CI
### Pre-commit (автоматически при коммите)
- **Ruff** — стиль + security (S-rules настроены в `ruff.toml`)
- **Bandit** — Python security linter (eval, injection, weak crypto; конфиг в `bandit.yaml`)
- **Gitleaks** — секреты в коде

### CI (автоматически при push/PR)
- **Conventions + Tests** — параллельные jobs
- **BuildKit cache** — Docker layer cache через `actions/cache` (ключ: `requirements-backend.txt` + `Dockerfile.backend`)
- **pip-audit** — CVE в Python-зависимостях
- **Trivy** — filesystem scan (HIGH/CRITICAL)
- **Snyk Code** — SAST (статический анализ кода, advisory-only; требует `SNYK_TOKEN` secret)
- **CodeRabbit** — AI code review на каждый PR (конфиг `.coderabbit.yaml` с iron rules DDS)

### Dependabot (еженедельно)
- Авто-PR для pip, npm, GitHub Actions зависимостей

### MCP-серверы (Claude Code)
- **Context7** — актуальная документация библиотек в промпте
- **Docker** — управление контейнерами без docker compose exec
- **Playwright** — E2E-тестирование и browser automation (headless)
- **PostgreSQL** — read-only доступ к БД через `readonly_agent` (порт 5434)
- **GitHub** — PR, issues, code search без gh CLI

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
Полный список в `.claude/rules/` и `docs/KNOWN_PITFALLS.md`. Критичные:
- Запрос без `project_id` / `is_deleted` фильтра
- `db.delete()` вместо `soft_delete()`
- f-string в SQL, Float для денег, `datetime.utcnow()`
- Бизнес-логика в роутере, `.scalars().all()` без `.limit()`
- `except Exception` без `asyncio.CancelledError` в scheduler jobs

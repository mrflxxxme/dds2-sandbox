# CLAUDE.md — DDS (управленческий учёт для e-commerce / Wildberries)

## Стек
FastAPI + PostgreSQL 15 + PgBouncer + Redis + MinIO + Next.js 15 (React 19)

## Команды
```bash
docker compose up -d                              # Запуск
docker compose exec backend pytest tests/ -x      # Тесты
docker compose logs backend --tail=50             # Логи
bash scripts/check_conventions.sh                 # Проверка конвенций
bash scripts/check_docs.sh                        # Проверка актуальности документации
make test-fast                                    # Параллельные тесты (xdist)
make test-changed                                 # Только изменённые тесты (testmon)
make test-unit                                    # Только unit-тесты
cd frontend-react && npx vitest run              # Frontend тесты
cd frontend-react && npx playwright test          # E2E тесты (73)
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
9. **rate limit** — write endpoints через `Depends(rate_limit_write)` из `backend/utils/rate_limit.py`

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

### Agent TDD + Teams
Детали в `.claude/rules/agent_workflow.md` и `docs/AGENT_DEVELOPMENT.md`.
- **Фича** → уточни ТЗ, жди подтверждения, потом кодь
- **Баг/мелочь** → сразу делай
- **Backend + Frontend** → АВТОМАТИЧЕСКИ создавать Team (TeamCreate), параллельные teammates
- **Один файл = один агент**, Alembic миграции ТОЛЬКО последовательно

### Технические детали
Подробности в `.claude/rules/` и DOMAIN файлах. Ключевое:
- **PgBouncer**: `prepared_statement_cache_size=0`, `DATABASE_URL_SYNC` для Alembic/ETL
- **Кэш**: `invalidate_cache(prefix)` добавляет `:*` сам — НЕ передавать wildcard
- **Crypto**: `backend/utils/crypto.py`, legacy_fallback — НЕ менять без data-migration
- **WB API**: sync_log ВСЕГДА обновлять в finally, deductions → см. `DOMAIN_WB.md`

## Архитектура frontend
```
src/app/(main)/p/[slug]/ — основное приложение (21 страниц: dds, import, txn, inbox, reports, planning, cost, funnel, trends, refs, settings, opiu, orders, plan-fact, team, monitoring, bulk-cost, container-loader, order-geography, warehouse, supply-chain)
src/app/(tma)/tma/[slug]/ — Telegram Mini App (capital, chat, funnel, pnl, pulse, warehouse)
src/lib/api/ — модульный API клиент (client.ts + 14 доменных файлов, JWT auth + auto-refresh)
src/lib/utils.ts — formatNumber, formatDate, exportToExcel
src/components/ — DataTable, FormModal, KpiCard, PageGuard, PageHeader, TabLayout, TanStackDataTable, Toast
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
| AI Агенты | orchestrator→agents→synthesizer; 19 tools в 11 модулях (ai/tools/); memory=BrandNote | services/ai/ |
| Telegram | polling с прокси на проде; HMAC auth для TMA | telegram_bot, telegram_service |
| Поставки | FactoryOrder→CostOrder→Warehouse; VehicleStatus; split_to_vehicles | supply_chain/, cost.py |
| Фронтенд | types/api.ts; formatNumber(); loading+error+empty states | src/app/, src/lib/api/ |

## Быстрая навигация (для агентов)
- **Карта backend** → `backend/MAP.md` (типовые паттерны, импорты, где что лежит)
- **Шаблоны** → `.claude/templates/` (скелеты service, router, test, model, schema, page)
- **Грабли** → `docs/KNOWN_PITFALLS.md` (ошибки которые агенты повторяют)
- **Worktrees** → `scripts/worktree-start.sh` / `worktree-finish.sh` (параллельная работа)
- **Runbooks** → `.claude/runbooks/common-scenarios.md` (пошаговые сценарии: endpoint, страница, баг, миграция)

## Git
- Коммиты: `feat:` / `fix:` / `infra:` / `refactor:` / `test:` (русский или англ.)
- Ветки: `dev` → проверка → merge в `main` → production auto-deploy
- НИКОГДА не деплоить через SSH — только CI/CD
- Перед коммитом: тесты + check_conventions.sh

## CI и безопасность
- **Pre-commit**: Ruff + Bandit + Gitleaks (автоматически)
- **Pre-push**: pytest-testmon + vitest + check_conventions.sh (блокирует при ошибке)
- **CI**: Tests + Security (pip-audit, Trivy, Snyk) + Conventions + CodeRabbit AI review
- **Auto-flow**: push dev → auto-PR → green CI → auto-merge → deploy
- **Branch protection `main`**: require Tests + Security Audit, strict
- Установка хуков: `make setup` | Экстренный пропуск: `git push --no-verify`

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
- `uvicorn --workers >1` + `--limit-max-requests` (race condition — см. P24)
- Расчёт на `restart: unless-stopped` для unhealthy (он только для exited — нужен `autoheal`)

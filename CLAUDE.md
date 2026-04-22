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
cd frontend-react && npx vitest run                                # Frontend тесты
cd frontend-react && npx playwright test tests/e2e/smoke.spec.ts   # Smoke (27 страниц, ~2 мин)
cd frontend-react && npx playwright test                           # Все E2E (73 теста, локально/debug)
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
- **Backend + Frontend** (обе части реально нужны) → 2 параллельных агента (или Team)
- **Только backend / только frontend** → lead делает сам, последовательно (без спавна агентов)
- **Subagents по триггеру** (`code-reviewer`, `security-reviewer`, etc.) → ПОСЛЕ работы, не параллельно с ней
- **Валидация** (pytest/vitest/conventions) → Bash напрямую, без спавна агентов
- **Один файл = один агент**, Alembic миграции ТОЛЬКО последовательно

### Технические детали
Подробности в `.claude/rules/` и DOMAIN файлах. Ключевое:
- **PgBouncer**: `prepared_statement_cache_size=0`, `DATABASE_URL_SYNC` для Alembic/ETL
- **Кэш**: `invalidate_cache(prefix)` добавляет `:*` сам — НЕ передавать wildcard
- **Crypto**: `backend/utils/crypto.py`, legacy_fallback — НЕ менять без data-migration
- **WB API**: sync_log ВСЕГДА обновлять в finally, deductions → см. `DOMAIN_WB.md`

## Архитектура frontend
```
src/app/(main)/p/[slug]/ — основное приложение (22 страниц: dds, import, txn, inbox, reports, planning, cost, funnel, trends, refs, settings, opiu, orders, plan-fact, team, monitoring, bulk-cost, container-loader, order-geography, warehouse, supply-chain, ai-chat)
src/app/(tma)/tma/[slug]/ — Telegram Mini App (capital, chat, funnel, pnl, pulse, warehouse)
src/lib/api/ — модульный API клиент (client.ts + 15 доменных файлов, JWT auth + auto-refresh)
src/lib/utils.ts — formatNumber, formatDate, exportToExcel
src/components/ — DataTable, FormModal, KpiCard, PageGuard, PageHeader, TabLayout, TanStackDataTable, Toast, BoxDetailCell
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
| Отчёты | кэш 300s; invalidate_project_reports(); ОПИУ+БДР синхронизировать; Cost-DNA — per-category revenue decomposition | reports/, opiu_service, wb_bdr_service, cost_dna_service |
| AI Chat (web) | CRUD conversations, SSE streaming, file upload; вызывает orchestrator из AI домена | routers/ai_chat, ai_chat_service, models/ai_chat |
| Планирование | plan_items привязаны к category; cashflow = txn+plan | planning/, routers/planning |
| Себестоимость | FIFO; duty per container; cost_price = себест + пошлина + логистика | cost/, cost_parsers, cost_parser_helpers |
| Склад | FBO vs FBS; stock_date ≠ report_date; остатки WB daily sync | warehouse_*, fbo_supply_service |
| WB API | Semaphore; Retry-After; partial save; sync_log в finally | integrations/, funnel/, scheduler/jobs/ |
| Сборка | assembly/ — crud+status+analytics (рефакторнут) | assembly/, routers/assembly |
| AI Агенты | orchestrator→agents→synthesizer; 19 tools в 11 модулях (ai/tools/); memory=BrandNote | services/ai/ |
| Telegram | polling с прокси на проде; HMAC auth для TMA | telegram_bot, telegram_service |
| Поставки | FactoryOrder→CostOrder→Warehouse; VehicleStatus; split_to_vehicles | supply_chain/, cost.py |
| Контрагенты+Займы | upsert by INN/contract_number; мультивалюта RUB/CNY раздельно; Faktura.ru парсер; enrich_purpose regex; backfill скрипт | counterparty_service, loan_service, reports/counterparty_turnovers, etl/parsers/faktura.py, etl/master_logic.py (DOMAIN_COUNTERPARTY.md) |
| Фронтенд | types/api.ts; formatNumber(); loading+error+empty states | src/app/, src/lib/api/ |

## Быстрая навигация (для агентов)
- **Lead-agent canon v2** → `.claude/rules/lead_agent_v2.md` (роутинг задач, параллелизм, cloud-команды, anti-patterns — источник правды для Opus 4.7)
- **Opus 4.7 specifics** → `docs/OPUS_4_7_MIGRATION.md` (что изменилось vs 4.6: literal понимание, verbosity, subagent delegation, task budget)
- **Карта backend** → `backend/MAP.md` (типовые паттерны, импорты, где что лежит)
- **AI workflow** → `docs/AI_WORKFLOW.md` (шпаргалка: сценарии, subagents, skills, что работает само)
- **Шаблоны** → `.claude/templates/` (скелеты service, router, test, model, schema, page)
- **Грабли** → `docs/KNOWN_PITFALLS.md` (ошибки которые агенты повторяют)
- **Worktrees** → `scripts/worktree-start.sh` / `worktree-finish.sh` (параллельная работа)
- **Runbooks** → `.claude/runbooks/common-scenarios.md` (пошаговые сценарии: endpoint, страница, баг, миграция)
- **Ревью** → `REVIEW.md` (чеклист для AI и human ревью)
- **Статус** → `/status` (быстрая диагностика: git, docker, health, миграции)

## AI-агенты и skills (`.claude/`)
**Subagents** (`.claude/agents/`) — проактивно вызывать по описанию. **Все на `opus` 4.7** (Max подписка, требование владельца 2026-04-21):
| Агент | Когда |
|-------|-------|
| `code-reviewer` | после правок кода — качество + конвенции |
| `security-reviewer` | auth/SQL/crypto/user-input — OWASP + DDS-специфика |
| `performance-optimizer` | новые endpoint/массовые выборки — N+1, bundle, slow queries |
| `api-designer` | новые/изменённые routers/schemas — OpenAPI, breaking changes |
| `database-reviewer` | миграции, сложные SQL, PgBouncer |
| `tdd-guide` | новые фичи — тесты первыми |
| `build-error-resolver` | pytest/build fail — минимальные фиксы |
| `planner` | планирование фичи/рефакторинга |

**Slash skills** (`.claude/commands/`) — строгие процессы:
- **Разработка**: `/new-endpoint`, `/new-page`, `/migration`, `/tdd`, `/plan`
- **Крупные фичи** (локально): `/spec` (spec-driven, 3 артефакта, regressions 6%→2%) — для 2-5 файлов
- **Крупные фичи** (облако ☁️): `/ultraplan` — cross-domain рефакторы (3+ DOMAIN), миграция auth, новый домен, большая интеграция. 3 explorer-агента + critic, освобождает локальный терминал. См. `.claude/rules/lead_agent_v2.md` §5
- **Рефакторинг**: `/codemod` (AST-grep + LLM для 10+ файлов)
- **Emergency**: `/hotfix` (прод-инцидент), `/rollback` (откат деплоя)
- **Проверки**: `/smoke`, `/verify`, `/review`, `/status`, `/build-fix`
- **Ревью больших PR** (облако ☁️): `/ultrareview` — PR > 500 LOC, миграции БД, security-sensitive, money-handling. До 5 мая 2026 — 3 бесплатных запуска
- **Рефлексия**: `/learn` (авто-после коммита), `/docs`, `/pause`, `/resume`

## Git
- Коммиты: `feat:` / `fix:` / `infra:` / `refactor:` / `test:` (русский или англ.)
- Ветки: `dev` → проверка → merge в `main` → production auto-deploy
- НИКОГДА не деплоить через SSH — только CI/CD
- Перед коммитом: тесты + check_conventions.sh

## CI и безопасность
- **Pre-commit**: Ruff + Bandit + Gitleaks (автоматически)
- **Pre-push**: pytest-testmon + vitest + check_conventions.sh + check_slopsquatting.sh (блокирует при ошибке)
- **CI**: Tests + Security (pip-audit, Trivy, Snyk) + Conventions + Claude Code AI review (`claude-review.yml`, opus-4-7 везде)
- **Coverage**: `pytest-cov` (backend) + `vitest --coverage` (frontend) + Codecov upload в `test.yml` (коммит d892f45)
- **Reactive CI**: `ci-failure-issue.yml` — при fail Tests/Security автоматически создаёт tracking issue, закрывает когда green
- **Slopsquatting**: `check_slopsquatting.sh` — валидирует новые imports против PyPI/npm (защита от AI-галлюцинаций пакетов)
- **Conventions enforcement**: Check 10 (`db.delete()` на SoftDelete) теперь **error**, не warn — коммит `fe5d74f`. Обход: комментарий `# no-soft-delete-check: <reason>` рядом со строкой
- **Auto-flow**: push dev → auto-PR → green CI → auto-merge → deploy
- **Branch protection `main`**: require Tests + Security Audit, strict
- **Deep review** (`/ultrareview` multi-agent, ~$15-25/ревью): `auto-label-deep-review.yml` ставит label `deep-review` на PR с миграциями, `Numeric(18,` изменениями, auth/crypto файлами, diff > 1000 LOC. Lead вызывает `/ultrareview` в CLI перед merge. Правила и бюджет: `REVIEW.md`
- Установка хуков: `make setup` | Экстренный пропуск: `git push --no-verify`

## Деплой по серверам (3-серверная архитектура)
| Что меняется | Workflow | Куда |
|---|---|---|
| `backend/`, `frontend-react/`, `Dockerfile.backend`, `docker-compose.app.yml` | `cd-production.yml` | App-сервер (130.49.150.69) |
| `infra/monitoring/**` (alert_rules, prometheus, grafana, alertmanager) | `deploy-monitoring.yml` | Monitoring (10.0.0.3) |
| Data-сервер (PG, Redis, MinIO) | вручную (нет workflow) | Data (10.0.0.1) |
- Monitoring деплой требует secrets `MON_HOST` / `MON_USER` / `MON_SSH_KEY` / `MON_PATH`
- Hot-reload Prometheus через `POST /-/reload` (требует `--web.enable-lifecycle`)

## Operational workflows (.github/workflows/)
| Workflow | Триггер | Назначение |
|---|---|---|
| `test.yml` | push / PR | Tests (pytest + vitest, без E2E — E2E вынесен в nightly) |
| `e2e-nightly.yml` | **cron** `0 3 * * *` (ежедневно 03:00 UTC) + manual | Smoke E2E (27 страниц не крашатся) — не блокирует deploy |
| `security.yml` | push / PR / **cron** `0 5 * * *` (daily 05:00 UTC) | pip-audit, Trivy, Snyk, npm audit |
| `claude-review.yml` | PR / `@claude` comment | Claude AI-ревью PR single-agent (opus-4-7 везде, 20-25 turns) |
| `auto-label-deep-review.yml` | PR open/sync | ставит label `deep-review` на PR с migrations / money / auth / >1000 LOC → сигнал lead'у запустить `/ultrareview` перед merge |
| `auto-pr.yml` | push в `dev` | автоматический PR `dev → main` |
| `auto-merge.yml` | green CI на auto-PR | авто-merge при зелёном CI |
| `cd-production.yml` | merge в `main` | деплой на app-сервер |
| `post-merge.yml` | push в `main` | ждёт `cd-production` через GH API → HTTP healthcheck `app.vyatkin-wb.ru` → GH issue при fail |
| `todo-sentinel.yml` | **cron** `0 6 * * 1` (Mon 06:00 UTC) + manual | стары TODO/FIXME (>30 дней) → tracking issue |
| `known-bugs-sentinel.yml` | **cron** `0 6 * * 2` (Tue 06:00 UTC) + manual | P{N} markers в коде → tracking issue |
| `weekly-retrospective.yml` | **cron** `0 7 * * 0` (Sun 07:00 UTC) + manual | анализ git log за неделю → suggestions |
| `auto-docs-learn.yml` | **cron** `0 6,18 * * *` (09:00 + 21:00 MSK) + manual | headless Claude → /learn + /docs для pending коммитов → auto-commit `[auto-learn]` в dev |
| `deploy-monitoring.yml` | изменения `infra/monitoring/**` | rsync на monitoring-сервер (без `--delete`, исключает `.env` — см. P-incident 2026-04-14) |
| `server-cleanup.yml` | **cron** `0 3 * * 0` (вс 03:00 UTC) + manual | еженедельная очистка диска app-сервера |
| `server-diagnose.yml` | manual | диагностика прод-сервера (логи, ресурсы) |
| `server-fix.yml` | manual | оперативные фиксы (рестарт сервисов, чистка кэша) |
| `server-restart.yml` | manual | рестарт прод-контейнеров |
| `check-sync-log.yml` | manual | диагностика WB sync_log |
| `manual-funnel-sync.yml` | manual | принудительный пересинк WB funnel |

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

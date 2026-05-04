# CLAUDE.md — DDS (управленческий учёт e-commerce / Wildberries)

## Стек
FastAPI + PostgreSQL 15 + PgBouncer + Redis + MinIO + Next.js 15 (React 19)

## Команды
```bash
docker compose up -d                              # запуск
docker compose exec backend pytest tests/ -x      # тесты backend
bash scripts/check_conventions.sh                 # конвенции
make test-fast | test-changed | test-unit         # backend варианты
cd frontend-react && npx vitest run               # frontend тесты
cd frontend-react && npx playwright test tests/e2e/smoke.spec.ts  # smoke E2E
```

## Iron rules (нарушение = баг, проверяет post_edit_check.py)
1. КАЖДЫЙ запрос к БД — фильтр `project_id`
2. SoftDeleteMixin → `.where(Model.is_deleted == False)`
3. Удаление → `model.soft_delete()`, НЕ `db.delete()`
4. Время → `from backend.utils.time import utcnow`, НЕ `datetime.utcnow()`
5. Деньги → `Numeric(18, 2)`, НЕ Float
6. SQL → `:param`, НЕ f-string
7. После мутации → `invalidate_cache(prefix)` (без `:*`)
8. Логика → `services/`, роутер только HTTP
9. Write endpoints → `Depends(rate_limit_write)`

## Архитектура
**Backend:** `routers/` (HTTP) → `services/` (логика) → `models/` (ORM). `schemas/` Pydantic. `etl/` парсеры. `integrations/` внешние API. `scheduler/jobs/` — только worker container.

**Frontend:** `src/app/(main)/p/[slug]/` основное приложение, `src/app/(tma)/tma/[slug]/` TMA, `src/lib/api/` модульный клиент, `src/types/api.ts` типы.

**Порядок нового модуля:** Model → Migration → Schema → Service → Router → Test.

**Frontend:** типы → `types/api.ts`, API → `lib/api/`, числа → `formatNumber()`, обязательно loading/error/empty states.

## Workflow (детали в `.claude/rules/lead_agent_v2.md`)
- Фича → уточни ТЗ → ТЗ approved → код
- Баг/мелочь → сразу
- Backend+Frontend (обе части) → 2 параллельных teammate
- Только backend ИЛИ frontend → lead сам, sequential
- Subagents (`code-reviewer`, `security-reviewer` etc.) → ПОСЛЕ работы, по триггеру
- Alembic миграции → ТОЛЬКО sequential, lead

## Технические заметки
- **PgBouncer:** `prepared_statement_cache_size=0`, `DATABASE_URL_SYNC` для Alembic/ETL
- **Кэш:** `invalidate_cache(prefix)` сам добавляет `:*`
- **Crypto:** `backend/utils/crypto.py`, legacy_fallback — НЕ менять без data-migration
- **WB API:** sync_log в `finally`, deductions → `DOMAIN_WB.md`

## Домены (детали в `backend/DOMAIN_*.md`)
| Домен | Ключевое | Файлы |
|-------|----------|-------|
| Транзакции | dedupe by txn_id | `etl/`, `transactions_service` |
| Отчёты | кэш 300s; синк ОПИУ+БДР | `reports/`, `opiu_service`, `wb_bdr_service` |
| AI Chat | SSE streaming, file upload | `routers/ai_chat`, `models/ai_chat` |
| Себестоимость | FIFO; duty per container | `cost/`, `cost_parsers` |
| Склад | FBO vs FBS; daily WB sync | `warehouse_*`, `fbo_supply_service` |
| WB API | Semaphore, Retry-After, partial save | `integrations/`, `funnel/` |
| Сборка | crud+status+analytics | `assembly/` |
| AI Агенты | orchestrator→agents→synthesizer | `services/ai/` |
| Поставки | FactoryOrder→CostOrder→Warehouse | `supply_chain/` |
| Контрагенты+Займы | upsert by INN; мультивалюта | `counterparty_service`, `loan_service`, `DOMAIN_COUNTERPARTY.md` |

## Навигация
- `.claude/rules/lead_agent_v2.md` — lead-agent canon (роутинг, параллелизм, iron rules)
- `backend/MAP.md` — карта backend
- `backend/DOMAIN_*.md` — домены
- `.claude/rules/learnings.md` — накопленные решения
- `docs/KNOWN_PITFALLS.md` — повторяющиеся грабли
- `memory/MEMORY.md` — feedback + project state
- `/status` — диагностика (git, docker, миграции)

## Subagents (по триггеру, всё на opus 4.7)
`code-reviewer`, `security-reviewer`, `performance-optimizer`, `api-designer`, `database-reviewer`, `tdd-guide`, `build-error-resolver`, `planner`.

## Skills (детали `.claude/commands/`)
- Разработка: `/new-endpoint`, `/new-page`, `/migration`, `/tdd`, `/plan`
- Крупные фичи: `/spec` (локально), `/ultraplan` ☁️ (cross-domain)
- Рефакторинг: `/codemod`
- Emergency: `/hotfix`, `/rollback`
- Проверки: `/smoke`, `/verify`, `/review`, `/status`, `/build-fix`
- Большие PR: `/ultrareview` ☁️ (миграции, security, money)
- Рефлексия: `/learn` (после коммита), `/docs`, `/pause`, `/resume`

## Git
- Коммиты: `feat:` / `fix:` / `infra:` / `refactor:` / `test:`
- Ветки: `dev` → CI green → merge `main` → auto-deploy
- НЕ деплоить через SSH, только CI/CD
- Перед коммитом: тесты + `check_conventions.sh`

## CI и безопасность
- Pre-commit: Ruff + Bandit + Gitleaks
- Pre-push: pytest-testmon + vitest + conventions + slopsquatting
- CI: Tests + Security (pip-audit, Trivy, Snyk) + Conventions + Claude Code review
- Auto-flow: push dev → auto-PR → green CI → auto-merge → cd-production
- Branch protection main: require Tests + Security
- `make setup` — установка хуков, экстренный пропуск `git push --no-verify`

## Деплой (3-серверная)
| Что | Workflow | Куда |
|---|---|---|
| backend, frontend, Dockerfile | `cd-production.yml` | App (130.49.150.69) |
| `infra/monitoring/**` | `deploy-monitoring.yml` | Monitoring (10.0.0.3) |
| Data (PG/Redis/MinIO) | вручную | Data (10.0.0.1) |

## Среды
| Среда | Ветка | URL |
|---|---|---|
| Local | dev | http://localhost:3000 |
| Production | main | https://app.vyatkin-wb.ru |

## При баге или новом модуле
1. Новая SoftDelete-модель → добавь в `SOFT_MODELS` в `scripts/check_conventions.sh`
2. Новый отчёт с кэшем → prefix в `invalidate_project_reports()` в `backend/cache.py`
3. Новый домен → создай `backend/DOMAIN_*.md` + строку в таблице доменов
4. Новый антипаттерн → check в `scripts/check_conventions.sh`
5. Урок из бага → `memory/project_known_bugs.md`

## Анти-паттерны (критичные)
- Запрос без `project_id` / `is_deleted`
- `db.delete()` вместо `soft_delete()`
- f-string в SQL, Float для денег, `datetime.utcnow()`
- Бизнес-логика в роутере, `.scalars().all()` без `.limit()`
- `except Exception` без `asyncio.CancelledError` в scheduler jobs
- `uvicorn --workers >1` + `--limit-max-requests` (race, P24)
- Расчёт на `restart: unless-stopped` для unhealthy (нужен autoheal)

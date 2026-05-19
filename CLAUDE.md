# CLAUDE.md — DDS

> Slim canon (~60 строк, Anthropic 2026 style). Детали — по @-import ниже.

## Стек
FastAPI + PostgreSQL 15 + PgBouncer + Redis + MinIO + Next.js 15 (React 19).

## Команды
```bash
docker compose up -d                              # запуск
docker compose exec backend pytest tests/ -x      # backend тесты
make test-fast | test-changed | test-unit
cd frontend-react && npx vitest run               # frontend тесты
cd frontend-react && npx playwright test tests/e2e/smoke.spec.ts
bash scripts/check_conventions.sh                 # конвенции
make sync-prod                                    # локалка = копия прода
```

## Iron rules (enforced by `scripts/hooks/post_edit_check.py`)
1. Каждый DB-запрос — фильтр `project_id`.
2. SoftDeleteMixin → `.where(Model.is_deleted == False)`.
3. Удаление → `model.soft_delete()`, не `db.delete()`.
4. Время → `from backend.utils.time import utcnow`.
5. Деньги → `Numeric(18, 2)`, не `Float`.
6. SQL → `:param`, не f-string.
7. После мутации → `invalidate_cache(prefix)` (без `:*`).
8. Логика → `services/`, роутер только HTTP.
9. Write endpoints → `Depends(rate_limit_write)`.

## Архитектура
`routers/` (HTTP) → `services/` (логика) → `models/` (ORM). `schemas/` Pydantic. `etl/` парсеры. `integrations/` внешние API. `scheduler/jobs/` — worker container only.

**Frontend:** `src/app/(main)/p/[slug]/` основное, `src/app/(tma)/tma/[slug]/` TMA, `src/lib/api/` клиент, `src/types/api.ts` типы. Числа — `formatNumber()`. Обязательно loading/error/empty states.

**Порядок нового модуля:** Model → Migration → Schema → Service → Router → Test.

## Workflow
- Баг / мелочь / «как устроено X» → действуй сразу, без уточнений.
- Сложная фича (>3 файлов И неясно WHAT/WHERE И hard-to-revert) → уточни. Иначе — кодь.
- Миграции Alembic → sequential, lead. Перед коммитом: `alembic upgrade head && downgrade -1 && upgrade head`.
- Push — **только** по явному запросу пользователя.
- Параллелизм (worktree-teammates) — **только** когда явно прошу или задача очевидно cross-domain large. По умолчанию lead делает sequential.

## Технические заметки
- **PgBouncer:** `prepared_statement_cache_size=0` обязателен; `DATABASE_URL_SYNC` для Alembic/ETL.
- **Кэш:** `invalidate_cache(prefix)` добавляет `:*` сам.
- **Crypto:** `backend/utils/crypto.py` с `legacy_fallback` — не менять без data-migration.
- **WB API:** `sync_log` в `finally`; deductions — `DOMAIN_WB.md`.
- **Sync-prod:** маскирует ключи (см. `scripts/pull-prod-snapshot.sh`); локалка не дёргает WB/Telegram.

## Git
- Коммиты: `feat:` / `fix:` / `infra:` / `refactor:` / `test:` (русский ok).
- Ветки: `dev` → CI green → `main` → auto-deploy. Не деплоить через SSH.

## Прогрессивный контекст (читать по необходимости, не auto-load)
- @backend/DOMAIN_INDEX.md — карта доменов и ссылки на `backend/DOMAIN_*.md`.
- @backend/MAP.md — карта backend.
- @.claude/rules/lead_agent_v2.md — детали роутинга / параллелизма (только если задача требует).
- @.claude/rules/learnings.md — накопленные грабли (~50KB, читать таргетно).
- @memory/MEMORY.md — manual feedback + known bugs.
- @docs/_archive/ — устаревшая документация (AGENTS.md, ARCHITECTURE.md, BUSINESS_RULES.md, CONVENTIONS.md, PLAYBOOK.md, REVIEW.md) — справка, не правила.

## Skills + Subagents
- Core skills: `/smoke`, `/verify`, `/migration`, `/hotfix`, `/plan`. Остальные в `.claude/commands/` — legacy, работают, но не основной путь.
- Subagent: `code-reviewer` (вызывать после работы). Остальные `.claude/agents/*` — по запросу.
- Cloud: `/ultraplan` (большая cross-domain), `/ultrareview` (PR >500 LOC / миграции / money).

## Анти-паттерны (критичные)
Без `project_id` / `is_deleted`; `db.delete()` для SoftDelete; f-string в SQL; Float для денег; `datetime.utcnow()`; логика в роутере; `.scalars().all()` без `.limit()`; `except Exception` без `asyncio.CancelledError` в scheduler.

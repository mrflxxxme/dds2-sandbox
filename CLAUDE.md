# CLAUDE.md — DDS

Единственный always-on канон проекта. Остальное грузится по требованию — ссылки в конце.

## Стек
FastAPI + PostgreSQL 15 + PgBouncer + Redis + MinIO + Next.js 15 (React 19). Solo dev.

## Команды
```bash
docker compose up -d                            # запуск
docker compose exec backend pytest tests/ -x    # backend тесты
make test-fast | test-changed | test-unit       # быстрые срезы
cd frontend-react && npx vitest run             # frontend тесты
bash scripts/check_conventions.sh               # проверка конвенций
make sync-prod                                  # локалка = копия прода
```

## Iron rules
1. Каждый DB-запрос фильтрует `project_id`.
2. Модель с `SoftDeleteMixin` → `.where(Model.is_deleted == False)`.
3. Удаление → `model.soft_delete()`, не `db.delete()`.
4. Время → `from backend.utils.time import utcnow`, не `datetime.utcnow()`.
5. Деньги → `Numeric(18, 2)`, не `Float`.
6. SQL → `:param`-binding, не f-string в `text()`.
7. После мутации → `invalidate_cache(prefix)` (суффикс `:*` добавится сам).
8. Бизнес-логика → `services/`; роутер — только HTTP и валидация.
9. Write-эндпоинты → `Depends(rate_limit_write)`.

Правила 1–6 и частично 8 проверяет hook `scripts/hooks/post_edit_check.py`. Правила 7 и 9 не энфорсятся ничем — следи сам.

## Архитектура
`routers/` (HTTP) → `services/` (логика) → `models/` (ORM). `schemas/` — Pydantic, `etl/` — парсеры, `integrations/` — внешние API, `scheduler/jobs/` — только worker-контейнер.
**Порядок нового модуля:** Model → Migration → Schema → Service → Router → Test.
**Frontend:** `src/app/(main)/p/[slug]/` — основное, `(tma)/tma/[slug]/` — Telegram Mini App. Клиент — `src/lib/api/`, типы — `src/types/api.ts`. Числа через `formatNumber()`. На каждой странице обязательны loading / error / empty / data состояния.

## Приём задачи
Уточняющий вопрос — последнее средство. Уточняй ТОЛЬКО когда выполнены все три условия: задача затрагивает >3 файлов и многошаговая; неясно WHAT или WHERE; выбор трудно откатить (схема БД, контракт API, удаление кода). Во всех прочих случаях — баги, «как устроено X», мелкие правки, короткие запросы — действуй сразу.

## Роутинг задач
| Запрос | Действие |
|---|---|
| новый endpoint / страница / миграция БД | `/new-endpoint` · `/new-page` · `/migration` |
| крупная фича, cross-domain | `/plan` |
| прод упал / откат деплоя | `/hotfix` · `/rollback` |
| pytest или сборка падают | `/build-fix` |
| перед коммитом / после фичи | `/verify` · `/learn` |
| баг, вопрос, мелкая правка | без skill — сразу |

После написания кода запускай `/review` (фан-аут профильных субагентов по diff-путям, единый вердикт APPROVE/WARNING/BLOCK; обычно — шагом `/verify`). Субагенты `code-reviewer`, `security-reviewer`, `database-reviewer`, `performance-optimizer`, `api-designer` — по запросу или когда задача в их зоне.

## Workflow
- Миграции Alembic — sequential, делает только lead. Перед коммитом прогнать `alembic upgrade head && downgrade -1 && upgrade head`.
- Фичи и багфиксы — через TDD: падающий тест первым, потом минимальная реализация.
- Обновление документации — в тот же коммит, что и код.
- Push в remote — только по явному запросу. Деплой — через CI (`dev` → CI green → `main` → auto-deploy), не через SSH.
- Коммиты: `feat:` / `fix:` / `infra:` / `refactor:` / `test:` (русский текст ok).

## Параллелизм
По умолчанию lead работает sequential. Worktree-teammates — только для крупных cross-domain задач и только по явному запросу.
- Зоны владения: backend (`backend/`, `migrations/`, `tests/`) ‖ frontend (`frontend-react/`). `models/`, `schemas/`, `.claude/`, `cache.py`, миграции — lead sequential.
- В промпт каждому teammate: относительные пути (это worktree!); при изменении API — types-first; `git status` в финальном отчёте; долгие задачи — `run_in_background`.

## Анти-паттерны
Запрос без `project_id` или `is_deleted`; `db.delete()` для SoftDelete-модели; f-string в SQL; `Float` для денег; `datetime.utcnow()`; бизнес-логика в роутере; `.scalars().all()` без `.limit()`; `SELECT *`; `except Exception` без проброса `asyncio.CancelledError` в scheduler jobs.

## Технические заметки
- **PgBouncer:** `prepared_statement_cache_size=0` обязателен; для Alembic/ETL — `DATABASE_URL_SYNC`.
- **Crypto:** `backend/utils/crypto.py` с `legacy_fallback` — не менять без data-migration.
- **Sync-prod:** маскирует ключи; локальная среда не обращается к WB/Telegram.

## Контекст по требованию
Не грузится автоматически — читай, когда задача этого требует:
- [backend/DOMAIN_INDEX.md](backend/DOMAIN_INDEX.md) — карта доменов и ссылки на `backend/DOMAIN_*.md`.
- [backend/MAP.md](backend/MAP.md) — навигация по backend: где что лежит, типовые паттерны.
- [.claude/rules/learnings.md](.claude/rules/learnings.md) — накопленные грабли.
- `.claude/rules/{backend,frontend,design,migrations}.md` — детальные правила, грузятся по `paths` при правке кода.

@memory/MEMORY.md

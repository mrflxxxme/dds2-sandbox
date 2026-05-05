# DDS2 — lead-agent (v2, Opus 4.7) — компактная версия

> Полная версия с обоснованиями: `_archive/lead_agent_full.md` (читать ТОЛЬКО при явном запросе «почему так»; не загружается автоматически).

## Кто ты
Tech lead DDS2 (FastAPI + PG + Next.js 15, solo dev). Дефолт effort `medium`; `xhigh` только если задача явно требует архитектурного мышления (`/ultraplan`, сложный рефакторинг, design review). Простые правки/баги/поиск — без extended thinking.

## Приём задачи (clarification budget = МИНИМУМ)
Уточняющий вопрос — **последнее** средство, не первое. По умолчанию — действуй.

**Уточняй ТОЛЬКО если** все 3 условия выполнены одновременно:
- Задача выглядит >3 файлов И многошаговая
- Непонятно WHAT (что именно изменить) ИЛИ WHERE (какие файлы)
- Тебе нужно сделать выбор, который сложно откатить (схема БД, контракт API, удаление кода)

**НЕ уточняй** для:
- Багов / «не работает X» / «почему ошибка» — читай код, фикси, готово
- «Как устроено X» / «покажи код» / «объясни» — Read/Grep напрямую, отвечай
- Мелких правок / переименований / cleanup в 1-3 файлах
- Любого запроса <60 символов (короткий = действуй, не переспрашивай)

Триггеры из таблицы ниже (hotfix, smoke, status, verify) — всегда без уточнений.

## Роутинг
| Запрос | Действие |
|---|---|
| новый endpoint/API | `/new-endpoint` |
| новая страница/UI | `/new-page` |
| миграция БД | `/migration` (sequential, lead only) |
| TDD-стиль | `/tdd` |
| большая фича / cross-domain рефактор / новый домен | `/ultraplan` ☁️ |
| фича средней сложности (2-5 файлов) | `/spec` |
| PR > 500 LOC / миграции / auth / money | `/ultrareview` ☁️ |
| мелкое ревью | `/review` |
| rename в 10+ файлах | `/codemod` |
| прод упал | `/hotfix` |
| откат | `/rollback` |
| быстрая проверка (30 сек) | `/smoke` |
| перед коммитом | `/verify` |
| статус | `/status` |
| pytest/build падает | `/build-fix` |
| баг / «не работает X» / «как устроено X» | без skill, прямо |

## Параллелизм (правило, не выбор)

**Backend + Frontend = ОБЯЗАТЕЛЬНО parallel.** Hook `prompt-team-detect.sh` инжектит директиву — исполняешь без обсуждения.

**Auto-fan-out (всегда параллельно в одном turn-е):**
- Backend + Frontend → 2 teammates `isolation: worktree`, `run_in_background: true`
- Сбор контекста из 3+ файлов перед решением → spawn explore-subagents одним turn-ом
- Комплексное ревью (security + perf + api-design) → 3 reviewer одним turn-ом
- Независимые расследования бага в разных слоях → spawn в одном turn-е

Когда задача затрагивает И backend (`backend/`, миграции, сервисы, API) И frontend (`frontend-react/`, страницы, компоненты):
1. Lead делает Phase 1 sequentially: Model → Migration → Schema (если нужно).
2. Lead в **одном turn-е** spawn-ит 2 teammates с `isolation: worktree`, `run_in_background: true`:
   - Backend teammate: `backend/`, `migrations/`, `tests/`
   - Frontend teammate: `frontend-react/src/`, `frontend-react/tests/`
3. Каждый teammate получает constraints: relative paths only, types-first, 40k token budget, fail-fast при 2× pre-commit fail.

**Sequential только когда:**
- Только backend ИЛИ только frontend (даже 10 файлов) → lead sam.
- Alembic миграции → ВСЕГДА lead, ВСЕГДА sequential.
- Поиск кода в 1-2 местах → Grep/Glob/Read напрямую (без Explore-агента).
- Reviewer-subagents → ПОСЛЕ работы, не параллельно с ней.
- Shared-файлы (`models/`, `schemas/`, `cache.py`, `CLAUDE.md`, `.claude/`) → lead sequentially.

**Бюджет teammate:** 40k токенов средний / 80k long-running. Превышение без результата → fail-fast.

**File ownership:** Backend → `backend/`, `migrations/`, `tests/`. Frontend → `frontend-react/`. Shared (lead only, sequential) → `models/`, `schemas/`, `CLAUDE.md`, `.claude/`, `cache.py`.

## Iron rules (post_edit_check.py enforces)
1. Каждый запрос к БД фильтрует `project_id`.
2. SoftDelete → `.where(Model.is_deleted == False)`.
3. Удаление → `model.soft_delete()`, НЕ `db.delete()`.
4. Время → `from backend.utils.time import utcnow`.
5. Деньги → `Numeric(18, 2)`, НЕ Float.
6. SQL → `:param`, НЕ f-string.
7. После мутации → `invalidate_cache(prefix)` (без `:*`).
8. Логика в `services/`, роутер только HTTP.
9. Write endpoints → `Depends(rate_limit_write)`.

## Auto-docs sync (atomic коммит)
После работы перед коммитом проверить `git diff --stat`. Если затронуты:
- Новый домен → создать `backend/DOMAIN_<NAME>.md` + строка в таблице `CLAUDE.md`
- Изменён endpoint/схема домена → обновить `backend/DOMAIN_*.md`
- Новый Skill / hook / правило → обновить `CLAUDE.md`
- Новый паттерн/антипаттерн → `learnings.md` / `check_conventions.sh`
- Исправлен баг → `memory/project_known_bugs.md`

Docs-обновления идут в **тот же коммит** что и код (atomic). **Push — только по запросу пользователя**, не авто.

Hook `post_stop_check.sh` напоминает; pending пишется в `.claude/.pending-docs.log`.

## Анти-паттерны
- НЕ писать мета-объяснения («сейчас сделаю X, потом Y») — 4.7 verbosity сама калибруется.
- НЕ ослаблять параллелизм «на всякий».
- НЕ применять блокирующий security-фикс без чтения `memory/feedback_register_enabled_prod.md` и аналогов.
- НЕ дублировать локально работу `/ultraplan` или `/ultrareview` пока они идут.
- НЕ деплоить через SSH — только CI/CD.
- НЕ создавать `.md` файлы кроме явно требуемых (DOMAIN_*.md / docs обновления).
- НЕ коммитить без `/verify` (или хотя бы `/smoke`) на крупных изменениях.
- НЕ оставлять docs устаревшими после изменения домена — синк в том же коммите.
- НЕ пушить без явного запроса пользователя.

## Cloud-offload (когда есть смысл)
- `/ultraplan` — план дороже реализации (cross-domain, auth, новый домен, большая интеграция). Локально работай параллельно над другим, не дублируй.
- `/ultrareview` — PR > 500 LOC, миграции, security/money. До 5 мая 2026 — 3 бесплатных запуска. После — $5-20.

## Навигация
- `backend/MAP.md` — карта backend
- `backend/DOMAIN_*.md` — домены
- `.claude/rules/learnings.md` — накопленные решения
- `.claude/rules/agent_workflow.md` — детали параллелизма (читать перед спавном teammates)
- `memory/MEMORY.md` — feedback + project state
- `_archive/lead_agent_full.md` — полная версия с обоснованиями (читать только при явном запросе «почему так»)

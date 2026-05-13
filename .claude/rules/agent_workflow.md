# Agent Workflow — операционные детали worktree-параллелизма

> **Канон:** [`lead_agent_v2.md`](lead_agent_v2.md) — роутинг, когда параллелить, бюджеты, iron rules.
> Этот файл — только то, чего в каноне нет: расширенная таблица File Ownership и 5 constraints, которые ОБЯЗАТЕЛЬНО копировать в промпт каждого worktree-teammate.
> Грузится в каждый сеанс (нет `paths`), потому что hook `prompt-team-detect.sh` может инжектить параллелизм для любого промпта.

## File Ownership (расширенная)
| Зона | Файлы | Владелец |
|------|-------|----------|
| Backend | `backend/`, `migrations/`, `docker/`, `tests/` | Backend teammate |
| Frontend | `src/`, `frontend-react/`, `next.config.*` | Frontend teammate |
| Shared (sequential only) | `models/`, `schemas/`, `CLAUDE.md`, `.claude/`, `cache.py` | Lead agent |
| Infra | `docker-compose.yml`, `.github/`, `scripts/` | Lead agent |

**Правила координации:**
- Alembic миграции — ТОЛЬКО lead, sequential
- Один файл = один агент (никогда двое в одном файле)
- Model → Migration → Schema — sequential lead, ПОТОМ параллелятся backend ‖ frontend
- `cache.py` — lead, ПОСЛЕ завершения backend teammate

## Constraints для worktree-teammate (копировать в промпт каждому)

Вставлять **каждому** teammate при `isolation: worktree`. Прецедент: `feedback_worktree_isolation.md` (2026-04-20 — 30 мин на merge-конфликты).

### 1. Relative paths only
> Ты работаешь в git worktree. **ВСЕ** пути — относительные, от текущей рабочей директории. **НИКАКИХ** `/Users/a1/Desktop/dds_app/...` в Read/Write/Edit/Bash — иначе правки уйдут в main dir вместо worktree, создадут add/add конфликты при merge. **Hook `TeammateIdle` (`scripts/hooks/teammate_idle_check.sh`) проверяет это автоматически и блокирует idle с exit 2.**

### 2. Types-first для тестов
> Если меняешь API — сначала обнови `frontend-react/src/types/api.ts` и `frontend-react/src/lib/api/<domain>.ts`, **потом** пиши/обновляй тесты.

### 3. Git-status-check в конце
> Перед завершением работы выполни `git status` и `git diff --stat` в своей рабочей директории. В отчёте main-агенту перечисли: (a) изменённые файлы с путями, (b) подтверждение что все пути — внутри worktree, (c) `git log -1 --oneline` если коммитил.

### 4. Long-running → run_in_background
> Если ожидаемое время выполнения >5 мин — `run_in_background: true`. Иначе пользователь думает что Claude завис.

### 5. Pre-commit fail-fast (advisory budget 40k / 80k токенов)
> Если pre-commit падает 2 раза подряд на одном файле — fail-fast, отчёт lead-агенту, НЕ крутить цикл. Прецедент 2026-04-21: `agent-a9ac9883`, 25 мин вместо 15. Превышение бюджета без результата = teammate зациклился.

## Quality gate перед idle (новое 2026-05-13)
`TeammateIdle` hook (`scripts/hooks/teammate_idle_check.sh`) запускается ДО возврата управления lead-агенту и проверяет в worktree teammate'а:
- Iron rule violations (Float, datetime.utcnow, db.delete, f-string в SQL)
- Types-first для frontend
- Абсолютные пути проекта (worktree leak)
- `check_conventions.sh` smart-mode

Exit 2 → stderr возвращается teammate как feedback, teammate продолжает фиксить, lead не получает преждевременный «готово».

---
paths:
  - "**/*"
---
# Agent Workflow — DDS2

## Принцип
**По умолчанию lead делает задачу сам, последовательно.** Параллельные агенты включаются только когда задача реально делится на изолированные слои (backend + frontend).

**Подробно:** `docs/AGENT_DEVELOPMENT.md`

## Когда параллелить
| Тип задачи | Как делать |
|---|---|
| Только backend | lead сам, последовательно |
| Только frontend | lead сам, последовательно |
| Backend + Frontend (обе части реально нужны) | 2 параллельных агента или Team (Backend ‖ Frontend) |
| Рефакторинг / баг / поиск кода | lead сам |
| Очень крупная фича `/spec`-уровня | по обсуждению с пользователем |

## Фазы (для фичи backend+frontend)
```
Фаза 0:   Lead (opus): EnterPlanMode → уточняет → ТЗ → ExitPlanMode (только при approval)
Фаза 1:   Lead (opus): Model → Migration → Schema (последовательно)
Фаза 2:   Backend ‖ Frontend (2 агента, opus, isolation: worktree)
Фаза 2.5: code-reviewer / security-reviewer ПО ТРИГГЕРУ (последовательно, не параллельно)
Фаза 3:   Bash напрямую — pytest && vitest && check_conventions.sh → коммит
```

**Если задача только backend или только frontend** — Фаза 2 делается lead'ом без спавна агентов. Фазы 1 и 3 — те же.

**Model policy: opus 4.7 везде** (Max подписка, требование владельца 2026-04-21, см. `memory/feedback_model_always_opus.md`).

## Subagents по триггеру (последовательно после работы)
| Агент | Триггер |
|-------|---------|
| `code-reviewer` | после крупного диффа (≥ 3 файла) |
| `security-reviewer` | если задеты auth / SQL / crypto / user-input |
| `performance-optimizer` | новые endpoints с массовыми выборками |
| `database-reviewer` | миграции, сложные SQL, PgBouncer |
| `api-designer` | новые/изменённые routers/schemas |

**НЕ запускать параллельно с работой** — только после её завершения. Параллельный review не давал выигрыша и иногда применял блокирующие фиксы без чтения memory (прецедент 2026-04-21).

## Skills (быстрые шаблоны)
| Skill | Когда |
|-------|-------|
| `/new-endpoint` | новый API endpoint (schema → service → router → test) |
| `/migration` | Alembic миграция (heads → revision → upgrade/downgrade) |
| `/new-page` | Next.js страница (types → api → page с loading/error/empty) |
| `/plan` | альтернатива встроенному Plan Mode для backend-only задач |
| `/spec` | spec-driven development для крупных фич |

## Plan Mode для фич
Lead вызывает `EnterPlanMode` ПЕРЕД любыми правками. Без `ExitPlanMode` (approval пользователя) код не пишется. Решает «агент сразу кодит» из `feedback_ask_questions`.

Баги и мелкие изменения — без ТЗ, сразу анализ → фикс → тесты → коммит.

## Изоляция параллельных агентов (`isolation: worktree`)
Используется ТОЛЬКО когда запускаются 2+ агента одновременно (backend + frontend).

- Создаёт временный git worktree автоматически
- Убирается сам если агент не сделал изменений
- Если изменения есть — путь и ветка возвращаются для merge
- 0 конфликтов файлов между параллельными агентами

Lead agent (Phase 1, Phase 3 коммит) → НЕ изолируется (работает в main worktree).

## File Ownership Rules (для backend+frontend параллелизма)
| Зона | Файлы | Владелец |
|------|-------|----------|
| Backend | `backend/`, `migrations/`, `docker/`, `tests/` | Backend teammate |
| Frontend | `src/`, `frontend-react/`, `next.config.*` | Frontend teammate |
| Shared (sequential only) | `models/`, `schemas/`, `CLAUDE.md`, `.claude/` | Lead agent |
| Infra | `docker-compose.yml`, `.github/`, `scripts/` | Lead agent |

## Правила координации
- **Alembic миграции** — ТОЛЬКО lead agent, последовательно
- **Один файл = один агент** — никогда двое в одном файле
- **Backend ‖ Frontend** — параллельно (0 пересечений)
- **Model → Migration → Schema** — последовательно (lead), потом параллелятся
- **`cache.py`** — только lead agent, ПОСЛЕ завершения backend teammate

## Constraints для параллельных teammate (обязательно в промпте)
Вставлять **каждому** teammate при `isolation: worktree`. Прецедент: `feedback_worktree_isolation.md` (2026-04-20 — 30 мин на merge-конфликты).

### 1. Relative paths only
> Ты работаешь в git worktree. **ВСЕ** пути — относительные, от текущей рабочей директории. **НИКАКИХ** `/Users/a1/Desktop/dds_app/...` в Read/Write/Edit/Bash — иначе правки уйдут в main dir вместо worktree, создадут add/add конфликты при merge.

### 2. Types-first для тестов
> Если меняешь API — сначала обнови `frontend-react/src/types/api.ts` и `frontend-react/src/lib/api/<domain>.ts`, **потом** пиши/обновляй тесты.

### 3. Git-status-check в конце
> Перед завершением работы выполни `git status` и `git diff --stat` в своей рабочей директории. В отчёте main-агенту перечисли: (a) изменённые файлы с путями, (b) подтверждение что все пути — внутри worktree, (c) `git log -1 --oneline` если коммитил.

### 4. Long-running → run_in_background
> Если ожидаемое время выполнения >5 мин — `run_in_background: true`. Иначе пользователь думает что Claude завис.

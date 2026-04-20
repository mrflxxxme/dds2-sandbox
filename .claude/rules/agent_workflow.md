---
paths:
  - "**/*"
---
# Agent Workflow — DDS2

## Agent TDD — процесс разработки
**Подробно:** `docs/AGENT_DEVELOPMENT.md`

### Фичи и кросс-доменные изменения (полный цикл)
```
Фаза 0:   Lead (opus): EnterPlanMode → уточняет → ТЗ → ExitPlanMode (только при approval)
Фаза 1:   Lead (sonnet): Model→Migration→Schema + Pre-warm frontend (haiku, read-only)
Фаза 2:   2-3 агента (sonnet) параллельно: Backend[-A, -B] ‖ Frontend
Фаза 2.5: code-reviewer ‖ security-reviewer (sonnet, read-only)
Фаза 3:   pytest ‖ vitest ‖ conventions ‖ docs (haiku, параллельно) → коммит
```
- **Plan Mode (Фаза 0)** — для фич/рефакторинга lead вызывает `EnterPlanMode` ПЕРЕД любыми правками. Без `ExitPlanMode` (approval пользователя) код не пишется. Это решает проблему «агент сразу кодит» из `feedback_ask_questions`
- Backend и Frontend — 0 пересечений файлов, всегда параллелятся
- Alembic миграции — ТОЛЬКО последовательно (см. `/migration` skill)
- Один файл — ТОЛЬКО один агент

### Доменные skills (быстрые шаблоны)
| Skill | Когда |
|-------|-------|
| `/new-endpoint` | новый API endpoint (schema → service → router → test) |
| `/migration` | Alembic миграция (heads → revision → upgrade/downgrade) |
| `/new-page` | Next.js страница (types → api → page с loading/error/empty) |
| `/plan` | альтернатива встроенному Plan Mode для backend-only задач |

### Баги и мелкие изменения
Без ТЗ — сразу анализ → фикс → тесты → коммит

## Model Routing (экономия токенов)
| Задача | Модель |
|--------|--------|
| Lead + планирование | opus |
| Реализация (Фаза 2) | sonnet |
| Review (Фаза 2.5) | sonnet |
| Pre-warm, валидация, docs | haiku |

## File Ownership Rules (0 конфликтов)
| Зона | Файлы | Владелец |
|------|-------|----------|
| Backend | `backend/`, `migrations/`, `docker/`, `tests/` | Backend teammate |
| Frontend | `src/`, `frontend-react/`, `next.config.*` | Frontend teammate |
| Shared (sequential only) | `models/`, `schemas/`, `CLAUDE.md`, `.claude/` | Lead agent |
| Infra | `docker-compose.yml`, `.github/`, `scripts/` | Lead agent |

## Правила координации
- **Alembic миграции** — ТОЛЬКО lead agent, последовательно
- **Один файл = один агент** — никогда двое в одном файле
- **Backend ‖ Frontend** — всегда параллельно (0 пересечений)
- **Model → Migration → Schema** — последовательно (lead), потом параллелятся
- **Рефакторинг** — один teammate пишет тесты, другой рефакторит (разные файлы)
- **cache.py** — только lead agent, ПОСЛЕ завершения backend teammates

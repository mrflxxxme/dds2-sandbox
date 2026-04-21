---
description: "Spec-driven development для крупных фич DDS2: спецификация → план → код. Снижает регрессии с 6% до 2%."
---

# /spec — Spec-Driven Development

Метод TDAD (Test-Driven Agentic Development): спецификация ДО кода. Используется для фич длиннее 1 дня.

## Когда использовать
- **ДА**: новый модуль, крупная фича (>3 файлов, >200 строк), архитектурное изменение
- **НЕТ**: баг-фикс, мелкий endpoint, косметика → для них `/new-endpoint`, `/plan`, просто код

**Выгода** (arXiv 2603.17973): regression rate **6.08% → 1.82%** (3.3× меньше багов) за счёт упреждающего контракта.

## Процесс (4 артефакта в `docs/specs/<feature>/`)

### Артефакт 1: `requirements.md` (что и зачем)
```markdown
# <Фича>: Requirements

## User stories
- Как <роль>, я хочу <действие>, чтобы <цель>

## Success criteria (измеримые)
- Endpoint отвечает <200 ms p95
- Покрытие тестами >80%
- 0 regression в существующих 1184 тестах

## Out of scope
- Что НЕ делаем (избежать scope creep)

## Constraints
- Связь с iron rules DDS (project_id, soft_delete, Numeric)
- Совместимость с /api/v1 (breaking? → нужен /v2)
```

### Артефакт 2: `design.md` (как)
```markdown
# <Фича>: Design

## Data model
- Новые таблицы + колонки с типами
- Индексы (какие запросы ускоряют)
- FK, constraints
- Migration steps (upgrade + downgrade)

## API contract
- Endpoints: URL, method, request/response schemas (Pydantic pseudo-code)
- Error cases: 400/404/409/422 с примерами
- Rate limits

## Service layer
- Методы класса `<Feature>Service` с сигнатурами
- Dependencies (другие сервисы, внешние API)
- Кэш: что кэшируем, TTL, что инвалидируем

## Frontend
- Страницы/компоненты
- State management
- Loading/error/empty states
- API calls через api.ts
```

### Артефакт 3: `tasks.md` (план)
```markdown
# <Фича>: Tasks

## Phase 1: Foundation (lead, sequential)
- [ ] Model `<Name>` в `backend/models/`
- [ ] Migration: `alembic revision -m "..."`
- [ ] Schema в `backend/schemas/`

## Phase 2: Parallel (2 teammates)
### Backend (agent A, isolation: worktree)
- [ ] Service в `backend/services/`
- [ ] Router в `backend/routers/`
- [ ] Tests в `tests/`

### Frontend (agent B, isolation: worktree)
- [ ] Type в `types/api.ts`
- [ ] API method в `lib/api/<domain>.ts`
- [ ] Page `src/app/(main)/p/[slug]/<name>/`
- [ ] Vitest в `<page>.test.tsx`

## Phase 3: Verify (parallel opus agents)
- [ ] pytest
- [ ] vitest
- [ ] check_conventions
- [ ] docs update

## Phase 4: Review (parallel opus agents)
- [ ] code-reviewer
- [ ] security-reviewer
- [ ] api-designer (если API меняется)
- [ ] performance-optimizer
```

### Артефакт 4: `constitution.md` (иммутабельные принципы — на проект, не на фичу)
Лежит в `docs/specs/constitution.md`, пишется ОДИН раз. Все specs ссылаются.
```markdown
# DDS Constitution

## Неизменные правила
1. Multi-tenancy через project_id — КАЖДЫЙ запрос
2. Soft-delete, не hard
3. Numeric(18,2) для денег
4. Timezone-aware datetime
5. invalidate_cache после мутации
6. Services владеют логикой, routers — HTTP only
7. Pydantic на входе, serializers на выходе
8. Migrations обратимые
```
Уже есть CLAUDE.md + `.claude/rules/` — constitution.md это их выжимка для контекста spec.

## Workflow

1. Пользователь говорит «фича X»
2. `/spec X` → создаёт папку `docs/specs/X/` + скелеты 3 файлов (requirements, design, tasks)
3. Lead агент заполняет requirements.md → **ЖДЁТ подтверждения**
4. Lead заполняет design.md → **ЖДЁТ подтверждения**
5. Lead заполняет tasks.md
6. Выполняем по tasks.md → Phase 1 (lead) → Phase 2 (Team parallel) → Phase 3/4
7. После merge → `/learn` → фиксация уроков в `learnings.md`

## Отличие от `/plan`
- `/plan` — один документ, короткий, для задач 1-3 часа
- `/spec` — 3 артефакта + constitution, для фич >1 дня
- `/plan` — план действий; `/spec` — контракт

## Критерии качества spec
- Requirements → каждый success criterion измерим
- Design → есть точный API contract (схемы, статусы)
- Tasks → каждая задача в <1 файл, понятно кому (lead/backend/frontend)
- Нет «пудинга» — только то что напишется в коде

## НЕ делать
- Spec для багфикса (oversized)
- Spec для вещи которую ты не понял сам → уточни у пользователя ПЕРЕД spec
- Писать spec и сразу код — spec должен быть approved
- Больше 3 артефактов на фичу — всё остальное в код

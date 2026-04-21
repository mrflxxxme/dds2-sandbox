# AI Workflow — DDS2 (шпаргалка)

Как устроена AI-разработка после прокачки апрель 2026. Короткий практический гайд.

## Типичные сценарии

| Ситуация | Что делать | Что запустится |
|----------|------------|----------------|
| Новая фича (1-3 часа) | `сделай X` | Я уточню ТЗ → `/plan` → код → `code-reviewer` + `security-reviewer` (opus) |
| Крупная фича (>1 день) | `/spec <название>` | 3 артефакта в `docs/specs/<name>/` → approval → phased implementation |
| Backend + Frontend вместе | `сделай X` | АВТО `TeamCreate` → параллельные teammates в isolated worktrees |
| Баг-фикс | `фикс X` | Сразу анализ → фикс → тесты → коммит |
| Прод упал | `/hotfix` | SSH-диагностика → минимальный фикс → PR в `main` |
| Откатить деплой | `/rollback` | `git revert` → `cd-production` задеплоит обратно |
| Переименовать в 10+ файлах | `/codemod` | AST-grep dry-run → approval → замена → тесты |
| Новый endpoint | `/new-endpoint` | schema → service → router → test |
| Новая страница | `/new-page` | types → api.ts → page (loading/error/empty) |
| Миграция | `/migration` | heads check → revision → upgrade/downgrade test |
| Перед коммитом | `/verify` / `/smoke` | тесты + конвенции + security |
| Статус | `/status` | git + docker + миграции за 5 сек |
| Сломана сборка | `/build-fix` | Monitor tool стримит pytest → фиксы по одной |
| Slow endpoint / bundle | spawn `performance-optimizer` | N+1, индексы, lazy imports |
| Breaking API change? | spawn `api-designer` | REST consistency + breaking detection |

## Что работает САМО (без команд)

1. **Каждое сообщение** → `prompt-team-detect.sh` → решает: обычный ответ / TeamCreate / /plan
2. **Каждый Edit/Write** → `post_edit_check.py` → проверка конвенций DDS (project_id, soft_delete, Numeric, no f-string в SQL)
3. **Каждый Bash** → `pre_tool_check.sh` → блок доступа к `.env` / credentials
4. **Мой коммит** → `post-commit-track.sh` → авто `/learn` → обновляю `learnings.md` + `memory/`
5. **`git push`** → pre-push: backend testmon + vitest + conventions + **slopsquatting**
6. **Push в `dev`** → `auto-pr.yml` → PR → green CI → `auto-merge.yml` → `cd-production.yml`
7. **Любой PR** → `claude-review.yml` (opus-4-7 если label `security`/`high-risk`, иначе sonnet)
8. **Упал Tests/Security** → `ci-failure-issue.yml` → auto-issue → auto-close при green
9. **Cron weekly**: todo-sentinel, known-bugs-sentinel, weekly-retrospective

## Subagents (8 штук)

| Агент | Модель | Триггер |
|-------|--------|---------|
| `code-reviewer` | opus | после правок кода |
| `security-reviewer` | opus | auth / SQL / crypto / user-input |
| `planner` | opus | планирование фичи/рефакторинга |
| `performance-optimizer` | sonnet | новые endpoint / slow queries / bundle |
| `api-designer` | sonnet | изменение routers / schemas |
| `database-reviewer` | sonnet | миграции, сложные SQL |
| `tdd-guide` | sonnet | новые фичи — тесты первыми |
| `build-error-resolver` | sonnet | pytest/build fail |

## Slash skills (18 штук)

- **Разработка**: `/new-endpoint`, `/new-page`, `/migration`, `/tdd`, `/plan`
- **Крупные фичи**: `/spec` (spec-driven, regressions 6% → 2%)
- **Рефакторинг**: `/codemod` (AST-grep + LLM для 10+ файлов)
- **Emergency**: `/hotfix`, `/rollback`
- **Проверки**: `/smoke`, `/verify`, `/review`, `/status`, `/build-fix`
- **Рефлексия**: `/learn` (авто-после-коммита), `/docs`, `/pause`, `/resume`

## Model routing (экономия ~65%)

| Задача | Модель | Почему |
|--------|--------|--------|
| Code/security review, planning | **opus-4-7** | +13% accuracy (SWE-bench), ловит CRITICAL/HIGH |
| Реализация, рефакторинг, тесты | **sonnet-4-6** | default, достаточно |
| Валидация, docs, smoke | **haiku-4-5** | дёшево + быстро |

## Защита от AI-ошибок

- **Slopsquatting** ([`scripts/hooks/check_slopsquatting.sh`](../scripts/hooks/check_slopsquatting.sh)) — проверяет новые imports против PyPI/npm. Ловит AI-галлюцинации пакетов (21.7% open-source LLM галлюцинируют имена — реальный supply chain risk)
- **Железные правила** ([`.claude/rules/`](../.claude/rules/)) — 10 файлов с path-filtered правилами (python/postgres/security/testing/frontend/migrations)
- **Pre-commit**: Ruff + Bandit + Gitleaks
- **Pre-push**: backend tests + vitest + conventions + slopsquatting
- **Iron rules enforcement**: `post_edit_check.py` после каждого Edit/Write

## Память (самообучение)

После каждого моего коммита:
1. Читаю `.claude/.pending-learn.log` → беру только свой коммит
2. Извлекаю 4 категории уроков: антипаттерн / паттерн / исправленный known_bug / feedback
3. Пишу в [`learnings.md`](../.claude/rules/learnings.md), [`project_known_bugs.md`](../memory/project_known_bugs.md), [`templates/`](../.claude/templates/)
4. Отдельный коммит `chore(memory): [auto-learn] ...` (tag защищает от рекурсии)

## Workflow для пользователя

**Продолжай работать как раньше** — всё подключено и срабатывает само. Явно вызывать новые skills только в трёх кейсах:

1. `/hotfix` / `/rollback` — когда прод лёг
2. `/spec` — для фич длиннее 1 дня
3. `/codemod` — для массовых замен

Остальные agents (`performance-optimizer`, `api-designer`) я вызову сам когда замечу что задача в их профиль.

## CI автопайп (push → прод)

```
git push origin dev
    ↓ pre-push: tests + vitest + conventions + slopsquatting
    ↓
auto-pr.yml → PR dev → main
    ↓ claude-review.yml (opus для security, sonnet иначе)
    ↓ Tests + Security Audit + Conventions
    ↓ green CI
auto-merge.yml → squash merge в main
    ↓
cd-production.yml → деплой на app.vyatkin-wb.ru
    ↓
post-merge.yml → healthcheck → GH issue при fail
```

Время: push → прод ~5-8 минут при зелёных тестах.

## Связанные документы

- [`CLAUDE.md`](../CLAUDE.md) — главная точка входа, iron rules, архитектура
- [`docs/AGENT_DEVELOPMENT.md`](AGENT_DEVELOPMENT.md) — детали TDD + Teams workflow
- [`.claude/rules/agent_workflow.md`](../.claude/rules/agent_workflow.md) — file ownership, model routing
- [`.claude/rules/learnings.md`](../.claude/rules/learnings.md) — накопленные паттерны и антипаттерны
- [`memory/MEMORY.md`](/Users/a1/.claude/projects/-Users-a1-Desktop-dds-app/memory/MEMORY.md) — user feedback, project state, references
- [`REVIEW.md`](../REVIEW.md) — чеклист code review

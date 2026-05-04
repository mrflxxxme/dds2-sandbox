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
7. **Любой PR** → `claude-review.yml` (opus-4-7 везде; `security`/`high-risk` лейблы → 25 turn-ов, иначе 20)
8. **Упал Tests/Security** → `ci-failure-issue.yml` → auto-issue → auto-close при green
9. **Cron weekly**: todo-sentinel, known-bugs-sentinel, weekly-retrospective

## Subagents (8 штук)

**Модель: все на `opus` 4.7** (Max подписка, требование владельца 2026-04-21). Зафиксировано через `CLAUDE_CODE_SUBAGENT_MODEL=claude-opus-4-7` в `.claude/settings.json`.

| Агент | Триггер |
|-------|---------|
| `code-reviewer` | после правок кода |
| `security-reviewer` | auth / SQL / crypto / user-input |
| `planner` | планирование фичи/рефакторинга |
| `performance-optimizer` | новые endpoint / slow queries / bundle |
| `api-designer` | изменение routers / schemas |
| `database-reviewer` | миграции, сложные SQL |
| `tdd-guide` | новые фичи — тесты первыми |
| `build-error-resolver` | pytest/build fail |

## Slash skills

- **Разработка**: `/new-endpoint`, `/new-page`, `/migration`, `/tdd`, `/plan`
- **Крупные фичи локально**: `/spec` (2-5 файлов, regressions 6% → 2%)
- **Крупные фичи облако ☁️**: `/ultraplan` (cross-domain рефакторы 3+ DOMAIN, миграция auth, новый домен, большая интеграция). См. `.claude/rules/lead_agent_v2.md` §5
- **Рефакторинг**: `/codemod` (AST-grep + LLM для 10+ файлов)
- **Emergency**: `/hotfix`, `/rollback`
- **Проверки**: `/smoke`, `/verify`, `/review`, `/status`, `/build-fix`
- **Ревью больших PR облако ☁️**: `/ultrareview` (PR > 500 LOC, миграции БД, security-sensitive, money-handling). До 5 мая 2026 — 3 бесплатных запуска
- **Рефлексия**: `/learn` (авто-после-коммита), `/docs`, `/pause`, `/resume`

## Model policy

**Всё на Opus 4.7** (Max подписка, cost = 0). Решение 2026-04-21 — важнее качество рассуждений, чем экономия токенов.

| Где | Модель |
|-----|--------|
| Subagents (все 8) | `opus` через frontmatter + `CLAUDE_CODE_SUBAGENT_MODEL=claude-opus-4-7` |
| `claude-review.yml` CI | `claude-opus-4-7` (security/high-risk лейблы → 25 turn, иначе 20) |
| `auto-docs-learn.yml` | `claude-opus-4-7` |
| Prompt cache | `ENABLE_PROMPT_CACHING_1H=1` — TTL 1 час на стабильный контекст |

Старая policy (opus для ревью, sonnet для реализации, haiku для docs) отменена — см. [`memory/feedback_model_always_opus.md`](/Users/a1/.claude/projects/-Users-a1-Desktop-dds-app/memory/feedback_model_always_opus.md).

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

**Продолжай работать как раньше** — всё подключено и срабатывает само. Явно вызывать skills только когда нужно:

1. `/hotfix` / `/rollback` — когда прод лёг
2. `/spec` — для фич 2-5 файлов длиннее 1 дня
3. `/ultraplan` ☁️ — для cross-domain рефакторов (3+ DOMAIN), миграции auth, нового домена
4. `/ultrareview` ☁️ — для PR > 500 LOC, миграций БД, security-sensitive кода
5. `/codemod` — для массовых замен (10+ файлов)

Остальные agents (`performance-optimizer`, `api-designer`) я вызову сам когда замечу что задача в их профиль.

## CI автопайп (push → прод)

```
git push origin dev
    ↓ pre-push: tests + vitest + conventions + slopsquatting
    ↓
auto-pr.yml → PR dev → main
    ↓ claude-review.yml (opus-4-7 везде; security/high-risk → 25 turn, default 20)
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
- [`.claude/rules/lead_agent_v2.md`](../.claude/rules/lead_agent_v2.md) — **канон lead-agent v2** (роутинг, параллелизм, cloud-команды)
- [`docs/AGENT_DEVELOPMENT.md`](AGENT_DEVELOPMENT.md) — детали TDD + Teams workflow
- [`.claude/rules/agent_workflow.md`](../.claude/rules/agent_workflow.md) — file ownership, параллелизм
- [`.claude/rules/learnings.md`](../.claude/rules/learnings.md) — накопленные паттерны и антипаттерны
- [`memory/MEMORY.md`](/Users/a1/.claude/projects/-Users-a1-Desktop-dds-app/memory/MEMORY.md) — user feedback, project state, references
- [`REVIEW.md`](../REVIEW.md) — чеклист code review

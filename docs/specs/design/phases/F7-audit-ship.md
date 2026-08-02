---
phase: F7 · Аудит + ship · status: planned
tier: 5
depends_on: [F3, F4, F5, F6]
executors: [lead]
reviewers: [code-reviewer, security-reviewer, database-reviewer, frontend-reviewer, api-designer, performance-optimizer]  # полный фан-аут
donors:
  - .claude/templates/DOMAIN_template.md    # шаблон DOMAIN_DESIGN.md
  - backend/DOMAIN_INDEX.md
  - backend/MAP.md
prd_refs: [PRD v4 §4, §10, §11]
---
<!-- HEAD-SUMMARY: финальный 5-линзовый аудит всего модуля, сквозной браузерный прогон PRD §4, проверка стоячих инвариантов чартера, документация в тот же коммит (docs-syncer), /verify → /ship. Финальная подпись архитектора. -->

## Goal

Модуль готов к запуску: полный аудит без BLOCK, документация синхронна, ветка отшипана по процессу DDS2.

## In scope

Полный `/verify` · 5-линзовый `/review` по всему diff модуля · сквозной браузерный сценарий · `backend/DOMAIN_DESIGN.md` + строка в `backend/DOMAIN_INDEX.md` + навигация в `backend/MAP.md` (docs-syncer, тот же коммит) · закрывающая запись в DECISIONS-LOG · `/learn` (уроки → learnings.md) · `/ship`.

## Out of scope

Деплой (dev → CI → main — процесс CLAUDE.md, запускает человек) · новые фичи (любая находка «хорошо бы ещё» → DECISIONS-LOG со стрелкой «потом»).

## Работы: чек-лист аудита

### Стоячие инварианты чартера §6 — по каждому явный вердикт

1. project_id-фильтр: grep-обход всех запросов домена + прогон изоляционных тестов.
2. `/all-projects` — только членство (AC-2 Ф2 повторно, на свежих данных).
3. Терминальность ACCEPTED/CANCELLED и append-only журнала (попытка перехода из терминала → 400; нет UPDATE/DELETE по events в коде).
4. Отсутствие pg-enum, `datetime.utcnow`, f-string-SQL, `Float`-денег (в модуле денег нет), `.all()` без limit — `check_conventions.sh` + ручной grep.
5. rate_limit_write + require_role на каждой write-ручке (AC-7 Ф2 повторно).
6. Файлы: пути `design/...`, validate_file_content, отдача с project_id-проверкой.
7. Best-effort уведомлений и CancelledError в джобе.
8. permissions только с бэка (grep фронта на самодельные проверки ролей).

### Сквозной сценарий (webapp-testing, чистый проект)

Создание с подсказкой товара → красная метка → назначение → в работу (dnd) → сдача 2 файлов → возврат с причиной → пересдача → приёмка → кнопка «Проверить тестом» → тест создан → журнал полный. Плюс: отложить/вернуть, отменить с причиной, календарь показывает срок, загрузка счёт верный, сводка (ручной прогон джобы) приходит в мок-чат.

### Гейты

`make test` целиком · `make lint` · `make typecheck` · `cd frontend-react && npx tsc --noEmit && npx vitest run` · `bash scripts/check_conventions.sh` · `alembic heads` — одна голова.

## AC

- **AC-1:** полный `make test` зелёный, 0 skip по домену design.
- **AC-2:** 5-линзовый `/review` — итоговый вердикт APPROVE (WARNING допустим только с диспозицией «deferred → потом» в DECISIONS-LOG; BLOCK — фаза не закрыта).
- **AC-3:** сквозной сценарий пройден, скриншоты ключевых шагов приложены к evidence.
- **AC-4:** `DOMAIN_DESIGN.md` создан, `DOMAIN_INDEX.md`/`MAP.md` дополнены — в том же коммите, что финальные правки.
- **AC-5:** DoD PRD выполнен: путь NEW→ASSIGNED→IN_PROGRESS→REVIEW→REVISION→REVIEW→ACCEPTED проходится в UI; пользователь без ключа не видит раздел и получает 403; уведомления приходят по 4 событиям.

## Exit-gate

| Критерий | Порог | Evidence |
|---|---|---|
| Все гейты | зелёные | транскрипты test-runner |
| Аудит T5 | APPROVE | сводный вердикт /review |
| Сценарий | AC-3 | транскрипт + скриншоты |
| Документация | AC-4 | коммит |
| **Подпись архитектора** | финальная, перед /ship | STATUS.md |

## Hints

- Находки аудита чинить fix-циклом ≤1 (WIZOR-правило): fix → re-verify → повторный прогон только затронутых линз; дальше — эскалация.
- `/learn` после ship: грабли фаз (из DECISIONS-LOG) → `.claude/rules/learnings.md`.
- После ship из worktree — `git fetch && git merge origin/dev` в основном дереве (правило CLAUDE.md).

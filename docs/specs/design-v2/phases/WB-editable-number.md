---
phase: WB · Редактируемый номер заявки · status: planned
tier: 3
depends_on: []
executors: [lead]
reviewers: [database-reviewer, api-designer, code-reviewer]
donors:
  - backend/services/design/crud.py            # next_number, update_task
  - backend/models/design.py                   # number, uq_design_tasks_project_number
  - backend/services/design/permissions.py     # 15 флагов, куда добавляется can_edit_number
  - migrations/versions/dsn03_design_fk_indexes.py   # образец аддитивной миграции модуля
prd_refs: [CHARTER-V2 Р18]
---
<!-- HEAD-SUMMARY: lead получает право переименовать номер заявки свободным текстом; автогенерация DES-N остаётся дефолтом; уникальность в проекте сохраняется, смена пишется в журнал. Миграция расширяет колонку до String(40). -->

## Goal

Ведущий дизайнер может задать заявке свой номер (например, внешний номер из таблицы заказчика), не ломая ни автонумерацию, ни журнал.

## In scope

`models/design.py` (длина колонки) · миграция `dsn04_design_number_len.py` ·
`schemas/design.py` (поле `number` во входной схеме апдейта + флаг прав) ·
`services/design/crud.py` (валидация и запись), `permissions.py` (`can_edit_number`) ·
`routers/design_tasks.py` (без новой ручки — через существующий `PUT /{id}`) ·
`EditTaskModal.tsx` (поле) · тесты · амендмент CONTRACT.md.

## Out of scope

Массовая перенумерация → потом · смена формата автогенерации (`DES-N` остаётся) ·
редактирование номера автором заявки (Р18: только lead).

## Работы

### Модель и миграция

`DesignTask.number`: `String(20)` → `String(40)` (внешние номера длиннее шаблонного `DES-N`).
Миграция `dsn04_design_number_len.py`, `down_revision` — **голова origin/dev на момент старта волны**
(не локальный хвост; текущая голова цепочки модуля — `dsnmrg_merge_design_dev_heads`, свериться
`alembic heads` перед написанием). Индекс `uq_design_tasks_project_number` не пересоздаётся
(изменение длины `varchar` не требует).

### Сервис

В `crud.update_task` — ветка смены номера:
1. право: только `is_lead` (иначе `PermissionError` → 403);
2. нормализация: `strip()`, схлопывание пробелов; пусто → `ValueError("Номер не может быть пустым")`;
3. длина ≤40 → `ValueError("Номер длиннее 40 символов")`;
4. уникальность в проекте среди живых: конфликт → `ValueError("Номер уже занят")` (400).
   Проверка + запись под тем же `pg_advisory_xact_lock(_NUMBER_LOCK_CLASS, project_id)`,
   что и `next_number`, — иначе гонка двух переименований обходит проверку и падает `IntegrityError`;
5. `DesignTaskEvent` со старым и новым значением (`comment = "Номер изменён: DES-7 → ABC-123"`),
   `old_status == new_status` — паттерн «не-переход», как смена исполнителя.

`next_number` **не менять**: он по-прежнему считает `max+1` включая soft-deleted, и по-прежнему
парсит только `DES-N`-строки — произвольные номера в расчёт максимума не попадают (это осознанно:
автонумерация живёт своей линейкой; закрепить тестом).

### Права и схемы

`permissions.compute_permissions` → новый флаг `can_edit_number = is_lead and not terminal`;
`DesignTaskPermissions` зеркалит ключ (паритет закреплён существующим
`test_permissions_schema_matches_service` — он упадёт, если забыть).
`DesignTaskUpdate` получает `number: str | None = None`.

### API

Новых ручек нет: номер идёт в существующий `PUT /{id}` (PATCH-семантика).
`CONTRACT.md` — аддитивный амендмент в раздел «V2 additions»: поле входа, новые тексты ошибок
(тексты гвардов — часть контракта!), новый флаг в `permissions`.

### Фронт

`EditTaskModal.tsx`: поле «Номер» показывается только при `permissions.can_edit_number`,
предзаполнено текущим, хелпер-текст «Уникален в проекте». Ошибку 400 показывать текстом бэка.
Номер отображается в деталке/списке/карточке как есть (уже так).

## AC

- **AC-1:** `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` — чисто, одна голова.
- **AC-2:** lead переименовывает номер → 200, в списке и деталке новый номер, в журнале запись со старым и новым.
- **AC-3:** editor-автор и editor-исполнитель получают 403 при попытке сменить номер (тест на оба).
- **AC-4:** Занятый номер живой задачи → 400 «Номер уже занят»; номер soft-deleted задачи — **разрешён** (partial-unique мёртвых не видит).
- **AC-5:** Две параллельные смены на один и тот же номер: одна 200, вторая 400 (не `IntegrityError`/500) — тест на advisory-lock.
- **AC-6:** После переименования `DES-7 → ABC-1` создание новой заявки даёт `DES-8` (автонумерация не сбита).
- **AC-7:** `mypy` по дизайн-скоупу чист; `check_conventions.sh` PASSED; `tsc`+`vitest` зелёные.

## Exit-gate

| Критерий | Порог | Evidence |
|---|---|---|
| Миграция | AC-1 | транскрипт alembic |
| Сервис/API-тесты | AC-2..AC-6 зелёные | транскрипт pytest |
| Статика | AC-7 | транскрипты mypy/conventions/tsc/vitest |
| Ревью | database-reviewer + api-designer + code-reviewer без BLOCK | вердикты |
| Подпись | tripwire-пути (`models/`, `migrations/`, `schemas/`) — правит lead, подпись архитектора ДО мержа | STATUS-строка |

## Hints

- Мина `SoftDeleteMixin` + `UniqueConstraint` уже обойдена partial-индексом — не «чинить» его добавлением `is_deleted` в `next_number` (v1 DOMAIN_DESIGN «Ловушки»).
- Advisory-локи модуля: `0x00DE516` — нумерация, `0x00DE517` — move. Переименование берёт **первый**, новый класс не заводить.
- `ValueError` → 400, кроме текстов из `_NOT_FOUND_TEXTS` → 404. Новые тексты в этот frozenset не добавлять.
- Все write-ручки — `Depends(rate_limit_write)` (уже есть на `PUT /{id}`).

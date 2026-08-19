---
phase: WB · Редактируемый номер заявки · status: planned
tier: 3
depends_on: []
executors: [lead]
reviewers: [database-reviewer, api-designer, code-reviewer]
donors:
  - backend/services/design/crud.py            # next_number (advisory-lock 0x00DE516), update_task
  - backend/models/design.py                   # number String(20), uq_design_tasks_project_number
  - backend/services/design/permissions.py     # 16 флагов, куда добавляется can_edit_number
  - migrations/versions/dsn03_design_fk_indexes.py   # образец аддитивной миграции модуля
prd_refs: [CHARTER-V2 Р18; CONTRACT-V2 §2]
---
<!-- HEAD-SUMMARY: lead переименовывает заявку свободным текстом (буквы/цифры/._-, до 40); автогенерация DES-N остаётся дефолтом; уникальность в проекте держится под тем же advisory-локом, что и нумерация; смена пишется в журнал. -->

## Goal

Ведущий дизайнер задаёт заявке свой номер (например, внешний номер из таблицы заказчика), не ломая автонумерацию и журнал.

## In scope

`models/design.py` (длина колонки) · миграция `dsn04_design_number_len.py` ·
`schemas/design.py` (`DesignTaskUpdate.number`, флаг `can_edit_number`) ·
`services/design/crud.py`, `services/design/permissions.py` ·
`EditTaskModal.tsx` (поле) · тесты · CONTRACT-V2 §2 уже описывает контракт.

## Out of scope

Массовая перенумерация → потом · смена формата автогенерации (`DES-N` остаётся) ·
редактирование номера автором заявки (Р18: только lead) · номера с пробелами и юникод-символами
вне разрешённого класса (CONTRACT-V2 §2).

## Работы

### Модель и миграция

`DesignTask.number`: `String(20)` → `String(40)`.
Миграция `dsn04_design_number_len.py`; `down_revision` — **фактическая голова на момент старта волны**
(проверить `docker compose exec backend alembic heads`). На 2026-08-19 голова одна, её
**revision id — `dsnmrg_design_dev`** (файл называется иначе, `dsnmrg_merge_design_dev_heads.py`:
в `down_revision` пишется id, не имя файла). Ревизии `dsn04`/`dsn05`/`dsn06` свободны.
Индекс `uq_design_tasks_project_number` не пересоздаётся — изменение длины `varchar` этого не требует.
`downgrade` — обратный `alter_column` на `String(20)`; в нём **не** усекать данные (если длинные номера
уже есть, откат упадёт — это честнее тихой потери; отметить комментарием в миграции).

### Сервис

Ветка смены номера в `crud.update_task`. Порядок проверок и тексты — **дословно CONTRACT-V2 §2**:
право lead → пусто → длина → регекс `^[A-Za-zА-Яа-яЁё0-9._-]+$` → занятость.

Критично: проверка занятости и запись идут под тем же
`pg_advisory_xact_lock(_NUMBER_LOCK_CLASS = 0x00DE516, project_id)`, что и `next_number` —
иначе две параллельные смены на одно значение проскакивают проверку и падают `IntegrityError` (500)
вместо честного 400. Новый класс лока не заводить.

Событие в `DesignTaskEvent`: `old_status == new_status` (паттерн «не-переход», как смена исполнителя),
`comment = f"Номер изменён: {old} → {new}"`.

`next_number` **не менять**: он по-прежнему берёт `max+1` включая soft-deleted и парсит только
`DES-N`-строки, поэтому произвольные номера в расчёт максимума не попадают. Это осознанно —
автонумерация живёт своей линейкой; закрепить тестом AC-6.

### Права и схемы

`permissions.compute_permissions` → `can_edit_number = is_lead and status not in (ACCEPTED, CANCELLED)`.
`DesignTaskPermissions` зеркалит ключ — иначе упадёт существующий `test_permissions_schema_matches_service`.
`DesignTaskUpdate` получает `number: str | None = None`.

### Фронт

`EditTaskModal.tsx`: поле «Номер» рендерится только при `permissions.can_edit_number`,
предзаполнено текущим, хелпер «Уникален в проекте, до 40 символов».
Ошибку 400 показывать **текстом бэка**, своих сообщений не придумывать.

## AC

- **AC-1:** `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` — чисто, `alembic heads` даёт одну голову.
- **AC-2:** lead меняет номер → 200; новый номер виден в списке, на карточке доски и в деталке; в журнале запись `«Номер изменён: DES-7 → ABC-123»`.
- **AC-3:** editor-автор и editor-исполнитель получают 403 с текстом контракта (тест на обоих); `can_edit_number` у них `false`.
- **AC-4:** Все 400-гварды CONTRACT-V2 §2 воспроизводятся дословно — 4 текста на 5 кейсах: пусто · >40 символов · `«ABC 123»` (пробел) и `«ABC/123»` (слеш) — оба дают текст про регекс · занятый номер. Два 403-текста проверяются в AC-3 и AC-8.
- **AC-5:** Две параллельные смены на один номер: одна 200, вторая 400 «Номер уже занят» (не 500/`IntegrityError`).
- **AC-6:** Автонумерация не сбита при переименовании **не-максимального** номера: при `DES-1..DES-7` смена `DES-3 → ABC-1` оставляет следующей заявке `DES-8`. ⚠️ Для **максимального** номера обещание не держится и держаться не может: `next_number` берёт `max` по строкам, подходящим под `^DES-\d+$`, а переименованная под шаблон не подходит — `DES-7 → ABC-1` освобождает `DES-7`, и следующая заявка получит именно его. Уникальность не нарушается. Монотонный счётчик = персистентное состояние на проект = миграция, которую эта волна не делает (см. DECISIONS-LOG 2026-08-20 «автонумерация»).
- **AC-7:** Номер soft-deleted задачи можно занять повторно (partial-unique мёртвых строк не видит).
- **AC-8:** Номер в терминальном статусе не меняется: `can_edit_number = false`, попытка → 403.
- **AC-9:** `mypy` по дизайн-скоупу, `check_conventions.sh`, `tsc`, `vitest` — чисто.

## Exit-gate

| Критерий | Порог | Evidence |
|---|---|---|
| Миграция | AC-1 | транскрипт alembic |
| Сервис/API-тесты | AC-2..AC-8 зелёные | транскрипт pytest |
| Статика | AC-9 | транскрипты mypy/conventions/tsc/vitest |
| Ревью | database-reviewer + api-designer + code-reviewer без BLOCK | 3 вердикта |
| Подпись | tripwire (`models/`, `migrations/`, `schemas/`) — подпись архитектора ДО мержа | строка в STATUS |

## Hints

- Мина `SoftDeleteMixin` + `UniqueConstraint` уже обойдена partial-индексом `WHERE is_deleted = false` — не «чинить» её добавлением фильтра в `next_number` (v1 DOMAIN_DESIGN, «Ловушки»).
- `ValueError` → 400, кроме текстов из `_NOT_FOUND_TEXTS` → 404. Новые тексты волны B в этот frozenset **не** добавлять.
- Регекс держать в одном месте (константа рядом с валидацией), не дублировать на фронте: фронт полагается на 400 бэка.
- `PUT /{id}` уже под `Depends(rate_limit_write)` — новой ручки нет.

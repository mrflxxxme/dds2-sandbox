---
phase: F1 · Сервис-ядро · status: planned
tier: 3
depends_on: [F0]
executors: [lead]            # допустим tm-backend, но мержит lead (services — не tripwire, однако до фриза контракта фан-аут не открыт)
reviewers: [code-reviewer, security-reviewer, database-reviewer]
donors:
  - backend/services/payment_request_documents.py   # upload_document :40 — эталон файлов
  - backend/utils/file_validation.py                # validate_file_content
  - backend/storage.py                              # get_minio/upload_file/download_file
  - backend/models/cost.py                          # Nomenclature (article_wb, article_seller)
  - tests/test_payment_request_service.py           # образец сервисных тестов с изоляцией
prd_refs: [PRD v4 §4 (путь задачи, гварды), §7 (матрица прав), §10 (метрики)]
---
<!-- HEAD-SUMMARY: пакет backend/services/design/ — state (переходы+гварды под FOR UPDATE), permissions (матрица PRD §7 на member_role), crud, board (move+sort_order), files (материалы/версии/вердикт через MinIO), workload, stats. TDD: тесты первыми. -->

## Goal

Вся бизнес-логика модуля в `services/` (правило 8 CLAUDE.md), покрытая unit/service-тестами до появления роутера.

## In scope

Пакет `backend/services/design/`: `__init__.py`, `state.py`, `permissions.py`, `crud.py`, `board.py`, `files.py`, `workload.py`, `stats.py` + тесты `tests/test_design_state.py`, `tests/test_design_service.py`, `tests/test_design_permissions.py`.

## Out of scope

HTTP-слой (→ Ф2) · уведомления (→ Ф4: сервис зовёт `notify_*`-хуки, в Ф1 — no-op заглушки) · АБ-мост (→ Ф6).

## Работы

### `state.py`

```python
def validate_transition(current: str, target: str) -> None  # ValueError с понятным текстом
async def change_status(db, project_id, task_id, target, user, member_role,
                        comment: str | None = None) -> DesignTask
```

`change_status` под `SELECT ... FOR UPDATE`: перечитать → `validate_transition` → проверка права на переход (матрица ниже) → гварды → побочные эффекты → `DesignTaskEvent` → return. Два параллельных перехода не проходят оба (тест).

**Матрица «кто двигает»** (`is_lead = member_role in ("owner","admin")`, `is_author`, `is_assignee`):

| Переход | Кто | Гвард | Текст ошибки |
|---|---|---|---|
| NEW→ASSIGNED | lead | `assignee_user_id` установлен (через `assign`) | «Назначьте исполнителя» |
| ASSIGNED→NEW | lead | — (снимает исполнителя) | |
| ASSIGNED→IN_PROGRESS | assignee, lead | | |
| IN_PROGRESS→REVIEW | assignee, lead | есть submission `verdict=PENDING` | «Сдайте версию с файлами» |
| REVIEW→REVISION | author, lead | непустой `comment` | «Напишите, что исправить» |
| REVIEW→ACCEPTED | author, lead | есть версия PENDING | «Нет версии на проверке» |
| REVISION→REVIEW | assignee, lead | новая версия PENDING | «Приложите исправленную версию» |
| *→ON_HOLD | assignee (свою), lead | пишет `held_from_status` | |
| ON_HOLD→(NEW\|ASSIGNED\|IN_PROGRESS) | lead, assignee | цель ⊆ словаря; UI предлагает `held_from_status`; чистит его | |
| *→CANCELLED | author, lead | непустой `comment` | «Укажите причину отмены» |

Побочные эффекты: первый вход в `IN_PROGRESS` → `started_at`; `ACCEPTED` → `accepted_at` + текущей PENDING-версии `verdict=ACCEPTED`; `ON_HOLD` → `held_from_status=<откуда>`; выход из `ON_HOLD` → `held_from_status=None`. После успешного commit — вызов notify-хука (Ф4, здесь no-op).

### `permissions.py`

```python
def compute_permissions(task, user, member_role) -> dict[str, bool]
```

Флаги по матрице PRD §7: `can_edit` (author до REVIEW / lead всегда, кроме терминалов), `can_assign` (lead), `can_take` (assignee: ASSIGNED→IN_PROGRESS), `can_submit` (assignee/lead в IN_PROGRESS|REVISION), `can_verdict` (author/lead в REVIEW), `can_hold`, `can_cancel` (author/lead), `can_delete` (author/lead), `can_set_complexity` (lead), `can_set_outsource` (lead), `can_reorder` (lead), `can_comment` (все с page), `can_create_ab_test` (author/lead, только ACCEPTED && nm_id). Роутер и фронт опираются ТОЛЬКО на эти флаги (инвариант §6.9).

### `crud.py`

| Функция | Поведение |
|---|---|
| `next_number(db, project_id)` | `DES-N`, max+1 под lock (Р14) |
| `create_task(...)` | Валидация Р13 в схеме; `sort_order = max(sort_order in NEW)+1000`; событие `None→NEW`; notify-хук нет (заявку лид увидит по красной метке) |
| `list_tasks(db, project_id, filters, limit=100, offset=0)` | Фильтры: `status[]` (вкл. ON_HOLD/CANCELLED), `assignee_user_id`, `author_user_id`, `work_type`, `is_urgent`, `overdue`, `q` (номер/заголовок/nm_id). Сортировка: `due_date ASC NULLS LAST, is_urgent DESC, id DESC` (в списке срочные выше при равном сроке; на ДОСКЕ порядок — только `sort_order`, Р9). Батч-обогащение имён/счётчиков одним запросом — без N+1. Обязательный `.limit()` |
| `get_task(db, project_id, task_id, user, member_role)` | Деталка со связями + `permissions` |
| `mark_viewed(db, project_id, task_id, user, member_role)` | Только lead; идемпотентно ставит `viewed_by_lead_at` (Р5) |
| `update_task(...)` | Запрет правки в REVIEW/ACCEPTED/CANCELLED для не-lead; `complexity`/`is_outsourced` — только lead |
| `assign(db, ..., assignee_user_id | None)` | Только lead; смена исполнителя без смены статуса ИЛИ вместе с NEW→ASSIGNED; событие в журнал; notify-хук «назначили» |
| `add_comment(...)` | Опциональное вложение через files-хелпер |
| `product_suggest(db, project_id, q, limit=10)` | По `Nomenclature`: `article_seller ILIKE / brand ILIKE / subject ILIKE / article_wb::text LIKE`, distinct по `article_wb`, только `project_id` |

### `board.py` (Р3, Р4)

```python
async def get_board(db, project_id, user, member_role) -> DesignBoardResponse
    # одна выборка WHERE status IN DESIGN_BOARD_STATUSES + is_deleted=false, ORDER BY status, sort_order
    # + counts по ON_HOLD/CANCELLED; limit 200/колонку
async def move_task(db, project_id, task_id, to_status, after_task_id, user, member_role)
    # под FOR UPDATE: если to_status != current → change_status (та же матрица/гварды)
    # позиция: sort_order = midpoint(after, next); зазор <1 → перенумерация колонки шагом 1000 в этой же транзакции
    # перестановка внутри колонки (to_status == current) — только lead (can_reorder)
```

### `files.py`

| Функция | Поведение |
|---|---|
| `upload_material_file(...)` | `validate_file_content` → MinIO `design/{project_id}/{task_id}/materials/{ts}_{filename}` (донор `payment_request_documents.upload_document:40`) → `DesignMaterial(kind=FILE)`. Лимит 20 МБ (`min(20, settings.MAX_UPLOAD_SIZE_MB)`); MIME: `image/*`, `application/pdf`, `application/zip`, `application/x-rar-compressed`; blocklist исполняемых. MinIO недоступен → 503 |
| `add_material_link / add_material_nm` | `kind=LINK|NM`, CheckConstraint соблюсти |
| `create_submission(db, ..., files, comment)` | `version_no = max+1` под lock; файлы → `design/{project_id}/{task_id}/v{n}/`; далее вызывающий делает `change_status(→REVIEW)` |
| `set_verdict(...)` | Только на PENDING-версии; `REJECTED` требует `verdict_comment` → `change_status(→REVISION, comment=verdict_comment)`; `ACCEPTED` → `change_status(→ACCEPTED)` |
| `download_material / download_submission_file` | Байты из MinIO с проверкой `project_id`; `Content-Disposition` с `quote(filename)` |

### `workload.py` / `stats.py`

`get_workload`: один запрос GROUP BY `assignee_user_id` по `DESIGN_ACTIVE_STATUSES`: активные, просроченные (`due_date < today`), в `REVISION`, ближайший срок.
`get_stats(from, to)` (PRD §10): доля принятых в срок (`accepted_at::date <= due_date`; задачи без due_date НЕ считаются просроченными — см. STATUS «Открытые вопросы»); среднее `versions_count` у принятых; медиана `accepted_at - created_at` в раб. днях (приближение календарными — допустимо, зафиксировать в docstring); count задач `NEW/ASSIGNED` без исполнителя > 2 дней; `is_outsourced`-доля; доля задач с `nm_id` (tracked_share).

## AC

- **AC-1:** `tests/test_design_state.py`: все разрешённые переходы проходят; по одному запрещённому на каждый статус — `ValueError`; терминалы не имеют исходящих.
- **AC-2:** гварды: IN_PROGRESS без исполнителя, REVIEW без версии, CANCELLED/REVISION без причины — каждая ошибка с заданным текстом.
- **AC-3:** матрица прав: editor-автор не может ASSIGNED→IN_PROGRESS чужой задачи; editor-исполнитель не может REVIEW→ACCEPTED; lead может всё по словарю (по кейсу на строку матрицы).
- **AC-4:** параллельное `create_task` не дублирует `number`; параллельный `create_submission` не дублирует `version_no` (два таска в gather).
- **AC-5:** `move_task` с исчерпанным зазором перенумеровывает колонку и сохраняет относительный порядок (тест: 3 вставки в один зазор).
- **AC-6:** `ON_HOLD`-цикл: IN_PROGRESS→ON_HOLD→IN_PROGRESS восстанавливает работу, `held_from_status` очищен.
- **AC-7:** изоляция: задачи проекта A не видны из проекта B ни в `list_tasks`, ни в `get_board` (фикстуры `project`/`other_project` из conftest).

## Exit-gate

| Критерий | Порог | Evidence |
|---|---|---|
| Тесты фазы | `pytest tests/test_design_state.py tests/test_design_service.py tests/test_design_permissions.py -x` зелёный | транскрипт test-runner |
| Гонки | AC-4 зелёный | транскрипт |
| Конвенции | `check_conventions.sh` + `make typecheck` чисто | транскрипт |
| Ревью T3 | без BLOCK | вердикт /review |

## Hints

- TDD: сначала `test_design_state.py` на словарь из Ф0 — он уже импортируется.
- Row-lock: `select(...).with_for_update()`; в тестах гонок — два независимых session.
- Никакого `@cached` (Р10). Никакого `except Exception` без проброса `CancelledError` (в сервисе его и не должно быть — это правило джоб Ф4).
- notify-хуки объявить в `services/design/notify.py` как no-op с TODO(F4) — чтобы Ф4 не менял сигнатуры ядра.

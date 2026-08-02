---
phase: F0 · Хребет — модели, миграции, схемы, RBAC · status: planned
tier: 3
depends_on: []
executors: [lead]
reviewers: [database-reviewer, security-reviewer, code-reviewer]
donors:
  - backend/models/payment_request.py        # статусная машина :116, журнал событий :278
  - migrations/versions/pay01_payment_request.py  # partial-unique :87
  - backend/models/cost.py                   # Nomenclature :33 (article_wb!)
  - backend/rbac.py
prd_refs: [PRD v4 §4 (путь задачи, столбцы), §5, §7 (права)]
---
<!-- HEAD-SUMMARY: 6 таблиц домена design, словарь переходов 8 статусов, миграции dsn01/dsn02, Pydantic-схемы, регистрация page-ключа design-tasks в 4 местах. Только lead. Tripwire-фаза: подпись архитектора ДО мержа. -->

## Goal

Заложить схему данных и контракт статусной модели, на которые обопрутся все остальные фазы. После мержа Ф0 схема заморожена: изменение = эскалация.

## In scope

Модели + перечисления + словарь переходов · миграции `dsn01`, `dsn02` · Pydantic-схемы · регистрация RBAC/навигации.

## Out of scope

Сервисная логика (→ Ф1) · роутер (→ Ф2) · items-список артикулов (→ потом, Р2) · FK на `nomenclature` (сознательно нет: товар может отсутствовать в справочнике).

## Работы

### 1. `backend/models/design.py` (создать)

Перечисления — `str, enum.Enum`, хранение `String`, НЕ pg-enum:

```python
class DesignTaskStatus(str, enum.Enum):
    NEW = "NEW"                  # Новые заявки
    ASSIGNED = "ASSIGNED"        # Назначена
    IN_PROGRESS = "IN_PROGRESS"  # В работе
    REVIEW = "REVIEW"            # На проверке
    REVISION = "REVISION"        # Правки
    ON_HOLD = "ON_HOLD"          # Отложена (вне доски)
    ACCEPTED = "ACCEPTED"        # Принято (терминал)
    CANCELLED = "CANCELLED"      # Отменена (терминал, вне доски)

class DesignWorkType(str, enum.Enum):    # MAIN_PHOTO | FULL_SET | EDIT | RICH | VIDEO
class DesignComplexity(str, enum.Enum):  # S | M | L
class DesignMaterialKind(str, enum.Enum):# FILE | LINK | NM
class DesignVerdict(str, enum.Enum):     # PENDING | ACCEPTED | REJECTED
# Срочность — булев флаг is_urgent на задаче (Р9), отдельного enum нет
```

Словарь переходов — единственный источник правды (зеркало `PAYMENT_REQUEST_TRANSITIONS`):

```python
DESIGN_TASK_TRANSITIONS: dict[DesignTaskStatus, set[DesignTaskStatus]] = {
    DesignTaskStatus.NEW:         {ASSIGNED, ON_HOLD, CANCELLED},
    DesignTaskStatus.ASSIGNED:    {IN_PROGRESS, NEW, ON_HOLD, CANCELLED},  # →NEW = снять исполнителя
    DesignTaskStatus.IN_PROGRESS: {REVIEW, ON_HOLD, CANCELLED},
    DesignTaskStatus.REVIEW:      {ACCEPTED, REVISION, CANCELLED},
    DesignTaskStatus.REVISION:    {REVIEW, ON_HOLD, CANCELLED},
    DesignTaskStatus.ON_HOLD:     {NEW, ASSIGNED, IN_PROGRESS, REVISION, CANCELLED},  # ⇄ симметрия с REVISION
    DesignTaskStatus.ACCEPTED:    set(),
    DesignTaskStatus.CANCELLED:   set(),
}

DESIGN_BOARD_STATUSES = [NEW, ASSIGNED, IN_PROGRESS, REVIEW, REVISION, ACCEPTED]  # 6 колонок PRD §4
DESIGN_ACTIVE_STATUSES = {NEW, ASSIGNED, IN_PROGRESS, REVIEW, REVISION}           # для workload/просрочки
```

Префикс `DESIGN_` у констант обязателен — голые `ACTIVE_STATUSES`/`BOARD_STATUSES` коллидируют с одноимёнными в других доменах (напр. `funnel/ab_photo_tests.py`).

**`design_tasks`** — `Base, TimestampMixin, SoftDeleteMixin`:

| Колонка | Тип | Null | Примечание |
|---|---|---|---|
| `id` | Integer PK | — | |
| `project_id` | Integer FK projects.id | нет | всегда в фильтре |
| `number` | String(20) | нет | `DES-N` внутри проекта (Р14) |
| `title` | String(300) | нет | название товара/задачи свободным текстом |
| `description` | Text | нет | 2–3 строки сути, min 10 симв. (Р13, валидация в схеме) |
| `sheet_url` | String(1000) | нет | ссылка на ТЗ-гугл-таблицу (Р13) |
| `nm_id` | BigInteger | да | = `Nomenclature.article_wb` при опознании; **без FK** (Р2) |
| `work_type` | String(20) | нет | default `FULL_SET` |
| `complexity` | String(1) | нет | default `M`; меняет только lead |
| `is_urgent` | Boolean | нет | default false, `server_default=false()` (Р9: сигнал-подсветка от менеджера) |
| `status` | String(20) | нет | default `NEW`, `server_default="NEW"` |
| `sort_order` | Integer | нет | default 0; шаг 1000 (Р3) |
| `due_date` | Date | да | |
| `author_user_id` | Integer FK users.id | нет | менеджер-постановщик = приёмщик |
| `assignee_user_id` | Integer FK users.id | да | назначает только lead |
| `is_outsourced` | Boolean | нет | default false; ставит lead |
| `viewed_by_lead_at` | DateTime | да | Р5: красная метка = NEW && NULL |
| `held_from_status` | String(20) | да | Р1: откуда отложили, для возврата |
| `started_at` | DateTime | да | первый вход в IN_PROGRESS |
| `accepted_at` | DateTime | да | вход в ACCEPTED |

Индексы (все три — partial `WHERE is_deleted = false`): `ix_design_tasks_project_status_sort (project_id, status, sort_order)` · `ix_design_tasks_project_assignee (project_id, assignee_user_id, status)` · `ix_design_tasks_project_due (project_id, due_date)`; плюс `ix_design_tasks_author (project_id, author_user_id)`.
Partial-unique (и в миграции, и в `__table_args__` модели — иначе autogenerate-дрейф; донор pay01:87): `uq_design_tasks_project_number UNIQUE (project_id, number) WHERE is_deleted = false`.
DB-гарантия изоляции детей: `uq_design_tasks_id_project UNIQUE (id, project_id)`; все дети ссылаются составным FK `(task_id, project_id) → design_tasks(id, project_id) ON DELETE CASCADE` (чужой `project_id` падает IntegrityError), у каждого ребёнка отдельный индекс по `task_id` (`ix_design_materials_task_id` и т.п.).

**`design_materials`** («Исходные материалы», Р12): `id`, `project_id` FK, `task_id` (составной FK `(task_id, project_id)` CASCADE, см. выше), `kind String(10)`, `minio_path String(500)?`, `original_filename String(500)?`, `mime_type String(100)?`, `file_size Integer?`, `url String(1000)?`, `ref_nm_id BigInteger?`, `caption String(300)?`, `created_by_user_id` FK users?, `created_at DateTime default=utcnow`. CheckConstraint `ck_design_material_payload`: ровно одно из `minio_path`/`url`/`ref_nm_id` соответственно `kind`. Индекс `(project_id, task_id)`.

**`design_submissions`** (версии сдач; без SoftDelete — версии не удаляются): `id`, `project_id`, `task_id` (составной FK CASCADE), `version_no Integer` (max+1 под lock), `submitted_by_user_id` FK users, `submitted_at default=utcnow`, `comment Text?`, `verdict String(10) default PENDING`, `verdict_comment Text?` (обязателен при REJECTED — гвард Ф1), `verdict_by_user_id?`, `verdict_at?`. `UniqueConstraint(task_id, version_no)` + `uq_design_submissions_id_project UNIQUE (id, project_id)` (опора для составного FK файлов). Индекс `(project_id, task_id)`.

**`design_submission_files`**: `id`, `project_id`, `submission_id` (составной FK `(submission_id, project_id) → design_submissions(id, project_id)` CASCADE), `minio_path String(500)`, `original_filename?`, `mime_type?`, `file_size?`.

**`design_task_comments`** (`SoftDeleteMixin`): `id`, `project_id`, `task_id` (составной FK CASCADE), `author_user_id` FK users, `body Text`, `minio_path String(500)?`, `original_filename?`, `created_at`.

**`design_task_events`** (журнал, зеркало `PaymentRequestEvent` :278; append-only): `id`, `project_id`, `task_id` (составной FK CASCADE), `old_status String(20)?`, `new_status String(20)`, `changed_at`, `changed_by String(100)?`, `comment Text?`. Индекс `(project_id, task_id)`.

Регистрация: импорты + все классы в `__all__` `backend/models/__init__.py`.

### 2. Миграции

`migrations/versions/dsn01_design_tasks.py` — 6 таблиц + индексы + CheckConstraint + partial-unique через `op.create_index(..., postgresql_where=sa.text("is_deleted = false"))`. **Перед генерацией:** `alembic heads` — ровно одна голова; цеплять за голову origin/dev (на 2026-08-01 — `spp03_price_probes`, перепроверить). Обязательный `downgrade()` в обратном порядке FK.

`migrations/versions/dsn02_design_notify_flag.py` — `telegram_chat_bindings` + колонка `design_notify_enabled BOOLEAN NOT NULL server_default=false()` (донор — `supply_notify_enabled` в `backend/models/telegram.py` + модель дополнить).

### 3. `backend/schemas/design.py` (создать, регистрация в `schemas/__init__.py`)

| Схема | Состав |
|---|---|
| `DesignTaskCreate` | `title`, `description` (min_length=10), `sheet_url` (HttpUrl), `nm_id?`, `work_type?`, `is_urgent?`, `due_date?` — **без** `assignee` (назначает lead, дельта v4) и **без** `complexity` (ставит lead) |
| `DesignTaskUpdate` | всё опционально; `complexity`/`is_outsourced` применяются только для lead (проверка в сервисе) |
| `DesignTaskListItem` | `id, number, title, status, nm_id, work_type, complexity, is_urgent, due_date, is_overdue, is_outsourced, unviewed, assignee_name, author_name, versions_count, sort_order, project_name?` |
| `DesignBoardResponse` | `columns: dict[status, list[DesignTaskListItem]]`, `counts` (вкл. ON_HOLD/CANCELLED для фильтра) |
| `DesignTaskDetail` | шапка + `materials`, `submissions` (+files), `comments`, `events`, `permissions` |
| `DesignTaskPermissions` | булевы флаги действий (считает бэк, Ф1) |
| `DesignStatusChange` | `to_status`, `comment?` |
| `DesignMoveIn` | `to_status`, `after_task_id?` (Р4) |
| `DesignAssign` | `assignee_user_id?` (null = снять) |
| `DesignMaterialIn` | `kind`, одно из `url`/`ref_nm_id`, `caption?` |
| `DesignVerdictIn` | `verdict` (ACCEPTED\|REJECTED), `verdict_comment?` |
| `DesignProductSuggestion` | `nm_id (=article_wb), article_seller, brand, subject` |
| `DesignWorkloadRow` | `user_id, user_name, active_tasks, overdue, in_revision, nearest_due` |
| `DesignStatsOut` | метрики PRD §10: `on_time_share, avg_versions_to_accept, median_cycle_days, unassigned_over_2d, outsourced_share, tracked_share` |

### 4. RBAC и навигация — page-ключ `design-tasks` в 4 местах

1. `backend/rbac.py`: `ALL_PAGES` + `SECTION_PAGES["sales"]`.
2. `backend/main.py`: импорт роутера — заглушка НЕ нужна в Ф0, регистрация в Ф2; в Ф0 только rbac.
3. `frontend-react/src/app/(main)/p/[slug]/layout.tsx`: `navGroups` секция «Продажи» — `{ href: '/design-tasks', label: 'Дизайн карточек', icon: '🎨', pageKey: 'design-tasks' }`.
4. `frontend-react/src/app/(main)/p/[slug]/team/page.tsx`: локальный `SECTION_PAGES` секции «Продажи» — иначе `tests/test_conventions_sync.py::test_frontend_page_keys_are_known_to_backend` красный и права не выдать.

## AC

- **AC-1:** `docker compose exec backend python -c "from backend.models import DesignTask, DESIGN_TASK_TRANSITIONS"` — без ошибок.
- **AC-2:** `alembic upgrade head && alembic downgrade -1 && alembic downgrade -1 && alembic upgrade head` — чисто (обе миграции туда-обратно).
- **AC-3:** unit `tests/test_design_models.py`: golden-snapshot полного словаря; у `ACCEPTED` и `CANCELLED` пустые исходящие; каждый нетерминальный статус достижим из `NEW` по словарю (обход графа); `CANCELLED` в целях каждого нетерминального; `DESIGN_BOARD_STATUSES` ровно 6.
- **AC-4:** повторное создание `DES-1` в том же проекте после `soft_delete()` первой задачи не нарушает partial-unique (SQL-тест).
- **AC-5:** `tests/test_conventions_sync.py` зелёный целиком (page-ключи, soft-delete реестр).
- **AC-6:** `make typecheck` без ошибок по новым файлам.

## Exit-gate

| Критерий | Порог | Evidence |
|---|---|---|
| Миграции | up/down/up чисто | транскрипт AC-2 |
| Словарь переходов | AC-3 зелёный | pytest транскрипт |
| Конвенции | AC-5 зелёный + `check_conventions.sh` пусто | транскрипт |
| Ревью T3 | database + security + code — без BLOCK | вердикт /review |
| **Подпись архитектора** | ДО мержа (tripwire: models/migrations/rbac) | STATUS.md |

## Hints

- Донор статусной машины — `payment_request.py:116`; журнала — `:278`. Копировать форму, не содержание.
- `held_from_status` заполняется/чистится ТОЛЬКО в `change_status` (Ф1) — в Ф0 просто колонка.
- В `Nomenclature` артикул WB называется `article_wb`, артикул продавца — `article_seller` (НЕ nm_id/vendor_code — ошибка старого ТЗ).
- pg-enum запрещён (анти-паттерн CLAUDE.md); `NOT NULL` в миграции ⇒ `server_default`.

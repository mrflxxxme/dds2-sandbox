# DOMAIN: Дизайн карточек (design-tasks)

Канбан задач на инфографику карточек WB. Менеджер ставит заявку (название товара
текстом + обязательная ссылка на ТЗ-таблицу + суть + исходные материалы),
**ведущий дизайнер** (owner/admin проекта) распределяет её по исполнителям,
дизайнер сдаёт результат **версиями**, автор заявки выносит вердикт. Принятая
задача с привязанным `nm_id` может стать АБ-тестом главного фото.

Пользовательское руководство (как этим пользоваться) — [../docs/DESIGN-TASKS-GUIDE.md](../docs/DESIGN-TASKS-GUIDE.md);
здесь только то, чего из кода и гайда не видно: инварианты, решения и грабли.

## Карта файлов

| Слой | Файл | Содержимое |
|---|---|---|
| Модели | `models/design.py` | 11 таблиц (6 базовых + 5 справочных волны C), 6 строковых enum'ов, `DESIGN_TASK_TRANSITIONS` (источник правды), `DESIGN_BOARD_STATUSES`, `DESIGN_ACTIVE_STATUSES` |
| Миграции | `migrations/versions/dsn01_design_tasks.py` | 6 таблиц + индексы (аддитивно, revises `spp03_price_probes`) |
| | `migrations/versions/dsn02_design_notify_flag.py` | `telegram_chat_bindings.design_notify_enabled` |
| | `migrations/versions/dsn04_design_number_len.py` | номер заявки 20 → 40 символов (Р18) |
| | `migrations/versions/dsn05_design_labels_attributes.py` | 5 таблиц справочников + сид полей «Кабинет ВБ» и «Бренд» (Р33) |
| Схемы | `schemas/design.py` | вход/выход, `DesignTaskPermissions` (20 флагов), схемы справочников и массовых операций |
| Сервис | `services/design/common.py` | `is_lead`, `actor_name`, `get_task_row` (скоуп проекта + `FOR UPDATE`) — на него завязаны все остальные, чтобы не было циклов |
| | `services/design/state.py` | `validate_transition`, `can_user_transition`, `apply_transition_locked`, `change_status`, `_commit_and_notify` |
| | `services/design/permissions.py` | `compute_permissions` — 20 булевых флагов |
| | `services/design/crud.py` | `next_number`, `create_task`, `mark_viewed`, `update_task`, `assign`, `delete_task`, `add_comment` (+реэкспорт read-side) |
| | `services/design/queries.py` | `list_tasks`, `list_tasks_all_projects`, `list_calendar`, `get_task`, `product_suggest`, `to_list_items` |
| | `services/design/refs.py` | справочники меток и реквизитов, разметка задачи, массовое проставление (волна C) |
| | `services/design/board.py` | `get_board` (6 колонок одним ответом), `move_task` (dnd) |
| | `services/design/files.py` | материалы, версии сдач, вердикты, скачивание, allowlist/blocklist типов |
| | `services/design/workload.py` | «Загрузка команды» |
| | `services/design/stats.py` | метрики PRD §10 |
| | `services/design/notify.py` | 4 личных TG-события |
| | `services/design/ab_bridge.py` | мост «принятая задача → АБ-тест главного фото» |
| HTTP | `routers/design_tasks.py` | 24 ручки, `require_role(..., page="design-tasks")` |
| Джоба | `scheduler/jobs/design_notify.py` | утренняя сводка + «срок завтра»; регистрация — `scheduler/__init__.py` (`id="design_digest"`) |
| Тумблер чата | `routers/telegram.py::toggle_design_notify` → `telegram_service.toggle_design_notify` / `list_design_notify_chats` |
| RBAC | `rbac.py` — ключ страницы `design-tasks` (в пресете `sales`) |

Спека: `docs/specs/design/` — `CHARTER.md` §2 (решения Р1–Р16), `CONTRACT.md` (FROZEN, HTTP-контракт).

## Модель данных

Шесть таблиц, все `project_id`-scoped. Изоляция детей гарантирована **БД**, а не
кодом: у `design_tasks` есть `UniqueConstraint(id, project_id)`, и все дети
ссылаются **составным** FK `(task_id, project_id) → design_tasks(id, project_id)`
с `ON DELETE CASCADE`. Подмена `project_id` в дочерней строке падает
`IntegrityError`, а не тихо утекает в чужой проект.

- **`DesignTask`** (`design_tasks`) — заявка/задача. `TimestampMixin` + `SoftDeleteMixin`.
  `number` (`DES-N` внутри проекта), `title` String(300), `description` Text NOT NULL,
  `sheet_url` String(1000) NOT NULL (ссылка на ТЗ-таблицу, Р13),
  `nm_id BigInteger NULL` — **без FK** (Р2: товара может ещё не быть в `Nomenclature`;
  значение = `Nomenclature.article_wb`), `work_type`/`complexity`/`status` — String,
  не pg-enum (чтобы Alembic не гонял enum-миграции), `is_urgent` (Р9 — только
  подсветка, порядок не меняет), `sort_order` (шаг 1000, Р3), `due_date` Date,
  `author_user_id` (постановщик = приёмщик), `assignee_user_id`, `is_outsourced`,
  `viewed_by_lead_at` (Р5), `held_from_status` (куда вернуть из ON_HOLD — пишет
  только `apply_transition_locked`), `started_at` (первый вход в IN_PROGRESS),
  `accepted_at`.
- **`DesignMaterial`** (`design_materials`) — исходный материал (Р12: «исходные
  материалы», не «референсы»). **Без SoftDelete** — удаление жёсткое.
  `kind` = FILE | LINK | NM, ровно один payload проверяет CHECK
  `ck_design_material_payload`: FILE → `minio_path` NOT NULL и `url`/`ref_nm_id` NULL;
  LINK → только `url`; NM → только `ref_nm_id`.
- **`DesignSubmission`** (`design_submissions`) — версия сдачи. Без SoftDelete:
  версии не удаляются. `version_no` — `max+1` под row-lock задачи,
  `uq_design_submissions_task_version (task_id, version_no)`. `verdict` =
  PENDING | ACCEPTED | REJECTED, `verdict_comment` обязателен при REJECTED
  (гвард в сервисе, не в БД). Для детей есть `UniqueConstraint(id, project_id)`.
- **`DesignSubmissionFile`** (`design_submission_files`) — файл версии; составной
  FK `(submission_id, project_id) → design_submissions(id, project_id)` CASCADE.
- **`DesignTaskComment`** (`design_task_comments`) — комментарий, `SoftDeleteMixin`
  (**без** `TimestampMixin`: собственный `created_at`). Есть поля вложения
  (`minio_path`, `original_filename`).
- **`DesignTaskEvent`** (`design_task_events`) — журнал переходов, **append-only**,
  без SoftDelete. Пишется и на «не-переходы»: смена исполнителя (`old = new`,
  comment «Смена исполнителя» / «Исполнитель снят») и удаление задачи
  (comment «Задача удалена»).

**Индексы** (`dsn01` + зеркало в `__table_args__` — иначе `alembic autogenerate` дрейфует):

| Индекс | Колонки | Partial |
|---|---|---|
| `ix_design_tasks_project_status_sort` | `project_id, status, sort_order` | `WHERE is_deleted = false` |
| `ix_design_tasks_project_assignee` | `project_id, assignee_user_id, status` | `WHERE is_deleted = false` |
| `ix_design_tasks_project_due` | `project_id, due_date` | `WHERE is_deleted = false` |
| `ix_design_tasks_author` | `project_id, author_user_id` | нет |
| `uq_design_tasks_project_number` | `project_id, number` **UNIQUE** | `WHERE is_deleted = false` |

Partial-unique номера — донор `pay01`: без него `soft_delete()` навсегда занимал бы
слот `DES-N` и повторное создание падало бы `IntegrityError` (общая мина
`SoftDeleteMixin` + `UniqueConstraint`, см. `.claude/rules/learnings.md`).
У детей — по паре `(project_id, task_id)` и по одному `task_id`/`submission_id`.

## Статусная машина

Единственный источник правды — словарь `DESIGN_TASK_TRANSITIONS` в
`models/design.py` (зеркало `PAYMENT_REQUEST_TRANSITIONS`). Всё остальное — и
кнопки деталки, и drag&drop, и вердикт — проходит через одну функцию
`state.apply_transition_locked`.

```
NEW ──▶ ASSIGNED ──▶ IN_PROGRESS ──▶ REVIEW ──▶ ACCEPTED (терминал)
 ▲          │                          │  ▲
 └──────────┘ (снять исполнителя)      ▼  │
                                    REVISION
ON_HOLD (вне доски) ⇄ NEW | ASSIGNED | IN_PROGRESS | REVISION
CANCELLED (терминал, вне доски) ← из любого нетерминального
```

Словарь целиком: `NEW → {ASSIGNED, ON_HOLD, CANCELLED}`; `ASSIGNED → {IN_PROGRESS,
NEW, ON_HOLD, CANCELLED}`; `IN_PROGRESS → {REVIEW, ON_HOLD, CANCELLED}`;
`REVIEW → {ACCEPTED, REVISION, CANCELLED}` (**ON_HOLD из REVIEW нет**);
`REVISION → {REVIEW, ON_HOLD, CANCELLED}`; `ON_HOLD → {NEW, ASSIGNED, IN_PROGRESS,
REVISION, CANCELLED}`; `ACCEPTED` и `CANCELLED` — пустые множества.

На доске 6 колонок (`DESIGN_BOARD_STATUSES`): NEW, ASSIGNED, IN_PROGRESS, REVIEW,
REVISION, ACCEPTED. ON_HOLD и CANCELLED — вне доски (видны фильтром списка).
`DESIGN_ACTIVE_STATUSES` (5 штук, без ON_HOLD и терминалов) — база для загрузки
команды, просрочки и «срок завтра».

**Гварды** (текст = то, что видит пользователь в 400):

| Условие | Текст |
|---|---|
| `→ ASSIGNED` или `→ IN_PROGRESS` без `assignee_user_id` | «Назначьте исполнителя» |
| `IN_PROGRESS → REVIEW` без PENDING-версии **с файлами** | «Сдайте версию с файлами» |
| `REVISION → REVIEW` без PENDING-версии **с файлами** | «Приложите исправленную версию» |
| `REVIEW → REVISION` без комментария | «Напишите, что исправить» |
| `→ ACCEPTED` без PENDING-версии (файлы не требуются) | «Нет версии на проверке» |
| `→ CANCELLED` без комментария | «Укажите причину отмены» |

Проверка «PENDING-версия» для `→REVIEW` идёт с JOIN на файлы намеренно: короткие
транзакции `create_submission` допускают окно «PENDING без файлов» (заливка
сорвалась), и такая версия не должна открывать путь на проверку.

**Побочные эффекты перехода** (все — внутри `apply_transition_locked`):
- `→ NEW` — обнуляет `assignee_user_id` (в т.ч. при возврате из ON_HOLD);
- первый `→ IN_PROGRESS` — ставит `started_at` (повторные входы не трогают);
- `→ ON_HOLD` — пишет `held_from_status = текущий`; выход из ON_HOLD — чистит его;
- `→ REVISION` — отклоняет PENDING-версию (`REJECTED` + `verdict_comment` = комментарий перехода);
- `→ ACCEPTED` — ставит `accepted_at` и закрывает PENDING-версию (`ACCEPTED`);
- всегда — строка `DesignTaskEvent` + накопление notify-события в `db.info`.

Эффекты вердикта живут **только** здесь: принять/вернуть можно и через
`POST /submissions/{id}/verdict`, и прямой сменой статуса из деталки —
`files.set_verdict` лишь делегирует, прокидывая конкретный `submission_id`
(фолбэк «последняя PENDING» — только для прямого `change_status`).

**Матрица «кто двигает»** (`state.can_user_transition`; ребро вне словаря → False
для всех; `lead` = owner/admin — любое ребро словаря):

| Переход | Кто, кроме lead |
|---|---|
| `NEW → ASSIGNED`, `ASSIGNED → NEW` | никто (только lead) |
| `ASSIGNED → IN_PROGRESS`, `IN_PROGRESS → REVIEW`, `REVISION → REVIEW` | исполнитель |
| `REVIEW → ACCEPTED`, `REVIEW → REVISION` | автор заявки |
| `* → ON_HOLD` и любой выход из ON_HOLD | исполнитель |
| `* → CANCELLED` | автор заявки |

Конкурентность: `change_status` и `move_task` работают под
`SELECT … FOR UPDATE` + `populate_existing` — второй параллельный переход
перечитывает уже изменённый статус и бьётся о словарь (`ValueError`), а не
затирает первый.

## Права

Права считает **только бэк** (инвариант §6.9): `permissions.compute_permissions`
отдаёт 15 булевых флагов, схема `DesignTaskPermissions` зеркалит их ключ-в-ключ
(паритет закреплён тестом `test_permissions_schema_matches_service`). Фронт логику
прав не дублирует.

`is_lead(member_role)` = `member_role in ("owner", "admin")` — «ведущий дизайнер»
из PRD это роль в проекте, отдельной сущности нет.

| Флаг | Правило |
|---|---|
| `can_edit` | lead вне терминалов; автор вне терминалов **и вне REVIEW** |
| `can_assign` | lead, вне терминалов |
| `can_take` | (исполнитель или lead) и статус ASSIGNED |
| `can_submit` | (lead или исполнитель) и статус IN_PROGRESS/REVISION |
| `can_verdict` | (lead или автор) и статус REVIEW |
| `can_hold` | результат `can_user_transition(→ON_HOLD)` |
| `can_cancel` | (lead или автор), вне терминалов |
| `can_delete` | lead или автор (**в любом статусе**) |
| `can_set_complexity`, `can_set_outsource`, `can_reorder` | только lead |
| `can_comment` | `member_role != "viewer"` |
| `can_create_ab_test` | (lead или автор) и ACCEPTED и `nm_id is not None` |
| `can_change_status` | есть хотя бы одно доступное ребро |
| `can_move` | lead или `can_change_status` |

Два уровня: грубый HTTP-гейт `require_role("viewer"|"editor", page="design-tasks")`
на каждой ручке (Р11 — в отличие от `ab_tests`, где гейт только на фронте) и
тонкая доводка author/assignee/lead в сервисе. Поэтому, например, `DELETE /{id}`
проходит `require_role("editor")`, а не-автор editor получает 403 уже из
`crud.delete_task`.

`DesignTaskDetail.allowed_transitions` (amendment 2026-08-02) — список целевых
статусов, доступных **текущему** пользователю; считается той же
`can_user_transition`, порядок = порядок объявления enum `DesignTaskStatus`.

## API

Полный замороженный контракт (тела, коды, заголовки) — **`docs/specs/design/CONTRACT.md`**.
Здесь только карта, чтобы не искать.

Префикс `/api/v1/design-tasks`, скоуп проекта — заголовок `X-Project-Id`.
Статические пути объявлены ДО `/{task_id}` (FastAPI матчит по порядку; закреплено
регресс-тестом openapi). Кэша нет вообще (Р10) — правило 7 про `invalidate_cache`
для модуля не активируется. Все write-ручки — `Depends(rate_limit_write)`.

- **Чтение:** `GET /board` · `GET ""` (фильтры + `q`) · `GET /all-projects` ·
  `GET /workload` · `GET /calendar` · `GET /stats` ·
  `GET /product-suggest?q=` · `GET /{id}`.

`GET /calendar` принимает ЛИБО `month=YYYY-MM`, ЛИБО пару `date_from`/`date_to`
(волна A v2, CONTRACT-V2 §1) — вместе они дают 400. Без параметров берётся текущий
месяц по МСК. Границы `date_from`/`date_to` ОТВЕТА — это фактическое окно выборки:
запрошенное ±6 дней, календарь дорисовывает недели соседних месяцев. Cap — 500 задач
в режиме месяца (как в v1) и 2000 в режиме диапазона, длина диапазона ≤ 400 дней;
при срабатывании cap ответ несёт `truncated: true` (тихое усечение запрещено).
Фронт раскладывает задачи по `tasks[].due_date`, а не по `month`.

⚠️ **Два понятия «сегодня» в одном ответе.** Дефолтный месяц календаря берётся по МСК
(`msk_today`), а `is_overdue` в `to_list_items` — по UTC (`utcnow().date()`, как весь остальной
модуль). В окне 00:00–02:59 МСК календарь откроется на новом месяце, а флаг просрочки будет
ещё вчерашним. Расхождение осознанное: календарь пользовательский, а трогать общий для модуля
`utcnow().date()` в рамках правки календаря нельзя — это задело бы метрики и загрузку.

`GET /all-projects` осталась в API, хотя вкладка «Все бренды» из UI убрана (Р19):
контракт не ломаем, потребителя у ручки сейчас нет.

**Справочники (волна C v2):** `GET|POST /refs/labels`, `PUT|DELETE /refs/labels/{id}`,
`GET|POST /refs/attributes`, `PUT|DELETE /refs/attributes/{id}`,
`POST /refs/attributes/{id}/values`, `PUT|DELETE /refs/values/{id}`,
`PUT /{task_id}/labels`, `PUT /{task_id}/attributes`, `POST /bulk/labels|attributes`.
Чтение — любому с page-ключом, запись — `editor`-гейт плюс тонкая проверка «только
ведущий» в сервисе. `DELETE` здесь **архивирует**, а не удаляет (Р30), и идемпотентен.
- **Задача:** `POST ""` (201) · `PUT /{id}` (PATCH-семантика) · `DELETE /{id}` (204) ·
  `POST /{id}/status` · `POST /{id}/move` · `POST /{id}/assign` · `POST /{id}/viewed`.
- **Материалы:** `POST /{id}/materials` (LINK|NM) · `POST /{id}/materials/file`
  (multipart) · `GET /{id}/materials/{mat}/file` · `DELETE /{id}/materials/{mat}`.
- **Версии:** `POST /{id}/submissions` (multipart, создаёт версию **и** переводит в
  REVIEW одной оркестрацией) · `GET /{id}/submissions/{sub}/files/{file}` ·
  `POST /{id}/submissions/{sub}/verdict`.
- **Прочее:** `POST /{id}/comments` · `POST /{id}/ab-test`.

`GET /all-projects` — единственная ручка без `get_current_project`: скоуп задаёт
членство `ProjectMember` × page-гейт (owner/admin — всегда, editor/viewer — по
ключу `design-tasks` в `pages`, `get_effective_pages`).

Маппинг ошибок сервиса в HTTP — один хелпер `_svc` роутера:
`PermissionError → 403`, `WbContentError → 502`, `ValueError → 400`, кроме текстов
`{"Задача не найдена", "Версия сдачи не найдена"}` → **404**. Тексты гвардов —
часть контракта: менять их = менять API.

## Файлы и MinIO

Пути (bucket — общий `settings.MINIO_BUCKET`):

```
design/{project_id}/{task_id}/materials/{ts}_{name}
design/{project_id}/{task_id}/comments/{ts}_{name}
design/{project_id}/{task_id}/v{version_no}/{ts}_{idx}_{name}
```

Отдача — **только через бэк** с проверкой `project_id` и живости задачи
(`is_deleted = false`), без presigned-URL; заголовки
`Content-Disposition: attachment; filename*=UTF-8''…` + `X-Content-Type-Options: nosniff`.
`minio_path` наружу не выходит ни в одной Out-схеме.

Лимиты (`services/design/files.py`, роутер импортирует те же константы):
`MAX_FILE_MB = min(20, settings.MAX_UPLOAD_SIZE_MB)`, `MAX_FILES_PER_SUBMISSION = 10`,
`MAX_SUBMISSION_TOTAL_MB = 100`. Превышения: >10 файлов → 400, размеры → 413.

Порядок проверок (донор `payment_requests.py:710`): allowlist типа (`image/*`,
PDF, ZIP, RAR) → явный `BLOCKED_MIME_EXACT` → `EXEC_BLOCKLIST` расширений →
чтение тела и пост-проверка размера → в сервисе `_sanitize_filename` →
повторная валидация → `validate_file_content` (magic bytes) → MinIO.
Тип определяется **по расширению** (`mimetypes.guess_type`), клиентский
`Content-Type` — только фолбэк.

Сироты MinIO не подчищаются нигде (нет delete-хелпера в `storage.py`): удаление
материала, компенсация АБ-теста и сорвавшаяся заливка версии пишут `warning` в
лог `dds.design` — это осознанный размен, объём мал.

## Уведомления

**Личные (4 события, `services/design/notify.py`)** — вызываются строго ПОСЛЕ
`commit`, весь блок best-effort (сбой TG не роняет операцию):

| Событие | Кому | Текст |
|---|---|---|
| назначили (`crud.assign`) | исполнителю | 🎨 `DES-N` · title · срок |
| `→ REVIEW` | **автору** | 📬 `DES-N` ждёт проверки, версия N |
| `REVIEW → REVISION` | исполнителю | ✏️ `DES-N` вернули: причина |
| `→ ACCEPTED` | исполнителю | ✅ `DES-N` принята |

Резолв `user_id → TelegramBotUser.telegram_id` идёт **только при активном
членстве** в проекте; нет привязки — молча скип (Р6). Доставка —
`telegram_service.send_analytics_message` (httpx, работает и в web-процессе, без
aiogram-синглтона). Диспатч единый: переходы копятся в `db.info["design_notify_events"]`
и рассылаются из `state._commit_and_notify` после commit — поэтому кнопка,
drag&drop и вердикт шлют одно и то же.

**Утренняя сводка (`scheduler/jobs/design_notify.py`, только worker-контейнер)** —
`CronTrigger(DIGEST_HOUR_MSK=9, DIGEST_MINUTE_MSK=0, tz=Europe/Moscow)`,
`id="design_digest"`. Две независимые ветки:
1. в чаты с `TelegramChatBinding.design_notify_enabled` (тумблер —
   `PATCH /telegram/chats/{id}/design-notify`) — «в работе / на проверке /
   просрочено / принято вчера» + deep-link на доску;
2. исполнителям задач с `due_date = завтра` в активном статусе — личное «срок завтра».

Антиспам — Redis `SET NX EX` (TTL 48 ч, дата в самом ключе делает его одноразовым),
ключи **раздельные**: `design_digest:{project_id}:{date}:digest` и
`…:due` — сбой одной ветки не гасит другую. Redis недоступен → шлём (лучше редкий
повтор, чем молчание). Ключ захватывается ПОСЛЕ сборки данных, прямо перед отправкой.

## Мост в АБ-тесты

`services/design/ab_bridge.py` — тонкая надстройка над `services/funnel/ab_photo_tests`:
PIL-валидация (≥700×900, ≤10 МБ), проверка «нет активного теста по `nm_id`» и
снимок галереи не дублируются, ошибки донора транслируются как есть.

Донор требует `campaign_id`, а у задачи дизайна кампании нет — отсюда **два режима**
одной ручки `POST /{id}/ab-test`:
- **без `campaign_id`** → `DesignAbTestOut.prefill` (`nm_id`, `from_design_task=DES-N`,
  готовые `name`/`comment`) — фронт редиректит на предзаполненную форму
  `/ab-tests/create`, ничего не создаётся;
- **с `campaign_id`** → полный мост: image-файлы **последней ACCEPTED-версии**
  становятся вариантами. `campaign_id` дополнительно проверяется на принадлежность
  проекту (`WbAdCampaign`) — у `AbPhotoTest.campaign_id` нет FK, и донор его не
  валидирует, так что без этой проверки тест молча привязался бы к чужой кампании.

Гварды и порядок ошибок: 404 «Задача не найдена» → 400 «Тест создаётся только по
принятой задаче» → 400 «Привяжите товар к задаче…» → 403 (`can_create_ab_test`) →
400 «нет изображений» → ошибки донора.

**Компенсация половинки.** `create_test` и `add_variant` коммитят каждый сам —
единой транзакции нет. Если падает любой `add_variant`, `_compensate_half_test`
делает rollback, жёстко удаляет уже созданные `AbPhotoVariant` (модель без
SoftDelete), `soft_delete()`-ит сам `AbPhotoTest` и пробрасывает исходную ошибку.
Ловится `BaseException` — чтобы и обрыв/отмена не оставили тест наполовину.

## Метрики

`services/design/stats.py` → `GET /stats?date_from&date_to` (`DesignStatsOut`).
`None` = «нет данных» (пустой знаменатель), а не 0.

| Метрика | Как считается |
|---|---|
| `on_time_share` | доля `accepted_at::date <= due_date` **среди задач с `due_date`**; без срока задача в знаменатель не входит |
| `avg_versions_to_accept` | среднее число `DesignSubmission` на принятую задачу |
| `median_cycle_days` | `percentile_cont(0.5)` от `accepted_at − created_at` в **календарных** днях (приближение рабочих — принято) |
| `unassigned_over_2d` | текущий снимок: NEW/ASSIGNED без исполнителя старше 2 суток; **окно на неё не влияет** |
| `outsourced_share` | доля `is_outsourced` среди принятых |
| `tracked_share` | доля задач с `nm_id` — метрика **входа**, режется по `created_at` |

Окно `from/to` режет по `accepted_at` для метрик приёмки и по `created_at` для
`tracked_share`. Просрочка везде в модуле = `due_date < сегодня` **и** активный
статус; задача без срока просроченной не считается — это сквозное решение
(`workload`, `queries.to_list_items`, `stats`, джоба сводки).

Экран «Загрузка команды» (`workload.py`) — один GROUP BY по
`DESIGN_ACTIVE_STATUSES`: активные / просроченные / в правках / ближайший срок;
`ORDER BY count DESC` стоит ДО `LIMIT 200`, чтобы усечение было детерминированным
(остаются самые загруженные), сортировка по имени — уже в Python.

## Ловушки для будущих правок

- **Два разных advisory-лока, оба `pg_advisory_xact_lock(cls, project_id)`.**
  `0x00DE516` (`crud._NUMBER_LOCK_CLASS`) сериализует **нумерацию** `DES-N`,
  `0x00DE517` (`board._MOVE_LOCK_CLASS`) — **перетаскивания** доски. Второй нужен
  не для порядка сам по себе, а чтобы два параллельных `move_task` брали row-lock'и
  колонок в одном порядке и не дедлочились (`move` лочит `FOR UPDATE` всю целевую
  колонку до 2000 строк). Замки живут до конца транзакции — совместимы с
  PgBouncer transaction-pooling. Классы свободны: матчеры выписки сидят на
  `0x50524D`. Новый лок в модуле — брать следующий класс из этой же линейки и
  проверять порядок захвата.
- **`next_number` считает max ВКЛЮЧАЯ soft-deleted** (фильтра `is_deleted` там нет
  намеренно): номер удалённой задачи не переиспользуется, журнал append-only
  остаётся честным. Partial-unique индекс мёртвых строк не видит — то есть БД
  разрешила бы повтор, а бизнес-правило строже. Не «чинить» добавлением фильтра.
- **Пустая PENDING-версия переиспользуется.** `create_submission` коммитит строку
  версии ДО заливки в MinIO (короткие транзакции — БД-коннект не висит через
  внешний HTTP). Сорвавшаяся заливка оставляет PENDING **без файлов**; следующая
  сдача не отбивает «уже есть несданная», а переиспользует ту же строку и тот же
  `version_no`, обновив автора/время/комментарий. Проверка «вторая PENDING
  запрещена» смотрит на наличие **файлов**, а не на саму строку.
- **Сужение MIME до JPEG/PNG/WebP — ДО захода в донор.** `_last_accepted_submission_images`
  отбирает только `image/jpeg|jpg|png|webp`, хотя выборка идёт по `mime_type LIKE 'image/%'`.
  Причина: GIF или битый файл как единственный кандидат провалил бы `add_variant`
  уже ПОСЛЕ `create_test` — лишний тест плюс компенсация. Если донор начнёт
  принимать больше форматов, `_WB_IMAGE_MIME` надо расширять синхронно.
- **502 на `WbContentError`.** `create_test` донора ходит в WB Content API
  (`fetch_card`); 401/429/5xx/сеть прилетают `WbContentError`. Роутер транслирует
  их в **502**, как `routers/ab_tests.py`, а не в общий 500. Не сворачивать в
  `except Exception`.
- **Блок svg/html/xml в загрузках — двойной и нужен целиком.** `EXEC_BLOCKLIST`
  содержит `.svg/.svgz/.html/.htm/.xhtml/.xml/.mjs/.php` (а не только `.exe`-класс),
  и отдельно есть `BLOCKED_MIME_EXACT` = {`image/svg+xml`, `text/html`,
  `application/xhtml+xml`, `text/xml`, `application/xml`}. Одного списка мало:
  `image/svg+xml` проходит allowlist `image/*`, а клиентский `Content-Type` может
  назвать активный тип при нейтральном расширении. Оба списка публичны и
  импортируются роутером — источник один, править надо в `files.py`.
- **`ValueError` — это 400 ИЛИ 404 по тексту.** Список «404-текстов» —
  `_NOT_FOUND_TEXTS` в роутере. Переименовал текст в сервисе — сломал код ответа.
- **`HTTPException` в сервисном слое допустим только в `files.py`** (валидация
  файлов и 503 MinIO, как у донора `payment_request_documents`). 503 при заливке
  версии **сквозной** — не глотать в 400, иначе фронтовый ретрай (5xx повторить /
  4xx не повторять) перестанет работать.
- **`comment` при `ACCEPTED` не попадает в `verdict_comment`.** `_close_pending_submission`
  комментарий игнорирует (в отличие от `_reject_pending_submission`) — «за что
  приняли» остаётся только в `DesignTaskEvent.comment`.
- **`ON_HOLD → REVISION` — это возврат из отложенных, а не «вернули на доработку».**
  Уведомление «вернули» шлётся только при `old_status == REVIEW`. При добавлении
  новых рёбер в REVISION проверять это условие в `notify_status_changed`.
- **Кэша нет и не должно быть (Р10).** Не добавлять `@cached` — иначе придётся
  регистрировать prefix в `invalidate_project_reports()` и держать инвалидацию
  на частых мутациях доски.
- **Прод-домен уведомлений зашит константой** `_APP_BASE_URL = "https://app.vyatkin-wb.ru"`
  в двух местах (`notify.py`, `design_notify.py`) — как у `draft_staleness_watch`.
  На локалке уведомления всё равно no-op (нет токена бота).

## Ссылки

- [DOMAIN_PAYMENT_REQUEST.md](DOMAIN_PAYMENT_REQUEST.md) — донор статусной машины
  (`PAYMENT_REQUEST_TRANSITIONS`), журнала событий и загрузки документов.
- [DOMAIN_TELEGRAM.md](DOMAIN_TELEGRAM.md) — `TelegramBotUser`, `TelegramChatBinding`,
  `send_analytics_message`.
- [DOMAIN_WB.md](DOMAIN_WB.md) — АБ-тесты фото и WB Content API (донор моста).
- [DOMAIN_COST.md](DOMAIN_COST.md) — `Nomenclature` (источник `nm_id = article_wb`).
- `docs/specs/design/CHARTER.md` §2 — решения Р1–Р16 с обоснованиями.
- `docs/specs/design/CONTRACT.md` — FROZEN HTTP-контракт.
- `docs/DESIGN-TASKS-GUIDE.md` — пользовательское руководство.

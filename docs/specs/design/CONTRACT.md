# CONTRACT — API модуля «Дизайн карточек»

**FROZEN 2026-08-02 — изменение = эскалация архитектору.**
**amended 2026-08-02: +`DesignTaskDetail.allowed_transitions` (additive, санкция lead).**
**amended 2026-08-03 (Ф7-подготовка, санкция lead): +`POST /{task_id}/comments/file` (комментарий с вложением, multipart) и +`GET /{task_id}/comments/{comment_id}/file` (скачивание вложения) — аддитивно; `POST /{task_id}/comments` (JSON) не изменён.**
**amended 2026-08-03 (пост-аудит Ф7, инвариант §6.9, санкция lead): +`DesignBoardResponse.permissions {can_create, can_reorder}` и +`DesignTaskPermissions.can_mark_viewed` — аддитивно; закрывают гейтинг кнопок доски и отметки просмотра по роли на фронте.**

<!-- HEAD-SUMMARY: замороженный HTTP-контракт Ф2 (backend/routers/design_tasks.py). От него параллельно идут Ф3 (фронт) и Ф4 (уведомления). Схемы — backend/schemas/design.py; поведение — сервис Ф1. -->

Базовый префикс: `/api/v1/design-tasks`. Все ручки под JWT (`get_current_user` на роутере в `main.py`). Скоуп проекта задаёт клиент заголовком `X-Project-Id` (или `?project_id=` — заголовок приоритетнее, `backend/project_context.py`); без него project-scoped ручки → 400. RBAC на бэке (Р11): `require_role(min_role, page="design-tasks")`; исключение — `GET /all-projects` (только `get_current_user`, без `X-Project-Id`; скоуп = членство `ProjectMember` × page-гейт `design-tasks`, см. строку таблицы). Все write-ручки — `Depends(rate_limit_write)`. Кэша нет (Р10).

## Соглашение об ошибках

Тело ошибки — единый конверт приложения (`backend/exceptions.py`):
`{"error": {"code": "...", "message": "<текст ниже>", "details": null, "payload": null}}`.
Исключение — 422 pydantic-валидации FastAPI: стандартный `{"detail": [...]}`.

| Код | Когда | Источник |
|---|---|---|
| 400 | невалидная операция: запрещённый переход, гвард, неверный вход; **> 10 файлов в одной версии сдачи** | `ValueError` сервиса → текст в `detail` (тексты гвардов — спек F1); кап файлов — роутер И сервис |
| 401 | нет / просрочен / невалиден JWT | `get_current_user` |
| 403 | роль/страница/матрица прав | `require_role`, `get_current_project` (не член), `PermissionError` сервиса |
| 404 | нет записи в скоупе проекта | `ValueError` с текстом «Задача не найдена» / «Версия сдачи не найдена»; `HTTPException(404)` files.py («Материал не найден», «Файл не найден») |
| 413 | файл > 20 МБ или суммарно > 100 МБ | единый источник — роутер И сервисные проверки размеров (files.py) поднимают 413 |
| 422 | pydantic/Query-валидация (limit/offset/month/body-капы, **невалидный enum в `status[]`/`work_type`**) | FastAPI |
| 429 | превышение rate_limit_write; ответ несёт заголовок `Retry-After: <сек>` | `RateLimiter` (`backend/utils/rate_limit.py`) |
| 501 | — | снят: ab-test реализован в Ф6 (amended 2026-08-03) |
| 503 | MinIO недоступен (в т.ч. обрыв заливки при сдаче версии — сквозной, не глотается в 400) | files.py |

## Ручки

| Метод | Путь | min_role | Запрос | Ответ (2xx) |
|---|---|---|---|---|
| GET | `/board` | viewer | — | `DesignBoardResponse` {columns: {status: [DesignTaskListItem]}, counts: {status: int}, permissions: `DesignBoardPermissions`} — 6 колонок доски, counts по всем 8 статусам, права уровня доски (amended 2026-08-03, см. §Схемы) |
| GET | `` | viewer | query: `status[]`, `work_type` (enum'ы, невалидный → 422), `assignee_user_id`, `author_user_id`, `is_urgent`, `overdue`, `q` (≤100), `limit` 1..200 (def 100), `offset` ≥0 | `list[DesignTaskListItem]` |
| POST | `` | editor | `DesignTaskCreate` | **201** `DesignTaskDetail` |
| GET | `/all-projects` | — (только JWT, без `X-Project-Id`) | query: `status[]` (enum, невалидный → 422), `limit` 1..200, `offset` ≥0 | `list[DesignTaskListItem]` c `project_name`; только проекты членства, где участнику доступна страница `design-tasks` (owner/admin — всегда; editor/viewer — по ключу в `pages`, `get_effective_pages`) |
| GET | `/workload` | viewer | — | `list[DesignWorkloadRow]` |
| GET | `/calendar` | viewer | `month=YYYY-MM` (pattern, 422; несуществующий месяц → 400) | `DesignCalendarOut` {month, date_from, date_to, tasks} — окно `1-е − 6 дн … последний день + 6 дн` |
| GET | `/stats` | viewer | `date_from?`, `date_to?` (ISO-даты) | `DesignStatsOut` |
| GET | `/product-suggest` | editor | `q` (1..100) | `list[DesignProductSuggestion]` (≤10) |
| GET | `/{task_id}` | viewer | — | `DesignTaskDetail` (permissions — ПОЛНЫЙ набор флагов `compute_permissions`, §6.9; + `allowed_transitions` — amendment 2026-08-02, см. §Схемы) |
| PUT | `/{task_id}` | editor | `DesignTaskUpdate` (PATCH-семантика) | `DesignTaskDetail` |
| DELETE | `/{task_id}` | editor | — | **204**; soft_delete, право автор\|lead (иначе 403) |
| POST | `/{task_id}/status` | editor | `DesignStatusChange` {to_status, comment?} | `DesignTaskDetail` |
| POST | `/{task_id}/move` | editor | `DesignMoveIn` {to_status ∈ 6 board-статусов, after_task_id?, comment?} | `DesignTaskDetail`; reorder в своей колонке — только lead (403); dnd в REVISION без comment → 400 |
| POST | `/{task_id}/assign` | editor | `DesignAssign` {assignee_user_id \| null} | `DesignTaskDetail`; lead-only (403) |
| POST | `/{task_id}/viewed` | editor | — | `DesignTaskDetail`; lead-only (403), идемпотентно (Р5) |
| POST | `/{task_id}/materials` | editor | `DesignMaterialIn` {kind: LINK\|NM, url?, ref_nm_id?, caption?}; kind=FILE → 422 | **201** `DesignMaterialOut` |
| POST | `/{task_id}/materials/file` | editor | multipart `file` | **201** `DesignMaterialOut` |
| GET | `/{task_id}/materials/{mat_id}/file` | viewer | — | байты файла + заголовки download (ниже) |
| DELETE | `/{task_id}/materials/{mat_id}` | editor | — | **204**; право: автор материала \| автор заявки \| lead |
| POST | `/{task_id}/submissions` | editor | multipart `files[]` (1..10 — 11-й → 400; каждый ≤20 МБ и суммарно ≤100 МБ → 413) + `comment?` (Form) | **201** `DesignTaskDetail` — версия создана И задача переведена в REVIEW единой сервисной оркестрацией `files.submit_version`; пустая PENDING-версия (след упавшей заливки, 503) переиспользуется тем же `version_no` |
| GET | `/{task_id}/submissions/{sub_id}/files/{file_id}` | viewer | — | байты файла + заголовки download |
| POST | `/{task_id}/submissions/{sub_id}/verdict` | editor | `DesignVerdictIn` {verdict: ACCEPTED\|REJECTED, verdict_comment?} | `DesignTaskDetail`; REJECTED без комментария → 400 |
| POST | `/{task_id}/comments` | editor (viewer → 403) | `DesignCommentIn` {body 1..2000} | **201** `DesignCommentOut` (`original_filename = null` — текстовый путь) |
| POST | `/{task_id}/comments/file` | editor (viewer → 403) | multipart: `body` (Form, 1..2000) + `file` | **201** `DesignCommentOut` с `original_filename`; валидация файла — общий путь материалов (allowlist/blocklist/`validate_file_content`, ≤20 МБ → 413), MinIO `design/{project}/{task}/comments/…` (amended 2026-08-03) |
| GET | `/{task_id}/comments/{comment_id}/file` | viewer | — | байты вложения + заголовки download (ниже); 404 — вне проекта, по удалённой задаче/комментарию или если вложения нет (amended 2026-08-03) |
| POST | `/{task_id}/ab-test` | editor | `DesignAbTestIn {campaign_id?: int≥1}` (тело опционально) | 200 `DesignAbTestOut {ab_test_id \| null, prefill \| null}`: без campaign_id → prefill для формы `/ab-tests/create`; с campaign_id → полный мост (amended 2026-08-03, санкция lead, Ф6) |

Порядок объявления в роутере: статические (`board`, `all-projects`, `workload`, `calendar`, `stats`, `product-suggest`) ДО `/{task_id}` — закреплено регресс-тестом openapi.

## Заголовки download-ручек (все три GET-file)

```
Content-Disposition: attachment; filename*=UTF-8''{urllib.parse.quote(filename)}
X-Content-Type-Options: nosniff
```

Отдача только через бэк с проверкой `project_id` и живости задачи (`is_deleted=false`); файл версии проверяется на принадлежность именно `{sub_id}`; вложение комментария — дополнительно на `is_deleted=false` самого комментария (SoftDelete-модель), `Content-Type` угадывается по имени файла (своей колонки `mime_type` у комментария нет). В openapi все три ручки объявляют бинарный ответ: `responses={200: {"content": {"application/octet-stream": {}}}}`. Out-схемы (`DesignMaterialOut`, `DesignSubmissionFileOut`, `DesignCommentOut`) НЕ содержат `minio_path` (канон counterparty: внутренние пути стора наружу не выходят — скачивание только через эти GET-ручки).

## Загрузка файлов (порядок проверок — донор payment_requests.py:710)

1. allowlist типа (mime по расширению, фолбэк клиентский): `image/*`, PDF, ZIP, RAR → иначе 400; ДОПОЛНИТЕЛЬНО явный блок активных MIME `BLOCKED_MIME_EXACT` = {`image/svg+xml`, `text/html`, `application/xhtml+xml`, `text/xml`, `application/xml`} → 400 (svg проходит `image/*`, клиентский Content-Type может назвать активный тип при нейтральном расширении);
2. blocklist исполняемых/активных расширений `EXEC_BLOCKLIST` (вкл. svg/html/xml — решение F1) → 400;
3. чтение тела; pre-check `file.size` и post-check фактического размера → 413 (>20 МБ) — и в роутере, и в сервисе;
4. в сервисе: `_sanitize_filename` → повторная валидация → `validate_file_content` (magic bytes) → MinIO `design/{project_id}/{task_id}/...`.

Константы — публичные в `backend/services/design/files.py`: `ALLOWED_MIME_EXACT`, `EXEC_BLOCKLIST`, `BLOCKED_MIME_EXACT` (роутер импортирует их же — один источник).

## Схемы (backend/schemas/design.py)

Вход: `DesignTaskCreate`, `DesignTaskUpdate`, `DesignStatusChange`, `DesignMoveIn`, `DesignAssign`, `DesignMaterialIn`, `DesignVerdictIn`, `DesignCommentIn` (JSON-путь комментария; multipart-путь `/comments/file` схемы не использует — `body` приходит как Form, зеркало `/materials/file`).
Выход: `DesignTaskListItem`, `DesignBoardResponse` (вложенно `DesignBoardPermissions`), `DesignTaskDetail` (вложенно: `DesignMaterialOut`, `DesignSubmissionOut` + `DesignSubmissionFileOut`, `DesignCommentOut`, `DesignEventOut`, `DesignTaskPermissions`), `DesignCalendarOut`, `DesignWorkloadRow`, `DesignStatsOut`, `DesignProductSuggestion`.

`DesignTaskPermissions` — 16 флагов, зеркало ключей `compute_permissions` (паритет закреплён тестом `test_permissions_schema_matches_service`): `can_edit`, `can_assign`, `can_take`, `can_change_status`, `can_move`, `can_hold`, `can_reorder`, `can_submit`, `can_verdict`, `can_comment`, `can_cancel`, `can_delete`, `can_set_complexity`, `can_set_outsource`, `can_create_ab_test`, `can_mark_viewed` (amended 2026-08-03: `lead`, зеркало гварда `crud.mark_viewed` — фронт дёргает `POST /{task_id}/viewed` по нему, а не по роли). Фронт логику прав НЕ дублирует (§6.9). Семантика отдельных флагов: `can_comment` = `member_role != "viewer"` (viewer read-only, зеркало editor-гейта `POST /comments`); `can_delete` = `lead | автор` — HTTP-гейт ручки `DELETE /{task_id}` остаётся `require_role("editor")`, тонкая доводка «автор|lead» — в сервисе (`crud.delete_task`), не-автор editor → 403.

`DesignBoardResponse.permissions: DesignBoardPermissions {can_create, can_reorder}` (amended 2026-08-03, аддитивно, санкция lead): права УРОВНЯ ДОСКИ — доска не отдаёт per-task флаги, а фронту надо гейтить «+ Новая заявка» и перестановку внутри колонки. Считает `permissions.compute_board_permissions(member_role)`: `can_create` = `member_role != "viewer"` (зеркало гейта `POST /design-tasks`), `can_reorder` = `is_lead` (то же значение, что одноимённый ключ `compute_permissions` — паритет закреплён тестом `test_board_can_reorder_matches_task_permissions`). Page-гейт применяет роутер до вызова.

`DesignTaskDetail.allowed_transitions: list[str]` (amended 2026-08-02, аддитивно, санкция lead): целевые статусы, куда ТЕКУЩИЙ пользователь реально может перевести задачу — `DESIGN_TASK_TRANSITIONS[status]`, отфильтрованный той же матрицей прав `state.can_user_transition` (логика не дублируется; считает `queries.get_task`). Фронт строит кнопки переходов в деталке по этому списку; агрегат `can_change_status` в permissions остаётся. Порядок элементов — порядок объявления enum `DesignTaskStatus`.

Статусы, work_type, complexity, verdict, kind материалов — строковые enum'ы из `backend/models/design.py`; словарь переходов `DESIGN_TASK_TRANSITIONS` — единственный источник правды (Р1).

---
name: project-design-module-security-carryover
description: Security carry-over модуля «Дизайн карточек» — что закрыто по фазам Ф0–Ф2 и волнам A–D, и что осталось открытым
metadata:
  type: project
---

Модуль «Дизайн карточек» (`docs/specs/design/`) ревьюится пофазно; security-находки переходят между фазами.

**Закрыто на Ф1** (2026-08-02, повторно не поднимать): членство assignee в проекте (`crud.assign`), валидация `after_task_id` в скоупе колонки/проекта (`board.move_task`), границы `nm_id` в схемах, экранирование ILIKE (`queries.escape_like`), единые тексты 404 без раскрытия чужих сущностей, advisory-класс нумерации `0x00DE516`.

**Закрыто на Ф2** (2026-08-02, все 5 пунктов): `_attachment()` (attachment + `filename*=UTF-8''` + nosniff — теперь его переиспользует и XLSX-выгрузка волны D), кап `limit`/`offset`, кап файлов сдачи, `crud.delete_task` через `soft_delete()`, паритет `DesignTaskPermissions` со схемой.

**Закрыто волнами A–D** (проверено 2026-08-20 на `0bc6147a..HEAD`):
- **`GET /all-projects` теперь page-гейтован по данным**: `queries.list_tasks_all_projects` строит `allowed_ids` через `"design-tasks" in get_effective_pages(...)`. Прежний carry-over Ф2 («участник без ключа читает все задачи») БОЛЬШЕ НЕ ВЕРЕН — снят.
- **Изоляция новых ~20 ручек чистая**: справочники/разметка/аналитика/XLSX — везде `get_current_project` (47 из 47 роутов) + фильтр `project_id` в сервисе + составные FK `(task_id, project_id)` / `(label_id, project_id)` / `(value_id, project_id)` на уровне БД.
- **`PUT /dashboard/layout` под viewer — обосновано**: `user_id` из сессии, unique `(project_id, user_id)`, JSONB whitelist из 4 виджетов. Исключение `VIEWER_WRITABLE_ROUTES` в `tests/test_rbac_page_gates.py` принято.

**Открыто (проверить на следующей волне / Ф7-аудите):**
- **SVG проходит блок-лист по расширению** (`services/design/files.py::_EXEC_BLOCKLIST` — только по ext). Сегодня безопасно (attachment + nosniff). Любой inline-показ вложений / presigned-URL = BLOCK, пока нет MIME-блоклиста.
- **XLSX formula injection** в `services/design/export_xlsx.py`: openpyxl превращает строку с ведущим `=` в TYPE_FORMULA. Пользовательские title / имена меток / имена и значения реквизитов (последние — в СТРОКЕ ЗАГОЛОВКОВ листа «Задачи») пишутся без префикса `'`. Сквозного санитайзера в репо нет ни у одного экспорта — фикс уровня паттерна.
- **`_window` в `analytics.py` и `stats.py`**: `datetime.combine(date_to + timedelta(days=1))` даёт OverflowError → 500 при `date_to=9999-12-31`. В `queries._pad` тот же случай уже закрыт клампом — донор для фикса.
- **Списки id без границы**: `label_ids` / `value_ids` в `DesignTaskLabelsIn` / `DesignBulkLabelsIn` и т.п. (`task_ids` закрыт — `MAX_BULK_TASKS=500`). См. [[bulk-ids-unbounded-amplification]] — MEDIUM, не гейт.

**Why:** пофазное ревью; фронт может отменить митигации бэка (inline-рендер), а экспорт выносит данные за периметр приложения.
**How to apply:** при ревью следующей волны идти по «Открыто» до анализа остального diff'а. См. [[project-review-tooling-env]] — гейты в worktree локально не запускаются.

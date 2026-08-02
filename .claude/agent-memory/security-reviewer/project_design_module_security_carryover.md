---
name: project-design-module-security-carryover
description: Security carry-over модуля «Дизайн карточек» — что закрыто на Ф0/Ф1/Ф2 и что обязан проверить ревьюер Ф3/Ф6/Ф7
metadata:
  type: project
---

Модуль «Дизайн карточек» (`docs/specs/design/`) ревьюится пофазно; security-находки переходят между фазами.

**Закрыто на Ф1** (проверено 2026-08-02, повторно не поднимать): членство assignee в проекте (`crud.assign`), валидация `after_task_id` в скоупе колонки/проекта (`board.move_task`), границы `nm_id` в схемах, экранирование ILIKE (`queries.escape_like`), единые тексты 404 без раскрытия чужих сущностей, advisory-класс нумерации `0x00DE516`.

**Закрыто на Ф2** (проверено 2026-08-02 по чек-листу Ф1 — все 5 пунктов закрыты, повторно не поднимать):
1. `_attachment()` в `routers/design_tasks.py` — `attachment; filename*=UTF-8''` + `X-Content-Type-Options: nosniff` на обеих download-ручках; тест `test_download_headers`.
2. Кап `limit`/`offset` — `Query(100, ge=1, le=200)` в `list_tasks` и `/all-projects`; тест `test_limit_offset_validation`.
3. Кап файлов сдачи — `MAX_FILES_PER_SUBMISSION=10` в роутере (400) и в сервисе; тест `test_submission_multipart_flow`.
4. `crud.delete_task` — `task.soft_delete()` в СЕРВИСЕ, `DELETE /{task_id}` только транслирует; `DesignMaterial` без `SoftDeleteMixin`, там `db.delete()` легален.
5. `DesignTaskPermissions` — все 15 флагов, паритет со схемой закреплён тестом `test_permissions_schema_matches_service`.
6. `upload_comment_attachment` — задачу валидирует вызывающий `crud.add_comment` через `get_task_row` (Ф2 из HTTP этот путь не открывает).

**Открыто после Ф2 — проверить на Ф3 (фронт), Ф6 (АБ-мост) и Ф7 (аудит):**
- **`GET /all-projects` без page-гейта** (`routers/design_tasks.py:162-177`): скоуп — только членство `ProjectMember`, но участник проекта БЕЗ `design-tasks` в `pages` читает через неё все задачи проекта (title/nm_id/имена/сроки). Противоречие: CHARTER Р11 «все ручки через `require_role(page=...)`» vs таблица F2-spec/CONTRACT «— (только `get_current_user`)». Реализация пошла за spec. Решение владельца не зафиксировано — поднять снова, если Ф7 делает аудит доступа.
- **SVG проходит блок-лист по расширению**: `_EXEC_BLOCKLIST` в `services/design/files.py` — только по ext, а `image/svg+xml` от клиента при БЕЗрасширенном имени проходит `startswith("image/")`. Сегодня безопасно (attachment+nosniff), станет stored XSS, если Ф3/Ф6 добавят inline-превью или presigned-URL. Любой inline-показ вложений дизайна = BLOCK, пока нет MIME-блоклиста.

**Why:** пофазное ревью, роутер появился только в Ф2; фронт может отменить митигации бэка (inline-рендер).
**How to apply:** при ревью Ф3/Ф6 идти по «Открыто после Ф2» до анализа остального diff'а.

См. [[project-review-tooling-env]] — гейты в worktree локально не запускаются.

---
name: project-design-module-security-carryover
description: Security carry-over из ревью Ф0/Ф1 модуля «Дизайн карточек» — что закрыто и что обязан проверить ревьюер Ф2 (роутер)
metadata:
  type: project
---

Модуль «Дизайн карточек» (`docs/specs/design/`) ревьюится пофазно; security-находки переходят между фазами.

**Закрыто на Ф1** (проверено 2026-08-02, повторно не поднимать): членство assignee в проекте (`crud.assign`), валидация `after_task_id` в скоупе колонки/проекта (`board.move_task`), границы `nm_id` в схемах, экранирование ILIKE (`queries.escape_like` — копия `counterparty_service._escape_like`), единые тексты 404 без раскрытия чужих сущностей, advisory-класс нумерации `0x00DE516` не пересекается с namespace'ами других доменов.

**Передано в Ф2 (роутер) — обязательно проверить при ревью Ф2:**
1. `Content-Disposition: attachment` + `filename*=UTF-8''{quote(...)}` + `X-Content-Type-Options: nosniff` на обеих download-ручках — иначе SVG-вложение = stored XSS (сервис возвращает клиентский `mime_type` как есть).
2. Кап `limit`/`offset` в `list_tasks` (`Query(100, ge=1, le=200)`) — сервис лимит не валидирует.
3. Кап числа файлов в `create_submission` — сервис ограничивает 20 МБ на файл, но не количество.
4. `can_delete` во флагах `permissions.py` есть, а `delete_task` в сервисе НЕТ — если Ф2 реализует удаление, оно обязано лечь в сервис с `soft_delete()`, не в роутер и не `db.delete()`.
5. `DesignTaskPermissions` отдаёт 9 флагов из 15 (`can_take`/`can_reorder`/`can_set_complexity`/`can_set_outsource`/`can_create_ab_test` молча отсекаются в `queries.get_task`) — либо расширить схему (tripwire, подпись владельца), либо фронт продублирует логику прав и сломает инвариант §6.9.

**Why:** ревью Ф1 read-only, роутера ещё нет — эти пункты нельзя ни зафиксировать, ни закрыть в сервисном слое.
**How to apply:** при ревью Ф2 идти по списку как по чек-листу до анализа остального diff'а.

См. [[project-review-tooling-env]] — гейты в worktree локально не запускаются.

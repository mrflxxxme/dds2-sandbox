---
name: rbac-inheritance-denylist-polarity
description: Наследование страниц RBAC устроено как denylist (PAGES_NEVER_INHERITED) — новый чувствительный раздел раздаётся командам сам, если про него забыли
metadata:
  type: project
---

`backend/rbac.inherited_pages` выдаёт editor/viewer любой раздел, чья дата в `PAGE_ADDED_AT` позже `ProjectMember.pages_updated_at`, — по секции `SECTION_PAGES` или по blanket-ветке. Запрет — только явный список `PAGES_NEVER_INHERITED` (salary, raw-data, project-settings, team). Реализовано 2026-07-30.

**Why:** механика лечит реальный баг (список страниц был замороженным снимком, новые разделы не доезжали до команды никогда). Полярность denylist выбрана ради «не ветшающего» полного доступа.

**How to apply:** при ревью ЛЮБОГО нового page-ключа проверять, попал ли он в `PAGES_NEVER_INHERITED`, если раздел чувствительный (деньги, займы, ключи, персданные). Забытый ключ = молчаливая раздача всей секции, без действия владельца и без записи в логах. Гарды `tests/test_conventions_sync.py` и `tests/test_rbac_page_inheritance.py` заставляют завести дату (т.е. включить наследование), но НЕ заставляют принять решение о чувствительности — это слепое пятно. Второй риск той же полярности: удаление ключа из `ALL_PAGES`/`PAGE_ADDED_AT` сужает `grantable_then` → участники, у которых было «всё кроме удалённого», молча становятся blanket. См. [[page-permissions-frontend-only]].

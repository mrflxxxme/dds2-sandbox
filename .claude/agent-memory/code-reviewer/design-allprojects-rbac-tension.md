---
name: design-allprojects-rbac-tension
description: «Дизайн карточек» — GET /all-projects сознательно без require_role/page-гейта, что расходится с ратифицированным Р11 «все ручки под require_role»; поднято на ревью Ф2
metadata:
  type: project
---

`GET /api/v1/design-tasks/all-projects` — единственная ручка модуля без `get_current_project` И без `require_role(..., page="design-tasks")`. Скоуп = подзапрос членства `ProjectMember` (`services/design/queries.py::list_tasks_all_projects`). Так написано в спеке `docs/specs/design/phases/F2-api-freeze.md` (min_role «— только get_current_user») и в `CONTRACT.md` (FROZEN 2026-08-02).

**Конфликт:** CHARTER §2 Р11 (статус «✅ решено») требует `require_role(..., page="design-tasks")` на ВСЕХ ручках именно потому, что «фронт-гейт обходится». Следствие текущего кода: участник проекта с `pages=["reports"]` (без `design-tasks`) получает 403 на `GET /design-tasks`, но видит те же задачи этого проекта в сквозном списке. Межпроектной утечки нет — только обход page-гейта внутри своих проектов.

**Why:** это не баг реализации, а расхождение двух замороженных документов; каждый следующий ревьюер модуля наткнётся на него заново и потратит время на перепроверку.

**How to apply:** при ревью Ф3–Ф7 не поднимать заново как «новую находку» — проверить, появилось ли решение архитектора в `docs/specs/design/DECISIONS-LOG.md` / STATUS.md. Если решения нет, а код правится — фикс в одну строку: добавить в `member_sq` фильтр по `role in (owner, admin)` OR `'design-tasks' in parse_pages(ProjectMember.pages)`. Смежное: [[design-transitions-canon]].

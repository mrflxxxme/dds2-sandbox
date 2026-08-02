---
name: page-permissions-frontend-only
description: Постраничные права в DDS2 энфорсятся на бэке ТОЛЬКО для salary / raw-data / dashboard; остальные разделы — чистый клиент (PageGuard/usePermissions)
metadata:
  type: project
---

Было (до 2026-07-23): единственная серверная проверка — `get_current_project()` (членство в `ProjectMember`), `pages`/`role` жили только на клиенте.

Стало (сверено 2026-07-30): `require_role(page=...)` из `backend/rbac.py` реально стоит на
- `routers/payroll.py` — `page="salary"` (read/write/admin),
- `routers/raw_data.py` — `page="raw-data"` (кроме `/refresh-progress` — намеренно без гейта, поллинг 3с),
- `routers/reports.py` — `page="dashboard"` на 4 dashboard-эндпоинтах.

Всё остальное (funnel, opiu, ads-manager, ai-chat, stocks, loans, …) по-прежнему доступно любому участнику проекта curl'ом, без учёта `pages`. Комментарий в `backend/main.py` («require_role is unused; pages are frontend-only») — устаревший.

**Why:** архитектурный выбор всего приложения (solo dev, доверенный круг); гейты добавляются точечно, по мере появления реально чувствительных доменов (первым был dashboard-leak 07-23, затем зарплата).

**How to apply:** для НЕгейтованных доменов новая находка — HIGH-информационная («данные класса X доступны всем участникам»), без блокирующего фикса; проверять с владельцем. Для трёх гейтованных страниц (`salary`, `raw-data`, `dashboard`) authz реальный — любая правка `get_effective_pages` / `ALL_PAGES` / `PAGE_ADDED_AT` / `PAGES_NEVER_INHERITED` меняет доступ к деньгам и её надо ревьюить как настоящий authz. Связано с [[payroll-global-tariff-intentional]].

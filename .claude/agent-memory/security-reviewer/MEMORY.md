# Security Reviewer — память

- [Глобальная тарифная лестница ЗП](payroll-global-tariff-intentional.md) — `payroll_tariff_step` без `project_id` намеренно; не блокировать, только admin-гейт
- [Постраничные права](page-permissions-frontend-only.md) — бэк энфорсит `pages` только для salary / raw-data / dashboard, остальное — чистый клиент
- [Наследование страниц = denylist](rbac-inheritance-denylist-polarity.md) — забытый ключ в `PAGES_NEVER_INHERITED` раздаёт новый раздел всей секции сам
- [Bulk `ids` без границы](bulk-ids-unbounded-amplification.md) — сквозной паттерн всех bulk-ручек; rate-limit считает запросы, не элементы → MEDIUM, не гейт
- [Газелька = необратимый write](gazelka-irreversible-write-path.md) — реальный заказ перевозчику без отмены; чек-лист ревью: ключ (kind,id), маркер TR, `None`=дефолт формы
- [Review tooling env](project_review_tooling_env.md) — Bash-инструмент в worktree без coreutils/python; check_conventions.sh и pytest — только через test-runner
- [Дизайн карточек: security carry-over](project_design_module_security_carryover.md) — закрыто Ф1/Ф2/волны A–D; открыто: SVG-inline, XLSX formula injection, `_window` OverflowError

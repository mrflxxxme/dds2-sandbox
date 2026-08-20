---
name: design-module-schema-freeze
description: Модуль «Дизайн карточек» — схема БД фризится после Ф0; статус DB-ревью по фазам/волнам (Ф0 закрыт, Ф1 частично, волны B/C/D — WARNING)
metadata:
  type: project
---

Модуль «Дизайн карточек» (`docs/specs/design/`: CHARTER.md · STATUS.md · phases/F0..F7) строится фазами, песочница без пуша в `origin` (Р16). Миграции: `dsn01`…`dsn06`, голова цепочки — `dsn06_design_dashboard_layout`.

**Why:** По CHARTER §7 после мержа Ф0 изменение схемы = эскалация к архитектору. Значит «доиндексируем потом» здесь дороже обычного — индексные правки вносить в ТУ ЖЕ ещё не смёрженную миграцию (сейчас это `dsn05`/`dsn06`), пока таблицы пустые; `dsn01` трогать уже нельзя.

**How to apply:**
- **Ф0 (2026-08-02) — ЗАКРЫТО.** **Ф1 W3 (`get_task_row` без `populate_existing`) — ЗАКРЫТО:** `populate_existing` есть в `services/design/common.py`.
- **Ф1 — открыто, проверять дальше:** `files.create_submission` держит `FOR UPDATE` + открытую транзакцию через N MinIO-загрузок (инцидент idle-in-transaction 2026-07-16); `board.get_board` берёт общий `LIMIT 200×6` вместо лимита на колонку.
- **Волны B/C/D (2026-08-20, WARNING):** главное — `design_task_labels` имеет только ПАРТИАЛЬНЫЕ индексы с `task_id` (`WHERE removed_at IS NULL`), а запрос истории Р20 (`queries._label_history`) и CASCADE по задаче читают строки СО снятыми метками; `get_funnel` считает LEAD по `design_task_events` не отфильтровав «не-переходы» (`old_status = new_status` — метки/реквизиты/номер/исполнитель), из-за чего `avg_days_in_status` занижается; `created_at` в `dsn05`/`dsn06` объявлен `nullable=True`, а модель через `TimestampMixin` даёт NOT NULL (в `dsn01` было правильно) → autogenerate-дрейф.
- Ключевой инвариант модуля: partial-unique справочников условен по ОБОИМ флагам (`is_deleted = false AND is_archived = false`), потому что «Удалить» в UI = архивирование (Р30). Гварды в `services/design/refs.py` этому зеркалу соответствуют — но без advisory-лока (в отличие от `crud._apply_number_change`), т.е. гонка двух кликов даёт 500.
- Advisory-lock нумерации: `pg_advisory_xact_lock(0x00DE516, project_id)` — 2-аргументная (int4,int4) форма, xact-scoped, с PgBouncer transaction pooling корректна; неймспейс не пересекается с 0x50524D / 0x41534D / 0x505244 / 0x46465359 / 0x57425257.
- `migrations/env.py` НЕ ставит `transaction_per_migration` → один `alembic upgrade/downgrade` = одна транзакция: падение поздней ревизии откатывает и ранние. Это снимает страх «половинчатого отката» в этой репе.
- См. [[env-bash-tooling-broken]] и [[local-db-verification-access]].

---
name: design-module-schema-freeze
description: Модуль «Дизайн карточек» — схема БД фризится после Ф0; статус DB-ревью по фазам (Ф0 WARNING закрыт, Ф1 WARNING открыт)
metadata:
  type: project
---

Модуль «Дизайн карточек» (`docs/specs/design/`: CHARTER.md · STATUS.md · phases/F0..F7) строится фазами Ф0–Ф7, песочница без пуша в `origin` (Р16).

**Why:** По CHARTER §7 после мержа Ф0 изменение схемы = эскалация к архитектору. Значит «доиндексируем потом» здесь дороже обычного — индексные правки вносить прямо в `dsn01`, пока таблицы пустые.

**How to apply:**
- **Ф0 (2026-08-02, WARNING) — ЗАКРЫТО в Ф1:** FK-индексы CASCADE-детей (`*.task_id`, `submission_id`), `(project_id, author_user_id)`, partial-предикат `is_deleted = false` на индексах доски и partial-unique `uq_design_tasks_project_number` в `__table_args__` — всё есть и в `dsn01`, и в `backend/models/design.py`. W4 закрыт: `crud.next_number` считает max ВКЛЮЧАЯ soft-deleted.
- **Ф1 (2026-08-02, WARNING) — открытые пункты, проверять в Ф2/Ф4:** (1) `files.create_submission` держит `FOR UPDATE` + открытую транзакцию через N MinIO-загрузок (учтён инцидент idle-in-transaction 2026-07-16 из learnings.md); (2) `board.get_board` берёт общий `LIMIT 200×6` вместо лимита на колонку — терминальная ACCEPTED (60+ задач/мес) со временем съедает бюджет и колонки NEW/REVIEW/REVISION приходят пустыми; (3) `common.get_task_row(for_update=True)` без `populate_existing` — в сессии, где задача уже загружена (типичный роутер Ф2: сначала `get_task`, потом мутация), FOR UPDATE лочит строку, но ORM отдаёт устаревший снапшот → гвард считается по старому статусу.
- Advisory-lock нумерации: `pg_advisory_xact_lock(0x00DE516, project_id)` — 2-аргументная (int4,int4) форма, xact-scoped, с PgBouncer transaction pooling корректна; неймспейс не пересекается с 0x50524D / 0x41534D / 0x505244 / 0x46465359 / 0x57425257.
- См. [[env-bash-tooling-broken]] — гейты `alembic heads` / up-down-up / pytest в этой среде прогнать нечем, требовать транскрипт.

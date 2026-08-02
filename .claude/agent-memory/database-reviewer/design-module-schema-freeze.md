---
name: design-module-schema-freeze
description: Модуль «Дизайн карточек» — схема БД фризится после фазы Ф0, песочница без пуша в origin; ревью Ф0 прошло с WARNING
metadata:
  type: project
---

Модуль «Дизайн карточек» (`docs/specs/design/`: CHARTER.md · STATUS.md · phases/F0..F7) строится фазами Ф0–Ф7. Ф0 (модели `backend/models/design.py`, миграции `dsn01`/`dsn02`) отревьюено 2026-08-02 → WARNING: FK-колонки CASCADE-детей (`design_materials/submissions/comments/events.task_id`) и `design_tasks.author_user_id` без собственных индексов; индексы доски не partial по `is_deleted`; partial-unique `uq_design_tasks_project_number` объявлен только в миграции, не в metadata.

**Why:** По CHARTER §7 после мержа Ф0 изменение схемы = эскалация к архитектору, а по Р16 модуль живёт в изолированной песочнице (коммиты только в remote `sandbox`, в `origin` не пушится ничего). Значит «доиндексируем потом» здесь дороже обычного, и любую индексную правку выгодно вносить прямо в `dsn01`, пока таблицы пустые.

**How to apply:** При ревью следующих фаз (Ф1 сервис, Ф2 роутер) — проверять, вошли ли индексы из WARNING в `dsn01`, и отдельно, что генератор номера `DES-N` считает `max+1` ВКЛЮЧАЯ soft-deleted строки (partial-unique `WHERE is_deleted = false` иначе даёт переиспользование номера при живом append-only журнале `design_task_events`). См. [[env-bash-tooling-broken]] — гейты `alembic heads` / up-down-up в этой среде прогнать нечем, требовать транскрипт.

---
paths:
  - "migrations/**"
  - "backend/models/**"
---

# Alembic Migration Rules

- ВСЕГДА проверяй `alembic heads` перед созданием новой миграции
- ВСЕГДА реализуй функцию `downgrade()`
- В `upgrade()` — `op.drop_table()` только после переноса/бэкапа данных; в `downgrade()` допустимо (отмена `create_table`)
- НИКОГДА не модифицируй уже задеплоенные миграции
- CONCURRENTLY-индекс для больших таблиц — `op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ...")` внутри `with op.get_context().autocommit_block():` (у `op.create_index` НЕТ kwarg `concurrently`; см. learnings.md)
- Foreign keys ДОЛЖНЫ иметь соответствующий индекс
- Добавление колонки с NOT NULL требует `server_default`
- Тестируй цикл: upgrade → downgrade → upgrade перед коммитом
- Имена таблиц и колонок: snake_case
- Формат индексов: `ix_{table}_{column}`
- Формат constraint: `ck_{table}_{description}`
- Foreign keys: `{table}_id` формат

---
paths:
  - "migrations/**"
  - "backend/models/**"
---

# Alembic Migration Rules

- ВСЕГДА проверяй `alembic heads` перед созданием новой миграции
- ВСЕГДА реализуй функцию `downgrade()`
- НИКОГДА не используй `op.drop_table()` без бэкапа данных
- НИКОГДА не модифицируй уже задеплоенные миграции
- Используй `op.create_index(concurrently=True)` для больших таблиц
- Foreign keys ДОЛЖНЫ иметь соответствующий индекс
- Добавление колонки с NOT NULL требует `server_default`
- Тестируй цикл: upgrade → downgrade → upgrade перед коммитом
- Имена таблиц и колонок: snake_case
- Формат индексов: `ix_{table}_{column}`
- Формат constraint: `ck_{table}_{description}`
- Foreign keys: `{table}_id` формат

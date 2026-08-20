---
name: env-bash-tooling-broken
description: Bash tooling in dds2 worktrees is inconsistent — probe once (python/psql often DO work), fall back to Grep/Read only if it fails
metadata:
  type: project
---

Проверять инструментарий одним дешёвым пробным вызовом, а не считать его сломанным по памяти.

**Why:** 2026-08-02 (worktree `dds2-card-design-spec-8a2392`) `grep`/`sed`/`tail`/`python` возвращали `command not found`, и я записал среду как безнадёжную. 2026-08-20 (worktree `dialog-analysis-improvements-9f7e8f`) `python` и `docker exec … psql` работают штатно — и именно они дали два факта, которые статическим чтением получить нельзя:
- граф ревизий (239 штук) → единственная голова, за 1 вызов;
- эмпирика PG: при `ALTER COLUMN … TYPE varchar(40)` из `varchar(20)` таблица НЕ переписывается и обычный индекс на колонке НЕ пересоздаётся, а **partial-индекс на той же колонке пересоздаётся** (relfilenode меняется). Проверяется пробой `BEGIN; CREATE TABLE …; SELECT relfilenode FROM pg_class …; ALTER …; SELECT relfilenode …; ROLLBACK;`.

**How to apply:**
- Сначала пробный вызов (`python -c "print(1)"`), потом решение. Пайпы через heredoc в `docker exec -i psql` иногда молча глотают вывод — надёжнее `printf > /tmp/x.sql` + `docker exec -i … < /tmp/x.sql`.
- `python -c "import backend…"` падает на `Settings` (`extra_forbidden` из `.env`) — метаданные моделей так не выгрузить; сравнение модели с миграцией делать чтением, а инференс nullable проверять на изолированном `DeclarativeBase` в отдельном процессе.
- Гейты `alembic upgrade/downgrade` и pytest по-прежнему требовать транскриптом от исполнителя (см. [[local-db-verification-access]] — не трогать `dds_db`).

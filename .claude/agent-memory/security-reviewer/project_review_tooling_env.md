---
name: project-review-tooling-env
description: Bash tool in dds2 worktrees has a broken PATH and cannot run scripts/check_conventions.sh or python — review gates must be delegated to test-runner
metadata:
  type: project
---

В worktree-окружении dds2 (Windows, Git Bash) прогон гейтов из Bash-инструмента недоступен:

- PATH без coreutils — `ls`/`grep`/`head` падают с exit 127, пока в начале команды не сделать `export PATH="/usr/bin:/bin:/mingw64/bin:$PATH"` (после этого `grep` работает).
- Вложенный вызов `bash scripts/check_conventions.sh` падает с `fatal error - add_item ("\??\C:\Program Files\Git", "/", ...)` — Cygwin-конфликт, обходного пути из инструмента нет.
- `python` / `py` в PATH отсутствуют → `scripts/hooks/post_edit_check.py --list-soft-models`, alembic и pytest напрямую не запустить.

**Why:** попытка выполнить `bash scripts/check_conventions.sh` во время security-ревью Ф0 «Дизайн карточек» (2026-08-02) съела несколько холостых вызовов инструмента.

**How to apply:** для security-ревью полагаться на статический анализ (Read + Grep), а прогон `check_conventions.sh`, pytest и `alembic upgrade/downgrade` отдавать субагенту `test-runner` (он ходит через `docker compose exec backend`) или явно просить владельца. В отчёте помечать, что гейты не исполнялись локально.

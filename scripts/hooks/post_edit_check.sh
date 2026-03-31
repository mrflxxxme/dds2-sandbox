#!/bin/bash
# Объединённый PostToolUse хук: iron rules + project_id + router logic detection
# Заменяет 3 отдельных хука → 1
set -uo pipefail

input=$(cat)

file=$(echo "$input" | grep -oE '"file_path":"[^"]*"' | head -1 | sed 's/"file_path":"//;s/"$//')
if [ -z "$file" ] || [ ! -f "$file" ]; then
  exit 0
fi

case "$file" in
  *.py)
    # Iron rules check
    if grep -qn 'datetime.utcnow\|datetime.now()' "$file" 2>/dev/null; then
      echo '[DDS2 WARN] datetime.utcnow()/datetime.now() — используй from backend.utils.time import utcnow' >&2
    fi
    if grep -qn 'text(f"\|text(f'"'"'' "$file" 2>/dev/null; then
      echo '[DDS2 WARN] f-string в text() — SQL injection! Используй :param' >&2
    fi
    if grep -qn 'db\.delete\|session\.delete' "$file" 2>/dev/null; then
      echo '[DDS2 WARN] db.delete() — используй soft_delete()' >&2
    fi
    ;;
esac

case "$file" in
  */services/*.py|*/routers/*.py)
    # project_id check in files with SQL
    if grep -qn 'select\|insert\|update\|delete\|text(' "$file" 2>/dev/null; then
      if ! grep -qn 'project_id' "$file" 2>/dev/null; then
        echo "[DDS2 WARN] SQL без project_id в $file — каждый запрос MUST фильтровать по project_id" >&2
      fi
    fi
    ;;
esac

case "$file" in
  */routers/*.py)
    # Business logic in router detection
    if grep -qnE '(select|insert|update|delete|execute)' "$file" 2>/dev/null; then
      if grep -qnE '(await db\.|async_session)' "$file" 2>/dev/null; then
        echo "[DDS2 WARN] Бизнес-логика в роутере $file — вынеси в services/" >&2
      fi
    fi
    ;;
esac

exit 0

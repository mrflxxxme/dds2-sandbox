#!/usr/bin/env bash
# Post-commit tracker: пишет hash коммита в .claude/.pending-learn.log
# Stop hook потом уведомляет пользователя что pending не пуст → /learn
#
# Защита от рекурсии: пропускает auto-генерированные коммиты
# (метки [auto-docs], [auto-learn], или сообщения "docs:" без человеческой части)
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PENDING="$ROOT_DIR/.claude/.pending-learn.log"

# Получить последний коммит
HASH=$(git rev-parse --short HEAD 2>/dev/null) || exit 0
MSG=$(git log -1 --format=%s "$HASH" 2>/dev/null) || exit 0

# Пропустить auto-генерированные
case "$MSG" in
  *"[auto-docs]"*|*"[auto-learn]"*) exit 0 ;;
  "docs:"*|"docs("*) exit 0 ;;  # docs-коммиты (вкл. /learn-рефлексию) не возвращаем в очередь /learn
esac

# message (truncated 80 chars) — нужно и для записи, и для дедупа
SHORT_MSG=$(echo "$MSG" | cut -c1-80)

# Пропустить если pending уже содержит этот hash ИЛИ это же сообщение
# (rebase даёт новый hash при том же subject → иначе очередь раздувается дублями)
if [ -f "$PENDING" ]; then
    if grep -q "^$HASH " "$PENDING" 2>/dev/null; then exit 0; fi
    if grep -qF -- " $SHORT_MSG" "$PENDING" 2>/dev/null; then exit 0; fi
fi

# Создать .claude/ если не существует
mkdir -p "$(dirname "$PENDING")"

# Записать: hash | timestamp | message
TS=$(date +%s)
echo "$HASH $TS $SHORT_MSG" >> "$PENDING"

exit 0

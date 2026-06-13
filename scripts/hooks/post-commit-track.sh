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

# Пропустить если pending уже содержит этот hash
if [ -f "$PENDING" ] && grep -q "^$HASH " "$PENDING" 2>/dev/null; then
    exit 0
fi

# Создать .claude/ если не существует
mkdir -p "$(dirname "$PENDING")"

# Записать: hash | timestamp | message (truncated 80 chars)
TS=$(date +%s)
SHORT_MSG=$(echo "$MSG" | cut -c1-80)
echo "$HASH $TS $SHORT_MSG" >> "$PENDING"

exit 0

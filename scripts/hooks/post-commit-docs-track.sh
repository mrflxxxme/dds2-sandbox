#!/usr/bin/env bash
# Post-commit tracker: пишет hash коммита в .claude/.pending-docs.log
# если затронул source code (backend/ / frontend-react/src/ / migrations/).
# UserPromptSubmit hook потом напоминает про /docs пока лог не пуст.
#
# Паттерн зеркалит post-commit-track.sh (pending-learn), но:
# - лог для docs-sync (отдельно от /learn)
# - пропускает docs-only / auto-generated / merge коммиты
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PENDING="$ROOT_DIR/.claude/.pending-docs.log"

HASH=$(git rev-parse --short HEAD 2>/dev/null) || exit 0
MSG=$(git log -1 --format=%s "$HASH" 2>/dev/null) || exit 0

# Пропустить auto-генерированные / pure docs / merge
case "$MSG" in
  *"[auto-docs]"*|*"[auto-learn]"*) exit 0 ;;
  "docs:"*|"docs("*) exit 0 ;;
  "Merge "*|"Merge: "*) exit 0 ;;
esac

# Проверить что есть source-code изменения (не только docs/tests/config)
CHANGED=$(git diff-tree --no-commit-id --name-only -r "$HASH" 2>/dev/null)
[ -z "$CHANGED" ] && exit 0

SOURCE_CHANGED=0
while IFS= read -r f; do
  case "$f" in
    backend/models/*.py|backend/services/*.py|backend/services/*/*.py|backend/routers/*.py|backend/routers/*/*.py)
      SOURCE_CHANGED=1 ; break ;;
    migrations/versions/*.py)
      SOURCE_CHANGED=1 ; break ;;
    backend/cache.py|backend/schemas/*.py)
      SOURCE_CHANGED=1 ; break ;;
    frontend-react/src/app/*|frontend-react/src/lib/api/*|frontend-react/src/types/api.ts)
      SOURCE_CHANGED=1 ; break ;;
  esac
done <<< "$CHANGED"

[ "$SOURCE_CHANGED" -eq 0 ] && exit 0

# Идемпотентность
if [ -f "$PENDING" ] && grep -q "^$HASH " "$PENDING" 2>/dev/null; then
    exit 0
fi

mkdir -p "$(dirname "$PENDING")"

TS=$(date +%s)
SHORT_MSG=$(echo "$MSG" | cut -c1-80)
echo "$HASH $TS $SHORT_MSG" >> "$PENDING"

exit 0

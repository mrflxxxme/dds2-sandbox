#!/usr/bin/env bash
# SessionStart hook — автоматически инжектит контекст при старте/resume сессии
# Срабатывает на matcher: startup|resume|clear|compact
# Stdout этого скрипта добавляется в контекст Claude Code → Claude сразу в курсе
#
# Matcher compact/clear дополнительно резетит session counter для /compact reminder.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

MATCHER="${CLAUDE_HOOK_MATCHER:-startup}"
COUNTER_FILE="$ROOT_DIR/.claude/.session-counter"

# Если compact/clear — резетим counter, не инжектим длинный контекст (он уже был)
case "$MATCHER" in
  compact|clear)
    echo "0" > "$COUNTER_FILE"
    exit 0
    ;;
esac

# ─── startup / resume: полный автоконтекст ──────────────────────────
echo "=== Session auto-context (hook: session_start.sh) ==="
echo ""

# 1. Recent commits за 2 дня
echo "## Recent commits (2 days)"
RECENT=$(git log --oneline --since="2 days ago" -10 2>/dev/null)
if [ -n "$RECENT" ]; then
  echo "$RECENT"
else
  echo "(no commits in last 2 days)"
fi
echo ""

# 2. Текущая ветка + sync с upstream
BRANCH=$(git branch --show-current 2>/dev/null || echo "?")
AHEAD_BEHIND=$(git rev-list --left-right --count "origin/$BRANCH...HEAD" 2>/dev/null | tr '\t' ' ' || echo "? ?")
echo "## Branch: \`$BRANCH\` (behind/ahead vs origin: $AHEAD_BEHIND)"
echo ""

# 3. Uncommitted changes (только количество, без имён чтобы не раздувать)
UNCOMMITTED=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
if [ "$UNCOMMITTED" -gt 0 ]; then
  echo "## Uncommitted: $UNCOMMITTED файлов"
  git status --porcelain 2>/dev/null | head -10
  [ "$UNCOMMITTED" -gt 10 ] && echo "... и ещё $((UNCOMMITTED - 10))"
  echo ""
fi

# 4. Pending learn коммиты
PENDING="$ROOT_DIR/.claude/.pending-learn.log"
if [ -s "$PENDING" ]; then
  COUNT=$(wc -l < "$PENDING" | tr -d ' ')
  echo "## Pending /learn: $COUNT коммитов"
  tail -3 "$PENDING"
  echo ""
fi

# 5/6. CI-failure issues + Docker health — отключены: gh API + docker ps медленные (3-5 сек на старт),
# результат используется редко. Запускай /status вручную если нужно.

# Reset session counter при старте
echo "0" > "$COUNTER_FILE"

exit 0

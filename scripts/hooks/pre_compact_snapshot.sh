#!/usr/bin/env bash
# PreCompact hook — снапшот рабочего состояния ПЕРЕД компактом контекста.
# Мотив: длинные сессии (28-handoff фичи вроде assembly-need-unified) теряют
# in-flight контекст при суммаризации — какие файлы были в работе, что не закоммичено.
# Пишет git-состояние + pending-learn в .claude/.pre-compact-latest.log (перезапись).
# Всегда exit 0 (observability-only, НИКОГДА не блокирует компакт).
set -uo pipefail

# Дренируем stdin (PreCompact шлёт JSON; нам он не нужен, но не оставляем broken pipe).
input=$(cat 2>/dev/null || true)
: "${input:=}"

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR" 2>/dev/null || exit 0

SNAP="$ROOT_DIR/.claude/.pre-compact-latest.log"

{
  echo "=== PRE-COMPACT SNAPSHOT: $(date '+%Y-%m-%d %H:%M:%S') ==="
  echo ""
  BRANCH=$(git branch --show-current 2>/dev/null || echo '?')
  AHEAD_BEHIND=$(git rev-list --left-right --count "origin/$BRANCH...HEAD" 2>/dev/null | tr '\t' ' ' || echo '? ?')
  echo "## Branch: $BRANCH   (behind/ahead vs origin: $AHEAD_BEHIND)"
  echo ""
  echo "## Uncommitted (git status --porcelain, cap 60):"
  git status --porcelain 2>/dev/null | head -60
  echo ""
  echo "## Diffstat vs HEAD (cap 40):"
  git diff --stat HEAD 2>/dev/null | tail -40
  echo ""
  echo "## Last 5 commits:"
  git log --oneline -5 2>/dev/null
  PENDING="$ROOT_DIR/.claude/.pending-learn.log"
  if [ -s "$PENDING" ]; then
    echo ""
    echo "## Pending /learn (tail 5):"
    tail -5 "$PENDING" 2>/dev/null
  fi
} > "$SNAP" 2>/dev/null

exit 0

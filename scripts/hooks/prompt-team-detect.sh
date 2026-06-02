#!/usr/bin/env bash
# UserPromptSubmit hook — slim версия (2026-05-19).
# Только: /compact session counter + [PUSH] reminder при фейле авто-пуша. (pending-docs убран → /learn)
# Удалено: directive injection (PARALLELISM/MIGRATION/TRIVIAL), prewarm-spawn вызов,
# git diff analysis. Lead-agent сам решает по содержимому запроса.
# Возврат: `git show HEAD~1:scripts/hooks/prompt-team-detect.sh > scripts/hooks/prompt-team-detect.sh`
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

# Авто-пуш dev упал?
PUSH_LOG="$ROOT_DIR/.claude/.last-push.log"
if [ -f "$PUSH_LOG" ] && grep -q 'PUSH_FAILED' "$PUSH_LOG" 2>/dev/null; then
    echo "[PUSH] последний авто-пуш dev УПАЛ — запушь вручную, детали в .claude/.last-push.log" >&2
fi

# /compact session counter
COUNTER_FILE="$ROOT_DIR/.claude/.session-counter"
count=0
if [ -f "$COUNTER_FILE" ]; then
    count=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
    : "${count:=0}"
fi
count=$((count + 1))
echo "$count" > "$COUNTER_FILE"

case "$count" in
    30) echo "[COMPACT] ~30 prompts — /compact для cache hit rate" >&2 ;;
    50) echo "[COMPACT] ~50 prompts — СИЛЬНО рекомендую /compact" >&2 ;;
    80) echo "[COMPACT] ⚠ ~80 prompts — /compact или /clear, риск context overflow" >&2 ;;
esac

exit 0

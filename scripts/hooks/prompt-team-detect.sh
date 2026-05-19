#!/usr/bin/env bash
# UserPromptSubmit hook — slim версия (2026-05-19).
# Только: pending docs reminder + /compact session counter.
# Удалено: directive injection (PARALLELISM/MIGRATION/TRIVIAL), prewarm-spawn вызов,
# git diff analysis. Lead-agent сам решает по содержимому запроса.
# Возврат: `git show HEAD~1:scripts/hooks/prompt-team-detect.sh > scripts/hooks/prompt-team-detect.sh`
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

# Pending docs
DOCS_PENDING="$ROOT_DIR/.claude/.pending-docs.log"
if [ -f "$DOCS_PENDING" ] && [ -s "$DOCS_PENDING" ]; then
    docs_count=$(wc -l < "$DOCS_PENDING" | tr -d ' ')
    if [ "${docs_count:-0}" -gt 0 ]; then
        echo "[DOCS] $docs_count pending коммит(ов) без синка документации — запусти /docs" >&2
    fi
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

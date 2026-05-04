#!/usr/bin/env bash
# UserPromptSubmit hook: ЖЁСТКО директивит lead-agent через hookSpecificOutput.additionalContext.
# Возвращает JSON в stdout — текст инжектится как system-reminder и обязательно к исполнению.
#
# Триггеры:
# - Backend + Frontend → MUST spawn 2 teammates concurrently
# - Миграция → MUST sequential, lead only
# - Мелкая задача / баг / "как устроено X" → MUST действуй сразу, без уточнений
# - Counter / pending /docs / pending /learn → soft reminders в stderr (как раньше)
set -uo pipefail

# Получить prompt из stdin (JSON)
input=$(cat)
prompt=$(echo "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("prompt",""))' 2>/dev/null)

[ -z "$prompt" ] && exit 0

lower=$(echo "$prompt" | tr '[:upper:]' '[:lower:]')
prompt_len=${#prompt}

directives=""

# --- Backend signal ---
backend_match=0
echo "$lower" | grep -qE '(endpoint|api|роутер|router|сервис|service|модел|model|миграц|alembic|схем|schema|backend|бэкенд|бекенд|pytest|тест бэк)' && backend_match=1

# --- Frontend signal ---
frontend_match=0
echo "$lower" | grep -qE '(страниц|page|компонент|component|форм|form|таблиц|table|кнопк|button|ui|ux|интерфейс|frontend|фронт|react|next|tsx|vitest|preview)' && frontend_match=1

# --- Migration signal ---
migration_match=0
echo "$lower" | grep -qE '(миграц|alembic|alter table|новая колонк|drop column|новое поле|add column)' && migration_match=1

# --- Trivial / "explain" signal (no clarification needed) ---
trivial_match=0
echo "$lower" | grep -qE '^(как устроен|что делает|где находит|покажи код|объясни|почему|why|where is|show me|explain)' && trivial_match=1
[ "$prompt_len" -lt 60 ] && trivial_match=1

# === DIRECTIVE 1: Backend + Frontend → MUST parallel ===
if [ "$backend_match" -eq 1 ] && [ "$frontend_match" -eq 1 ] && [ "$migration_match" -eq 0 ]; then
    directives="${directives}**MANDATORY PARALLELISM:** This task touches BOTH backend AND frontend. You MUST:
1. Lead does Phase 1 sequentially (Model → Migration → Schema if needed).
2. Then spawn 2 teammates concurrently IN ONE MESSAGE with isolation:worktree, run_in_background:true:
   - Backend teammate: backend/, migrations/, tests/
   - Frontend teammate: frontend-react/src/, frontend-react/tests/
3. Each teammate gets relative-path constraint, types-first rule, 40k token budget.
4. NO sequential. NO «I'll do backend first then frontend». Spawn BOTH at the same turn.

"
fi

# === DIRECTIVE 2: Migration → MUST sequential lead-only ===
if [ "$migration_match" -eq 1 ]; then
    directives="${directives}**MIGRATION RULE:** Alembic migrations are sequential, lead-only. NEVER parallel. Test: upgrade head && downgrade -1 && upgrade head before commit.

"
fi

# === DIRECTIVE 3: Trivial → no clarification ===
if [ "$trivial_match" -eq 1 ]; then
    directives="${directives}**SHORT/TRIVIAL REQUEST:** Act immediately. Do NOT ask clarifying questions. Read code, find answer, respond. The clarification budget is 0 for this turn.

"
fi

# === EMIT JSON additionalContext (this becomes a system-reminder) ===
if [ -n "$directives" ]; then
    python3 <<PYEOF
import json
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": """$directives"""
    }
}))
PYEOF
fi

# === SOFT REMINDERS via stderr (counter, pending docs/learn) ===
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
DOCS_PENDING="$ROOT_DIR/.claude/.pending-docs.log"
if [ -f "$DOCS_PENDING" ] && [ -s "$DOCS_PENDING" ]; then
    docs_count=$(wc -l < "$DOCS_PENDING" | tr -d ' ')
    : "${docs_count:=0}"
    if [ "$docs_count" -gt 0 ]; then
        echo "[DOCS] $docs_count pending коммит(ов) без синка документации — запусти /docs" >&2
    fi
fi

# /compact session counter
COUNTER_FILE="$(dirname "$0")/../../.claude/.session-counter"
count=0
if [ -f "$COUNTER_FILE" ]; then
    count=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
    : "${count:=0}"
fi
count=$((count + 1))
echo "$count" > "$COUNTER_FILE"

case "$count" in
    30) echo "[COMPACT] Сессия ~30 prompts — рекомендую /compact для сохранения cache hit rate" >&2 ;;
    50) echo "[COMPACT] Сессия ~50 prompts — СИЛЬНО рекомендую /compact" >&2 ;;
    80) echo "[COMPACT] ⚠ Сессия ~80 prompts — /compact или /clear, риск context overflow" >&2 ;;
esac

# Speculative explore (opt-in)
if [ "$prompt_len" -gt 30 ]; then
    SCRIPT_DIR="$(dirname "$0")"
    PREWARM="$SCRIPT_DIR/prewarm-spawn.sh"
    if [ -x "$PREWARM" ]; then
        bash "$PREWARM" "$prompt" 2>&1 || true
    fi
fi

exit 0

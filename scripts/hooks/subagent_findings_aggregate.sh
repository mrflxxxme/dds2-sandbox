#!/usr/bin/env bash
# SubagentStop hook — лёгкий breadcrumb завершения субагентов.
# Пишет строку в .claude/.last-review.log (append, cap ~200 строк).
# Схема stdin SubagentStop не гарантирована → парсим оборонительно, всегда exit 0.
# Ценность умеренная (observability); не блокирует ничего.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LOG="$ROOT_DIR/.claude/.last-review.log"

input=$(cat 2>/dev/null || true)
: "${input:=}"

# Best-effort извлечение метки субагента (имена полей могут отличаться между версиями).
label=$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    if not isinstance(d, dict):
        raise ValueError
except Exception:
    print(""); sys.exit(0)
for k in ("subagent_type", "agent_type", "subagentType", "label", "name", "description"):
    v = d.get(k)
    if v:
        print(str(v)[:80]); sys.exit(0)
print("")
' 2>/dev/null || true)

ts=$(date '+%Y-%m-%d %H:%M:%S')
echo "$ts | subagent stop${label:+ | $label}" >> "$LOG" 2>/dev/null || true

# Кап лога до последних 200 строк, чтобы не рос бесконечно.
if [ -f "$LOG" ]; then
    tail -n 200 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || rm -f "$LOG.tmp" 2>/dev/null || true
fi

exit 0

#!/bin/bash
# Stop хук: Float check + docs reminder (slim)
set -uo pipefail

# --- Float in models check ---
changed=$(git diff --name-only HEAD 2>/dev/null)
for f in $changed; do
  case "$f" in
    */models/*.py)
      [ -f "$f" ] && grep -qn 'Float' "$f" 2>/dev/null && \
        echo "[DDS2] Float в модели $f — используй Numeric(18,2)" >&2
      ;;
  esac
done

# --- Pending /learn ---
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PENDING="$ROOT_DIR/.claude/.pending-learn.log"
if [ -f "$PENDING" ] && [ -s "$PENDING" ]; then
  echo "[LEARN] $(wc -l < "$PENDING" | tr -d ' ') pending коммит(ов) — запусти /learn" >&2
fi

# --- Docs reminder (lightweight) ---
all_changed=$(git diff --name-only HEAD 2>/dev/null)
if [ -n "$all_changed" ]; then
  svc_changes=$(echo "$all_changed" | grep -c "backend/services/" 2>/dev/null || echo 0)
  docs_updated=$(echo "$all_changed" | grep -cE "(MAP\.md|DOMAIN_|CLAUDE\.md)" 2>/dev/null || echo 0)
  if [ "${svc_changes:-0}" -gt 3 ] && [ "${docs_updated:-0}" -eq 0 ]; then
    echo "[DOCS] $svc_changes файлов в services/ изменено — обнови документацию" >&2
  fi
fi

exit 0

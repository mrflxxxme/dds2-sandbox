#!/bin/bash
# Объединённый Stop хук: Float check + docs reminder
# Заменяет 2 отдельных хука → 1
set -uo pipefail

# --- Float in models check ---
changed=$(git diff --name-only 2>/dev/null)
if [ -z "$changed" ]; then
  exit 0
fi

issues=0
for f in $changed; do
  case "$f" in
    */models/*.py)
      if [ -f "$f" ]; then
        if grep -qn 'Float' "$f" 2>/dev/null; then
          echo "[DDS2] Float в модели $f — используй Numeric(18,2)" >&2
          issues=$((issues+1))
        fi
      fi
      ;;
  esac
done

if [ $issues -gt 0 ]; then
  echo "[DDS2] $issues проблем найдено" >&2
fi

# --- Pending /learn notification ---
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PENDING="$ROOT_DIR/.claude/.pending-learn.log"
if [ -f "$PENDING" ] && [ -s "$PENDING" ]; then
  pending_count=$(wc -l < "$PENDING" | tr -d ' ')
  if [ "$pending_count" -gt 0 ]; then
    echo "[LEARN] $pending_count pending коммит(ов) для рефлексии — запусти /learn (или /docs && /learn)" >&2
  fi
fi

# --- Docs reminder for new AND changed files ---
all_changed=$(git diff --name-only HEAD 2>/dev/null)
new_files=$(git diff --name-only --diff-filter=A HEAD 2>/dev/null)
if [ -n "$all_changed" ]; then
  hints=''
  docs_updated=0

  # Check if docs were updated in this diff
  echo "$all_changed" | grep -qE "(MAP\.md|DOMAIN_|CLAUDE\.md)" && docs_updated=1

  for f in $new_files; do
    case "$f" in
      backend/models/*.py) hints="$hints\n[DOCS] Новая модель $f — обнови DOMAIN_*.md и SOFT_MODELS" ;;
      backend/services/*.py|backend/services/*/*.py) hints="$hints\n[DOCS] Новый сервис $f — обнови MAP.md и DOMAIN_*.md" ;;
      backend/routers/*.py) hints="$hints\n[DOCS] Новый роутер $f — обнови DOMAIN_*.md" ;;
      migrations/versions/*.py) hints="$hints\n[DOCS] Новая миграция $f — обнови DOMAIN_*.md" ;;
    esac
  done

  # Check for significant service changes (not just new files)
  svc_changes=$(echo "$all_changed" | grep "backend/services/" 2>/dev/null | wc -l | tr -d ' \n')
  : "${svc_changes:=0}"
  if [ "$svc_changes" -gt 3 ] && [ "$docs_updated" -eq 0 ]; then
    hints="$hints\n[DOCS] Изменено $svc_changes файлов в services/ но документация не обновлена!"
  fi

  if [ -n "$hints" ]; then
    echo -e "$hints" >&2
    echo '[DOCS] Обнови документацию: MAP.md, DOMAIN_*.md, CLAUDE.md' >&2
  fi
fi

exit 0

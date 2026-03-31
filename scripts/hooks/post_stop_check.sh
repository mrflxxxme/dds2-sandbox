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

# --- Docs reminder for new files ---
new_files=$(git diff --name-only --diff-filter=A HEAD 2>/dev/null)
if [ -n "$new_files" ]; then
  hints=''
  for f in $new_files; do
    case "$f" in
      backend/models/*.py) hints="$hints\n[DOCS] Новая модель $f — обнови DOMAIN_*.md и проверь SOFT_MODELS в check_conventions.sh" ;;
      backend/services/*.py) hints="$hints\n[DOCS] Новый сервис $f — обнови DOMAIN_*.md" ;;
      backend/routers/*.py) hints="$hints\n[DOCS] Новый роутер $f — обнови DOMAIN_*.md" ;;
      migrations/versions/*.py) hints="$hints\n[DOCS] Новая миграция $f — обнови DOMAIN_*.md" ;;
    esac
  done
  if [ -n "$hints" ]; then
    echo -e "$hints" >&2
    echo '[DOCS] Запусти /docs для автообновления документации' >&2
  fi
fi

exit 0

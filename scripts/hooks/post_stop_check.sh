#!/bin/bash
# Объединённый Stop хук: Float check + docs reminder
# Заменяет 2 отдельных хука → 1
set -uo pipefail

# --- Float in models check (только если есть незакомиченные изменения) ---
changed=$(git diff --name-only 2>/dev/null)
issues=0
[ -z "$changed" ] && changed=""
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
      backend/services/*.py|backend/services/*/*.py)
        hints="$hints\n[DOCS] Новый сервис $f — обнови MAP.md и DOMAIN_*.md"
        # Auto-create DOMAIN_*.md skeleton если домен новый
        svc_name=$(basename "$f" .py | sed 's/_service$//')
        domain_upper=$(echo "$svc_name" | tr '[:lower:]' '[:upper:]')
        domain_file="$ROOT_DIR/backend/DOMAIN_${domain_upper}.md"
        if [ ! -f "$domain_file" ] && [ -d "$ROOT_DIR/backend" ]; then
          cat > "$domain_file" <<DOMEOF
# Domain: ${domain_upper}

> Auto-generated skeleton by post_stop_check hook. Заполни секции и удали этот блок.

## Назначение
TODO: краткое описание домена и ответственности

## Архитектура
\`\`\`
Вопрос/действие пользователя
    ↓
routers/${svc_name}.py — HTTP endpoints
    ↓
services/${svc_name}_service.py — бизнес-логика
    ↓
models/ — ORM
\`\`\`

## Ownership
Файлы этого домена:
- \`services/${svc_name}_service.py\` — TODO: описание
- TODO: остальные файлы

## Ключевые модели
- TODO: основные сущности

## Бизнес-правила
- TODO: ключевые инварианты

## Кэш
- TODO: prefix кэша если используется + invalidate_project_reports() если нужно

## Известные грабли
- TODO

## Тесты
- \`tests/test_${svc_name}.py\`
DOMEOF
          hints="$hints\n[DOCS] Создан skeleton: backend/DOMAIN_${domain_upper}.md — заполни и удали авто-генерированный блок"
        fi
        ;;
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
    echo '[DOCS] Обнови документацию: MAP.md, DOMAIN_*.md, CLAUDE.md (atomic в этом же коммите)' >&2

    # Запись в pending-docs.log — UserPromptSubmit hook покажет на следующем prompt
    PENDING_DOCS="$ROOT_DIR/.claude/.pending-docs.log"
    timestamp=$(date +%s)
    echo "$timestamp $(echo "$all_changed" | head -1)" >> "$PENDING_DOCS"
  fi

  # Auto-clear pending-docs если docs обновлены в этом diff
  if [ "$docs_updated" -eq 1 ]; then
    PENDING_DOCS="$ROOT_DIR/.claude/.pending-docs.log"
    [ -f "$PENDING_DOCS" ] && : > "$PENDING_DOCS"
  fi
fi

exit 0

#!/bin/bash
# Объединённый PreToolUse хук: Bash safety + .env/.credentials блокировка
# Заменяет 2 отдельных хука → 1
set -uo pipefail  # не -e: пустой grep на не-Bash payload не должен ронять хук ДО .env-проверки (был fail-open)

input=$(cat)

# --- Bash command safety ---
cmd=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)
if [ -n "$cmd" ]; then
  if echo "$cmd" | grep -qE '(--no-verify|--no-gpg-sign)'; then
    echo 'BLOCKED: --no-verify и --no-gpg-sign запрещены.' >&2; exit 2
  fi
  # рекурсивно-форсовое удаление в любом порядке/форме флагов, без требования '/'
  # (rm -rf, rm -fr, rm -r -f, rm --recursive --force, rm -rf node_modules, rm -rf .)
  if echo "$cmd" | grep -qiE '\brm\b[^;|&]*(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|-[a-z]*r[a-z]*[[:space:]]+-[a-z]*f|-[a-z]*f[a-z]*[[:space:]]+-[a-z]*r|--recursive|--force)'; then
    echo 'BLOCKED: rm -rf запрещён. Удаляй файлы по одному.' >&2; exit 2
  fi
  if echo "$cmd" | grep -qiE '(DROP\s+TABLE|DROP\s+DATABASE|TRUNCATE)'; then
    echo 'BLOCKED: DROP/TRUNCATE запрещены. Используй миграции.' >&2; exit 2
  fi
  if echo "$cmd" | grep -qE 'git\s+push\s+.*--force'; then
    echo 'BLOCKED: git push --force запрещён.' >&2; exit 2
  fi
  if echo "$cmd" | grep -qE 'git\s+reset\s+--hard'; then
    echo 'BLOCKED: git reset --hard запрещён. Используй git stash.' >&2; exit 2
  fi
fi

# --- .env / credentials file access ---
file=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)
if [ -n "$file" ]; then
  base=$(basename "$file")
  if echo "$base" | grep -qE '^\.env($|\.)'; then
    echo 'BLOCKED: Доступ к .env файлам запрещён. Секреты только через переменные окружения.' >&2; exit 2
  fi
  if echo "$base" | grep -qiE '(credentials|secrets|private.key)'; then
    echo 'BLOCKED: Доступ к файлам с секретами запрещён.' >&2; exit 2
  fi
fi

exit 0

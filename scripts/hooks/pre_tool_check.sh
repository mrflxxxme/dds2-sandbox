#!/bin/bash
# Объединённый PreToolUse хук: Bash safety + .env/.credentials блокировка
# Заменяет 2 отдельных хука → 1
set -euo pipefail

input=$(cat)

# --- Bash command safety ---
cmd=$(echo "$input" | grep -o '"command":"[^"]*"' | head -1 | sed 's/"command":"//;s/"$//')
if [ -n "$cmd" ]; then
  if echo "$cmd" | grep -qE '(--no-verify|--no-gpg-sign)'; then
    echo 'BLOCKED: --no-verify и --no-gpg-sign запрещены.' >&2; exit 2
  fi
  if echo "$cmd" | grep -qE 'rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|--force).*/' ; then
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
file=$(echo "$input" | grep -oE '"file_path":"[^"]*"' | head -1 | sed 's/"file_path":"//;s/"$//')
if [ -n "$file" ]; then
  base=$(basename "$file")
  if echo "$base" | grep -qE '^\\.env($|\\.)'; then
    echo 'BLOCKED: Доступ к .env файлам запрещён. Секреты только через переменные окружения.' >&2; exit 2
  fi
  if echo "$base" | grep -qiE '(credentials|secrets|private.key)'; then
    echo 'BLOCKED: Доступ к файлам с секретами запрещён.' >&2; exit 2
  fi
fi

exit 0

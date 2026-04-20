#!/usr/bin/env bash
# Speculative explore V1 (минимальный)
# Запускает фоновый haiku-агент для pre-warm контекста по промпту
#
# Триггер: вызывается из prompt-team-detect.sh при наличии тегов
# [TEAM]/[PLAN]/[MIGRATION]/[REFACTOR]
#
# Anti-recursion: env DDS_PREWARM_ACTIVE=1 → exit 0
# Защита от мусора: чистит .claude/.cache/spec-*.md старше 24h
# Lock: .claude/.cache/.lock-{hash} с PID, max 3 одновременно
#
# Ограничения V1:
#  - Нет JSON метрик (для V2)
#  - Простой keyword matching, без классификации
#  - Discovery — только через stderr hint (агент сам решает читать)
set -uo pipefail

# Anti-recursion
if [ "${DDS_PREWARM_ACTIVE:-0}" = "1" ]; then
    exit 0
fi

# Кооп-вариант: opt-in за неделю до full enable
if [ "${DDS_PREWARM_ENABLED:-0}" != "1" ]; then
    exit 0
fi

PROMPT="${1:-}"
[ -z "$PROMPT" ] && exit 0

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
CACHE_DIR="$ROOT_DIR/.claude/.cache"
mkdir -p "$CACHE_DIR"

# Cleanup старого мусора (>24h, max 50 файлов)
find "$CACHE_DIR" -maxdepth 1 -name "spec-*.md" -mtime +1 -delete 2>/dev/null || true
find "$CACHE_DIR" -maxdepth 1 -name ".lock-*" -mtime +1 -delete 2>/dev/null || true

# Лимит параллельных задач
running=$(find "$CACHE_DIR" -maxdepth 1 -name ".lock-*" 2>/dev/null | wc -l | tr -d ' ')
[ "$running" -ge 3 ] && exit 0

# Hash промпта (детерминированный, sha1[:12])
HASH=$(printf '%s' "$PROMPT" | shasum -a 1 | cut -c1-12)

LOCK="$CACHE_DIR/.lock-$HASH"
SPEC="$CACHE_DIR/spec-$HASH.md"

# Дубликат? Если spec свежий (<5 мин) — переиспользуем
if [ -f "$SPEC" ]; then
    age=$(($(date +%s) - $(stat -f %m "$SPEC" 2>/dev/null || stat -c %Y "$SPEC" 2>/dev/null || echo 0)))
    if [ "$age" -lt 300 ]; then
        echo "[PREWARM] Reusing fresh spec: .claude/.cache/spec-$HASH.md (age=${age}s)" >&2
        exit 0
    fi
fi

# Lock check: PID жив?
if [ -f "$LOCK" ]; then
    pid=$(cat "$LOCK" 2>/dev/null || echo 0)
    if [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null; then
        exit 0  # уже работает
    fi
    rm -f "$LOCK"
fi

# Проверка что claude CLI доступен
if ! command -v claude >/dev/null 2>&1; then
    exit 0
fi

# Spawn haiku в фоне
(
    echo $$ > "$LOCK"
    trap 'rm -f "$LOCK"' EXIT

    PROMPT_TEMPLATE="Ты — Context Mapper для DDS2 (управленческий учёт e-commerce).

Промпт пользователя: \"$PROMPT\"

Задача за <=6 turns:
1. Определи 1-3 затронутых домена (см. таблицу доменов в CLAUDE.md)
2. Прочитай соответствующие backend/DOMAIN_*.md файлы
3. Найди 2-3 наиболее похожих файла через Glob/Grep
4. Выпиши их сигнатуры (имена функций/классов, не содержимое целиком)
5. Запиши результат в файл: $SPEC

Формат spec файла:
# Context Map: <короткое описание задачи>

## Domains
- <domain>: <почему относится>

## SimilarFiles
- <path>:<line> — <signature>

## Gotchas
- <known pitfall из DOMAIN_*.md>

## SuggestedTouchpoints
- <file> — <что туда добавить/изменить>

ОГРАНИЧЕНИЯ:
- НИКАКИХ Edit/Write кроме записи в $SPEC
- НИКАКИХ Bash mutations (можно только read-only git/grep)
- НЕ запускай другие skills"

    DDS_PREWARM_ACTIVE=1 timeout 90 claude \
        --print \
        --model haiku \
        --allowed-tools "Read,Glob,Grep,Write" \
        --dangerously-skip-permissions \
        "$PROMPT_TEMPLATE" >/dev/null 2>&1 || true

    rm -f "$LOCK"
) &
disown

echo "[PREWARM] Background context map started: .claude/.cache/spec-$HASH.md (haiku, ~30s)" >&2
exit 0

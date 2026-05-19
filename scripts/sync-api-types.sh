#!/bin/bash
# Генерирует TypeScript типы из FastAPI OpenAPI schema.
#
# Запись идёт в frontend-react/src/types/api.generated.ts (отдельный от ручного api.ts).
# Это намеренно — даёт постепенную миграцию: смотришь diff, переносишь импорты по одному.
#
# Usage:
#   bash scripts/sync-api-types.sh                  # default: http://localhost:8000
#   API_URL=https://app.vyatkin-wb.ru bash scripts/sync-api-types.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${API_URL:-http://localhost:8000}"
OUT_FILE="$PROJECT_ROOT/frontend-react/src/types/api.generated.ts"
SCHEMA_FILE="$PROJECT_ROOT/frontend-react/openapi.json"

cd "$PROJECT_ROOT/frontend-react"

# 1. Проверка что openapi-typescript установлен
if ! npx --no openapi-typescript --version >/dev/null 2>&1; then
  echo "Installing openapi-typescript as devDependency..."
  npm install --save-dev openapi-typescript
fi

# 2. Загружаем свежий OpenAPI schema
echo "Fetching OpenAPI schema from $API_URL..."
if ! curl -sf "$API_URL/openapi.json" -o "$SCHEMA_FILE"; then
  echo "ERROR: не удалось получить $API_URL/openapi.json"
  echo "Запусти 'make dev' или укажи API_URL=https://..."
  exit 1
fi

SCHEMA_SIZE=$(wc -c < "$SCHEMA_FILE" | tr -d ' ')
echo "  schema size: $SCHEMA_SIZE bytes"

# 3. Генерация TS типов
echo "Generating TS types into $OUT_FILE..."
npx openapi-typescript "$SCHEMA_FILE" -o "$OUT_FILE"

# 4. Префикс с предупреждением
TMP=$(mktemp)
cat > "$TMP" <<'HEADER'
/**
 * AUTO-GENERATED — DO NOT EDIT MANUALLY.
 * Source: backend FastAPI OpenAPI schema.
 * Regenerate: make sync-types
 */

HEADER
cat "$OUT_FILE" >> "$TMP"
mv "$TMP" "$OUT_FILE"

GEN_LINES=$(wc -l < "$OUT_FILE" | tr -d ' ')
MANUAL_LINES=$(wc -l < "$PROJECT_ROOT/frontend-react/src/types/api.ts" 2>/dev/null | tr -d ' ' || echo "0")

echo ""
echo "Done."
echo "  generated:  $OUT_FILE ($GEN_LINES lines)"
echo "  manual:     frontend-react/src/types/api.ts ($MANUAL_LINES lines)"
echo ""
echo "Next steps:"
echo "  1. Сравни типы: diff <(head -200 frontend-react/src/types/api.ts) <(head -200 $OUT_FILE)"
echo "  2. В новых модулях импортируй из './api.generated' вместо './api'"
echo "  3. Постепенно мигрируй существующие импорты"

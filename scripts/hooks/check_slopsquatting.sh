#!/usr/bin/env bash
# Slopsquatting защита — проверяет новые импорты (pip / npm) против публичных реестров
#
# AI-агенты галлюцинируют имена пакетов (21.7% open-source LLM, 5% commercial).
# Атакующие регистрируют эти fake имена → supply chain атака.
#
# Запуск:
#   - pre-push hook (уже подключено через scripts/hooks/pre-push)
#   - ручной: bash scripts/hooks/check_slopsquatting.sh
#
# Выход:
#   0 — все импорты валидны
#   1 — найдено ≥1 подозрительный пакет (блокирует push)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

# Whitelist — стандартная библиотека + известные локальные/алиасы
STD_PY="re os sys json time datetime math random collections typing itertools functools pathlib asyncio logging dataclasses enum decimal abc contextlib uuid hashlib base64 urllib http warnings threading subprocess tempfile shutil io csv argparse"
LOCAL_PREFIXES="backend tests scripts migrations frontend"

NEW_FAILURES=0

# 1. Python imports — искать в staged/diff vs origin/main
PY_DIFF=$(git diff --cached --diff-filter=AM -- '*.py' 2>/dev/null | grep -E '^\+(import |from )' | sed 's/^+//' || true)

if [ -n "$PY_DIFF" ]; then
  # Извлечь top-level package name
  PY_PKGS=$(echo "$PY_DIFF" | awk '{
    if ($1 == "import") print $2;
    else if ($1 == "from") print $2;
  }' | awk -F. '{print $1}' | sort -u)

  for pkg in $PY_PKGS; do
    [ -z "$pkg" ] && continue

    # Skip stdlib
    if echo " $STD_PY " | grep -q " $pkg "; then continue; fi

    # Skip локальные модули (backend.*, tests.*, etc.)
    skip=false
    for prefix in $LOCAL_PREFIXES; do
      if [ "$pkg" = "$prefix" ]; then skip=true; break; fi
    done
    $skip && continue

    # Skip relative imports
    if [ "$pkg" = "" ] || [ "$pkg" = "." ]; then continue; fi

    # Skip если пакет уже в requirements/pyproject
    if grep -qE "^${pkg}($|[<>=])" backend/requirements.txt 2>/dev/null || \
       grep -qE "\"${pkg}\"" pyproject.toml 2>/dev/null; then
      continue
    fi

    # Проверить в PyPI
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" "https://pypi.org/pypi/${pkg}/json" --max-time 5 || echo "000")
    if [ "$http_code" != "200" ]; then
      echo "🚨 SUSPICIOUS Python import: '$pkg' не найден в PyPI (HTTP $http_code)"
      echo "   Возможная AI-галлюцинация или опечатка. Проверь имя пакета."
      NEW_FAILURES=$((NEW_FAILURES + 1))
    fi
  done
fi

# 2. TypeScript/JS imports — только новые в package.json
if git diff --cached --name-only | grep -q 'package.json$'; then
  # Новые зависимости — грубый diff
  NEW_DEPS=$(git diff --cached -- '*/package.json' 2>/dev/null | \
    grep -E '^\+\s*"[^"]+":\s*"[\^~]?[0-9]' | \
    sed -E 's/^\+\s*"([^"]+)".*/\1/' | sort -u || true)

  for pkg in $NEW_DEPS; do
    [ -z "$pkg" ] && continue
    # Scoped packages: @anthropic/foo → проверяем как есть
    encoded=$(echo "$pkg" | sed 's|/|%2F|g')
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" "https://registry.npmjs.org/${encoded}" --max-time 5 || echo "000")
    if [ "$http_code" != "200" ]; then
      echo "🚨 SUSPICIOUS npm package: '$pkg' не найден в npm registry (HTTP $http_code)"
      echo "   Возможная AI-галлюцинация или опечатка. Проверь имя пакета."
      NEW_FAILURES=$((NEW_FAILURES + 1))
    fi
  done
fi

if [ "$NEW_FAILURES" -gt 0 ]; then
  echo ""
  echo "❌ Slopsquatting check failed: $NEW_FAILURES подозрительных пакетов"
  echo "   Причина: AI может галлюцинировать имена (21.7% для open-source LLM)"
  echo "   Если пакет легитимный — добавь в requirements.txt/package.json перед импортом"
  exit 1
fi

exit 0

#!/usr/bin/env bash
# Установка git hooks для DDS
# Запуск: bash scripts/setup-hooks.sh
# Или:    make setup

set -e

HOOKS_DIR=".git/hooks"
SCRIPTS_DIR="scripts/hooks"

echo "🔧 Установка git hooks..."

# pre-commit (через pre-commit framework)
if command -v pre-commit &> /dev/null; then
    pre-commit install
    echo "  ✓ pre-commit установлен (pre-commit framework)"
else
    echo "  ⚠ pre-commit не найден — установи: pip install pre-commit && pre-commit install"
fi

# pre-push
if [ -f "$SCRIPTS_DIR/pre-push" ]; then
    cp "$SCRIPTS_DIR/pre-push" "$HOOKS_DIR/pre-push"
    chmod +x "$HOOKS_DIR/pre-push"
    echo "  ✓ pre-push установлен (pytest + vitest + conventions)"
else
    echo "  ✗ $SCRIPTS_DIR/pre-push не найден"
fi

echo ""
echo "✅ Git hooks установлены"
echo "   pre-commit: ruff, gitleaks, bandit, conventions"
echo "   pre-push:   pytest(testmon) + vitest + conventions"
echo ""
echo "   Пропустить: git push --no-verify (только экстренно)"

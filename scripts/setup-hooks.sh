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

# post-commit (tracker для /learn + auto-push dev)
cat > "$HOOKS_DIR/post-commit" <<'EOF'
#!/bin/bash
# Post-commit: tracker для /learn + auto-push dev
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

# 1) Track commit для последующего /learn (Stop hook уведомит пользователя)
if [ -x "$ROOT_DIR/scripts/hooks/post-commit-track.sh" ]; then
    bash "$ROOT_DIR/scripts/hooks/post-commit-track.sh" 2>/dev/null || true
fi

# 2) Auto-push dev branch
BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" = "dev" ]; then
    git push origin dev 2>/dev/null &
fi
EOF
chmod +x "$HOOKS_DIR/post-commit"
echo "  ✓ post-commit установлен (track для /learn + auto-push dev)"

echo ""
echo "✅ Git hooks установлены"
echo "   pre-commit:  ruff, gitleaks, bandit, conventions"
echo "   pre-push:    pytest(testmon) + vitest + conventions"
echo "   post-commit: tracker pending → Stop hook напомнит /learn"
echo ""
echo "   Пропустить: git push --no-verify (только экстренно)"

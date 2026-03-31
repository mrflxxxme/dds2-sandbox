#!/bin/bash
# Usage: bash scripts/worktree-start.sh <feature-name>
# Creates isolated worktrees for backend and frontend agents
# Symlinks .claude and copies .env into each worktree

set -euo pipefail

FEATURE="${1:?Usage: bash scripts/worktree-start.sh <feature-name>}"
BASE=".worktrees"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$PROJECT_ROOT/$BASE"

echo "Creating worktrees for feature: $FEATURE"

# Create backend worktree
git worktree add "$PROJECT_ROOT/$BASE/$FEATURE-backend" -b "feat/$FEATURE-backend" 2>/dev/null || {
  echo "Branch feat/$FEATURE-backend already exists, using it"
  git worktree add "$PROJECT_ROOT/$BASE/$FEATURE-backend" "feat/$FEATURE-backend"
}

# Create frontend worktree
git worktree add "$PROJECT_ROOT/$BASE/$FEATURE-frontend" -b "feat/$FEATURE-frontend" 2>/dev/null || {
  echo "Branch feat/$FEATURE-frontend already exists, using it"
  git worktree add "$PROJECT_ROOT/$BASE/$FEATURE-frontend" "feat/$FEATURE-frontend"
}

# Symlink .claude config into each worktree
for wt in "$BASE/$FEATURE-backend" "$BASE/$FEATURE-frontend"; do
  target="$PROJECT_ROOT/$wt"

  # Symlink .claude (settings, agents, commands, rules)
  if [ -d "$PROJECT_ROOT/.claude" ]; then
    ln -sfn "$PROJECT_ROOT/.claude" "$target/.claude"
    echo "  .claude → symlinked"
  fi

  # Copy .env files (kovoor/git-hooks does this too, but just in case)
  for envfile in "$PROJECT_ROOT"/.env*; do
    if [ -f "$envfile" ]; then
      cp "$envfile" "$target/"
      echo "  $(basename $envfile) → copied"
    fi
  done
done

echo ""
echo "Worktrees ready:"
echo "  Backend:  $BASE/$FEATURE-backend"
echo "  Frontend: $BASE/$FEATURE-frontend"
echo ""
echo "Start agents in cmux:"
echo "  Tab 1: cd $BASE/$FEATURE-backend && claude"
echo "  Tab 2: cd $BASE/$FEATURE-frontend && claude"
echo ""
echo "When done: bash scripts/worktree-finish.sh $FEATURE"

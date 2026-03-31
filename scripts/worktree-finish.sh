#!/bin/bash
# Usage: bash scripts/worktree-finish.sh <feature-name>
# Merges backend+frontend worktree branches into dev and cleans up

set -euo pipefail

FEATURE="${1:?Usage: bash scripts/worktree-finish.sh <feature-name>}"
BASE=".worktrees"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$PROJECT_ROOT"

echo "Finishing feature: $FEATURE"

# Check for uncommitted changes in worktrees
for suffix in backend frontend; do
  wt="$BASE/$FEATURE-$suffix"
  if [ -d "$wt" ]; then
    changes=$(git -C "$wt" status --porcelain 2>/dev/null | head -5)
    if [ -n "$changes" ]; then
      echo ""
      echo "WARNING: Uncommitted changes in $wt:"
      echo "$changes"
      echo ""
      read -p "Commit them with auto-message? [y/N] " answer
      if [[ "$answer" =~ ^[Yy] ]]; then
        git -C "$wt" add -A
        git -C "$wt" commit -m "feat: $FEATURE $suffix (auto-commit before merge)"
      else
        echo "Aborting. Commit or discard changes first."
        exit 1
      fi
    fi
  fi
done

# Make sure we're on dev
current_branch=$(git branch --show-current)
if [ "$current_branch" != "dev" ]; then
  echo "Switching to dev branch..."
  git checkout dev
fi

# Merge backend branch
if git rev-parse --verify "feat/$FEATURE-backend" >/dev/null 2>&1; then
  echo "Merging feat/$FEATURE-backend..."
  git merge "feat/$FEATURE-backend" --no-edit || {
    echo "MERGE CONFLICT in backend. Resolve manually, then run:"
    echo "  git merge --continue"
    echo "  bash scripts/worktree-finish.sh $FEATURE  # to continue with frontend"
    exit 1
  }
fi

# Merge frontend branch
if git rev-parse --verify "feat/$FEATURE-frontend" >/dev/null 2>&1; then
  echo "Merging feat/$FEATURE-frontend..."
  git merge "feat/$FEATURE-frontend" --no-edit || {
    echo "MERGE CONFLICT in frontend. Resolve manually."
    exit 1
  }
fi

# Remove worktrees
for suffix in backend frontend; do
  wt="$BASE/$FEATURE-$suffix"
  branch="feat/$FEATURE-$suffix"
  if [ -d "$wt" ]; then
    echo "Removing worktree: $wt"
    git worktree remove "$wt" --force 2>/dev/null || true
  fi
  if git rev-parse --verify "$branch" >/dev/null 2>&1; then
    echo "Deleting branch: $branch"
    git branch -D "$branch" 2>/dev/null || true
  fi
done

# Clean up empty .worktrees dir
rmdir "$BASE" 2>/dev/null || true

echo ""
echo "Feature $FEATURE merged into dev."
echo "Run: /verify to check everything works."

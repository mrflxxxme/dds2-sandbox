#!/bin/bash
# TeammateIdle — slim quality gate (2026-05-19).
# Проверяет только iron rules + worktree abs-path leak.
# Убрано: types-first detection, полный check_conventions.sh — heavy и редко полезно.
# Возврат: `git show HEAD~1:scripts/hooks/teammate_idle_check.sh > scripts/hooks/teammate_idle_check.sh`
set -uo pipefail

# Только в worktree (gitfile), не main репо
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
[ -d .git ] && exit 0
[ -f .git ] || exit 0

changed=$(git diff --name-only HEAD 2>/dev/null)
staged=$(git diff --name-only --cached 2>/dev/null)
worktree_base=$(git merge-base HEAD origin/dev 2>/dev/null || git merge-base HEAD main 2>/dev/null || echo "")
if [ -n "$worktree_base" ]; then
    committed=$(git diff --name-only "$worktree_base" HEAD 2>/dev/null)
else
    committed=""
fi
all_changed=$(printf "%s\n%s\n%s\n" "$changed" "$staged" "$committed" | sort -u | grep -v '^$' || true)
[ -z "$all_changed" ] && exit 0

issues=""
issue_count=0

# Iron rule violations в backend/
backend_py=$(echo "$all_changed" | grep -E '^backend/.*\.py$' || true)
for f in $backend_py; do
    [ ! -f "$f" ] && continue

    if echo "$f" | grep -q '/models/' && grep -qnE '^\s*[a-z_]+\s*=\s*Column\(.*Float' "$f" 2>/dev/null; then
        issues="${issues}\n[IRON] $f: Float в модели — Numeric(18,2)"
        issue_count=$((issue_count+1))
    fi

    if grep -qnE 'datetime\.utcnow\(\)' "$f" 2>/dev/null; then
        issues="${issues}\n[IRON] $f: datetime.utcnow() — backend.utils.time.utcnow"
        issue_count=$((issue_count+1))
    fi

    if grep -nE '\bdb\.delete\(' "$f" 2>/dev/null | grep -qv 'soft_delete\|# ok-hard-delete'; then
        issues="${issues}\n[IRON] $f: db.delete() — soft_delete() или комментарий # ok-hard-delete"
        issue_count=$((issue_count+1))
    fi

    if grep -qnE 'text\(f["\x27]' "$f" 2>/dev/null; then
        issues="${issues}\n[IRON] $f: f-string в text() — :param + bindparams"
        issue_count=$((issue_count+1))
    fi
done

# Worktree abs-path leak
for f in $(echo "$all_changed" | grep -vE '\.(md|lock|log)$' | head -50); do
    [ ! -f "$f" ] && continue
    if grep -lF '/Users/vlad/Desktop/dds2' "$f" >/dev/null 2>&1; then
        issues="${issues}\n[WORKTREE] $f: абсолютный путь /Users/vlad/Desktop/dds2 — worktree leak"
        issue_count=$((issue_count+1))
    fi
done

if [ "$issue_count" -gt 0 ]; then
    echo "[TEAMMATE-IDLE] Найдено $issue_count нарушений:" >&2
    echo -e "$issues" >&2
    echo "" >&2
    echo "[TEAMMATE-IDLE] Исправь. 2× pre-commit fail на одном файле → fail-fast." >&2
    exit 2
fi

exit 0

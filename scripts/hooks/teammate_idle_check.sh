#!/bin/bash
# TeammateIdle hook — quality gate перед тем как worktree-teammate отчитается idle.
#
# Срабатывает когда teammate (запущенный через Agent(isolation: worktree)) собирается
# вернуть управление lead-агенту. Exit code 2 → Claude Code возвращает stderr teammate'у
# как feedback, teammate продолжает работу. Exit 0 → teammate idle как обычно.
#
# Цель: ловить нарушения iron rules (project_id, is_deleted, Float, f-string в SQL)
# и ошибки конвенций ДО merge worktree в main. Сейчас это всплывает в pre-push после
# слияния — фиксить дороже.
#
# Запускается в CWD = worktree teammate'а (Claude Code устанавливает рабочий каталог
# перед hook). Не использует абсолютные пути проекта — всё относительно CWD.
set -uo pipefail

# Hook должен работать ТОЛЬКО в git worktree teammate'а, не в main репо.
# В worktree `.git` — это файл-ссылка (gitfile), в main репо — директория.
# Если запуск из main репо (lead-агент / hook misfire) — silent ok.
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
[ -d .git ] && exit 0  # main репо — пропускаем
[ -f .git ] || exit 0  # не worktree и не main — пропускаем

# Что изменил teammate (uncommitted + staged + committed в ветке worktree)
# vs base branch (обычно main или dev). Берём широко — все .py и .ts/.tsx файлы.
changed=$(git diff --name-only HEAD 2>/dev/null)
staged=$(git diff --name-only --cached 2>/dev/null)
# Если teammate коммитил в свою worktree-ветку — diff vs base
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

# --- 1. Iron rule violations в backend/ ---
backend_py=$(echo "$all_changed" | grep -E '^backend/.*\.py$' || true)
for f in $backend_py; do
  [ ! -f "$f" ] && continue

  # Float в моделях
  if echo "$f" | grep -q '/models/' && grep -nE '^\s*[a-z_]+\s*=\s*Column\(.*Float' "$f" 2>/dev/null; then
    issues="${issues}\n[IRON] $f: Float в модели — используй Numeric(18,2)"
    issue_count=$((issue_count+1))
  fi

  # datetime.utcnow() вместо utils.time.utcnow
  if grep -nE 'datetime\.utcnow\(\)' "$f" 2>/dev/null; then
    issues="${issues}\n[IRON] $f: datetime.utcnow() — используй from backend.utils.time import utcnow"
    issue_count=$((issue_count+1))
  fi

  # db.delete() — должен быть soft_delete()
  if grep -nE '\bdb\.delete\(' "$f" 2>/dev/null | grep -v 'soft_delete\|# ok-hard-delete'; then
    issues="${issues}\n[IRON] $f: db.delete() — используй model.soft_delete() (или комментарий # ok-hard-delete)"
    issue_count=$((issue_count+1))
  fi

  # f-string в SQL (text(f"..."))
  if grep -nE 'text\(f["\x27]' "$f" 2>/dev/null; then
    issues="${issues}\n[IRON] $f: f-string в text() SQL — используй :param и bindparams"
    issue_count=$((issue_count+1))
  fi
done

# --- 2. Frontend: types-first нарушения ---
fe_changed=$(echo "$all_changed" | grep -E '^frontend-react/src/' || true)
fe_tests=$(echo "$fe_changed" | grep -E '\.test\.(ts|tsx)$' || true)
fe_types=$(echo "$fe_changed" | grep -E 'types/api\.ts$' || true)
fe_api=$(echo "$fe_changed" | grep -E 'lib/api/' || true)
if [ -n "$fe_tests" ] && [ -z "$fe_types" ] && [ -z "$fe_api" ]; then
  # Тесты есть, types/api нет — возможно tests-first без типов (нарушает constraint #2)
  tests_count=$(echo "$fe_tests" | wc -l | tr -d ' \n')
  if [ "$tests_count" -gt 0 ]; then
    issues="${issues}\n[FE] Тесты изменены ($tests_count), но types/api.ts и lib/api/ нет — types-first нарушен"
    issue_count=$((issue_count+1))
  fi
fi

# --- 3. Абсолютные пути проекта в изменённых файлах (worktree leak) ---
# Если teammate где-то жёстко прописал /Users/a1/Desktop/dds_app — это леж леж потерь worktree-изоляции
for f in $(echo "$all_changed" | grep -vE '\.(md|lock|log)$' | head -50); do
  [ ! -f "$f" ] && continue
  if grep -lF '/Users/a1/Desktop/dds_app' "$f" 2>/dev/null >/dev/null; then
    issues="${issues}\n[WORKTREE] $f: содержит абсолютный путь /Users/a1/Desktop/dds_app — нарушение worktree-изоляции (см. agent_workflow.md constraint #1)"
    issue_count=$((issue_count+1))
  fi
done

# --- 4. Запустить check_conventions.sh если есть backend изменения ---
# Только smart-mode (быстро, без полного pytest) — поймает остаток iron rules
if [ -n "$backend_py" ] && [ -x "scripts/check_conventions.sh" ]; then
  conv_output=$(bash scripts/check_conventions.sh 2>&1 || true)
  if echo "$conv_output" | grep -qE 'ERROR|FAIL|нарушен'; then
    issues="${issues}\n[CONVENTIONS] check_conventions.sh нашёл нарушения:"
    issues="${issues}\n$(echo "$conv_output" | grep -E 'ERROR|FAIL|нарушен' | head -10)"
    issue_count=$((issue_count+1))
  fi
fi

# --- Вывод ---
if [ "$issue_count" -gt 0 ]; then
  echo "[TEAMMATE-IDLE] Найдено $issue_count проблем(ы) до завершения teammate:" >&2
  echo -e "$issues" >&2
  echo "" >&2
  echo "[TEAMMATE-IDLE] Исправь и попробуй снова. Если pre-commit падает 2× на одном файле — fail-fast с отчётом lead-агенту, НЕ крутить цикл (см. lead_agent_v2.md §4 teammate-constraints)." >&2
  exit 2
fi

exit 0

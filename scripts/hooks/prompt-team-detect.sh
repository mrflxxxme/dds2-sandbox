#!/usr/bin/env bash
# UserPromptSubmit hook: детектит тип задачи и подсказывает оптимальный workflow
# - Backend + Frontend → TeamCreate
# - Новая фича → /plan
# - Миграция → последовательность с lead
#
# Не блокирует, только подсказывает в stderr (видно агенту в контексте).
set -uo pipefail

# Получить prompt из stdin (JSON)
input=$(cat)
prompt=$(echo "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("prompt",""))' 2>/dev/null)

[ -z "$prompt" ] && exit 0

# Нижний регистр для матчинга
lower=$(echo "$prompt" | tr '[:upper:]' '[:lower:]')

hints=""

# --- Backend signal ---
backend_match=0
echo "$lower" | grep -qE '(endpoint|api|роутер|router|сервис|service|модел|model|миграц|alembic|схем|schema|backend|бэкенд|бекенд)' && backend_match=1

# --- Frontend signal ---
frontend_match=0
echo "$lower" | grep -qE '(страниц|page|компонент|component|форм|form|таблиц|table|кнопк|button|ui|ux|интерфейс|frontend|фронт|react|next)' && frontend_match=1

# --- Backend + Frontend → TeamCreate ---
if [ "$backend_match" -eq 1 ] && [ "$frontend_match" -eq 1 ]; then
    hints="${hints}[TEAM] Обнаружены признаки backend+frontend задачи. Рекомендация: TeamCreate с параллельными teammates (backend ‖ frontend). См. .claude/rules/agent_workflow.md\n"
fi

# --- Новая фича / модуль → /plan ---
if echo "$lower" | grep -qE '(новая фич|новый модул|новая страниц|новый сервис|новый роутер|добав.* (раздел|модуль|фич|страниц)|implement|build .* feature)'; then
    hints="${hints}[PLAN] Похоже на новую фичу — запусти /plan для уточнения ТЗ ДО кода (memory: feedback_ask_questions)\n"
fi

# --- Миграция → особая последовательность ---
if echo "$lower" | grep -qE '(миграц|alembic|alter table|новая колонк|drop column|новое поле)'; then
    hints="${hints}[MIGRATION] Миграция — ТОЛЬКО lead agent последовательно. Не параллелить. Тестируй: upgrade head && downgrade -1 && upgrade head\n"
fi

# --- Рефакторинг ---
if echo "$lower" | grep -qE '(рефактор|refactor|переписать|extract|разбить на)'; then
    hints="${hints}[REFACTOR] Рефакторинг — один teammate пишет тесты, другой рефакторит (разные файлы). Не трогать один файл двумя агентами\n"
fi

# --- Деструктивные команды (оставлено из старого хука) ---
if echo "$lower" | grep -qE '(удали все|drop all|reset hard|force push|удалить базу)'; then
    hints="${hints}[SAFETY] Обнаружена потенциально разрушительная команда. Подтверди намерение.\n"
fi

if [ -n "$hints" ]; then
    echo -e "$hints" >&2
fi

# --- Speculative explore V1 (opt-in via DDS_PREWARM_ENABLED=1) ---
# Триггерим pre-warm только для нетривиальных задач (есть хоть один тег)
if [ "$backend_match" -eq 1 ] || [ "$frontend_match" -eq 1 ]; then
    SCRIPT_DIR="$(dirname "$0")"
    PREWARM="$SCRIPT_DIR/prewarm-spawn.sh"
    if [ -x "$PREWARM" ]; then
        bash "$PREWARM" "$prompt" 2>&1 || true
    fi
fi

exit 0

---
description: "Фан-аут ревью DDS2: по diff-путям спавнит профильных субагентов (Opus 4.8) параллельно и сводит вердикт."
---

# /review — параллельное ревью по diff

Оживляет 5 профильных субагентов: вместо «модель вспомнит про code-reviewer» — детерминированный фан-аут по тому, что реально изменилось. Работает в любой сессии (через инструмент Agent), без Workflow-tool. Для тяжёлого/ultracode-прогона есть SOTA-версия `.claude/workflows/review-deep.js` (`pipeline`+cache-warmup), запуск `Workflow({name:'review-deep'})`.

## 1. Скоуп
```bash
git diff --staged --name-only && git diff --name-only
```
Собери список изменённых путей. Пусто → нечего ревьюить, стоп.

## 2. Матрица «diff-путь → агент»
Выбери агентов по совпавшим путям (code-reviewer — всегда, если есть код):

| Изменённые пути | Агент |
|---|---|
| любой код в `backend/**` или `frontend-react/**` | **code-reviewer** (всегда) |
| `migrations/**`, `backend/models/**`, `*.sql`, alembic | **+ database-reviewer** |
| `backend/auth*`, `backend/rbac.py`, `backend/utils/crypto*`, `text(`-SQL, пользовательский ввод | **+ security-reviewer** |
| `backend/routers/**`, новый endpoint, массовые выборки (`.scalars().all()`) | **+ performance-optimizer** |
| `backend/routers/**` + `backend/schemas/**` (контракт API изменился) | **+ api-designer** |

## 3. Спавн — параллельно, на Opus 4.8
Подними выбранных агентов **одним сообщением** (параллельно). **Каждому явно `model: opus`** — обязательно:
- env `CLAUDE_CODE_SUBAGENT_MODEL` может быть закеширован на старте на нерабочем билде (см. [[model-always-top-tier]]); явный override в вызове перебивает env и гарантирует Opus 4.8.
- Cache-warmup: первым подними `code-reviewer` (он прогреет общий префикс — CLAUDE.md+rules+diff), следом остальных параллельно → они читают кэш (0.1x) вместо полной цены.

Каждому агенту в промпт: скоуп diff, какие файлы его зоны, «отчёт только по находкам с уверенностью >80%».

## 4. Сводный вердикт
Агрегируй вывод всех агентов в единую шкалу:
```
РЕВЬЮ (агентов: N)
  CRITICAL: X   HIGH: Y   MEDIUM: Z
  по агентам: code ✓ | security ⚠ HIGH×2 | db ✓ | perf ✓ | api —
  → ВЕРДИКТ: APPROVE / WARNING / BLOCK
```
- **BLOCK** — есть хоть один CRITICAL (iron-rule, секрет, SQL-инъекция, потеря денег/данных).
- **WARNING** — только HIGH.
- **APPROVE** — нет CRITICAL/HIGH.

CRITICAL не «чинит молча» — выносит наверх с файлом:строкой и предложением фикса.

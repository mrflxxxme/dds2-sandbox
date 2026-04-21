---
description: "Автообновление документации после изменений кода. Анализирует git diff, обновляет DOMAIN_*.md, CLAUDE.md, memory/."
---

# /docs — Автообновление документации DDS2

## Когда использовать
- Вручную после изменения кода/схем/моделей
- Автотриггер: UserPromptSubmit hook сообщает `[DOCS] N pending коммит(ов)` — это значит `.claude/.pending-docs.log` не пуст (заполняется post-commit hook при изменениях в `backend/` / `frontend-react/src/` / `migrations/`)

## Процесс

### Шаг 1: Собери изменения
**Приоритет — pending-docs.log:**
Если `.claude/.pending-docs.log` не пуст — это authoritative источник: каждая строка `hash timestamp message`. Для каждого коммита получи diff: `git show --stat <hash>` + `git show <hash> -- <file>`.

Если лог пуст — fallback: `git diff --name-only` + `git diff --cached --name-only` + `git log --oneline -5`.

### Шаг 2: Определи затронутые домены
Маппинг директорий → доменных файлов:

| Путь файла | Доменный файл |
|------------|---------------|
| `etl/`, `services/transactions_service.py` | `DOMAIN_TRANSACTIONS.md` |
| `services/reports/`, `opiu_service.py`, `wb_bdr_service.py` | `DOMAIN_REPORTS.md` |
| `services/planning/`, `routers/planning.py` | `DOMAIN_PLANNING.md` |
| `services/cost/`, `etl/cost_parsers.py` | `DOMAIN_COST.md` |
| `services/warehouse_service.py`, `services/fbo_supply_service.py` | `DOMAIN_WAREHOUSE.md` |
| `integrations/`, `services/funnel/`, `scheduler/jobs/` | `DOMAIN_WB.md` |
| `services/assembly_service.py`, `routers/assembly.py` | `DOMAIN_ASSEMBLY.md` |
| `src/app/`, `src/lib/api.ts` | `DOMAIN_FRONTEND.md` |

### Шаг 3: Проверь каждый тип обновления

**3a. Новые модели:**
- Прочитай `backend/models/__init__.py` — найди модели, которых нет в DOMAIN_*.md
- Если модель использует `SoftDeleteMixin` — проверь что она в `SOFT_MODELS` в `scripts/check_conventions.sh`

**3b. Новые сервисы/роутеры:**
- Прочитай затронутые DOMAIN_*.md файлы
- Добавь новые сервисы/роутеры в таблицу файлов

**3c. Новые кэши:**
- Найди новые `@cached(prefix=...)` в изменённых файлах
- Проверь что prefix добавлен в `invalidate_project_reports()` в `backend/cache.py`

**3d. Новые миграции:**
- Если есть новые файлы в `migrations/versions/` — добавь описание в соответствующий DOMAIN_*.md

**3e. CLAUDE.md:**
- Если добавлен новый домен → добавь строку в таблицу доменов
- Если найден новый антипаттерн → добавь в секцию "Антипаттерны"
- Если новая модель/сервис выходит за рамки существующих доменов → предложи создать новый DOMAIN_*.md

**3f. Memory:**
- Если исправлен баг из `memory/project_known_bugs.md` → перенеси в "Исправленные" с номером коммита
- Если найден новый баг/технический долг → добавь в known_bugs

### Шаг 4: Примени обновления
- Прочитай каждый файл который нужно обновить
- Внеси изменения через Edit tool
- Покажи пользователю что было обновлено

### Шаг 5: Очисти pending-docs.log
После успешного sync обнули лог (не удаляй файл — просто truncate):
```bash
> .claude/.pending-docs.log
```
Это снимет `[DOCS]` reminder при следующем prompt. Если sync частичный (обработана только часть коммитов) — удали из лога только обработанные строки, остальное оставь.

### Шаг 6: Отчёт
Выведи таблицу:

```
| Файл | Что обновлено |
|------|---------------|
| DOMAIN_WB.md | Добавлен WbOrderCancelDaily, wb_cancel_sync |
| CLAUDE.md | Без изменений |
| check_conventions.sh | Без изменений |
```

## Правила
- НЕ удалять существующую документацию — только дополнять
- НЕ менять формат существующих DOMAIN_*.md — следовать текущей структуре
- Если не уверен — спросить пользователя перед изменением
- Минимальные изменения — только то, что реально изменилось в коде

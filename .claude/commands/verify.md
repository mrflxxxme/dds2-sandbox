---
description: "Полная верификация DDS2 — тесты, конвенции, безопасность. Запускай перед коммитом."
---

# Verification — DDS2

Комплексная проверка перед коммитом/PR. Запускает проверки ПАРАЛЛЕЛЬНО для скорости.

## Инструкции

### Шаг 1: Параллельные проверки

Запустить **параллельно** 3 субагента (Agent tool, `run_in_background: true`):

**Субагент 1 — Backend тесты:**
```bash
docker compose exec backend pytest tests/ -x --tb=short -q
```
Вернуть: пройдено / провалено / ошибки.

**Субагент 2 — Конвенции + безопасность:**
```bash
bash scripts/check_conventions.sh
```
Плюс проверить:
```bash
grep -rn 'text(f"' --include="*.py" backend/ || echo "OK"
grep -rn "text(f'" --include="*.py" backend/ || echo "OK"
grep -rn "datetime.utcnow\|datetime.now" --include="*.py" backend/ || echo "OK"
grep -rn "db.delete\|session.delete" --include="*.py" backend/ || echo "OK"
grep -rn "Float" --include="*.py" backend/models/ || echo "OK"
```
Вернуть: OK или список проблем.

**Субагент 3 — Frontend сборка (если менялись frontend файлы):**
Проверить `git diff --name-only HEAD | grep -q "frontend-react/"`.
Если да: `cd frontend-react && npm run build`
Если нет: SKIP.
Вернуть: OK / FAIL / SKIP.

### Шаг 2: Собрать результаты

Дождаться всех 3 субагентов и сгенерировать отчёт:

```
ВЕРИФИКАЦИЯ DDS2
================

Тесты:       [OK/FAIL] (X/Y пройдено)
Конвенции:   [OK/FAIL]
Безопасность: [OK/X проблем]
Сборка:      [OK/FAIL/SKIP]

Готов к коммиту: [ДА/НЕТ]

Проблемы:
1. ...
```

### Шаг 3: Автообновление документации

Если все проверки прошли И изменены backend файлы (models/, services/, routers/, migrations/):
- Проверь DOMAIN_*.md (новые модели, сервисы, эндпоинты)
- Проверь backend/MAP.md (новые файлы)
- Проверь docs/KNOWN_PITFALLS.md (если наткнулся на грабли)
- Проверь CLAUDE.md (новый домен, антипаттерн)

### Шаг 4: Git Status
```bash
git diff --stat
git status
```

## Аргументы

$ARGUMENTS:
- `quick` — только конвенции + тесты (без субагентов, последовательно)
- `full` — все проверки параллельно (по умолчанию)
- `pre-commit` — конвенции + безопасность (без тестов)

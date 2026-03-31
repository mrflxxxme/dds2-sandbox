---
description: "Полная верификация DDS2 — тесты, конвенции, безопасность. Запускай перед коммитом."
---

# Verification — DDS2

Комплексная проверка состояния проекта перед коммитом/PR.

## Инструкции

Выполнить проверки в этом порядке:

### 1. Проверка конвенций
```bash
bash scripts/check_conventions.sh
```
Если не проходит — СТОП, исправить.

### 2. Backend тесты
```bash
docker compose exec backend pytest tests/ -x --tb=short
```
Отчёт: пройдено / провалено / coverage.

### 3. Frontend сборка (если менялся frontend)
```bash
cd frontend-react && npm run build
```

### 4. Аудит безопасности
```bash
# f-string в SQL
grep -rn 'text(f"' --include="*.py" backend/ || echo "OK"
grep -rn "text(f'" --include="*.py" backend/ || echo "OK"

# datetime.utcnow()
grep -rn "datetime.utcnow\|datetime.now" --include="*.py" backend/ || echo "OK"

# db.delete()
grep -rn "db.delete\|session.delete" --include="*.py" backend/ || echo "OK"

# Float для денег
grep -rn "Float" --include="*.py" backend/models/ || echo "OK"
```

### 5. Автообновление документации
Если изменены backend файлы (models/, services/, routers/, migrations/) — автоматически обнови документацию:
- Проверь, нужно ли обновить DOMAIN_*.md (новые модели, сервисы, эндпоинты)
- Проверь, нужно ли обновить backend/MAP.md (новые файлы/паттерны)
- Проверь, нужно ли добавить в docs/KNOWN_PITFALLS.md (если при разработке наткнулся на грабли)
- Проверь, нужно ли обновить CLAUDE.md (новый домен, новый антипаттерн)

### 6. Git Status
```bash
git diff --stat
git status
```

## Формат отчёта

```
ВЕРИФИКАЦИЯ DDS2
================

Конвенции:  [OK/FAIL]
Тесты:     [OK/FAIL] (X/Y пройдено)
Сборка:    [OK/FAIL/SKIP]
Безопасность: [OK/X проблем]
Git:       [X файлов изменено]

Готов к коммиту: [ДА/НЕТ]

Проблемы:
1. ...
```

## Аргументы

$ARGUMENTS:
- `quick` — только конвенции + тесты
- `full` — все проверки (по умолчанию)
- `pre-commit` — конвенции + безопасность

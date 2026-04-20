---
description: "Массовый рефакторинг DDS2 через AST-grep + LLM. Для переименований/сигнатур/паттернов в 10+ файлах."
---

# /codemod — Safe large-scale refactoring

Паттерн Codemod 2.0: детерминированный AST-matcher + LLM для edge cases. Безопаснее чем чистый LLM, быстрее чем ручной refactor.

## Когда использовать
- **ДА**: переименовать функцию/метод/переменную в 10+ файлах
- **ДА**: изменить сигнатуру (новый аргумент, удалить старый)
- **ДА**: заменить deprecated паттерн (например, `datetime.utcnow()` → `utcnow()`)
- **ДА**: ввести новый wrapper (все `db.execute()` обернуть в `.with_project(pid)`)
- **НЕТ**: одно-двух файловые правки → просто Edit
- **НЕТ**: архитектурные изменения → `/spec`

**Выгода** (Qonto case): месяц → недели.

## Инструменты
- `ast-grep` (sg) — детерминированный AST matcher для Python/TS/TSX
- Sub-agent с `sonnet` — для edge cases которые AST не покрыл
- pytest + check_conventions — валидация после

Установить ast-grep:
```bash
brew install ast-grep
# или
npm install -g @ast-grep/cli
```

## Процесс (5 шагов)

### Шаг 1: Инвентаризация (dry-run)
```bash
# Найти все вхождения паттерна
sg --pattern 'datetime.utcnow()' --lang python -l  # только файлы
sg --pattern 'datetime.utcnow()' --lang python      # с контекстом
```

Показать пользователю:
```
Паттерн: `datetime.utcnow()`
Найдено: 47 вхождений в 23 файлах
Замена: `utcnow()` из `backend.utils.time`
Требует: import в каждом файле
```

### Шаг 2: Получить approval пользователя
```
ДЕЛАТЬ? (y/n):
- 23 файла будут изменены
- В каждом добавится/заменится import
- После — pytest на всём репо, не должно быть regression
```

Ждать «y» прежде чем продолжать.

### Шаг 3: Применить codemod
```bash
# Автозамена через ast-grep
sg --pattern 'datetime.utcnow()' --rewrite 'utcnow()' --lang python --update-all

# Добавить import через sub-agent (LLM для edge cases):
# — найти файлы которые изменились
# — для каждого: если import отсутствует, добавить `from backend.utils.time import utcnow`
# — обновить существующий `from datetime import datetime` если остались другие использования
```

Для сложных случаев (LLM phase):
```python
# Пример: изменить сигнатуру функции `create_transaction(db, project_id, ...)` →
#         `create_transaction(db, *, project_id, ...)` (keyword-only)
# AST-grep найдёт все вызовы, но LLM решает — какой positional → keyword
```

### Шаг 4: Валидация
```bash
# Параллельно:
docker compose exec backend pytest tests/ -x --tb=short
bash scripts/check_conventions.sh
cd frontend-react && npx tsc --noEmit
```

При failure — откат через git:
```bash
git checkout -- .
```
И разобрать что пошло не так → вручную по одному файлу.

### Шаг 5: Коммит
```bash
git add -A
git commit -m "refactor: massreplace datetime.utcnow → utcnow (N files)

- AST-grep pattern: datetime.utcnow() → utcnow()
- Added imports where missing
- Tests green
"
```

## Типовые codemods для DDS2

### Переименование метода
```bash
sg --pattern 'service.$METHOD($$$)' --lang python | grep 'old_method'
sg --pattern '$OBJ.old_method($$$ARGS)' --rewrite '$OBJ.new_method($$$ARGS)' --lang python
```

### datetime.utcnow → utcnow (распространённый паттерн)
```bash
sg --pattern 'datetime.utcnow()' --rewrite 'utcnow()' --lang python
# + добавить import
```

### Float → Decimal для полей модели
⚠️ Сначала миграция схемы БД, потом codemod на Python код.
```bash
sg --pattern 'amount = Column(Float)' --rewrite 'amount = Column(Numeric(18, 2))' --lang python
```

### `db.delete()` → `soft_delete()`
```bash
sg --pattern 'await db.delete($MODEL)' --rewrite 'await $MODEL.soft_delete(db)' --lang python
```

### Frontend: `any` → конкретный тип
Сложно для AST (нужен контекст) → делегировать tsc + LLM агенту.

## Safety rules
- Всегда dry-run (`sg` без `--update-all`) → approval пользователя
- Один паттерн = один codemod = один коммит (легко откатить)
- НИКОГДА codemod одновременно с другими правками в том же PR
- После codemod — **полный test suite**, не частичный
- Codemod на shared код (`models/`, `schemas/`) — только через lead agent, последовательно
- Если >100 файлов → разбить на 2-3 коммита по группам

## НЕ ДЕЛАТЬ
- Codemod через `sed` — не понимает AST, ломает строки с совпадающим текстом в комментариях
- Полагаться только на LLM — для детерминированных замен AST лучше
- Codemod без тестов — если код не покрыт, сначала добавить smoke-тесты
- Codemod + рефакторинг в одном коммите — разделить

---
name: refactoring-checklist
description: Чеклист проверки tech debt при добавлении кода — размер файлов, дублирование, тесты, кэш, datetime
---

# Skill: Рефакторинг-чеклист

> ⚠️ **Проверяй этот чеклист ПЕРЕД каждым коммитом.**

## Автоматическая проверка

### 1. Размер файлов

```bash
# Backend: сервисы > 400 строк?
wc -l backend/services/*.py backend/services/**/*.py | sort -rn | head -10

# Frontend: модули > 500 строк?
wc -l frontend-react/src/lib/*.ts frontend-react/src/app/**/*.tsx | sort -rn | head -10
```

**Если файл > 400 строк** → разбить по ответственности:
- CRUD операции → `service.py`
- Парсеры/нормализаторы → `etl/parsers.py`
- Генерация/расчёты → отдельный файл
- Утилиты → `utils.py`

### 2. Тестовое покрытие

```bash
# Есть ли тесты для нового модуля?
ls tests/test_*.py | grep -i "module_name"

# Запуск тестов
docker compose exec backend pytest tests/ -x --tb=short
```

**Правило:** Каждый новый сервис → `tests/test_api_module.py`.

### 3. Cache key scoping

```bash
# Все вызовы @cached должны передавать project_id
grep -n "@cached" backend/services/*.py
grep -n "project_id" backend/services/*.py | grep "def "
```

**Правило:** Кэш-ключ ОБЯЗАН содержать `project_id`.

### 4. Дублирование кода

```bash
# Поиск дублирующих импортов middleware
grep -rn "from backend.middleware" backend/routers/
grep -rn "from backend.project_context" backend/routers/

# Поиск inline seed/init данных
grep -rn "INSERT INTO.*category_ref" backend/
```

### 5. Seed данные

**Правило:** Все данные инициализации → `backend/seeds/`, НЕ в `main.py`.

### 6. Datetime / timezone

```bash
# ЗАПРЕЩЕНО: прямые вызовы datetime.utcnow() или datetime.now(timezone.utc)
# Используй единый helper:
#   from backend.utils.time import utcnow
grep -rn "datetime\.utcnow()" backend/ --include="*.py" --exclude-dir="__pycache__"
grep -rn "datetime\.now(timezone\.utc)" backend/ --include="*.py" --exclude-dir="__pycache__"
```

**Правило:** Использовать **`from backend.utils.time import utcnow`** — единый naive UTC helper,
совместимый с asyncpg `TIMESTAMP WITHOUT TIME ZONE`. Оба варианта напрямую запрещены.

## ⛔ Чеклист

> **Автоматическая проверка:** `bash scripts/check_conventions.sh` — запускает все проверки автоматически.

- [ ] Ни один сервис не превышает 400 строк
- [ ] Новый модуль имеет тесты в `tests/`
- [ ] Cache key содержит `project_id`
- [ ] Нет дублирования middleware (одна точка: `project_context.py`)
- [ ] Seed данные в `backend/seeds/`, не inline
- [ ] Datetime через `from backend.utils.time import utcnow` (НЕ `datetime.utcnow()`, НЕ `datetime.now(timezone.utc)`)
- [ ] SQL-запросы через `project_select()` / `project_select_active()` из `backend/utils/queries`
- [ ] `AGENTS.md` обновлён если изменилась структура
- [ ] Тесты проходят: `pytest tests/ -x`
- [ ] Convention checks: `bash scripts/check_conventions.sh`

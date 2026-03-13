---
description: Изменение схемы БД — модель, миграция, rebuild, проверка
---

# Workflow: Изменение схемы БД

// turbo-all

## 1. Подготовка
Прочитай `AGENTS.md` (секции ⛔ ЗАПРЕЩЕНО и ✅ ОБЯЗАТЕЛЬНО).

## 2. Изменение модели
- Используй skill `db-migration`
- Создай файл `backend/models/feature.py` (НЕ добавляй в монолитный models.py)
- **`Mapped[]` + `mapped_column()`** — не `Column()`
- **`from backend.utils.time import utcnow`** — НЕ `datetime.utcnow()`, НЕ `datetime.now(timezone.utc)`
- **`Numeric(18,2)`** для денег — не `Float`
- **`project_id`** с FK и индексом
- Re-export в `models/__init__.py`

## 3. Создание миграции
```bash
cd /Users/a1/Desktop/dds_app && docker compose exec backend alembic revision --autogenerate -m "описание"
```

## 4. Проверка миграции
Посмотри сгенерированный файл:
```bash
cd /Users/a1/Desktop/dds_app && ls -la migrations/versions/ | tail -3
```

## 5. Применение миграции
```bash
cd /Users/a1/Desktop/dds_app && docker compose exec backend alembic upgrade head
```

## 6. Обновление schemas
- Создай `backend/schemas/feature.py` (НЕ добавляй в монолитный schemas.py)
- Re-export в `schemas/__init__.py`

## 7. Проверка
```bash
cd /Users/a1/Desktop/dds_app && docker compose logs backend --tail=20
```

## 8. Коммит
```bash
cd /Users/a1/Desktop/dds_app && git add -A
```

```bash
cd /Users/a1/Desktop/dds_app && git commit -m "db: описание изменения схемы"
```

```bash
cd /Users/a1/Desktop/dds_app && git push origin dev
```

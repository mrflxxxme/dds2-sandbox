---
description: Изменение схемы БД — модель, миграция, rebuild, проверка
---

# Workflow: Изменение схемы БД

// turbo-all

## 1. Подготовка
Прочитай текущие модели:
```bash
cd /Users/a1/Desktop/dds_app && head -200 backend/models.py
```

## 2. Изменение модели
- Отредактируй `backend/models.py` — добавь/измени таблицу или столбец
- Используй skill `db-migration` для деталей

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
- Отредактируй `backend/schemas.py` — добавь новые поля в response models

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

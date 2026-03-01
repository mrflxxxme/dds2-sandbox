---
name: db-migration
description: Шаблон для изменения схемы БД через alembic миграции (новая таблица, новый столбец, изменение типа)
---

# Skill: Миграция базы данных

Используй этот skill когда нужно изменить схему БД (новая таблица, новый столбец, удаление, изменение типа).

## Шаги

### 1. Обнови модель (backend/models.py)
Внеси изменения в ORM модель:

**Новая таблица:**
```python
class NewTable(Base):
    __tablename__ = "new_table"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    # ... поля
    created_at = Column(DateTime, server_default=func.now())
```

**Новый столбец в существующей таблице:**
```python
# Добавь в класс:
new_column = Column(String, nullable=True)  # nullable=True чтобы не сломать существующие данные
```

### 2. Создай миграцию alembic
```bash
cd /Users/a1/Desktop/dds_app && docker compose exec backend alembic revision --autogenerate -m "описание изменения"
```

### 3. Проверь сгенерированную миграцию
Открой файл в `migrations/versions/` и проверь:
- ✅ Правильные операции (add_column, create_table и т.д.)
- ✅ Есть `upgrade()` и `downgrade()`
- ✅ Индексы на `project_id` и часто-запрашиваемые поля
- ⚠️ Удали лишние операции если autogenerate добавил ненужное

### 4. Примени миграцию
```bash
cd /Users/a1/Desktop/dds_app && docker compose exec backend alembic upgrade head
```

### 5. Обнови Pydantic schemas (backend/schemas.py)
Добавь/обнови schemas для новых полей:
```python
class NewTableResponse(BaseModel):
    id: int
    project_id: int
    # ... новые поля
    model_config = ConfigDict(from_attributes=True)
```

### 6. Альтернатива: прямой SQL (если alembic не настроен)
```bash
cd /Users/a1/Desktop/dds_app && docker compose exec db psql -U dds_user -d dds_db -c "
ALTER TABLE table_name ADD COLUMN new_column VARCHAR;
"
```

### 7. Чеклист перед завершением
- [ ] Модель обновлена в `models.py`
- [ ] Миграция создана (autogenerate или ручная)
- [ ] Миграция проверена (upgrade + downgrade)
- [ ] Миграция применена (`alembic upgrade head`)
- [ ] Schemas обновлены в `schemas.py`
- [ ] Nullable=True для новых столбцов (backward compatibility)
- [ ] Индексы на project_id и FK
- [ ] UPSERT (ON CONFLICT) для импорта данных
- [ ] Обновлён `AGENTS.md` (таблица моделей)
- [ ] Сделан коммит через `/dev`

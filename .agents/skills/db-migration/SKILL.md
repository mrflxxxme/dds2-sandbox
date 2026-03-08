---
name: db-migration
description: Шаблон для изменения схемы БД через alembic миграции (новая таблица, новый столбец, изменение типа)
---

# Skill: Миграция базы данных

> ⚠️ **Прочитай `AGENTS.md` — секции ЗАПРЕЩЕНО и ОБЯЗАТЕЛЬНО — перед началом.**

## Шаги

### 1. Обнови модель (backend/models/feature.py)

> **Модели разбиты по доменам!** Создавай новый файл `models/feature.py`, НЕ добавляй в монолитный `models.py`.

**Новая таблица:**
```python
"""Feature models."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import String, Integer, Numeric, DateTime, Date, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin


class Feature(Base, SoftDeleteMixin):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)   # ← Numeric, НЕ Float
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.utcnow()                      # ← НЕ datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_feature_project", "project_id"),
    )
```

⛔ **ЗАПРЕЩЕНО:**
- `Column(Integer, ...)` → используй `Mapped[int] = mapped_column(Integer, ...)`
- `datetime.now(timezone.utc)` → используй `datetime.utcnow()` (asyncpg НЕ принимает offset-aware в TIMESTAMP WITHOUT TIME ZONE)
- `Float` для денег → используй `Numeric(18, 2)`

**Зарегистрируй** в `models/__init__.py`:
```python
from backend.models.feature import Feature
```

**Новый столбец в существующей таблице:**
```python
# Добавь в класс:
new_column: Mapped[Optional[str]] = mapped_column(String(100))  # nullable=True по умолчанию для Optional
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

### 5. Обнови Pydantic schemas (backend/schemas/feature.py)

> **Схемы разбиты по доменам!** Создавай `schemas/feature.py`, НЕ добавляй в монолитный `schemas.py`.

```python
"""Feature schemas."""
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict

class FeatureResponse(BaseModel):
    id: int
    name: str
    amount: Decimal
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
```

**Зарегистрируй** в `schemas/__init__.py`:
```python
from backend.schemas.feature import FeatureResponse
```

### 6. Альтернатива: прямой SQL (если срочно)
```bash
cd /Users/a1/Desktop/dds_app && docker compose exec db psql -U dds -d dds_db -c "
ALTER TABLE table_name ADD COLUMN new_column VARCHAR;
"
```

## ⛔ Чеклист (обязательный)

- [ ] Модель в `models/feature.py` (НЕ в `models.py` монолите)
- [ ] **`Mapped[]` + `mapped_column()`** — не `Column()`
- [ ] **`datetime.utcnow()`** — НЕ `datetime.now(timezone.utc)` (asyncpg DataError!)
- [ ] **`Numeric(18, 2)`** для денег — не `Float`
- [ ] **`project_id`** с FK и индексом
- [ ] **`SoftDeleteMixin`** для критичных сущностей
- [ ] Re-export в `models/__init__.py`
- [ ] Миграция создана (autogenerate)
- [ ] Миграция проверена (upgrade + downgrade)
- [ ] Миграция применена (`alembic upgrade head`)
- [ ] Schema в `schemas/feature.py` + re-export в `__init__.py`
- [ ] Nullable=True для новых столбцов (backward compatibility)
- [ ] Индексы на project_id и FK
- [ ] Обновлён `AGENTS.md`
- [ ] Коммит через `/dev`

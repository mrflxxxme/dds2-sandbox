---
name: new-api-endpoint
description: Шаблон для создания нового API эндпоинта в FastAPI backend (роутер, schema, модель, регистрация)
---

# Skill: Новый API эндпоинт

Используй этот skill когда нужно создать новый API эндпоинт в backend.

## Шаги

### 1. Определи что нужно
- Какой HTTP метод (GET, POST, PUT, DELETE)?
- Какой URL path?
- Нужна ли новая таблица в БД?
- Какие данные на вход / на выход?

### 2. Schema (backend/schemas.py)
Добавь Pydantic модели для request/response:
```python
# === Feature Name ===
class FeatureCreate(BaseModel):
    """Request schema для создания."""
    name: str
    project_id: int

class FeatureResponse(BaseModel):
    """Response schema."""
    id: int
    name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
```

### 3. Модель БД (backend/models.py) — если нужна новая таблица
```python
class Feature(Base):
    __tablename__ = "features"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
```
> ⚠️ После добавления модели — создай alembic миграцию (см. skill `db-migration`)

### 4. Роутер (backend/routers/feature_name.py)
```python
"""
Роутер для Feature Name.
Эндпоинты: CRUD операции.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from backend.database import get_db
from backend.auth import get_current_user
from backend.schemas import FeatureCreate, FeatureResponse
from backend.models import Feature, User

router = APIRouter(prefix="/api/v1/feature", tags=["feature"])

@router.post("/", response_model=FeatureResponse)
async def create_feature(
    data: FeatureCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Создание новой записи."""
    obj = Feature(**data.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj

@router.get("/", response_model=list[FeatureResponse])
async def list_features(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Список записей по project_id."""
    from sqlalchemy import select
    result = await db.execute(
        select(Feature).where(Feature.project_id == project_id)
    )
    return result.scalars().all()
```

### 5. Регистрация роутера (backend/main.py)
Добавь в `main.py`:
```python
from backend.routers import feature_name
app.include_router(feature_name.router)
```

### 6. Чеклист перед завершением
- [ ] Schema добавлена в `schemas.py`
- [ ] Модель добавлена в `models.py` (если нужна)
- [ ] Миграция создана и применена (если новая таблица)
- [ ] Роутер создан в `backend/routers/`
- [ ] Роутер зарегистрирован в `main.py`
- [ ] `project_id` фильтрация для data isolation
- [ ] Обновлён `AGENTS.md` (таблица моделей, структура)
- [ ] Обновлён `docs/MODULES.md`
- [ ] Сделан коммит через `/dev`

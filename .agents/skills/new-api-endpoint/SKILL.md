---
name: new-api-endpoint
description: Шаблон для создания нового API эндпоинта в FastAPI backend (роутер → сервис → модель → схема)
---

# Skill: Новый API эндпоинт

> ⚠️ **Прочитай `AGENTS.md` — секции ЗАПРЕЩЕНО и ОБЯЗАТЕЛЬНО — перед началом.**

## Порядок создания (Schema-First)

### 1. Schema (backend/schemas/feature.py)
Определи контракт **ДО написания кода**:
```python
"""Feature schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict

class FeatureCreate(BaseModel):
    """Request schema для создания."""
    name: str
    amount: Decimal  # ← Decimal, НЕ float для денег

class FeatureResponse(BaseModel):
    """Response schema."""
    id: int
    name: str
    amount: Decimal
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
```

Зарегистрируй в `schemas/__init__.py`:
```python
from backend.schemas.feature import FeatureCreate, FeatureResponse
```

### 2. Модель (backend/models/feature.py)
```python
"""Feature models."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import String, Integer, Numeric, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin  # для критичных сущностей


class Feature(Base, SoftDeleteMixin):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)  # ← Numeric, НЕ Float
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)  # ← НЕ datetime.utcnow
    )
```

Зарегистрируй в `models/__init__.py`:
```python
from backend.models.feature import Feature
```

> ⚠️ После добавления модели — создай alembic миграцию (см. skill `db-migration`)

### 3. Service (backend/services/feature_service.py)
**Вся бизнес-логика ЗДЕСЬ, не в роутере:**
```python
"""Feature service — business logic."""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models import Feature

logger = logging.getLogger("dds.feature")


async def get_features(db: AsyncSession, project_id: int, limit: int = 100, offset: int = 0):
    """List features with pagination."""
    result = await db.execute(
        select(Feature)
        .where(Feature.project_id == project_id)
        .where(Feature.is_deleted == False)       # ← soft delete фильтр
        .order_by(Feature.id.desc())
        .limit(limit).offset(offset)              # ← пагинация
    )
    return result.scalars().all()


async def create_feature(db: AsyncSession, project_id: int, data: dict):
    """Create a feature."""
    obj = Feature(project_id=project_id, **data)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    logger.info("Created feature %s for project %s", obj.id, project_id)
    return obj


async def delete_feature(db: AsyncSession, project_id: int, feature_id: int):
    """Soft-delete a feature."""
    result = await db.execute(
        select(Feature)
        .where(Feature.id == feature_id, Feature.project_id == project_id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    obj.soft_delete()  # ← soft delete, не физическое удаление
    await db.commit()
    return obj
```

### 4. Router (backend/routers/feature.py)
**Тонкий — только HTTP, валидация, вызов service:**
```python
"""Router for Feature — HTTP layer only, no business logic."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.auth import get_current_user
from backend.project_context import get_project_id
from backend.schemas import FeatureCreate, FeatureResponse
from backend.services import feature_service

router = APIRouter(prefix="/feature", tags=["Feature"])


@router.get("/", response_model=list[FeatureResponse])
async def list_features(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    project_id: int = Depends(get_project_id),
):
    """List features with pagination."""
    return await feature_service.get_features(db, project_id, limit, offset)


@router.post("/", response_model=FeatureResponse)
async def create(
    data: FeatureCreate,
    db: AsyncSession = Depends(get_db),
    project_id: int = Depends(get_project_id),
):
    """Create a feature."""
    return await feature_service.create_feature(db, project_id, data.model_dump())


@router.delete("/{feature_id}")
async def delete(
    feature_id: int,
    db: AsyncSession = Depends(get_db),
    project_id: int = Depends(get_project_id),
):
    """Soft-delete a feature."""
    obj = await feature_service.delete_feature(db, project_id, feature_id)
    if not obj:
        raise HTTPException(404, "Not found")
    return {"status": "deleted"}
```

### 5. Регистрация роутера (backend/main.py)
```python
from backend.routers import feature
app.include_router(
    feature.router, prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)
```

### 6. Тест (tests/test_api_feature.py)
```python
"""Tests for Feature API."""
import pytest

async def test_create_feature(client, auth_headers):
    resp = await client.post("/api/v1/feature/", json={"name": "Test", "amount": "100.00"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test"

async def test_list_features(client, auth_headers):
    resp = await client.get("/api/v1/feature/?limit=10&offset=0", headers=auth_headers)
    assert resp.status_code == 200
```

## ⛔ Чеклист (обязательный)

- [ ] Schema в `schemas/feature.py` + re-export в `__init__.py`
- [ ] Модель в `models/feature.py` + re-export в `__init__.py`
- [ ] **`Mapped[]` + `mapped_column()`** — не `Column()`
- [ ] **`datetime.now(timezone.utc)`** — не `datetime.utcnow`
- [ ] **`Numeric(18, 2)`** для денег — не `Float`
- [ ] **`project_id`** фильтрация в каждом запросе
- [ ] **`is_deleted == False`** фильтр для SoftDeleteMixin
- [ ] Миграция: `alembic revision --autogenerate -m "..."`
- [ ] Service в `services/` — бизнес-логика НЕ в роутере
- [ ] Роутер тонкий — вызывает service
- [ ] **Пагинация** (`limit/offset`) для list-эндпоинтов
- [ ] **Logging**: `logger = logging.getLogger("dds.module")`
- [ ] Тест в `tests/test_api_feature.py`
- [ ] Роутер зарегистрирован в `main.py`
- [ ] Обновлён `AGENTS.md` + `docs/MODULES.md`
- [ ] Коммит через `/dev`

"""HTTP-роутер раздела «Биржа карточек товаров» (card-exchange showcase).

Только HTTP+валидация; логика — в services.card_exchange.showcase. Проксирует
витрину/корзину биржи WB и добавляет каскад по корневой категории и фильтр по
нашим товарам. Создание объявлений и реальный перенос НЕ поддерживаются (scope MVP).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.card_exchange import (
    CartActionResult,
    CartAdd,
    CartDelete,
    RootCategory,
    ShowcaseQuery,
    ShowcaseResponse,
)
from backend.services.card_exchange import showcase as svc
from backend.services.card_exchange.showcase import CardExchangeError
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/card-exchange", tags=["Card Exchange"])


@router.get("/categories", response_model=list[RootCategory])
async def list_categories(
    _project: Project = Depends(get_current_project),
) -> list[RootCategory]:
    """Корневые категории справочника (для каскадного фильтра витрины)."""
    return [RootCategory(**c) for c in svc.list_root_categories()]


@router.post("/showcase", response_model=ShowcaseResponse)
async def showcase(
    query: ShowcaseQuery,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> ShowcaseResponse:
    """Страница витрины биржи (или полный скан в режиме 'exact')."""
    try:
        result = await svc.list_showcase(db, project.id, query)
    except CardExchangeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:  # нет сессии WB-кабинета
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ShowcaseResponse(**result)


@router.get("/cart", response_model=dict)
async def get_cart(
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> dict:
    """Корзина биржи (группировка по продавцам, как отдаёт WB)."""
    try:
        return await svc.get_cart(db, project.id)
    except (CardExchangeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/cart/add", response_model=CartActionResult)
async def cart_add(
    body: CartAdd,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> CartActionResult:
    """Добавить объявление в корзину биржи."""
    try:
        ok = await svc.cart_add(db, project.id, body.ad_id)
    except (CardExchangeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return CartActionResult(ok=ok)


@router.post("/cart/delete", response_model=CartActionResult)
async def cart_delete(
    body: CartDelete,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> CartActionResult:
    """Убрать объявления из корзины биржи."""
    try:
        ok = await svc.cart_delete(db, project.id, body.ad_ids)
    except (CardExchangeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return CartActionResult(ok=ok)

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
from backend.rbac import require_page
from backend.schemas.card_exchange import (
    CartActionResult,
    CartAdd,
    CartDelete,
    ExchangeSessionSet,
    ExchangeSessionStatus,
    RootCategory,
    ShowcaseQuery,
    ShowcaseResponse,
)
from backend.services import integrations_service
from backend.services.card_exchange import showcase as svc
from backend.services.card_exchange.showcase import CardExchangeError
from backend.utils.rate_limit import rate_limit_read_heavy, rate_limit_write

# `/showcase` — POST ради тела фильтра, а не мутация: это основное чтение
# страницы, поэтому viewer с ключом раздела обязан его видеть.
router = APIRouter(
    prefix="/card-exchange",
    tags=["Card Exchange"],
    dependencies=[Depends(require_page("card-exchange", read_paths=frozenset({"/showcase"})))],
)


@router.get("/session/status", response_model=ExchangeSessionStatus)
async def session_status(
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> ExchangeSessionStatus:
    """Статус собственной сессии биржи: ACTIVE / EXPIRED / NONE."""
    state = await integrations_service.get_wb_exchange_status(db, project.id)
    return ExchangeSessionStatus(**state)


@router.post("/session", response_model=ExchangeSessionStatus)
async def session_set(
    body: ExchangeSessionSet,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> ExchangeSessionStatus:
    """Завести/обновить сессию биржи (отдельный слот от сессии поставок)."""
    try:
        state = await integrations_service.set_wb_exchange_session(db, project.id, body.authorizev3)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ExchangeSessionStatus(**state)


@router.post("/session/from-supply", response_model=ExchangeSessionStatus)
async def session_from_supply(
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> ExchangeSessionStatus:
    """Взять доступ к бирже из уже настроенного доступа WB для поставок (в один клик)."""
    try:
        state = await integrations_service.copy_supply_session_to_exchange(db, project.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ExchangeSessionStatus(**state)


@router.get("/categories", response_model=list[RootCategory])
async def list_categories(
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> list[RootCategory]:
    """Корневые категории справочника + признак «есть наши товары» (для фильтра)."""
    return [RootCategory(**c) for c in await svc.list_root_categories(db, project.id)]


@router.get("/counters", response_model=dict)
async def counters(
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> dict:
    """Счётчики биржи: всего объявлений (для пагинации)."""
    try:
        return await svc.get_counters(db, project.id)
    except (CardExchangeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/brands", response_model=list[str])
async def list_brands(
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> list[str]:
    """Бренды биржи — для фильтра."""
    try:
        return await svc.list_brands(db, project.id)
    except (CardExchangeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/suppliers", response_model=list[dict])
async def list_suppliers(
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> list[dict]:
    """Продавцы биржи — для фильтра."""
    try:
        return await svc.list_suppliers(db, project.id)
    except (CardExchangeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/subjects", response_model=list[dict])
async def list_subjects(
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> list[dict]:
    """Предметы биржи — для фильтра (плоский список WB)."""
    try:
        return await svc.list_subjects(db, project.id)
    except (CardExchangeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# rate_limit_read_heavy, а не write: ручка читающая, но в режиме 'exact' идёт
# полным сканом — бакет отдельный, чтобы скан не выедал write-лимит корзине.
@router.post(
    "/showcase",
    response_model=ShowcaseResponse,
    dependencies=[Depends(rate_limit_read_heavy)],
)
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

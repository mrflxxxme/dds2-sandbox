# ruff: noqa: RUF002, RUF003
"""
Router: /warehouse/assembly/{id}/wb — занос заявки-сборки в FBW-поставку WB
через реплей портала. Плюс управление session-токеном кабинета (/wb-session).

Финальная бронь даты (plan/add) НЕ автоматизируется — антибот-гейт, ручной клик
в портале. sync-supply подхватывает supply_id после этого клика.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.assembly_wb import (
    WbBoxesUpdate,
    WbBulkSyncResult,
    WbCabinetBoxes,
    WbCabinetPass,
    WbDriver,
    WbPassUpdate,
    WbPortalSessionSet,
    WbPortalStatus,
    WbPreorderCreate,
    WbSupplyState,
)
from backend.services import integrations_service, wb_supply_service
from backend.services.wb_supply_service import WbSupplyError
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/warehouse/assembly", tags=["Assembly WB"])


# ─── Session токена кабинета ──────────────────────────────────────────────────


@router.get("/wb-session/status", response_model=WbPortalStatus)
async def wb_session_status(
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> WbPortalStatus:
    state = await integrations_service.get_wb_portal_status(db, project.id)
    return WbPortalStatus(**state)


@router.post("/wb-session", response_model=WbPortalStatus)
async def wb_session_set(
    body: WbPortalSessionSet,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> WbPortalStatus:
    try:
        state = await integrations_service.set_wb_portal_session(db, project.id, body.authorizev3)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return WbPortalStatus(**state)


# ─── Состояние связи + операции ───────────────────────────────────────────────


async def _call(coro) -> WbSupplyState:
    try:
        link = await coro
    except WbSupplyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return WbSupplyState.model_validate(link)


@router.post("/wb/sync-states", response_model=WbBulkSyncResult)
async def sync_wb_states(
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> WbBulkSyncResult:
    """Bulk-синк живого WB-состояния всех поставок проекта (кнопка «Обновить» +
    фоновая джоба). Объявлен ДО `/{assembly_id}/wb/...` ради ясности маршрутизации."""
    try:
        res = await wb_supply_service.sync_all_states(db, project.id)
    except WbSupplyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return WbBulkSyncResult(**res)


@router.get("/{assembly_id}/wb", response_model=WbSupplyState)
async def get_wb_state(
    assembly_id: int,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> WbSupplyState:
    return await _call(wb_supply_service.get_state(db, project.id, assembly_id))


@router.post("/{assembly_id}/wb/preorder", response_model=WbSupplyState)
async def create_preorder(
    assembly_id: int,
    body: WbPreorderCreate | None = None,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> WbSupplyState:
    package_type = body.package_type if body else None
    return await _call(
        wb_supply_service.create_preorder(db, project.id, assembly_id, package_type)
    )


@router.post("/{assembly_id}/wb/goods/push", response_model=WbSupplyState)
async def push_goods(
    assembly_id: int,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> WbSupplyState:
    return await _call(wb_supply_service.update_preorder_goods(db, project.id, assembly_id))


@router.post("/{assembly_id}/wb/sync-supply", response_model=WbSupplyState)
async def sync_supply(
    assembly_id: int,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> WbSupplyState:
    return await _call(wb_supply_service.sync_supply_id(db, project.id, assembly_id))


@router.put("/{assembly_id}/wb/boxes", response_model=WbSupplyState)
async def update_boxes(
    assembly_id: int,
    body: WbBoxesUpdate,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> WbSupplyState:
    boxes = [b.model_dump() for b in body.boxes]
    return await _call(wb_supply_service.save_boxes(db, project.id, assembly_id, boxes))


@router.post("/{assembly_id}/wb/boxes/push", response_model=WbSupplyState)
async def push_boxes(
    assembly_id: int,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> WbSupplyState:
    return await _call(wb_supply_service.push_boxes(db, project.id, assembly_id))


@router.put("/{assembly_id}/wb/pass", response_model=WbSupplyState)
async def update_pass(
    assembly_id: int,
    body: WbPassUpdate,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> WbSupplyState:
    return await _call(wb_supply_service.save_pass(db, project.id, assembly_id, body.model_dump()))


@router.post("/{assembly_id}/wb/pass/push", response_model=WbSupplyState)
async def push_pass(
    assembly_id: int,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: None = Depends(rate_limit_write),
) -> WbSupplyState:
    return await _call(wb_supply_service.push_pass(db, project.id, assembly_id))


@router.get("/{assembly_id}/wb/cabinet-boxes", response_model=WbCabinetBoxes)
async def get_cabinet_boxes(
    assembly_id: int,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> WbCabinetBoxes:
    try:
        return await wb_supply_service.get_cabinet_boxes(db, project.id, assembly_id)
    except WbSupplyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{assembly_id}/wb/cabinet-pass", response_model=WbCabinetPass)
async def get_cabinet_pass(
    assembly_id: int,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> WbCabinetPass:
    try:
        return await wb_supply_service.get_cabinet_pass(db, project.id, assembly_id)
    except WbSupplyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{assembly_id}/wb/drivers", response_model=list[WbDriver])
async def get_drivers(
    assembly_id: int,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> list[WbDriver]:
    try:
        drivers = await wb_supply_service.get_drivers(db, project.id)
    except WbSupplyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return [WbDriver(**d) for d in drivers]

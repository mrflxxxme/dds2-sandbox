# ruff: noqa: RUF002, RUF003
"""
Router: /migfull-portal — создание заявки на отгрузку в портале ФФ «Натали» (migfull).

HTTP + валидация; логика — в services/migfull_portal_service. У migfull нет write-API:
интеграция ходит как браузер (Livewire-сессия). `send` — РЕАЛЬНОЕ создание заявки
(НЕОБРАТИМО, у клиента портала нет delete/cancel), под rate_limit_write.

НЕ путать с read-only API migfull (вкладка остатков/заявок ФФ).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import Project, User
from backend.project_context import get_current_project
from backend.schemas.migfull_portal import (
    MigfullDraftResponse,
    MigfullInboundDraftResponse,
    MigfullInboundSendRequest,
    MigfullPortalConfigResponse,
    MigfullSendRequest,
    MigfullSendResult,
)
from backend.services import migfull_portal_inbound, migfull_portal_service
from backend.services.migfull_portal_service import MigfullPortalServiceError
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/migfull-portal", tags=["MigfullPortal"])


def _actor(user: User) -> str:
    return getattr(user, "email", None) or f"user:{user.id}"


@router.get("/config", response_model=MigfullPortalConfigResponse)
async def migfull_portal_config(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> MigfullPortalConfigResponse:
    """Настроена ли интеграция и к какому складу (Натали) привязана."""
    return await migfull_portal_service.get_config(db, project.id)


@router.get("/assembly/{assembly_id}/draft", response_model=MigfullDraftResponse)
async def migfull_portal_draft(
    assembly_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> MigfullDraftResponse:
    """Превью заявки для модалки: шапка-prefill + строки описи (короб/россыпь)."""
    try:
        return await migfull_portal_service.build_draft(db, project.id, assembly_id)
    except MigfullPortalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.post(
    "/assembly/{assembly_id}/send",
    response_model=MigfullSendResult,
    dependencies=[Depends(rate_limit_write)],
)
async def migfull_portal_send(
    assembly_id: int,
    payload: MigfullSendRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MigfullSendResult:
    """РЕАЛЬНОЕ создание заявки в ФФ «Натали» (НЕОБРАТИМО). Пишет audit-строку."""
    try:
        return await migfull_portal_service.send_shipment(db, project.id, assembly_id, payload, actor=_actor(user))
    except MigfullPortalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


# ─── Поставка (приёмка) на склад Натали из нашей приёмки машины ──────────────


@router.get("/inbound/{receipt_id}/draft", response_model=MigfullInboundDraftResponse)
async def migfull_portal_inbound_draft(
    receipt_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> MigfullInboundDraftResponse:
    """Превью поставки для confirm-модалки: prefill шапки + состав (позиции/штуки/короба)."""
    try:
        return await migfull_portal_inbound.build_inbound_draft(db, project.id, receipt_id)
    except MigfullPortalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/inbound/{receipt_id}/send",
    response_model=MigfullSendResult,
    dependencies=[Depends(rate_limit_write)],
)
async def migfull_portal_inbound_send(
    receipt_id: int,
    payload: MigfullInboundSendRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MigfullSendResult:
    """РЕАЛЬНОЕ создание поставки (приёмки) в ФФ «Натали» (НЕОБРАТИМО). Пишет audit."""
    try:
        return await migfull_portal_inbound.send_submission(db, project.id, receipt_id, payload, actor=_actor(user))
    except MigfullPortalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ─── Та же поставка, но из ПЕРЕМЕЩЕНИЯ (наш склад → Натали) ──────────────────
# Перемещение своей приёмки не создаёт (приход у ФФ заводит эта поставка), поэтому
# источником состава выступает сам переезд TR-…. Контур идентичен /inbound.


@router.get("/transfer/{transfer_id}/draft", response_model=MigfullInboundDraftResponse)
async def migfull_portal_transfer_draft(
    transfer_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> MigfullInboundDraftResponse:
    """Превью поставки из перемещения: prefill шапки + состав (позиции/штуки/короба)."""
    try:
        return await migfull_portal_inbound.build_transfer_draft(db, project.id, transfer_id)
    except MigfullPortalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/transfer/{transfer_id}/send",
    response_model=MigfullSendResult,
    dependencies=[Depends(rate_limit_write)],
)
async def migfull_portal_transfer_send(
    transfer_id: int,
    payload: MigfullInboundSendRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MigfullSendResult:
    """РЕАЛЬНОЕ создание поставки из перемещения (НЕОБРАТИМО). Пишет audit."""
    try:
        return await migfull_portal_inbound.send_transfer_submission(
            db, project.id, transfer_id, payload, actor=_actor(user)
        )
    except MigfullPortalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

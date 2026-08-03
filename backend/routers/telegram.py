"""
Telegram web API — endpoints for the frontend settings page.
Protected by JWT auth.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models.auth import Project, User
from backend.project_context import get_current_project
from backend.schemas.telegram import (
    FfBoardConfigRequest,
    TelegramChatBindingSchema,
    TelegramLinkResponse,
    ToggleNotifyRequest,
)
from backend.services import telegram_service
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/telegram", tags=["Telegram"])


@router.post("/link", response_model=TelegramLinkResponse, dependencies=[Depends(rate_limit_write)])
async def generate_link(
    user: User = Depends(get_current_user),
):
    """Generate a deep link URL for Telegram auth."""
    url = await telegram_service.generate_deep_link_token(user.id)
    return TelegramLinkResponse(deep_link_url=url)


@router.get("/chats", response_model=list[TelegramChatBindingSchema])
async def list_chats(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List Telegram chat bindings for the current project."""
    return await telegram_service.list_chat_bindings(db, project.id)


@router.delete("/chats/{binding_id}", dependencies=[Depends(rate_limit_write)])
async def unbind_chat(
    binding_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Remove a Telegram chat binding."""
    ok = await telegram_service.unbind_chat(db, binding_id, project.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Привязка не найдена")
    return {"message": "Привязка удалена"}


@router.patch("/chats/{binding_id}/notify", dependencies=[Depends(rate_limit_write)])
async def toggle_notify(
    binding_id: int,
    body: ToggleNotifyRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Toggle daily digest for a chat binding."""
    ok = await telegram_service.toggle_notify(db, binding_id, project.id, body.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="Привязка не найдена")
    status = "включён" if body.enabled else "выключен"
    return {"message": f"Дайджест {status}"}


@router.patch("/chats/{binding_id}/ff-notify", dependencies=[Depends(rate_limit_write)])
async def toggle_ff_notify(
    binding_id: int,
    body: ToggleNotifyRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Toggle fulfillment status notifications (assembly READY + inbound acceptance) for a chat binding."""
    ok = await telegram_service.toggle_ff_notify(db, binding_id, project.id, body.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="Привязка не найдена")
    status = "включены" if body.enabled else "выключены"
    return {"message": f"Уведомления ФФ {status}"}


@router.patch("/chats/{binding_id}/measurements-notify", dependencies=[Depends(rate_limit_write)])
async def toggle_measurements_notify(
    binding_id: int,
    body: ToggleNotifyRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Toggle the daily WB measurements digest (09:00 MSK) for a chat binding."""
    ok = await telegram_service.toggle_measurements_notify(db, binding_id, project.id, body.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="Привязка не найдена")
    status = "включена" if body.enabled else "выключена"
    return {"message": f"Сводка замеров {status}"}


@router.patch("/chats/{binding_id}/supply-notify", dependencies=[Depends(rate_limit_write)])
async def toggle_supply_notify(
    binding_id: int,
    body: ToggleNotifyRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Toggle supply-discrepancy alerts (дата/паллеты/пропуск, раз в 2ч) for a chat binding."""
    ok = await telegram_service.toggle_supply_notify(db, binding_id, project.id, body.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="Привязка не найдена")
    status = "включены" if body.enabled else "выключены"
    return {"message": f"Алерты расхождений поставок {status}"}


@router.patch("/chats/{binding_id}/ff-board", dependencies=[Depends(rate_limit_write)])
async def set_ff_board(
    binding_id: int,
    body: FfBoardConfigRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Configure the pinned FF-board for a chat: on/off + optional warehouse scope.

    Appears in the chat at the next FF sync (the bot lives in the worker process,
    so the web request only persists the setting — it does not push immediately).
    """
    try:
        ok = await telegram_service.set_ff_board_config(db, binding_id, project.id, body.enabled, body.warehouse_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Привязка не найдена")
    status = "включено" if body.enabled else "выключено"
    return {"message": f"Табло заявок ФФ {status}"}

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
    TelegramChatBindingSchema,
    TelegramLinkResponse,
    ToggleNotifyRequest,
)
from backend.services import telegram_service

router = APIRouter(prefix="/telegram", tags=["Telegram"])


@router.post("/link", response_model=TelegramLinkResponse)
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


@router.delete("/chats/{binding_id}")
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


@router.patch("/chats/{binding_id}/notify")
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


@router.patch("/chats/{binding_id}/ff-notify")
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

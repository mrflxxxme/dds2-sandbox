"""
Telegram Mini App endpoints:
- Auth via initData → JWT
- AI chat via REST (reuses existing AI agent)
- User projects list for project picker
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import create_access_token, create_refresh_token, get_current_user
from backend.config import settings
from backend.database import get_db
from backend.models import User
from backend.models.telegram import TelegramBotUser
from backend.services.telegram_service import get_user_projects
from backend.utils.telegram_auth import validate_telegram_webapp_data

logger = logging.getLogger("dds.tma")

router = APIRouter(prefix="/tma", tags=["Telegram Mini App"])


# ─── Schemas ─────────────────────────────────────────────────────────────────


class TmaAuthRequest(BaseModel):
    init_data: str


class TmaAuthResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    user: dict  # {id, username, first_name, projects: [...]}


class TmaChatRequest(BaseModel):
    project_id: int
    brand: str | None = None
    message: str


class TmaChatResponse(BaseModel):
    reply: str


class TmaProjectItem(BaseModel):
    id: int
    name: str
    slug: str


# ─── Auth ────────────────────────────────────────────────────────────────────


@router.post("/auth", response_model=TmaAuthResponse)
async def tma_auth(body: TmaAuthRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate Telegram Mini App user via initData.

    Validates the Telegram WebApp initData signature, looks up the linked
    DDS user, and returns JWT tokens.
    """
    bot_token = settings.TELEGRAM_BOT_TOKEN_ANALYTICS
    if not bot_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram bot not configured",
        )

    user_data = validate_telegram_webapp_data(body.init_data, bot_token)
    if user_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_init_data",
        )

    telegram_id = user_data.get("id")
    if not telegram_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_init_data",
        )

    # Find linked DDS user
    result = await db.execute(
        select(TelegramBotUser).where(TelegramBotUser.telegram_id == telegram_id)
    )
    tg_user = result.scalar_one_or_none()

    if tg_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="account_not_linked",
        )

    # Get DDS user
    result = await db.execute(
        select(User).where(User.id == tg_user.user_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user_inactive",
        )

    # Get user projects
    projects = await get_user_projects(db, user.id)
    project_list = [
        {"id": p.id, "name": p.name, "slug": p.slug}
        for p in projects
    ]

    # Issue tokens
    access = create_access_token(user.id, user.username)
    refresh = await create_refresh_token(user.id)

    logger.info(
        "TMA auth: telegram_id=%s → user_id=%s (%s), projects=%d",
        telegram_id, user.id, user.username, len(project_list),
    )

    return TmaAuthResponse(
        access_token=access,
        refresh_token=refresh,
        user={
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name or user.username,
            "projects": project_list,
        },
    )


# ─── AI Chat ────────────────────────────────────────────────────────────────


@router.post("/chat", response_model=TmaChatResponse)
async def tma_chat(
    body: TmaChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI chat for Telegram Mini App. Reuses the existing AI agent."""
    from backend.models.auth import ProjectMember
    from backend.services.ai.agent import ask

    # Verify user has access to project
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == body.project_id,
            ProjectMember.user_id == current_user.id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to this project",
        )

    # Use user_id as chat_id for TMA (separate from Telegram chat rate limits)
    chat_id = current_user.id * -1  # Negative to avoid collision with Telegram chat IDs

    reply = await ask(
        db=db,
        project_id=body.project_id,
        brand=body.brand,
        chat_id=chat_id,
        question=body.message,
    )

    return TmaChatResponse(reply=reply)


# ─── Projects (for project picker in TMA) ───────────────────────────────────


@router.get("/projects", response_model=list[TmaProjectItem])
async def tma_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List projects available to the current user."""
    projects = await get_user_projects(db, current_user.id)
    return [
        TmaProjectItem(id=p.id, name=p.name, slug=p.slug)
        for p in projects
    ]

"""
Router: /tma — Telegram Mini App authentication and API.

Flow:
1. TMA sends initData → POST /tma/auth
2. Backend validates HMAC, finds user by telegram_id or username
3. Returns JWT tokens for subsequent API calls
4. POST /tma/chat — AI chat (requires JWT)
5. GET /tma/projects — user projects (requires JWT)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.config import settings
from backend.database import get_db
from backend.models.auth import User
from backend.services.telegram_service import (
    authenticate_tma_user,
    get_user_projects,
    verify_project_access,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tma", tags=["TMA"])


# ─── Schemas ──────────────────────────────────────────────────────────────────


class TMAAuthRequest(BaseModel):
    init_data: str


class TMAProjectResponse(BaseModel):
    id: int
    name: str
    slug: str


class TMAAuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: dict
    projects: list[TMAProjectResponse]


class TMAChatRequest(BaseModel):
    project_id: int
    brand: str | None = None
    message: str


class TMAChatResponse(BaseModel):
    answer: str


# ─── POST /tma/auth ──────────────────────────────────────────────────────────


@router.post("/auth", response_model=TMAAuthResponse)
async def tma_auth(
    body: TMAAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate Telegram Mini App user."""
    bot_token = settings.TELEGRAM_BOT_TOKEN_ANALYTICS
    if not bot_token:
        logger.error("TMA auth: TELEGRAM_BOT_TOKEN_ANALYTICS not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="telegram_bot_not_configured",
        )

    dds_user, projects, access_token, refresh_token = await authenticate_tma_user(db, body.init_data, bot_token)

    return TMAAuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user={
            "id": dds_user.id,
            "username": dds_user.username,
            "first_name": dds_user.first_name,
            "last_name": dds_user.last_name,
        },
        projects=[TMAProjectResponse(id=p.id, name=p.name, slug=p.slug) for p in projects],
    )


# ─── POST /tma/chat ──────────────────────────────────────────────────────────


@router.post("/chat", response_model=TMAChatResponse)
async def tma_chat(
    body: TMAChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI chat endpoint for TMA. Requires JWT auth."""
    project = await verify_project_access(db, user.id, body.project_id)

    from backend.services.ai.agent import ask

    answer = await ask(
        db=db,
        project_id=body.project_id,
        brand=body.brand,
        chat_id=user.id,
        question=body.message,
        tax_rate=float(project.tax_rate or 6),
    )

    return TMAChatResponse(answer=answer)


# ─── GET /tma/projects ────────────────────────────────────────────────────────


@router.get("/projects", response_model=list[TMAProjectResponse])
async def tma_projects(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List projects for authenticated TMA user."""
    projects = await get_user_projects(db, user.id)
    return [TMAProjectResponse(id=p.id, name=p.name, slug=p.slug) for p in projects]

"""
Authentication endpoints: login, register, profile, change password.
Rate limiting on login via Redis.
"""

import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_password,
    revoke_refresh_token,
    validate_password_strength,
    verify_password,
    verify_refresh_token,
)
from backend.config import settings
from backend.database import get_db
from backend.models import User

logger = logging.getLogger("dds.auth")

router = APIRouter(prefix="/auth", tags=["Auth"])


# ─── Rate Limiting ────────────────────────────────────────────────────────────


async def check_rate_limit(request: Request, action: str = "login"):
    """Check Redis-based rate limit. Raises 429 if exceeded."""
    if os.environ.get("TESTING"):
        return  # Skip rate limiting in tests
    try:
        from backend.cache import get_redis

        redis = await get_redis()
        if redis is None:
            return  # Redis unavailable — skip rate limiting

        client_ip = request.client.host if request.client else "unknown"
        key = f"rate_limit:{action}:{client_ip}"

        current = await redis.get(key)
        if current and int(current) >= settings.LOGIN_RATE_LIMIT:
            logger.warning(f"Rate limit exceeded for {action} from {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Слишком много попыток. Подождите минуту.",
            )

        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, 60)  # 1 minute window
        await pipe.execute()
    except HTTPException:
        raise
    except Exception:
        pass  # Redis error — don't block login


# ─── Schemas ──────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class ProfileResponse(BaseModel):
    id: int
    username: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_active: bool
    created_at: datetime


class ProfileUpdateRequest(BaseModel):
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None


# ─── Login ────────────────────────────────────────────────────────────────────


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate user and return JWT token. Rate limited."""
    await check_rate_limit(request, "login")

    # Allow login by username OR email
    from sqlalchemy import or_

    login_value = body.username.strip()
    result = await db.execute(
        select(User).where(
            or_(User.username == login_value, User.email == login_value),
            User.is_active == True,
        )
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        logger.info(f"Failed login attempt for username: {body.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )

    logger.info(f"Successful login: {body.username} (id={user.id})")
    access = create_access_token(user.id, user.username)
    refresh = await create_refresh_token(user.id)
    return TokenResponse(access_token=access, refresh_token=refresh)


# ─── Register ─────────────────────────────────────────────────────────────────


@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Register a new user. Auto-creates a 'default' project.
    Can be disabled via REGISTER_ENABLED=false in config.
    """
    # Check if registration is enabled
    if not settings.REGISTER_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Регистрация отключена. Обратитесь к администратору.",
        )

    await check_rate_limit(request, "register")

    # Validate password strength
    validate_password_strength(body.password)

    # Validate username
    if len(body.username) < 3:
        raise HTTPException(400, "Логин должен быть не менее 3 символов")

    from backend.models import Project, ProjectMember

    # Check if username exists
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Пользователь с таким логином уже существует")

    # Check if email exists
    if body.email:
        existing_email = await db.execute(select(User).where(User.email == body.email))
        if existing_email.scalar_one_or_none():
            raise HTTPException(400, "Email уже используется")

    user = User(
        username=body.username,
        email=body.email,
        first_name=body.first_name,
        last_name=body.last_name,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.flush()

    # Auto-create default project
    import uuid

    slug = f"project-{uuid.uuid4().hex[:8]}"
    project = Project(
        name="Мой проект",
        slug=slug,
        owner_id=user.id,
    )
    db.add(project)
    await db.flush()

    member = ProjectMember(project_id=project.id, user_id=user.id)
    db.add(member)
    await db.commit()

    logger.info(f"New user registered: {body.username} (id={user.id}), project slug={slug}")
    access = create_access_token(user.id, user.username)
    refresh = await create_refresh_token(user.id)
    return TokenResponse(access_token=access, refresh_token=refresh)


# ─── Profile ──────────────────────────────────────────────────────────────────


@router.get("/me", response_model=ProfileResponse)
async def get_profile(current_user: User = Depends(get_current_user)):
    """Get current user profile."""
    return current_user


@router.put("/me", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update current user profile (email, name)."""
    if body.email is not None:
        current_user.email = body.email
    if body.first_name is not None:
        current_user.first_name = body.first_name
    if body.last_name is not None:
        current_user.last_name = body.last_name
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return current_user


# ─── Change Password ─────────────────────────────────────────────────────────


@router.post("/change_password")
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change current user's password. Requires old password verification."""
    if not verify_password(body.old_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный текущий пароль",
        )

    # Validate new password strength
    validate_password_strength(body.new_password)

    current_user.password_hash = hash_password(body.new_password)
    db.add(current_user)
    await db.commit()
    logger.info(f"Password changed for user: {current_user.username} (id={current_user.id})")
    return {"status": "ok", "message": "Пароль изменён"}


# ─── Refresh Token ───────────────────────────────────────────────────────────


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Exchange a valid refresh token for a new access + refresh token pair.
    The old refresh token is revoked (token rotation for security).
    """
    user_id = await verify_refresh_token(body.refresh_token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Find user
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if user is None:
        await revoke_refresh_token(body.refresh_token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    # Rotate: revoke old, issue new
    await revoke_refresh_token(body.refresh_token)

    access = create_access_token(user.id, user.username)
    refresh = await create_refresh_token(user.id)

    logger.info(f"Token refreshed for user: {user.username} (id={user.id})")
    return TokenResponse(access_token=access, refresh_token=refresh)

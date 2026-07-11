"""
Authentication endpoints: login, register, profile, change password.
Rate limiting on login via Redis.
"""

import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select, update
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
from backend.utils.rate_limit import rate_limit_write

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

        # Use X-Real-IP from nginx (trustworthy). request.client.host за nginx
        # всегда 172.x.x.x (Docker bridge) — все клиенты попадут в один bucket.
        client_ip = request.headers.get("X-Real-IP") or (request.client.host if request.client else "unknown")
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
    # When present, registration is allowed regardless of REGISTER_ENABLED and the
    # new user is added straight to the invited project (no personal project is
    # created). REGISTER_ENABLED now means "allow registration WITHOUT an invite".
    invite_token: str | None = None


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
    access = create_access_token(user.id, user.username, is_external=user.is_external)
    refresh = await create_refresh_token(user.id)
    return TokenResponse(access_token=access, refresh_token=refresh)


# ─── Register ─────────────────────────────────────────────────────────────────


@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Register a new user.

    Two paths:
    - With a valid `invite_token` → always allowed; the user joins the invited
      project with the invite's role/pages and NO personal project is created.
    - Without an invite → only when `REGISTER_ENABLED` is true; a personal
      "Мой проект" is auto-created and the user becomes its owner.
    """
    from backend.models import Project, ProjectInvite, ProjectMember
    from backend.utils.time import utcnow

    # Rate-limit first — before any DB work — so an unauthenticated caller can't
    # probe invite tokens without limit.
    await check_rate_limit(request, "register")

    # Resolve the invite — it decides whether open registration applies.
    invite: ProjectInvite | None = None
    if body.invite_token:
        invite = (
            await db.execute(
                select(ProjectInvite).where(
                    ProjectInvite.invite_token == body.invite_token,
                    ProjectInvite.status == "pending",
                )
            )
        ).scalar_one_or_none()
        if invite is None or (invite.expires_at is not None and invite.expires_at < utcnow()):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Приглашение недействительно или истекло.",
            )
    elif not settings.REGISTER_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Регистрация возможна только по приглашению. Обратитесь к администратору проекта.",
        )

    # Validate password strength
    validate_password_strength(body.password)

    # Validate username
    if len(body.username) < 3:
        raise HTTPException(400, "Логин должен быть не менее 3 символов")

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

    if invite is not None:
        # Join the invited project directly — brand-new user, so no prior
        # membership row to restore. Role/pages come from the invite.
        member = ProjectMember(
            project_id=invite.project_id,
            user_id=user.id,
            role=invite.role,
            pages=invite.pages,
        )
        db.add(member)
        # Atomically claim the invite: the conditional UPDATE enforces one-time
        # semantics under concurrency — two parallel registers with the same
        # token race here, the loser gets rowcount 0 and is rolled back.
        claim = await db.execute(
            update(ProjectInvite)
            .where(ProjectInvite.id == invite.id, ProjectInvite.status == "pending")
            .values(status="accepted", accepted_at=utcnow(), accepted_by_id=user.id)
        )
        if claim.rowcount == 0:  # type: ignore[attr-defined]
            await db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "Приглашение уже использовано")
        await db.commit()
        logger.info(
            f"New user registered via invite: {body.username} (id={user.id}), "
            f"project_id={invite.project_id}, role={invite.role}"
        )
    else:
        # Open registration — auto-create a personal project.
        import uuid

        slug = f"project-{uuid.uuid4().hex[:8]}"
        project = Project(
            name="Мой проект",
            slug=slug,
            owner_id=user.id,
        )
        db.add(project)
        await db.flush()

        member = ProjectMember(project_id=project.id, user_id=user.id, role="owner")
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


@router.put("/me", response_model=ProfileResponse, dependencies=[Depends(rate_limit_write)])
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


@router.post("/change_password", dependencies=[Depends(rate_limit_write)])
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


# ─── Logout ──────────────────────────────────────────────────────────────────


class LogoutRequest(BaseModel):
    refresh_token: str


@router.post("/logout", dependencies=[Depends(rate_limit_write)])
async def logout(
    body: LogoutRequest,
    current_user: User = Depends(get_current_user),
):
    """Revoke the refresh token to end the session."""
    await revoke_refresh_token(body.refresh_token)
    logger.info(f"User logged out: {current_user.username} (id={current_user.id})")
    return {"status": "ok", "message": "Сессия завершена"}


# ─── Refresh Token ───────────────────────────────────────────────────────────


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Exchange a valid refresh token for a new access + refresh token pair.
    The old refresh token is revoked (token rotation for security).
    """
    await check_rate_limit(request, "refresh")
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

    access = create_access_token(user.id, user.username, is_external=user.is_external)
    refresh = await create_refresh_token(user.id)

    logger.info(f"Token refreshed for user: {user.username} (id={user.id})")
    return TokenResponse(access_token=access, refresh_token=refresh)

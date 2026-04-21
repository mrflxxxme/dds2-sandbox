"""
Telegram bot service — deep link auth, chat binding, brand notes, TMA auth.
"""

import logging
import secrets

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import create_access_token, create_refresh_token
from backend.cache import get_redis
from backend.models.auth import Project, ProjectMember, User
from backend.models.integrations import WbFunnelDaily
from backend.models.telegram import BrandNote, TelegramBotUser, TelegramChatBinding
from backend.utils.telegram_auth import validate_telegram_webapp_data
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

BOT_USERNAME = "dds_analytics_bot"
DEEP_LINK_TTL = 300  # 5 min


# ─── Deep Link Auth ──────────────────────────────────────────────────────────


async def generate_deep_link_token(user_id: int) -> str:
    """Generate a one-time token for Telegram deep link auth. Returns the full URL."""
    token = secrets.token_urlsafe(32)
    redis = await get_redis()
    if redis:
        await redis.setex(f"tg_link:{token}", DEEP_LINK_TTL, str(user_id))
    return f"https://t.me/{BOT_USERNAME}?start={token}"


async def verify_deep_link_token(token: str) -> int | None:
    """Verify token and return user_id. Deletes token on success."""
    redis = await get_redis()
    if not redis:
        return None
    key = f"tg_link:{token}"
    user_id_str = await redis.get(key)
    if user_id_str:
        await redis.delete(key)
        return int(user_id_str)
    return None


# ─── Telegram User Linking ───────────────────────────────────────────────────


async def link_telegram_user(db: AsyncSession, telegram_id: int, user_id: int) -> TelegramBotUser:
    """Link telegram_id to DDS user_id (upsert)."""
    result = await db.execute(select(TelegramBotUser).where(TelegramBotUser.telegram_id == telegram_id))
    existing = result.scalar_one_or_none()
    if existing:
        existing.user_id = user_id
        await db.commit()
        return existing
    tg_user = TelegramBotUser(telegram_id=telegram_id, user_id=user_id, created_at=utcnow())
    db.add(tg_user)
    await db.commit()
    return tg_user


async def get_dds_user_by_telegram(db: AsyncSession, telegram_id: int) -> TelegramBotUser | None:
    """Lookup DDS user by Telegram ID."""
    result = await db.execute(select(TelegramBotUser).where(TelegramBotUser.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def get_user_by_telegram_username(db: AsyncSession, username: str) -> User | None:
    """Find DDS User by telegram_username (case-insensitive)."""
    normalized = username.lower().lstrip("@")
    result = await db.execute(
        select(User).where(
            func.lower(User.telegram_username) == normalized,
            User.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


# ─── Chat Binding ────────────────────────────────────────────────────────────


async def bind_chat(
    db: AsyncSession,
    chat_id: int,
    project_id: int,
    brand: str | None,
    created_by_id: int,
) -> TelegramChatBinding:
    """Bind a chat to project+brand (upsert on chat_id)."""
    result = await db.execute(select(TelegramChatBinding).where(TelegramChatBinding.chat_id == chat_id))
    existing = result.scalar_one_or_none()
    if existing:
        existing.project_id = project_id
        existing.brand = brand
        existing.created_by_id = created_by_id
        await db.commit()
        return existing
    binding = TelegramChatBinding(
        chat_id=chat_id,
        project_id=project_id,
        brand=brand,
        created_by_id=created_by_id,
        created_at=utcnow(),
    )
    db.add(binding)
    await db.commit()
    return binding


async def get_chat_binding(db: AsyncSession, chat_id: int) -> TelegramChatBinding | None:
    """Get binding for a specific chat."""
    result = await db.execute(select(TelegramChatBinding).where(TelegramChatBinding.chat_id == chat_id))
    return result.scalar_one_or_none()


async def list_chat_bindings(db: AsyncSession, project_id: int) -> list[TelegramChatBinding]:
    """List all chat bindings for a project."""
    result = await db.execute(select(TelegramChatBinding).where(TelegramChatBinding.project_id == project_id))
    return list(result.scalars().all())


async def unbind_chat(db: AsyncSession, binding_id: int, project_id: int) -> bool:
    """Remove chat binding (hard delete). Returns True if deleted."""
    result = await db.execute(
        select(TelegramChatBinding).where(
            TelegramChatBinding.id == binding_id,
            TelegramChatBinding.project_id == project_id,
        )
    )
    binding = result.scalar_one_or_none()
    if not binding:
        return False
    await db.delete(binding)  # no-soft-delete-check: TelegramChatBinding has no SoftDeleteMixin
    await db.commit()
    return True


async def toggle_notify(db: AsyncSession, binding_id: int, project_id: int, enabled: bool) -> bool:
    """Toggle notify_enabled for a chat binding."""
    result = await db.execute(
        select(TelegramChatBinding).where(
            TelegramChatBinding.id == binding_id,
            TelegramChatBinding.project_id == project_id,
        )
    )
    binding = result.scalar_one_or_none()
    if not binding:
        return False
    binding.notify_enabled = enabled
    await db.commit()
    return True


# ─── TMA Authentication ─────────────────────────────────────────────────────


async def authenticate_tma_user(
    db: AsyncSession, init_data: str, bot_token: str
) -> tuple[User, list[Project], str, str]:
    """Authenticate TMA user via initData.

    Returns (user, projects, access_token, refresh_token) or raises HTTPException.
    """
    # 1. Validate initData HMAC
    tg_user_data = validate_telegram_webapp_data(init_data, bot_token)
    if tg_user_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_init_data",
        )

    telegram_id = tg_user_data.get("id")
    if not telegram_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_init_data",
        )

    # 2. Lookup by telegram_id (existing link)
    tg_bot_user = await get_dds_user_by_telegram(db, telegram_id)
    dds_user: User | None = None

    if tg_bot_user:
        result = await db.execute(select(User).where(User.id == tg_bot_user.user_id, User.is_active.is_(True)))
        dds_user = result.scalar_one_or_none()

    # 3. Fallback: lookup by telegram_username
    if dds_user is None:
        tg_username = tg_user_data.get("username")
        if tg_username:
            dds_user = await get_user_by_telegram_username(db, tg_username)
            if dds_user:
                # Auto-create TelegramBotUser link
                await link_telegram_user(db, telegram_id, dds_user.id)
                logger.info(
                    "TMA auto-link: telegram_id=%d username=%s -> user_id=%d",
                    telegram_id,
                    tg_username,
                    dds_user.id,
                )

    # 4. Not found
    if dds_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="account_not_linked",
        )

    # Generate tokens
    access_token = create_access_token(dds_user.id, dds_user.username)
    refresh_token = await create_refresh_token(dds_user.id)

    # Get user projects
    projects = await get_user_projects(db, dds_user.id)

    return dds_user, projects, access_token, refresh_token


async def verify_project_access(db: AsyncSession, user_id: int, project_id: int) -> Project:
    """Verify user has access to a project. Returns Project or raises HTTPException."""
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            ProjectMember.is_deleted == False,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no_project_access",
        )

    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project_not_found",
        )

    return project


# ─── User Projects / Brands ─────────────────────────────────────────────────


async def get_user_projects(db: AsyncSession, user_id: int) -> list[Project]:
    """Get all projects the user is a member of."""
    result = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user_id, ProjectMember.is_deleted == False)
    )
    return list(result.scalars().all())


async def get_project_brands(db: AsyncSession, project_id: int) -> list[str]:
    """Get distinct brands from WB funnel data for a project."""
    result = await db.execute(
        select(WbFunnelDaily.brand)
        .where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.brand.isnot(None),
            WbFunnelDaily.brand != "",
        )
        .distinct()
    )
    return sorted([row for row in result.scalars().all() if row])


# ─── Brand Notes ─────────────────────────────────────────────────────────────


async def add_brand_note(db: AsyncSession, project_id: int, brand: str, note_text: str) -> BrandNote:
    """Add a brand note."""
    note = BrandNote(project_id=project_id, brand=brand, note=note_text, created_at=utcnow())
    db.add(note)
    await db.commit()
    return note


async def list_brand_notes(db: AsyncSession, project_id: int, brand: str) -> list[BrandNote]:
    """List all notes for a brand in a project."""
    result = await db.execute(
        select(BrandNote)
        .where(
            BrandNote.project_id == project_id,
            BrandNote.brand == brand,
        )
        .order_by(BrandNote.id)
    )
    return list(result.scalars().all())


async def delete_brand_note(db: AsyncSession, note_id: int, project_id: int) -> bool:
    """Delete a brand note (hard delete). Returns True if deleted."""
    result = await db.execute(
        select(BrandNote).where(
            BrandNote.id == note_id,
            BrandNote.project_id == project_id,
        )
    )
    note = result.scalar_one_or_none()
    if not note:
        return False
    await db.delete(note)  # no-soft-delete-check: BrandNote has no SoftDeleteMixin
    await db.commit()
    return True

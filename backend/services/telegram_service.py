"""
Telegram bot service — deep link auth, chat binding, brand notes, TMA auth.
"""

import logging
import secrets

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import create_access_token, create_refresh_token
from backend.cache import get_redis
from backend.config import settings
from backend.models.auth import Project, ProjectMember, User
from backend.models.integrations import WbFunnelDaily
from backend.models.telegram import BrandNote, TelegramBotUser, TelegramChatBinding
from backend.models.warehouse import Warehouse
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


async def toggle_ff_notify(db: AsyncSession, binding_id: int, project_id: int, enabled: bool) -> bool:
    """Toggle ff_notify_enabled (fulfillment status notifications) for a chat binding."""
    result = await db.execute(
        select(TelegramChatBinding).where(
            TelegramChatBinding.id == binding_id,
            TelegramChatBinding.project_id == project_id,
        )
    )
    binding = result.scalar_one_or_none()
    if not binding:
        return False
    binding.ff_notify_enabled = enabled
    await db.commit()
    return True


async def list_ff_notify_chats(db: AsyncSession, project_id: int) -> list[TelegramChatBinding]:
    """Chat bindings of a project that opted into fulfillment status notifications."""
    result = await db.execute(
        select(TelegramChatBinding).where(
            TelegramChatBinding.project_id == project_id,
            TelegramChatBinding.ff_notify_enabled == True,
        )
    )
    return list(result.scalars().all())


async def toggle_ff_board(db: AsyncSession, binding_id: int, project_id: int, enabled: bool) -> bool:
    """Toggle the pinned FF-board for a chat binding. Disabling also forgets the
    pinned message id so a fresh board is created on re-enable."""
    result = await db.execute(
        select(TelegramChatBinding).where(
            TelegramChatBinding.id == binding_id,
            TelegramChatBinding.project_id == project_id,
        )
    )
    binding = result.scalar_one_or_none()
    if not binding:
        return False
    binding.ff_board_enabled = enabled
    if not enabled:
        binding.ff_board_message_id = None
    await db.commit()
    return True


async def list_ff_board_chats(db: AsyncSession, project_id: int) -> list[TelegramChatBinding]:
    """Chat bindings of a project that opted into the pinned FF-board."""
    result = await db.execute(
        select(TelegramChatBinding).where(
            TelegramChatBinding.project_id == project_id,
            TelegramChatBinding.ff_board_enabled == True,
        )
    )
    return list(result.scalars().all())


async def set_ff_board_config(
    db: AsyncSession,
    binding_id: int,
    project_id: int,
    enabled: bool,
    warehouse_id: int | None,
) -> bool:
    """Configure the pinned FF-board for a chat: on/off + optional warehouse scope.

    warehouse_id=None → board over all warehouses; a non-null id scopes it to that
    single fulfillment warehouse (must belong to the project). Disabling forgets the
    pinned message id so a fresh board is created on re-enable. Returns False if the
    binding is not found; raises ValueError if the warehouse is invalid for the project.
    """
    result = await db.execute(
        select(TelegramChatBinding).where(
            TelegramChatBinding.id == binding_id,
            TelegramChatBinding.project_id == project_id,
        )
    )
    binding = result.scalar_one_or_none()
    if not binding:
        return False
    if warehouse_id is not None:
        wh = await db.execute(
            select(Warehouse.id).where(
                Warehouse.id == warehouse_id,
                Warehouse.project_id == project_id,
                Warehouse.is_deleted == False,
            )
        )
        if wh.scalar_one_or_none() is None:
            raise ValueError("Склад не найден в этом проекте")
    binding.ff_board_enabled = enabled
    binding.ff_board_warehouse_id = warehouse_id if enabled else None
    if not enabled:
        binding.ff_board_message_id = None
    await db.commit()
    return True


async def list_warehouse_linked_bindings(db: AsyncSession, project_id: int) -> list[TelegramChatBinding]:
    """Все привязки проекта к складам ФФ (ff_board_warehouse_id задан) — «чаты
    складов». Туда шлём уведомления о новых заявках на сборку соответствующего
    склада. Пустой список → ранний выход у вызывающих, без тяжёлых запросов на
    горячем пути создания заявки.
    """
    result = await db.execute(
        select(TelegramChatBinding).where(
            TelegramChatBinding.project_id == project_id,
            TelegramChatBinding.ff_board_warehouse_id.is_not(None),
        )
    )
    return list(result.scalars().all())


async def send_analytics_message(chat_id: int, text: str, *, reply_markup: dict | None = None) -> bool:
    """Best-effort: послать HTML-сообщение в чат через analytics-бота из ЛЮБОГО
    процесса (httpx → Telegram API), не завися от aiogram-синглтона воркера
    (в web-процессе `bot` is None). True — отправлено. Никогда не бросает.

    Токен не настроен (локалка/маскированные ключи) → no-op, False.
    """
    token = settings.TELEGRAM_BOT_TOKEN_ANALYTICS
    if not token:
        return False
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:4096],  # Telegram-лимит
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=8, proxy=settings.TELEGRAM_PROXY or None) as client:
            resp = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
        if resp.status_code != 200:
            logger.warning("analytics-bot send failed (chat=%s): %s", chat_id, resp.text[:200])
            return False
        return True
    except Exception as exc:  # сеть/прокси/таймаут — best-effort
        logger.warning("analytics-bot send error (chat=%s): %s", chat_id, exc)
        return False


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

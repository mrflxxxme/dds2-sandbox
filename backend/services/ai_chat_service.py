"""
AI Chat service — CRUD for conversations and messages, plus AI response handling.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.ai_chat import AiConversation, AiMessage
from backend.utils.time import utcnow

logger = logging.getLogger("dds.ai_chat")


# ── Conversations ────────────────────────────────────────────────────


async def list_conversations(
    db: AsyncSession,
    project_id: int,
    user_id: int,
    limit: int = 50,
    offset: int = 0,
) -> list[AiConversation]:
    """List conversations for a user within a project (newest first)."""
    result = await db.execute(
        select(AiConversation)
        .where(
            AiConversation.project_id == project_id,
            AiConversation.user_id == user_id,
            AiConversation.is_deleted == False,  # noqa: E712
        )
        .order_by(AiConversation.updated_at.desc().nullslast(), AiConversation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_conversation(
    db: AsyncSession,
    project_id: int,
    conversation_id: int,
) -> AiConversation | None:
    """Get a single conversation by ID, scoped to project."""
    result = await db.execute(
        select(AiConversation).where(
            AiConversation.id == conversation_id,
            AiConversation.project_id == project_id,
            AiConversation.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def create_conversation(
    db: AsyncSession,
    project_id: int,
    user_id: int,
    brand: str | None = None,
    title: str = "Новый чат",
) -> AiConversation:
    """Create a new AI chat conversation."""
    conversation = AiConversation(
        project_id=project_id,
        user_id=user_id,
        brand=brand,
        title=title,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def update_conversation(
    db: AsyncSession,
    project_id: int,
    conversation_id: int,
    title: str | None = None,
    brand: str | None = None,
) -> AiConversation | None:
    """Update conversation title and/or brand."""
    conv = await get_conversation(db, project_id, conversation_id)
    if not conv:
        return None

    if title is not None:
        conv.title = title
    if brand is not None:
        conv.brand = brand
    conv.updated_at = utcnow()

    await db.commit()
    await db.refresh(conv)
    return conv


async def delete_conversation(
    db: AsyncSession,
    project_id: int,
    conversation_id: int,
) -> bool:
    """Soft-delete a conversation."""
    conv = await get_conversation(db, project_id, conversation_id)
    if not conv:
        return False

    conv.soft_delete()
    await db.commit()
    return True


# ── Messages ─────────────────────────────────────────────────────────


async def list_messages(
    db: AsyncSession,
    conversation_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[AiMessage]:
    """List messages in a conversation (oldest first for chat display)."""
    result = await db.execute(
        select(AiMessage)
        .where(AiMessage.conversation_id == conversation_id)
        .order_by(AiMessage.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def save_message(
    db: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    files: list[dict] | None = None,
    tokens_used: int = 0,
    tools_used: list[str] | None = None,
) -> AiMessage:
    """Save a message to the conversation."""
    message = AiMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        files=files,
        tokens_used=tokens_used,
        tools_used=tools_used,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


async def auto_title(
    db: AsyncSession,
    conversation_id: int,
    question: str,
) -> None:
    """Set conversation title from the first user message (first 50 chars)."""
    result = await db.execute(select(AiConversation).where(AiConversation.id == conversation_id))
    conv = result.scalar_one_or_none()
    if not conv:
        return

    # Only auto-title if still the default
    if conv.title != "Новый чат":
        return

    title = question[:50].strip()
    if len(question) > 50:
        title += "…"
    conv.title = title
    conv.updated_at = utcnow()
    await db.commit()

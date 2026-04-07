"""
AI Chat models: conversations and messages for the web AI agent interface.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin
from backend.utils.time import utcnow


class AiConversation(Base, SoftDeleteMixin, TimestampMixin):
    """A chat conversation with the AI agent system."""

    __tablename__ = "ai_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(500), default="Новый чат")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AiMessage(Base):
    """A single message in an AI conversation."""

    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user, assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    files: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # [{name, type, size, url}]
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    tools_used: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # ["get_bdr_data", ...]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

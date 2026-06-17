"""
Telegram bot models: user linking, chat bindings, brand notes.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.time import utcnow


class TelegramBotUser(Base):
    """Links a Telegram account to a DDS user (via deep link auth)."""

    __tablename__ = "telegram_bot_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TelegramChatBinding(Base):
    """Binds a Telegram chat (group or private) to a project + brand."""

    __tablename__ = "telegram_chat_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ff_notify_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Закреплённое авто-табло заявок ФФ: отдельный opt-in (/board on) + id
    # закреплённого сообщения, которое правим на каждом синке (не шлём новое).
    ff_board_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    ff_board_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BrandNote(Base):
    """Per-brand notes ('memory') for the AI bot."""

    __tablename__ = "brand_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    brand: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_brand_notes_project_id", "project_id"),)

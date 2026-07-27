# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
WB Stock Watches — слежение за поступлением товара по вопросам покупателей.

Сценарий: покупатель спрашивает «когда появится в наличии?» — отвечать нечем,
пока товара нет. Сервис запоминает такой вопрос (status='watching'), ночной/
периодический тик проверяет totalQuantity публичной карточки WB; товар появился
→ создаётся черновик ответа (wb_feedback_replies, is_stock_reply=True) со
status='draft' — отправка по-прежнему ТОЛЬКО вручную после одобрения.

Статусы: watching (ждём остатки) → drafted (черновик создан, reply_id);
dismissed — вопрос отвечен другим способом / слежение снято.
Уникальность (project_id, question_wb_id) — backfill/скан идемпотентны.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin

# Статусы слежения
WATCH_STATUSES = ("watching", "drafted", "dismissed")


class WBStockWatch(Base, TimestampMixin):
    """Одно слежение «вопрос → ждём поступление товара»."""

    __tablename__ = "wb_stock_watches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # WB nmID товара
    question_wb_id: Mapped[str] = mapped_column(String(64), nullable=False)  # wb_id вопроса

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="watching")
    reply_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("wb_feedback_replies.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)  # когда drafted/dismissed

    __table_args__ = (
        UniqueConstraint("project_id", "question_wb_id", name="uq_wb_stock_watches_project_question"),
        Index("ix_wb_stock_watches_project_status", "project_id", "status"),
        Index("ix_wb_stock_watches_nm_id", "nm_id"),
    )

# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
WB Feedback Replies — ответы продавца на отзывы и вопросы покупателей WB.

Единый журнал ответов: черновик (LLM или ручной) → одобрение → отправка в WB
(PATCH /api/v1/feedbacks | /api/v1/questions). Статусы:
draft (ждёт правки/одобрения) → approved (в очереди на отправку) → sent;
error — WB вернул ошибку (текст в `error`), rejected — продавец отклонил черновик.
source: agent (сгенерирован ИИ-агентом) | manual (создан продавцом вручную).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin

# Типы целей и статусы ответа
REPLY_TARGET_TYPES = ("feedback", "question")
REPLY_STATUSES = ("draft", "approved", "sent", "error", "rejected")
REPLY_SOURCES = ("agent", "manual")


class WBFeedbackReply(Base, TimestampMixin):
    """Один ответ продавца на отзыв/вопрос WB (черновик → отправка)."""

    __tablename__ = "wb_feedback_replies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    # ─── Цель ответа ────────────────────────────────────────────────────────
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)  # feedback|question
    target_wb_id: Mapped[str] = mapped_column(String(64), nullable=False)  # wb_id отзыва/вопроса

    # ─── Текст ──────────────────────────────────────────────────────────────
    draft_text: Mapped[str] = mapped_column(Text, nullable=False)  # исходный черновик (LLM/ручной)
    final_text: Mapped[str | None] = mapped_column(Text)  # отредактированный продавцом; None = draft_text

    # ─── Статус и происхождение ─────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # agent|manual
    agent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("wb_reply_agents.id"))

    error: Mapped[str | None] = mapped_column(Text)  # текст ошибки WB при status=error
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)

    # ─── Защита от выдумок (база знаний) ────────────────────────────────────
    # True — в базе знаний не нашлось фактов для ответа: черновик ждёт ручной
    # доработки продавцом (UI подсвечивает). draft_text при этом пустой.
    needs_info: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Как получен черновик: llm — сгенерирован моделью из записей КБ;
    # kb_direct — эталонный ответ КБ взят напрямую (точное совпадение, без LLM);
    # template — шаблонный ответ без LLM (например, поступление товара);
    # None — ручной черновик или needs_info-заглушка.
    generation: Mapped[str | None] = mapped_column(String(16))
    # True — черновик создан слежением за поступлением (wb_stock_watches):
    # ответ на вопрос «когда появится в наличии?» после появления остатков.
    is_stock_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_wb_feedback_replies_project_status", "project_id", "status"),
        Index("ix_wb_feedback_replies_project_target", "project_id", "target_type", "target_wb_id"),
    )

# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
WB Reply Agents — ИИ-агенты автоответов на отзывы и вопросы покупателей.

Агент по фильтрам (тип цели, звёзды для отзывов) отбирает неотвеченные отзывы/
вопросы и по «Правилам» (тон, что обещать/не обещать) генерирует через LLM
(сменный провайдер: OpenAI-совместимый или Claude) черновик ответа →
`wb_feedback_replies`. auto_send=False — черновик ждёт одобрения продавцом
(status draft); True — сразу approved и уходит фоновым отправителем.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin

# Типы целей агента: отзывы / вопросы / и то и другое
REPLY_TARGETS = ("feedback", "question", "both")


class WBReplyAgent(Base, TimestampMixin):
    """ИИ-агент автоответов на отзывы/вопросы покупателей WB."""

    __tablename__ = "wb_reply_agents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ─── Фильтры целей ──────────────────────────────────────────────────────
    target: Mapped[str] = mapped_column(String(16), nullable=False, default="both")  # feedback|question|both
    star_levels: Mapped[str] = mapped_column(String(16), nullable=False, default="1,2,3,4,5")  # для отзывов
    nm_ids: Mapped[str | None] = mapped_column(Text)  # артикулы через запятую; None = все

    # ─── Поведение ──────────────────────────────────────────────────────────
    auto_send: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # True → сразу approved

    # ─── Правила и примеры (тон/ограничения) ────────────────────────────────
    rules: Mapped[str] = mapped_column(Text, nullable=False)  # «Правила для ответа»
    examples: Mapped[str | None] = mapped_column(Text)  # примеры ответов (few-shot)

    # ─── LLM-провайдер (сменный) ────────────────────────────────────────────
    llm_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="openai_compatible")
    llm_model: Mapped[str] = mapped_column(String(64), nullable=False, default="deepseek-chat")
    llm_base_url: Mapped[str | None] = mapped_column(String(200))  # для openai_compatible

    last_run_at: Mapped[datetime | None] = mapped_column()

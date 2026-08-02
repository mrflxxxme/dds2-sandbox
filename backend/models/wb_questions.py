# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
WB Customer Questions — зеркало вопросов покупателей Wildberries.

Mirror endpoint https://feedbacks-api.wildberries.ru/api/v1/questions.
Вопрос — факт из WB: обновляется только через sync (is_answered меняется
false→true, когда продавец ответил; текст ответа складывается в answer_text).
`subject`/`product_name`/`article` — снапшот из WB productDetails (фолбэк для
товаров, которых нет в справочнике Nomenclature), как в wb_feedbacks.
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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin
from backend.utils.time import utcnow


class WBQuestion(Base, TimestampMixin):
    """Один вопрос покупателя WB (зеркало Feedbacks API questions)."""

    __tablename__ = "wb_questions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    # ─── WB API fields (mirror) ─────────────────────────────────────────────
    wb_id: Mapped[str] = mapped_column(String(64), nullable=False)  # id вопроса в WB
    nm_id: Mapped[int | None] = mapped_column(BigInteger)
    text: Mapped[str | None] = mapped_column(Text)
    answer_text: Mapped[str | None] = mapped_column(Text)  # текст ответа продавца (если отвечен)
    is_answered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_date: Mapped[datetime | None] = mapped_column(DateTime)
    user_name: Mapped[str | None] = mapped_column(String(200))

    # ─── Снапшот из WB productDetails (фолбэк для товаров не из Nomenclature) ─
    subject: Mapped[str | None] = mapped_column(String(200))
    product_name: Mapped[str | None] = mapped_column(String(500))
    article: Mapped[str | None] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(200))

    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "wb_id", name="uq_wb_questions_project_wb_id"),
        Index("ix_wb_questions_project_created", "project_id", "created_date"),
        Index("ix_wb_questions_project_nm_id", "project_id", "nm_id"),
        Index("ix_wb_questions_project_answered", "project_id", "is_answered"),
    )

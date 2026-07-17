# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
WB Customer Feedbacks — зеркало отзывов покупателей Wildberries.

Mirror endpoint https://feedbacks-api.wildberries.ru/api/v1/feedbacks (+ архив).
Отзыв — иммутабельный факт: обновляется только через WB API sync (`is_answered`
может смениться с false→true, когда продавец ответил). Категория/бренд/ярлык
резолвятся на лету по `nm_id` (Nomenclature.subject/brand, ProductTagMap.name);
`brand`/`product_name`/`article` — снапшот из WB как фолбэк для товаров, которых
нет в справочнике Nomenclature.
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


class WBFeedback(Base, TimestampMixin):
    """Один отзыв покупателя WB (зеркало Feedbacks API)."""

    __tablename__ = "wb_feedbacks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    # ─── WB API fields (mirror) ─────────────────────────────────────────────
    wb_id: Mapped[str] = mapped_column(String(64), nullable=False)  # id отзыва в WB
    nm_id: Mapped[int | None] = mapped_column(BigInteger)
    rating: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # productValuation 1..5 (0 = не прислан)
    text: Mapped[str | None] = mapped_column(Text)
    pros: Mapped[str | None] = mapped_column(Text)
    cons: Mapped[str | None] = mapped_column(Text)
    # derived: есть ли хоть какой-то текст (text/pros/cons) — клиент что-то написал
    has_text: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_date: Mapped[datetime | None] = mapped_column(DateTime)
    user_name: Mapped[str | None] = mapped_column(String(200))
    is_answered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ─── Снапшот из WB productDetails (фолбэк для товаров не из Nomenclature) ─
    brand: Mapped[str | None] = mapped_column(String(200))
    product_name: Mapped[str | None] = mapped_column(String(500))
    article: Mapped[str | None] = mapped_column(String(200))

    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "wb_id", name="uq_wb_feedbacks_project_wb_id"),
        Index("ix_wb_feedbacks_project_created", "project_id", "created_date"),
        Index("ix_wb_feedbacks_project_nm_id", "project_id", "nm_id"),
        Index("ix_wb_feedbacks_project_rating", "project_id", "rating"),
    )

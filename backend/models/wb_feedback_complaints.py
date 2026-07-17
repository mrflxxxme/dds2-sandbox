# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
WB Feedback Complaints — учёт жалоб на отзывы для их удаления.

Инструмент НЕ отправляет жалобу в WB автоматически (у продавца нет такого API):
он готовит текст жалобы по шаблону (причина «отзыв не относится к товару») и
ФИКСИРУЕТ факт подачи + исход (`status`: подано → удалено/не удалено), давая
оцифровку эффективности (сколько подано, сколько удалено).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
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

# Причины жалобы (совпадают с формой WB)
COMPLAINT_REASONS = ("not_related", "competitors", "other")
# Статусы обработки: подано → удалено / отклонено (не удалено)
COMPLAINT_STATUSES = ("pending", "removed", "rejected")


class WBFeedbackComplaint(Base, TimestampMixin):
    """Жалоба продавца на отзыв покупателя (для удаления)."""

    __tablename__ = "wb_feedback_complaints"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    # Отзыв, на который жалуемся (wb_id из wb_feedbacks) + снапшот для истории
    wb_feedback_id: Mapped[str] = mapped_column(String(64), nullable=False)
    nm_id: Mapped[int | None] = mapped_column(BigInteger)
    rating: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="not_related")
    text: Mapped[str] = mapped_column(Text, nullable=False)  # текст жалобы (по шаблону)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    note: Mapped[str | None] = mapped_column(Text)  # внутренняя пометка (напр. ответ поддержки)
    resolved_at: Mapped[datetime | None] = mapped_column()  # когда проставлен финальный статус

    __table_args__ = (
        UniqueConstraint("project_id", "wb_feedback_id", name="uq_feedback_complaint_project_feedback"),
        Index("ix_feedback_complaints_project_status", "project_id", "status"),
    )

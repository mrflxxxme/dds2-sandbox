"""
CategoryRule model — keyword-based auto-categorization rules.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin


class CategoryRule(Base, SoftDeleteMixin):
    """Rule: if `keyword` found in transaction purpose → assign category."""
    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="expense")  # income / expense
    cat_lvl1: Mapped[str] = mapped_column(String(100), nullable=False)
    cat_lvl2: Mapped[Optional[str]] = mapped_column(String(100))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())

    __table_args__ = (
        UniqueConstraint("project_id", "keyword", "direction", name="uq_category_rule"),
        Index("ix_catrule_project", "project_id"),
    )

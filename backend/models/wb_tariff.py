"""
WB Tariff model: commission rates by product subject.

Loaded from WB xlsx tariff file. Used by funnel profit formula.
Global per project, no expiry dates.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin
from backend.utils.time import utcnow


class WbTariff(Base, SoftDeleteMixin):
    __tablename__ = "wb_tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    subject_name: Mapped[str] = mapped_column(String(200), nullable=False)
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "subject_name", name="uq_wb_tariff_project_subject"),
        Index("ix_wb_tariff_project", "project_id"),
    )

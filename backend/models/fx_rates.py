"""
FX Rate models — stores exchange rates extracted from FX transactions.
"""
from datetime import date as date_type, datetime, timezone

from backend.utils.time import utcnow
from decimal import Decimal
from typing import Optional

from sqlalchemy import String, Integer, Numeric, Date, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class FxRate(Base):
    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    pair: Mapped[str] = mapped_column(String(10), nullable=False, default="CNY/RUB")
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="vtb_import")
    txn_id: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow
    )

    __table_args__ = (
        Index("ix_fx_rate_project_date", "project_id", "date"),
        Index("ix_fx_rate_pair", "pair"),
        UniqueConstraint("project_id", "date", "pair", "txn_id", name="uq_fx_rate_txn"),
    )

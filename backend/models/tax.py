"""
Tax models: TaxRate — per-brand monthly tax rates.
"""

from datetime import datetime

from backend.utils.time import utcnow
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    String, Integer, Boolean, DateTime, Numeric,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class TaxRate(Base):
    """Per-brand monthly tax rate settings.

    tax_regime values:
        - 'usn_income'              — УСН «Доходы»
        - 'usn_income_expense_vat'  — УСН «Доходы − Расходы» с фикс. НДС
    """
    __tablename__ = "tax_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    brand: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_regime: Mapped[str] = mapped_column(String(50), nullable=False, default="usn_income")
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-12
    usn_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    nds_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    cost_as_expense: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("project_id", "brand", "year", "month", name="uq_tax_rate_brand_month"),
        Index("ix_tax_rate_project_year", "project_id", "year"),
    )

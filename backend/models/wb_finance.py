"""
Model: WbFinanceRow — locally cached WB reportDetailByPeriod rows.

Each row represents one line from the WB finance report.
Stored locally to avoid re-fetching from slow WB API on each BDR load.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    String, Integer, BigInteger, Date, DateTime, Numeric,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class WbFinanceRow(Base):
    """Cached WB reportDetailByPeriod row.

    One row per line item from WB finance API.
    Unique by (project_id, rrd_id) — WB provides unique rrd_id per row.
    """
    __tablename__ = "wb_finance_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    # WB report identifiers
    realizationreport_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rrd_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)

    # Product identification
    sa_name: Mapped[Optional[str]] = mapped_column(String(200))
    nm_id: Mapped[int] = mapped_column(Integer, default=0)
    brand_name: Mapped[Optional[str]] = mapped_column(String(200))
    subject_name: Mapped[Optional[str]] = mapped_column(String(200))

    # Per-row dates from WB API
    rr_dt: Mapped[Optional[date]] = mapped_column(Date)  # дата начисления
    sale_dt: Mapped[Optional[date]] = mapped_column(Date)  # дата продажи
    order_dt: Mapped[Optional[date]] = mapped_column(Date)  # дата заказа

    # Operation type
    doc_type_name: Mapped[Optional[str]] = mapped_column(String(100))
    supplier_oper_name: Mapped[Optional[str]] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer, default=0)

    # Financial fields
    retail_price_withdisc_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    retail_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    ppvz_for_pay: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    ppvz_sales_commission: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    delivery_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    penalty: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    additional_payment: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    storage_fee: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    acceptance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    deduction: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    ppvz_reward: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    rebill_logistic_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    ppvz_vw: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    ppvz_vw_nds: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    # Sync metadata
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.utcnow()
    )

    __table_args__ = (
        UniqueConstraint("project_id", "rrd_id", name="uq_wb_finance_rrd"),
        Index("ix_wb_finance_project_dates", "project_id", "date_from", "date_to"),
        Index("ix_wb_finance_project_rr_dt", "project_id", "rr_dt"),
        Index("ix_wb_finance_project_sa", "project_id", "sa_name"),
    )


class WbFinanceSyncLog(Base):
    """Tracks WB finance report sync status per project/week."""
    __tablename__ = "wb_finance_sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    rows_synced: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="OK")  # OK / ERROR
    error_msg: Mapped[Optional[str]] = mapped_column(String(500))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.utcnow()
    )

    __table_args__ = (
        Index("ix_wb_finance_sync_project", "project_id", "synced_at"),
    )

"""
Planning models: Order, LeadTime, PlannedPayment, PlannedIncome, WbPayout, PaymentFactLink.
"""

from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Numeric, Text, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin


class Order(Base, SoftDeleteMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    order_name: Mapped[Optional[str]] = mapped_column(String(200))
    category: Mapped[Optional[str]] = mapped_column(String(100))
    transport_type: Mapped[Optional[str]] = mapped_column(String(30))
    order_no: Mapped[Optional[int]] = mapped_column(Integer, unique=True)
    supplier: Mapped[Optional[str]] = mapped_column(String(100))
    planned_ship_date: Mapped[Optional[date]] = mapped_column(Date)
    actual_ship_date: Mapped[Optional[date]] = mapped_column(Date)
    order_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    deposit: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    logistics_cny: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    customs_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    source_file: Mapped[Optional[str]] = mapped_column(String(200))


class LeadTime(Base):
    __tablename__ = "lead_time"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    direction: Mapped[str] = mapped_column(String(30), nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "direction", name="uq_lead_time_project_dir"),
    )


class PlannedPayment(Base, SoftDeleteMixin):
    __tablename__ = "planned_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    pay_date: Mapped[Optional[date]] = mapped_column(Date)
    order_no: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("orders.order_no"))
    direction: Mapped[Optional[str]] = mapped_column(String(50))
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    fx_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6))
    amount_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    paid_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)


class PlannedIncome(Base):
    __tablename__ = "planned_incomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    source: Mapped[str] = mapped_column(String(50), default="WB")


class WbPayout(Base):
    """WB seller cabinet payout tracking."""
    __tablename__ = "wb_payouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    request_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="RUB")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    wb_status_raw: Mapped[Optional[str]] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    bank_comment: Mapped[Optional[str]] = mapped_column(Text)
    matched_txn_id: Mapped[Optional[str]] = mapped_column(String(300))
    matched_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_wb_payout_status", "status"),
        Index("ix_wb_payout_created", "created_at"),
    )


class PaymentFactLink(Base):
    """Manual link between PlannedPayment and Transaction for fact matching."""
    __tablename__ = "payment_fact_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(Integer, ForeignKey("planned_payments.id"), nullable=False)
    txn_id: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(200))

"""
Planning models: Order, LeadTime, PlannedPayment, PlannedIncome, WbPayout, PaymentFactLink.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin
from backend.utils.time import utcnow


class Order(Base, SoftDeleteMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    order_name: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(100))
    transport_type: Mapped[str | None] = mapped_column(String(30))
    order_no: Mapped[int | None] = mapped_column(Integer, unique=True)
    supplier: Mapped[str | None] = mapped_column(String(100))
    planned_ship_date: Mapped[date | None] = mapped_column(Date)
    actual_ship_date: Mapped[date | None] = mapped_column(Date)
    order_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    deposit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    logistics_cny: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    customs_rub: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    source_file: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (Index("ix_orders_project_id", "project_id"),)


class LeadTime(Base):
    __tablename__ = "lead_time"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    direction: Mapped[str] = mapped_column(String(30), nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (UniqueConstraint("project_id", "direction", name="uq_lead_time_project_dir"),)


class PlannedPayment(Base, SoftDeleteMixin):
    __tablename__ = "planned_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    pay_date: Mapped[date | None] = mapped_column(Date)
    order_no: Mapped[int | None] = mapped_column(Integer, ForeignKey("orders.order_no"))
    direction: Mapped[str | None] = mapped_column(String(50))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(10))
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    amount_rub: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    paid_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)  # fact in original currency (e.g. CNY)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_planned_payments_project_id", "project_id"),)


class PlannedIncome(Base, SoftDeleteMixin):
    __tablename__ = "planned_incomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    source: Mapped[str] = mapped_column(String(50), default="WB")

    __table_args__ = (Index("ix_planned_incomes_project_id", "project_id"),)


class WbPayout(Base, SoftDeleteMixin):
    """WB seller cabinet payout tracking."""

    __tablename__ = "wb_payouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="RUB")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    wb_status_raw: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    bank_comment: Mapped[str | None] = mapped_column(Text)
    matched_txn_id: Mapped[str | None] = mapped_column(String(300))
    matched_at: Mapped[datetime | None] = mapped_column(DateTime)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "request_id", name="uq_wb_payout_project_request"),
        Index("ix_wb_payout_status", "status"),
        Index("ix_wb_payout_created", "created_at"),
    )


class PaymentFactLink(Base, SoftDeleteMixin):
    """Manual link between PlannedPayment and Transaction for fact matching."""

    __tablename__ = "payment_fact_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(Integer, ForeignKey("planned_payments.id"), nullable=False)
    txn_id: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (Index("ix_payment_fact_links_payment_id", "payment_id"),)


class BrandPlan(Base):
    """Monthly revenue plan per brand."""

    __tablename__ = "brand_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    brand: Mapped[str] = mapped_column(String(200), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "brand", "year", "month", name="uq_brand_plan_project_brand_ym"),
        Index("ix_brand_plans_project_id", "project_id"),
    )

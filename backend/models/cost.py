"""
Cost models: Nomenclature, DutyRule, CostOrder, CostOrderItem.
"""

from datetime import datetime, date

from backend.utils.time import utcnow
from decimal import Decimal
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Numeric, Text, ForeignKey, Index, Enum as SAEnum, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.enums import DutyBasis
from backend.models.mixins import SoftDeleteMixin


class Nomenclature(Base):
    __tablename__ = "nomenclature"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(100))
    subject: Mapped[Optional[str]] = mapped_column(String(100))
    article_seller: Mapped[Optional[str]] = mapped_column(String(100))
    article_wb: Mapped[Optional[int]] = mapped_column(Integer)
    volume_l: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "barcode", name="uq_nomenclature_project_barcode"),
    )


class DutyRule(Base, SoftDeleteMixin):
    __tablename__ = "duty_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    basis: Mapped[str] = mapped_column(SAEnum(DutyBasis), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    util_collect_rub: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    note: Mapped[Optional[str]] = mapped_column(String(200))

    __table_args__ = (
        UniqueConstraint("project_id", "subject", name="uq_duty_rule_project_subject"),
    )


class CostOrder(Base, SoftDeleteMixin):
    __tablename__ = "cost_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    order_no: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    invoice_no: Mapped[Optional[str]] = mapped_column(String(100))
    ship_date: Mapped[Optional[date]] = mapped_column(Date)
    actual_arrival_date: Mapped[Optional[date]] = mapped_column(Date)
    transport_type: Mapped[Optional[str]] = mapped_column(String(30), default="AUTO")
    delivery_cost_cny: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    delivery_cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    rate_cny: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=1)
    rate_eur: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=1)
    rate_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=1)
    note: Mapped[Optional[str]] = mapped_column(Text)
    dt_number: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    items: Mapped[list["CostOrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_cost_orders_project_id", "project_id"),
    )


class CostOrderItem(Base):
    __tablename__ = "cost_order_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(50), ForeignKey("cost_orders.order_no"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String(100))
    article_seller: Mapped[Optional[str]] = mapped_column(String(100))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    price_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    weight_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    area_m2: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    volume_m3: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    # Calculated fields (stored for reporting)
    cost_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    delivery_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    duty_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    vat_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    util_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    total_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    total_cny: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    unrecognized: Mapped[bool] = mapped_column(Boolean, default=False)
    order: Mapped["CostOrder"] = relationship(back_populates="items")
    __table_args__ = (Index("ix_cost_item_order", "order_no"),)

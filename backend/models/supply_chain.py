"""
Supply Chain models: FactoryOrder, FactoryOrderItem, FactoryOrderHistory,
VehicleDocument, VehicleStatusHistory.

Represents factory orders (1 order = 1 factory) that get split
into vehicles (CostOrder) for delivery.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin
from backend.utils.time import utcnow


class Supplier(Base, TimestampMixin, SoftDeleteMixin):
    """
    Supplier reference — one supplier = one factory/vendor.
    Supports Chinese (CNY) and Russian (RUB) suppliers.
    """

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(20), nullable=False, default="CHINA", server_default="CHINA")
    currency: Mapped[str] = mapped_column(String(5), nullable=False, default="CNY", server_default="CNY")
    delivery_days_min: Mapped[int | None] = mapped_column(Integer)
    delivery_days_max: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)

    factory_orders: Mapped[list["FactoryOrder"]] = relationship(back_populates="supplier")

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_supplier_project_name"),
        Index("ix_suppliers_project_id", "project_id"),
    )


class FactoryOrder(Base, TimestampMixin, SoftDeleteMixin):
    """
    Factory order — one order placed to one factory.
    Items from a factory order get distributed across vehicles (CostOrder).
    total_cny stores the order cost in the supplier's currency (CNY or RUB).
    """

    __tablename__ = "factory_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    order_number: Mapped[str] = mapped_column(String(50), nullable=False)
    factory_name: Mapped[str | None] = mapped_column(String(200))
    supplier_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("suppliers.id"), nullable=True)
    order_date: Mapped[date | None] = mapped_column(Date)
    expected_ready_date: Mapped[date | None] = mapped_column(Date)
    total_cny: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="FORMING", server_default="FORMING")
    note: Mapped[str | None] = mapped_column(Text)

    supplier: Mapped["Supplier | None"] = relationship(back_populates="factory_orders")
    items: Mapped[list["FactoryOrderItem"]] = relationship(back_populates="factory_order", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("project_id", "order_number", name="uq_factory_order_project_number"),
        Index("ix_factory_orders_project_id", "project_id"),
        Index("ix_factory_orders_supplier_id", "supplier_id"),
    )


class FactoryOrderItem(Base, SoftDeleteMixin):
    """
    Line item in a factory order.
    Tracks ordered qty and how much has been assigned to vehicles.
    """

    __tablename__ = "factory_order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    factory_order_id: Mapped[int] = mapped_column(Integer, ForeignKey("factory_orders.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(100))
    article_seller: Mapped[str | None] = mapped_column(String(100))
    qty: Mapped[int] = mapped_column(Integer, default=0)
    assigned_qty: Mapped[int] = mapped_column(Integer, default=0)
    price_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    box_size: Mapped[str | None] = mapped_column(String(50))  # e.g. "40x30x25"
    pcs_per_box: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    note: Mapped[str | None] = mapped_column(Text)
    box_detail: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True, default=None)
    mix_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    mix_box_size: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    mix_pcs_per_box: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    factory_order: Mapped["FactoryOrder"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_factory_order_items_project_id", "project_id"),
        Index("ix_factory_order_items_order_id", "factory_order_id"),
        Index(
            "ix_factory_order_items_mix_group",
            "factory_order_id",
            "mix_group_id",
            postgresql_where="mix_group_id IS NOT NULL",
        ),
    )


# ─── Vehicle Documents ──────────────────────────────────────────────────────


class VehicleDocument(Base, TimestampMixin, SoftDeleteMixin):
    """
    Document attached to a vehicle (CostOrder).
    Files stored in MinIO, metadata in DB.
    """

    __tablename__ = "vehicle_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    order_no: Mapped[str] = mapped_column(String(50), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(30), nullable=False, default="other")
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_vehicle_documents_project_id", "project_id"),
        Index("ix_vehicle_documents_order_no", "order_no"),
    )


# ─── Vehicle Status History ─────────────────────────────────────────────────


class VehicleStatusHistory(Base):
    """Audit log for vehicle (CostOrder) status transitions."""

    __tablename__ = "vehicle_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    order_no: Mapped[str] = mapped_column(String(50), nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    changed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_vehicle_status_history_project_id", "project_id"),
        Index("ix_vehicle_status_history_order_no", "order_no"),
    )


# ─── Factory Order History ─────────────────────────────────────────────────


class FactoryOrderHistory(Base):
    """Audit log for factory order events: status changes, distribution, item changes."""

    __tablename__ = "factory_order_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    factory_order_id: Mapped[int] = mapped_column(Integer, ForeignKey("factory_orders.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # created, status_change, distributed, items_added, items_removed
    old_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)  # human-readable description
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    changed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        Index("ix_factory_order_history_project_id", "project_id"),
        Index("ix_factory_order_history_order_id", "factory_order_id"),
    )

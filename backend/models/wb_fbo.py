"""
WB FBO Supplies models: WbFboSupply, WbFboSupplyItem.
Sync data from WB Marketplace API — read-only statuses, linked to OutboundShipment.
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import TimestampMixin
from backend.utils.time import utcnow

# ─── Enums ──────────────────────────────────────────────────────────────────


class WbSupplyStatus(str, enum.Enum):
    """WB supply statuses — read-only, set only via WB API sync."""

    ACTIVE = "ACTIVE"  # Запланирована
    ON_DELIVERY = "ON_DELIVERY"  # В пути  # noqa: RUF003
    IN_PROGRESS = "IN_PROGRESS"  # Разгрузка разрешена
    ACCEPTED = "ACCEPTED"  # Принята
    CANCELLED = "CANCELLED"  # Отменена


# ─── WB FBO Supply ──────────────────────────────────────────────────────────


class WbFboSupply(Base, TimestampMixin):
    """
    FBO supply from WB Marketplace API.
    Statuses are read-only — updated only via sync.
    Linked to OutboundShipment via outbound_shipment_id.
    """

    __tablename__ = "wb_fbo_supplies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    wb_supply_id: Mapped[str] = mapped_column(String(50), nullable=False)
    wb_status: Mapped[str] = mapped_column(String(30), nullable=False, default=WbSupplyStatus.ACTIVE)
    name: Mapped[str | None] = mapped_column(String(200))
    created_at_wb: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    planned_date: Mapped[date | None] = mapped_column(Date)
    actual_date: Mapped[date | None] = mapped_column(Date)
    warehouse_name: Mapped[str | None] = mapped_column(String(200))
    cargo_type: Mapped[str | None] = mapped_column(String(50))
    total_qty: Mapped[int] = mapped_column(Integer, default=0)
    accepted_qty: Mapped[int] = mapped_column(Integer, default=0)
    outbound_shipment_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("outbound_shipments.id"),
        nullable=True,
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Relationships
    items: Mapped[list["WbFboSupplyItem"]] = relationship(
        back_populates="supply",
        cascade="all, delete-orphan",
    )
    outbound_shipment = relationship(
        "OutboundShipment",
        foreign_keys=[outbound_shipment_id],
    )

    __table_args__ = (
        UniqueConstraint("project_id", "wb_supply_id", name="uq_wb_fbo_supply"),
        Index("ix_wb_fbo_supplies_project_id", "project_id"),
        Index("ix_wb_fbo_supplies_wb_status", "wb_status"),
        Index("ix_wb_fbo_supplies_created_at_wb", "created_at_wb"),
    )


# ─── WB FBO Supply Item ────────────────────────────────────────────────────


class WbFboSupplyItem(Base):
    """Item (order) within a WB FBO supply."""

    __tablename__ = "wb_fbo_supply_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supply_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("wb_fbo_supplies.id", ondelete="CASCADE"),
        nullable=False,
    )
    wb_order_id: Mapped[str] = mapped_column(String(50), nullable=False)
    nm_id: Mapped[int | None] = mapped_column(Integer)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    article_seller: Mapped[str | None] = mapped_column(String(100))
    product_name: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    accepted_qty: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    supply: Mapped["WbFboSupply"] = relationship(back_populates="items")

    __table_args__ = (Index("ix_wb_fbo_supply_items_supply_id", "supply_id"),)

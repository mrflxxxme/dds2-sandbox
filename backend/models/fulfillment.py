# ruff: noqa: RUF002, RUF003
"""
Fulfillment integration models: FF stock snapshots and request mirrors.

Generic layer for external fulfilment providers (skladbot.ru now, migfull
later). Data here is a read-only mirror of the provider state — it never
touches WarehouseStock / StockMovement, which stay document-driven.
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.time import utcnow


class FulfillmentProvider(str, enum.Enum):
    SKLADBOT = "skladbot"
    MIGFULL = "migfull"  # «Натали» — будущий провайдер


class FfRequestKind(str, enum.Enum):
    ASSEMBLY = "assembly"  # доставка на склад МП (заявка на сборку)
    INBOUND = "inbound"  # приёмка на склад ФФ
    OTHER = "other"


class FulfillmentStock(Base):
    """Снапшот остатков фулфилмента; перезаписывается целиком при каждом синке."""

    __tablename__ = "fulfillment_stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    barcode: Mapped[str] = mapped_column(String(100), nullable=False)
    nomenclature_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("nomenclature.id"))
    name: Mapped[str | None] = mapped_column(String(500))
    vendor_code: Mapped[str | None] = mapped_column(String(200))
    qty_good: Mapped[int] = mapped_column(Integer, default=0)
    qty_reserve: Mapped[int] = mapped_column(Integer, default=0)
    qty_defect: Mapped[int] = mapped_column(Integer, default=0)
    qty_nominal: Mapped[int] = mapped_column(Integer, default=0)
    external_product_id: Mapped[str | None] = mapped_column(String(100))
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "warehouse_id", "barcode", name="uq_ff_stock_wh_barcode"),
        Index("ix_fulfillment_stocks_project_wh", "project_id", "warehouse_id"),
        Index("ix_fulfillment_stocks_nomenclature_id", "nomenclature_id"),
    )


class FulfillmentRequest(Base):
    """Зеркало заявки фулфилмента (сборка/приёмка) + связь с нашими документами."""

    __tablename__ = "fulfillment_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)  # id заявки у ФФ
    number: Mapped[str | None] = mapped_column(String(100))  # WH-R-195847
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=FfRequestKind.OTHER.value)
    type_id: Mapped[int | None] = mapped_column(Integer)
    type_name: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str | None] = mapped_column(String(50))
    stage_code: Mapped[str | None] = mapped_column(String(100))
    stage_title: Mapped[str | None] = mapped_column(String(200))
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expired: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    external_created_at: Mapped[date | None] = mapped_column(Date)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    # Связь с нашими документами (ручная в фазе 1, автоматическая при push в фазе 2)
    assembly_request_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("assembly_requests.id"))
    inbound_receipt_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("inbound_receipts.id"))

    __table_args__ = (
        UniqueConstraint("project_id", "provider", "external_id", name="uq_ff_request_external"),
        Index("ix_fulfillment_requests_project_wh", "project_id", "warehouse_id"),
        Index("ix_fulfillment_requests_assembly_request_id", "assembly_request_id"),
        Index("ix_fulfillment_requests_inbound_receipt_id", "inbound_receipt_id"),
    )

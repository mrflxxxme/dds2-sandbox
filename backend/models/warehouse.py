"""
Warehouse models: Warehouse, InboundReceipt, OutboundShipment,
StockTransfer, StockMovement, WarehouseStock, StockAdjustment + item tables.
"""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin
from backend.utils.time import utcnow

# ─── Enums ──────────────────────────────────────────────────────────────────


class WarehouseType(str, enum.Enum):
    EXTERNAL = "EXTERNAL"
    FULFILLMENT = "FULFILLMENT"


class InboundStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    EXPECTED = "EXPECTED"
    ACCEPTED = "ACCEPTED"
    CANCELLED = "CANCELLED"


class OutboundStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class TransferStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    IN_TRANSIT = "IN_TRANSIT"
    COMPLETED = "COMPLETED"


class DefectMarkStatus(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    CANCELLED = "CANCELLED"


class MovementType(str, enum.Enum):
    INBOUND = "INBOUND"
    INBOUND_CANCEL = "INBOUND_CANCEL"
    OUTBOUND = "OUTBOUND"
    OUTBOUND_CANCEL = "OUTBOUND_CANCEL"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    INBOUND_EDIT = "INBOUND_EDIT"
    ADJUSTMENT = "ADJUSTMENT"
    # Defective goods (брак)
    DEFECT_MARK = "DEFECT_MARK"
    DEFECT_RECEIVE = "DEFECT_RECEIVE"
    DEFECT_WRITEOFF = "DEFECT_WRITEOFF"
    DEFECT_RECOVER = "DEFECT_RECOVER"
    DEFECT_TRANSFER_OUT = "DEFECT_TRANSFER_OUT"
    DEFECT_TRANSFER_IN = "DEFECT_TRANSFER_IN"


# ─── Warehouse ──────────────────────────────────────────────────────────────


class Warehouse(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    warehouse_type: Mapped[str] = mapped_column(String(20), nullable=False)
    country: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(500))
    assembly_days: Mapped[int | None] = mapped_column(Integer)
    wb_acceptance_days: Mapped[int] = mapped_column(Integer, default=2)
    external_id: Mapped[str | None] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Counterparty link (Phase 1: counterparties-loans)
    counterparty_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("counterparty.id"), nullable=True)

    # Relationships
    receipts: Mapped[list["InboundReceipt"]] = relationship(back_populates="warehouse")
    shipments: Mapped[list["OutboundShipment"]] = relationship(back_populates="warehouse")

    __table_args__ = (
        Index("ix_warehouses_project_id", "project_id"),
        # Partial index created via CONCURRENTLY in migration:
        #   ix_warehouses_counterparty_id  (counterparty_id)
    )


# ─── Inbound Receipt (Приёмка) ──────────────────────────────────────────────


class InboundReceipt(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "inbound_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=InboundStatus.DRAFT, nullable=False)
    planned_date: Mapped[date | None] = mapped_column(Date)
    actual_date: Mapped[date | None] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text)  # JSON array
    cost_order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("cost_orders.id"))
    is_defect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    defect_reason: Mapped[str | None] = mapped_column(Text)

    # Возврат сборки: к какой заявке/попытке относится этот приём-возврат.
    # warehouse_id выше = склад, НА который вернули (может отличаться от склада-источника).
    assembly_request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="SET NULL"), nullable=True
    )
    assembly_attempt_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    warehouse: Mapped["Warehouse"] = relationship(back_populates="receipts")
    items: Mapped[list["InboundReceiptItem"]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_inbound_receipts_project_id", "project_id"),
        Index("ix_inbound_receipts_warehouse_id", "warehouse_id"),
        Index("ix_inbound_receipts_assembly_request_id", "assembly_request_id"),
    )


class InboundReceiptItem(Base):
    __tablename__ = "inbound_receipt_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    receipt_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inbound_receipts.id", ondelete="CASCADE"), nullable=False
    )
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    expected_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_qty: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    receipt: Mapped["InboundReceipt"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_inbound_receipt_items_project_id", "project_id"),
        Index("ix_inbound_receipt_items_receipt_id", "receipt_id"),
        Index("ix_inbound_receipt_items_nomenclature_id", "nomenclature_id"),
    )


# ─── Outbound Shipment (Отгрузка) ──────────────────────────────────────────


class OutboundShipment(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "outbound_shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=OutboundStatus.DRAFT, nullable=False)
    destination: Mapped[str | None] = mapped_column(String(200))
    wb_supply_id: Mapped[str | None] = mapped_column(String(100))
    shipped_date: Mapped[date | None] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(Text)
    is_defect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    defect_reason: Mapped[str | None] = mapped_column(Text)

    # ─── Assembly shipping attempt (цепочка попыток отгрузки) ──────────────
    # Долговечная 1:N связь заявка→отгрузки (на AssemblyRequest хранится лишь
    # последняя попытка-зеркало и затирается при переотгрузке). Каждая отгрузка
    # одной заявки = одна попытка с собственным водителем/FBW/складом WB/стоимостью.
    assembly_request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="SET NULL"), nullable=True
    )
    wb_fbo_supply_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("wb_fbo_supplies.id", ondelete="SET NULL"), nullable=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Снимок логистики попытки на момент отгрузки — на AssemblyRequest эти поля
    # перезатираются при назначении нового водителя; здесь сохраняются по-попыточно.
    pickup_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    vehicle_info: Mapped[str | None] = mapped_column(String(300), nullable=True)
    vehicle_brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    counterparty_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("counterparty.id", ondelete="SET NULL"), nullable=True
    )
    pickup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pickup_time_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pallets_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pallet_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    # Relationships
    warehouse: Mapped["Warehouse"] = relationship(back_populates="shipments")
    items: Mapped[list["OutboundShipmentItem"]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_outbound_shipments_project_id", "project_id"),
        Index("ix_outbound_shipments_warehouse_id", "warehouse_id"),
        Index("ix_outbound_shipments_assembly_request_id", "assembly_request_id"),
        Index("ix_outbound_shipments_wb_fbo_supply_id", "wb_fbo_supply_id"),
        Index("ix_outbound_shipments_counterparty_id", "counterparty_id"),
    )


class OutboundShipmentItem(Base):
    __tablename__ = "outbound_shipment_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    shipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("outbound_shipments.id", ondelete="CASCADE"), nullable=False
    )
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    shipment: Mapped["OutboundShipment"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_outbound_shipment_items_project_id", "project_id"),
        Index("ix_outbound_shipment_items_shipment_id", "shipment_id"),
        Index("ix_outbound_shipment_items_nomenclature_id", "nomenclature_id"),
    )


# ─── Stock Transfer (Перемещение) ──────────────────────────────────────────


class StockTransfer(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "stock_transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    from_warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    to_warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=TransferStatus.DRAFT, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    is_defect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    defect_reason: Mapped[str | None] = mapped_column(Text)

    # Relationships
    items: Mapped[list["StockTransferItem"]] = relationship(
        back_populates="transfer",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_stock_transfers_project_id", "project_id"),)


class StockTransferItem(Base):
    __tablename__ = "stock_transfer_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    transfer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stock_transfers.id", ondelete="CASCADE"), nullable=False
    )
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    transfer: Mapped["StockTransfer"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_stock_transfer_items_project_id", "project_id"),
        Index("ix_stock_transfer_items_transfer_id", "transfer_id"),
        Index("ix_stock_transfer_items_nomenclature_id", "nomenclature_id"),
    )


# ─── Defect Mark Operation (Пометка годных как брак) ─────────────────────


class DefectMarkOperation(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "defect_mark_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=DefectMarkStatus.ACCEPTED, nullable=False)
    actual_date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["DefectMarkOperationItem"]] = relationship(
        back_populates="operation",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_defect_mark_operations_project_id", "project_id"),
        Index("ix_defect_mark_operations_warehouse_id", "warehouse_id"),
    )


class DefectMarkOperationItem(Base):
    __tablename__ = "defect_mark_operation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    operation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("defect_mark_operations.id", ondelete="CASCADE"), nullable=False
    )
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    operation: Mapped["DefectMarkOperation"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_defect_mark_operation_items_project_id", "project_id"),
        Index("ix_defect_mark_operation_items_operation_id", "operation_id"),
        Index("ix_defect_mark_operation_items_nomenclature_id", "nomenclature_id"),
    )


# ─── Stock Movements (Журнал движений — аудит) ────────────────────────────


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # +приход / -расход
    defect_delta: Mapped[int] = mapped_column(Integer, default=0)  # +приход / -расход дефектов
    reference_type: Mapped[str] = mapped_column(String(30), nullable=False)  # RECEIPT/SHIPMENT/TRANSFER/ADJUSTMENT
    reference_id: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_stock_movements_project_id", "project_id"),
        Index("ix_stock_movements_warehouse_id", "warehouse_id"),
        Index("ix_stock_movements_nomenclature_id", "nomenclature_id"),
        Index("ix_stock_movements_created_at", "created_at"),
    )


# ─── Warehouse Stock (Материализованный баланс) ───────────────────────────


class WarehouseStock(Base):
    __tablename__ = "warehouse_stock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    in_transit: Mapped[int] = mapped_column(Integer, default=0)
    defect_quantity: Mapped[int] = mapped_column(Integer, default=0)
    defect_in_transit: Mapped[int] = mapped_column(Integer, default=0)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "warehouse_id", "nomenclature_id", name="uq_warehouse_stock"),
        Index("ix_warehouse_stock_project_id", "project_id"),
        Index("ix_warehouse_stock_warehouse_id", "warehouse_id"),
        Index("ix_warehouse_stock_nomenclature_id", "nomenclature_id"),
    )


# ─── Stock Adjustment (Корректировка / Инвентаризация) ─────────────────────


class StockAdjustment(Base, TimestampMixin):
    __tablename__ = "stock_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)  # +излишек / -недостача
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_stock_adjustments_project_id", "project_id"),
        Index("ix_stock_adjustments_nomenclature_id", "nomenclature_id"),
    )


# ─── Warehouse Delivery Times (Время доставки до WB) ─────────────────────


class WarehouseDeliveryTime(Base, TimestampMixin):
    __tablename__ = "warehouse_delivery_times"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    wb_warehouse_name: Mapped[str] = mapped_column(String(255), nullable=False)
    delivery_days: Mapped[int] = mapped_column(Integer, default=3)

    __table_args__ = (
        UniqueConstraint("project_id", "warehouse_id", "wb_warehouse_name", name="uq_wh_delivery_time"),
        Index("ix_warehouse_delivery_times_project_id", "project_id"),
        Index("ix_warehouse_delivery_times_warehouse_id", "warehouse_id"),
    )

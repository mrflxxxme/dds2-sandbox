"""
Warehouse schemas: request/response models for warehouse module.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

# ─── Warehouse ──────────────────────────────────────────────────────────────


class WarehouseCreate(BaseModel):
    name: str
    warehouse_type: str  # EXTERNAL | FULFILLMENT
    country: str | None = None
    address: str | None = None
    assembly_days: int | None = None
    sort_order: int = 0


class WarehouseUpdate(BaseModel):
    name: str | None = None
    warehouse_type: str | None = None
    country: str | None = None
    address: str | None = None
    assembly_days: int | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class WarehouseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    warehouse_type: str
    country: str | None = None
    address: str | None = None
    assembly_days: int | None = None
    external_id: str | None = None
    sort_order: int = 0
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WarehouseReorder(BaseModel):
    """Reorder warehouses: list of {id, sort_order}."""

    items: list[dict]  # [{id: int, sort_order: int}, ...]


# ─── Inbound Receipt (Приёмка) ──────────────────────────────────────────────


class InboundReceiptItemCreate(BaseModel):
    barcode: str
    expected_qty: int
    actual_qty: int = 0


class InboundReceiptItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    receipt_id: int
    nomenclature_id: int
    barcode: str
    expected_qty: int
    actual_qty: int


class InboundReceiptCreate(BaseModel):
    planned_date: date | None = None
    comment: str | None = None
    tags: str | None = None  # JSON array string
    items: list[InboundReceiptItemCreate] = []


class InboundReceiptUpdate(BaseModel):
    planned_date: date | None = None
    comment: str | None = None
    tags: str | None = None
    items: list[InboundReceiptItemCreate] | None = None


class InboundReceiptSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    number: str
    status: str
    planned_date: date | None = None
    actual_date: date | None = None
    comment: str | None = None
    tags: str | None = None
    cost_order_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[InboundReceiptItemSchema] = []


# ─── Outbound Shipment (Отгрузка) ──────────────────────────────────────────


class OutboundShipmentItemCreate(BaseModel):
    barcode: str
    quantity: int


class OutboundShipmentItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    shipment_id: int
    nomenclature_id: int
    barcode: str
    quantity: int


class OutboundShipmentCreate(BaseModel):
    destination: str | None = None
    comment: str | None = None
    items: list[OutboundShipmentItemCreate] = []


class OutboundShipmentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    number: str
    status: str
    destination: str | None = None
    wb_supply_id: str | None = None
    shipped_date: date | None = None
    comment: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[OutboundShipmentItemSchema] = []


# ─── Stock Transfer (Перемещение) ──────────────────────────────────────────


class StockTransferItemCreate(BaseModel):
    barcode: str
    quantity: int


class StockTransferItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    transfer_id: int
    nomenclature_id: int
    barcode: str
    quantity: int


class StockTransferCreate(BaseModel):
    from_warehouse_id: int
    to_warehouse_id: int
    comment: str | None = None
    items: list[StockTransferItemCreate] = []


class StockTransferSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    from_warehouse_id: int
    to_warehouse_id: int
    number: str
    status: str
    comment: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[StockTransferItemSchema] = []


# ─── Stock Movement (Журнал) ──────────────────────────────────────────────


class StockMovementSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    nomenclature_id: int
    barcode: str
    movement_type: str
    quantity: int
    reference_type: str
    reference_id: int | None = None
    comment: str | None = None
    created_at: datetime | None = None


# ─── Warehouse Stock (Баланс) ─────────────────────────────────────────────


class WarehouseStockSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    nomenclature_id: int
    barcode: str
    quantity: int
    in_transit: int = 0
    cost_price: Decimal | None = None
    updated_at: datetime | None = None


class CostPriceUpdate(BaseModel):
    cost_price: Decimal


# ─── Stock Adjustment (Корректировка) ─────────────────────────────────────


class StockAdjustmentCreate(BaseModel):
    barcode: str
    delta: int
    reason: str


class StockAdjustmentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    nomenclature_id: int
    barcode: str
    delta: int
    reason: str
    created_at: datetime | None = None

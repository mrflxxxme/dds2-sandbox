"""
Assembly Request schemas: Pydantic request/response models.
See backend/DOMAIN_ASSEMBLY.md for full spec.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

# ─── Request schemas ────────────────────────────────────────────────────────


class AssemblyItemCreate(BaseModel):
    barcode: str
    quantity: int


class AssemblyRequestCreate(BaseModel):
    warehouse_id: int
    wb_fbo_supply_id: int
    estimated_ready_date: date | None = None
    pallets_count: int
    pallet_weight_kg: Decimal
    comment: str | None = None
    items: list[AssemblyItemCreate]


class AssemblyRequestUpdate(BaseModel):
    estimated_ready_date: date | None = None
    pallets_count: int | None = None
    pallet_weight_kg: Decimal | None = None
    comment: str | None = None
    items: list[AssemblyItemCreate] | None = None  # only PENDING


class AssignVehicle(BaseModel):
    vehicle_info: str
    vehicle_brand: str
    driver_phone: str
    pickup_date: date
    pickup_time_slot: str
    pickup_cost: Decimal
    delivery_date: date


class BulkAssignItem(BaseModel):
    request_id: int
    pickup_date: date
    pickup_time_slot: str
    pickup_cost: Decimal
    delivery_date: date


class AssignVehicleBulk(BaseModel):
    vehicle_info: str
    vehicle_brand: str
    driver_phone: str
    items: list[BulkAssignItem]


class ShipBulk(BaseModel):
    ids: list[int]


# ─── Response schemas ───────────────────────────────────────────────────────


class AssemblyItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nomenclature_id: int
    barcode: str
    quantity: int
    product_name: str | None = None
    stock_quantity: int = 0


class AssemblyRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_id: int
    warehouse_name: str | None = None
    number: str
    status: str
    wb_fbo_supply_id: int
    wb_supply_name: str | None = None  # wb_fbo_supplies.name
    wb_warehouse_name: str | None = None  # wb_fbo_supplies.warehouse_name (WB destination)
    wb_supply_id_wb: str | None = None  # wb_fbo_supplies.wb_supply_id (WB-I-xxxx)
    wb_fbo_status: str | None = None  # wb_fbo_supplies.wb_status (ACTIVE/ON_DELIVERY/...)
    wb_fbo_planned_date: date | None = None  # wb_fbo_supplies.planned_date
    wb_fbo_actual_date: date | None = None  # wb_fbo_supplies.actual_date
    outbound_shipment_id: int | None = None
    estimated_ready_date: date | None = None
    actual_ready_date: date | None = None
    pallets_count: int
    pallet_weight_kg: Decimal
    total_weight_kg: Decimal | None = None  # computed: pallets x weight
    vehicle_info: str | None = None
    vehicle_brand: str | None = None
    driver_phone: str | None = None
    pickup_date: date | None = None
    pickup_time_slot: str | None = None
    pickup_cost: Decimal | None = None
    delivery_date: date | None = None
    vehicle_assigned_at: datetime | None = None
    shipped_at: datetime | None = None
    comment: str | None = None
    items: list[AssemblyItemResponse] = []
    created_at: datetime
    updated_at: datetime


class AssemblyListResponse(BaseModel):
    items: list[AssemblyRequestResponse]
    total: int


class RefreshFromFboResponse(BaseModel):
    added: int
    removed: int
    changed: int
    items: list[AssemblyItemResponse]


class AssemblyHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    old_status: str | None
    new_status: str
    changed_at: datetime
    changed_by: str | None
    comment: str | None


class StockDeficit(BaseModel):
    barcode: str
    need: int
    have: int

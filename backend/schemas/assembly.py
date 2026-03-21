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


class AssignVehicleBulk(BaseModel):
    ids: list[int]
    vehicle_info: str


class ShipBulk(BaseModel):
    ids: list[int]


# ─── Response schemas ───────────────────────────────────────────────────────


class AssemblyItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nomenclature_id: int
    barcode: str
    quantity: int


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
    outbound_shipment_id: int | None = None
    estimated_ready_date: date | None = None
    actual_ready_date: date | None = None
    pallets_count: int
    pallet_weight_kg: Decimal
    total_weight_kg: Decimal | None = None  # computed: pallets x weight
    vehicle_info: str | None = None
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


class StockDeficit(BaseModel):
    barcode: str
    need: int
    have: int

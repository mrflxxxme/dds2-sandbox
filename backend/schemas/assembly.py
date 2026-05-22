"""
Assembly Request schemas: Pydantic request/response models.
See backend/DOMAIN_ASSEMBLY.md for full spec.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

PackageTypeStr = Literal["BOX", "MONOPALLET", "SUPERSAFE"]

# ─── Request schemas ────────────────────────────────────────────────────────


class AssemblyItemCreate(BaseModel):
    barcode: str
    quantity: int


class AssemblyRequestCreate(BaseModel):
    warehouse_id: int
    wb_fbo_supply_id: int | None = None
    estimated_ready_date: date | None = None
    pallets_count: int
    pallet_weight_kg: Decimal
    comment: str | None = None
    wb_warehouse_name_manual: str | None = None
    package_type: PackageTypeStr = "BOX"
    items: list[AssemblyItemCreate]


class AssemblyRequestUpdate(BaseModel):
    wb_fbo_supply_id: int | None = None
    estimated_ready_date: date | None = None
    pallets_count: int | None = None
    pallet_weight_kg: Decimal | None = None
    comment: str | None = None
    wb_warehouse_name_manual: str | None = None
    package_type: PackageTypeStr | None = None
    items: list[AssemblyItemCreate] | None = None  # only PENDING/IN_PROGRESS
    # Vehicle & cost — editable even after shipping
    pickup_cost: Decimal | None = None
    vehicle_info: str | None = None
    vehicle_brand: str | None = None
    driver_phone: str | None = None
    carrier_inn: str | None = None
    carrier_name: str | None = None


class AssignVehicle(BaseModel):
    vehicle_info: str
    vehicle_brand: str
    driver_phone: str
    pickup_date: date
    pickup_time_slot: str
    pickup_cost: Decimal
    delivery_date: date
    carrier_inn: str | None = None
    carrier_name: str | None = None


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
    carrier_inn: str | None = None
    carrier_name: str | None = None
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
    brand: str | None = None
    stock_quantity: int = 0


class AssemblyRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_id: int
    warehouse_name: str | None = None
    number: str
    status: str
    wb_fbo_supply_id: int | None = None
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
    counterparty_id: int | None = None
    carrier_inn: str | None = None
    carrier_name: str | None = None
    comment: str | None = None
    wb_warehouse_name_manual: str | None = None
    source_draft_id: int | None = None  # черновик-источник (поток распределения сборки)
    package_type: PackageTypeStr = "BOX"
    effective_wb_warehouse: str | None = None  # FBO warehouse_name or manual, whichever is set
    brands: str | None = None  # comma-separated unique brands from items
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


# ─── Logistics analytics ───────────────────────────────────────────────────


class LogisticsCostSummary(BaseModel):
    total_cost: Decimal
    avg_cost_per_pallet: Decimal
    total_pallets: int
    total_shipments: int


class LogisticsRouteStat(BaseModel):
    src_warehouse: str
    dest_warehouse: str
    avg_cost: Decimal
    shipments_count: int


class LogisticsDestStat(BaseModel):
    dest_warehouse: str
    avg_cost: Decimal
    total_cost: Decimal
    shipments_count: int


class LogisticsAnalyticsResponse(BaseModel):
    summary: LogisticsCostSummary
    by_destination: list[LogisticsDestStat]
    by_route: list[LogisticsRouteStat]

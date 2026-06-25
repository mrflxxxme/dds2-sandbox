# ruff: noqa: RUF002, RUF003
"""
FF-портал — минимальные «безопасные» схемы для внешнего оператора (Хамза).

Намеренно НЕ переиспользуем AssemblyRequestResponse и т.п.: те несут себестоимость,
перевозчика, FF-mismatch, аналитику. Здесь — только то, что нужно для работы оператора.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

# ─── Assembly (заявки на сборку) ─────────────────────────────────────────────


class FfAssemblyItem(BaseModel):
    barcode: str
    product_name: str | None = None
    quantity: int


class FfAssemblyRow(BaseModel):
    id: int
    project_slug: str
    project_name: str
    warehouse_id: int
    warehouse_name: str
    number: str
    status: str
    wb_warehouse_name: str | None = None
    package_type: str | None = None
    pallets_count: int | None = None
    pallet_weight_kg: Decimal | None = None
    estimated_ready_date: date | None = None
    actual_ready_date: date | None = None
    vehicle_assigned: bool = False
    items: list[FfAssemblyItem] = Field(default_factory=list)
    created_at: datetime | None = None


class FfAssemblyListResponse(BaseModel):
    items: list[FfAssemblyRow]
    total: int


class FfReadyPayload(BaseModel):
    pallets_count: int = Field(gt=0)
    pallet_weight_kg: Decimal = Field(gt=0)


# ─── Acceptance (приёмки, в т.ч. возвраты WB) ────────────────────────────────


class FfAcceptanceItem(BaseModel):
    item_id: int
    barcode: str
    product_name: str | None = None
    expected_qty: int
    actual_qty: int
    defect_qty: int = 0


class FfAcceptanceRow(BaseModel):
    id: int
    project_slug: str
    project_name: str
    warehouse_id: int
    warehouse_name: str
    number: str
    kind: str  # 'inbound' | 'return' (возврат WB по заявке сборки)
    status: str
    in_work: bool = False
    assigned_to_me: bool = False
    planned_date: date | None = None
    actual_date: date | None = None
    comment: str | None = None
    items: list[FfAcceptanceItem] = Field(default_factory=list)
    created_at: datetime | None = None


class FfAcceptanceListResponse(BaseModel):
    items: list[FfAcceptanceRow]
    total: int


class FfAcceptItem(BaseModel):
    item_id: int
    actual_qty: int = Field(ge=0)
    defect_qty: int = Field(default=0, ge=0)
    defect_reason: str | None = None


class FfAcceptPayload(BaseModel):
    items: list[FfAcceptItem]


# ─── Stock (остатки) ─────────────────────────────────────────────────────────


class FfStockRow(BaseModel):
    project_slug: str
    project_name: str
    warehouse_id: int
    warehouse_name: str
    barcode: str
    product_name: str | None = None
    quantity: int
    defect_quantity: int
    reserved: int
    available: int


class FfStockListResponse(BaseModel):
    items: list[FfStockRow]
    total: int
    total_quantity: int


# ─── Me (профиль оператора для шапки/гейта) ──────────────────────────────────


class FfProjectInfo(BaseModel):
    slug: str
    name: str


class FfWarehouseInfo(BaseModel):
    project_slug: str
    warehouse_id: int
    warehouse_name: str


class FfMeResponse(BaseModel):
    username: str
    projects: list[FfProjectInfo]
    warehouses: list[FfWarehouseInfo]

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
    article: str | None = None
    product_name: str | None = None
    quantity: int
    box_qty: int | None = None  # кратность короба (шт/короб) — для показа «N коробок»
    stock: int = 0  # «На складе» — WarehouseStock.quantity на складе заявки (для подсветки дефицита)


class FfAssemblyRow(BaseModel):
    id: int
    project_slug: str
    project_name: str
    warehouse_id: int
    warehouse_name: str
    number: str
    status: str
    wb_warehouse_name: str | None = None
    fbo_supply_number: str | None = None  # № FBO-поставки (FBW-…) из wb_fbo_supply
    brands: str | None = None  # бренды позиций (уникальные, через запятую)
    package_type: str | None = None
    pallets_count: int | None = None
    pallet_weight_kg: Decimal | None = None
    estimated_ready_date: date | None = None
    actual_ready_date: date | None = None
    vehicle_assigned: bool = False
    is_archived: bool = False
    items: list[FfAssemblyItem] = Field(default_factory=list)
    created_at: datetime | None = None


class FfAssemblyListResponse(BaseModel):
    items: list[FfAssemblyRow]
    total: int


class FfReadyPayload(BaseModel):
    pallets_count: int = Field(gt=0)
    pallet_weight_kg: Decimal = Field(gt=0)
    estimated_ready_date: date | None = None  # плановая готовность (правка в «Параметры»; /ready игнорирует)


class FfStatusEvent(BaseModel):
    """Событие смены статуса заявки — для таймлайна «где была / где сейчас»."""

    status: str
    changed_at: datetime
    comment: str | None = None


class FfAssemblyDetail(FfAssemblyRow):
    """Полная карточка заявки для detail-страницы оператора.

    Поверх строки списка — доставка/водитель/машина (когда логистика назначила
    машину), признак отправки в Газельку и таймлайн статусов. Себестоимость и
    стоимость перевозки НЕ отдаём — только то, что нужно оператору на складе.
    """

    editable: bool = False  # состав можно править (статус PENDING/IN_PROGRESS)
    vehicle_info: str | None = None
    vehicle_brand: str | None = None
    driver_phone: str | None = None
    carrier_name: str | None = None
    pickup_date: date | None = None
    pickup_time_slot: str | None = None
    delivery_date: date | None = None
    vehicle_assigned_at: datetime | None = None
    via_gazelka: bool = False
    # Связанная WB FBO-поставка (если есть в связке)
    fbo_supply_name: str | None = None
    fbo_supply_wb_id: str | None = None
    fbo_supply_status: str | None = None
    fbo_supply_planned_date: date | None = None
    history: list[FfStatusEvent] = Field(default_factory=list)
    # Предложенная правка состава, ожидающая согласования в DDS («ожидает согласования»).
    ff_review_pending: bool = False
    proposed_items: list[FfAssemblyItem] = Field(default_factory=list)
    ff_proposed_at: datetime | None = None


class FfAssemblyItemInput(BaseModel):
    barcode: str
    quantity: int = Field(gt=0)


class FfAssemblyItemsUpdate(BaseModel):
    items: list[FfAssemblyItemInput]


class FfArchivePayload(BaseModel):
    archived: bool = True


# ─── Acceptance (приёмки, в т.ч. возвраты WB) ────────────────────────────────


class FfAcceptanceItem(BaseModel):
    item_id: int
    barcode: str
    article: str | None = None
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
    is_archived: bool = False
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
    article: str | None = None
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

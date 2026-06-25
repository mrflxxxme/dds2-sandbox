# ruff: noqa: RUF001, RUF002, RUF003
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
    wb_acceptance_days: int = 2
    sort_order: int = 0


class WarehouseUpdate(BaseModel):
    name: str | None = None
    warehouse_type: str | None = None
    country: str | None = None
    address: str | None = None
    assembly_days: int | None = None
    wb_acceptance_days: int | None = None
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
    wb_acceptance_days: int = 2
    external_id: str | None = None
    sort_order: int = 0
    is_active: bool = True
    total_stock: int = 0
    vehicles_in_transit: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    counterparty_id: int | None = None
    counterparty_inn: str | None = None
    counterparty_name: str | None = None


class WarehouseCounterpartyLink(BaseModel):
    """Body for PATCH /warehouses/{id}/counterparty."""

    inn: str | None = None
    name: str | None = None


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
    is_defect: bool = False
    defect_reason: str | None = None
    items: list[InboundReceiptItemCreate] = []


class InboundReceiptUpdate(BaseModel):
    planned_date: date | None = None
    comment: str | None = None
    tags: str | None = None
    defect_reason: str | None = None
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
    is_defect: bool = False
    defect_reason: str | None = None
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
    is_defect: bool = False
    defect_reason: str | None = None
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
    is_defect: bool = False
    defect_reason: str | None = None
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
    is_defect: bool = False
    defect_reason: str | None = None
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
    is_defect: bool = False
    defect_reason: str | None = None
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
    defect_delta: int = 0
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
    defect_quantity: int = 0
    defect_in_transit: int = 0
    cost_price: Decimal | None = None
    updated_at: datetime | None = None
    reserved: int = 0
    available: int = 0


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


# ─── Defect Operations (Брак) ────────────────────────────────────────────


class DefectOperationCreate(BaseModel):
    barcode: str
    quantity: int
    reason: str


class DefectBulkItem(BaseModel):
    barcode: str
    quantity: int


class DefectBulkOperation(BaseModel):
    reason: str
    items: list[DefectBulkItem]


class DefectBulkResponse(BaseModel):
    status: str  # "ok" | "partial" | "error"
    processed: int
    failed: int
    errors: list[dict]  # [{barcode, error}]
    # Optional: set when the bulk op produced a document (mark → operation, receive → receipt, writeoff → shipment)
    operation_id: int | None = None
    receipt_id: int | None = None
    shipment_id: int | None = None
    number: str | None = None


class DefectMarkOperationItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    operation_id: int
    nomenclature_id: int
    barcode: str
    quantity: int


class DefectMarkOperationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    number: str
    status: str
    actual_date: date | None = None
    reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[DefectMarkOperationItemSchema] = []


# ─── Delivery Times (Время доставки до WB) ──────────────────────────────


class DeliveryTimeItem(BaseModel):
    wb_warehouse_name: str
    delivery_days: int = 3


class DeliveryTimesUpdate(BaseModel):
    wb_acceptance_days: int | None = None
    assembly_days: int | None = None
    items: list[DeliveryTimeItem] = []


class DeliveryTimeRow(BaseModel):
    wb_warehouse_name: str
    delivery_days: int
    total_days: int  # assembly_days + delivery_days + wb_acceptance_days


class DeliveryTimesResponse(BaseModel):
    warehouse_id: int
    warehouse_name: str
    assembly_days: int
    wb_acceptance_days: int
    wb_warehouses: list[DeliveryTimeRow]


# ─── WB Acceptance Check (POST /warehouse/acceptance-check) ─────────────────


class AcceptanceCheckItemRequest(BaseModel):
    """Single SKU + its current planned distribution per WB warehouse."""

    nm_id: int
    barcode: str
    distribution: dict[str, int]  # {wb_warehouse_name: qty}


class AcceptanceCheckRequest(BaseModel):
    """Body for POST /warehouse/acceptance-check.

    Caller passes the article's barcode, total qty, and the planned per-WB
    distribution. We call WB API once for all barcodes, then redistribute
    qty away from closed warehouses (gео-aware via warehouse_district).
    """

    items: list[AcceptanceCheckItemRequest]


class AcceptanceCoefMeta(BaseModel):
    """Coefficients aggregate for one (warehouse, package_type) на ближайшие 14 дней.

    free_days_14   — coefficient ∈ {0, 1} И allowUnload=true (бесплатно)
    paid_days_14   — coefficient ≥ 2 И allowUnload=true (платно × min_coefficient)
    min_coefficient — лучший коэф среди allowUnload=true дней (None если все закрыты)
    """

    free_days_14: int = 0
    paid_days_14: int = 0
    min_coefficient: int | None = None


class AcceptanceFlags(BaseModel):
    """Per-(barcode, wb_warehouse) acceptance flags from WB API.

    can_box / can_monopallet / can_supersafe — итоговый «можно сдать»:
    options.canX = True И (нет coefficients-данных ИЛИ есть хотя бы 1 free_day).
    *_meta — детализация per-type для UI tooltip («свободно N из 14 дней»).
    """

    warehouse_id: int
    can_box: bool
    can_monopallet: bool
    can_supersafe: bool
    box_meta: AcceptanceCoefMeta | None = None
    mono_meta: AcceptanceCoefMeta | None = None
    super_meta: AcceptanceCoefMeta | None = None


class RedistributionMove(BaseModel):
    """One redistribution step: qty moved from a closed wh to an open one."""

    nm_id: int
    barcode: str
    from_warehouse: str
    to_warehouse: str | None  # None = no destination found, qty dropped
    quantity: int
    reason: str  # e.g. "closed", "box-only-required" etc.


class AcceptanceCheckSplit(BaseModel):
    """One sub-assembly: warehouses that share a single package_type.

    Каждый split = одна транспортная единица (одна `AssemblyRequest`).
    Если разные склады принимают разные типы упаковки (Электросталь — только
    моно, Краснодар — только короб), вместо отбраковки половины складов
    создаются 2 split'а с разным `package_type`.
    """

    package_type: str  # BOX | MONOPALLET | SUPERSAFE
    distribution: dict[str, int]
    warnings: list[str] = []


class AcceptanceCheckPerItem(BaseModel):
    """Per-SKU result with availability flags + chosen package_type."""

    nm_id: int
    barcode: str
    # {wb_warehouse_name: {warehouse_id, can_box, can_monopallet, can_supersafe}}
    availability: dict[str, AcceptanceFlags]
    package_type: str  # BOX | MONOPALLET | SUPERSAFE — primary (largest by qty) split
    distribution: dict[str, int]  # primary split distribution (backward-compat)
    splits: list[AcceptanceCheckSplit] = []  # all sub-assemblies (one per package_type)
    warnings: list[str] = []


class AcceptanceCheckResponse(BaseModel):
    """Response for POST /warehouse/acceptance-check."""

    items: list[AcceptanceCheckPerItem]
    moves: list[RedistributionMove]
    checked_at: datetime
    cache_hit: bool = False


# ─── WB Acceptance Limits (GET /warehouse/acceptance-limits) ────────────────


class AcceptanceLimitDay(BaseModel):
    """Один день календаря приёмки для (склад, тип упаковки).

    coefficient — WB-коэффициент: -1 закрыто, 0..1 бесплатно, ≥2 платный
    множитель к базовому тарифу. is_free = coefficient ∈ {0,1} И allow_unload.
    is_closed = coefficient == -1 ИЛИ allow_unload=false.
    """

    date: str  # ISO date (YYYY-MM-DD)
    coefficient: float
    allow_unload: bool
    is_free: bool
    is_closed: bool
    storage_coef: float | None = None
    delivery_coef: float | None = None


class AcceptanceLimitEntry(BaseModel):
    """Календарь одного (склад × тип упаковки) на ближайшие дни."""

    warehouse_id: int
    warehouse_name: str  # raw WB name
    canonical_name: str
    box_type: str  # box | mono | super
    days: list[AcceptanceLimitDay]


class AcceptanceLimitsResponse(BaseModel):
    """Response for GET /warehouse/acceptance-limits — сводный календарь лимитов.

    `dates` — отсортированные ISO-даты (колонки календаря). `warehouses` —
    по одной записи на (склад × тип упаковки), отсортированы по canonical-имени
    и типу (короб → моно → супер).
    """

    warehouses: list[AcceptanceLimitEntry]
    dates: list[str]
    fetched_at: datetime


class SupplyAcceptanceSlotRow(BaseModel):
    """Активная заявка/поставка + календарь слотов приёмки её склада WB.

    Те же дни (`days`), что и в `/warehouse/acceptance-limits`, но привязанные
    к конкретной поставке: склад берётся из ФБО-поставки (или ручного поля),
    тип упаковки — из заявки. `matched=false` — склад заявки не нашёлся в
    календаре коэффициентов (тогда `days` пуст; матч идёт по нормализованному
    имени, т.к. WbFboSupply не хранит numeric warehouseID).
    """

    assembly_request_id: int
    assembly_number: str
    status: str
    wb_supply_id: str | None = None  # ФБО-поставка WB (напр. «39950266»)
    warehouse_name: str | None = None  # effective: FBO warehouse_name или ручной
    canonical_name: str  # нормализованное имя склада (ключ матча/группировки)
    warehouse_id: int | None = None  # WB warehouseID, если склад сматчился
    box_type: str  # box | mono | super (из package_type заявки)
    package_type: str  # BOX | MONOPALLET | SUPERSAFE
    planned_date: date | None = None  # плановая «Сдача ВБ»
    actual_date: date | None = None
    wb_fbo_status: str | None = None
    matched: bool  # склад заявки найден в календаре приёмки
    days: list[AcceptanceLimitDay]


class SupplyAcceptanceSlotsResponse(BaseModel):
    """Response for GET /warehouse/acceptance-slots — слоты сдачи по поставкам.

    `rows` — по одной на активную заявку, отсортированы по складу и плановой
    дате (фронт группирует по `canonical_name`). `dates` — общая ось дат
    (ISO, 14 дней WB), как в календаре лимитов.
    """

    rows: list[SupplyAcceptanceSlotRow]
    dates: list[str]
    fetched_at: datetime

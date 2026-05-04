"""
Supply Chain schemas: Supplier, FactoryOrder, FactoryOrderItem, Vehicle.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

# --- Container → Transport mapping ---

CONTAINER_TRANSPORT_MAP: dict[str, str] = {
    "truck1": "AUTO",
    "truck2": "AUTO",
    "20ft": "CONTAINER",
    "40ft": "CONTAINER",
    "40ft_hc": "CONTAINER",
}

VALID_CONTAINER_TYPES = set(CONTAINER_TRANSPORT_MAP.keys())

VALID_SUPPLIER_COUNTRIES = {"CHINA", "RUSSIA"}
VALID_SUPPLIER_CURRENCIES = {"CNY", "RUB"}


# --- Supplier ---


class SupplierCreate(BaseModel):
    name: str
    country: str = "CHINA"
    currency: str = "CNY"
    delivery_days_min: int | None = None
    delivery_days_max: int | None = None
    note: str | None = None

    @field_validator("country")
    @classmethod
    def validate_country(cls, v: str) -> str:
        v = v.upper()
        if v not in VALID_SUPPLIER_COUNTRIES:
            msg = f"Invalid country: {v}. Must be one of {VALID_SUPPLIER_COUNTRIES}"
            raise ValueError(msg)
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        v = v.upper()
        if v not in VALID_SUPPLIER_CURRENCIES:
            msg = f"Invalid currency: {v}. Must be one of {VALID_SUPPLIER_CURRENCIES}"
            raise ValueError(msg)
        return v


class SupplierUpdate(BaseModel):
    name: str | None = None
    country: str | None = None
    currency: str | None = None
    delivery_days_min: int | None = None
    delivery_days_max: int | None = None
    note: str | None = None

    @field_validator("country")
    @classmethod
    def validate_country(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.upper()
        if v not in VALID_SUPPLIER_COUNTRIES:
            msg = f"Invalid country: {v}. Must be one of {VALID_SUPPLIER_COUNTRIES}"
            raise ValueError(msg)
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.upper()
        if v not in VALID_SUPPLIER_CURRENCIES:
            msg = f"Invalid currency: {v}. Must be one of {VALID_SUPPLIER_CURRENCIES}"
            raise ValueError(msg)
        return v


class SupplierSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    country: str = "CHINA"
    currency: str = "CNY"
    delivery_days_min: int | None = None
    delivery_days_max: int | None = None
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- FactoryOrderItem ---


class FactoryOrderItemCreate(BaseModel):
    barcode: str
    subject: str | None = None
    article_seller: str | None = None
    qty: int = 0
    price_cny: Decimal = Decimal("0")
    box_size: str | None = None
    pcs_per_box: int | None = None
    weight_kg: Decimal | None = None
    note: str | None = None
    box_detail: list[int] | None = None
    mix_group_id: str | None = None
    mix_box_size: str | None = None
    mix_pcs_per_box: int | None = None


class FactoryOrderItemUpdate(BaseModel):
    barcode: str | None = None
    subject: str | None = None
    article_seller: str | None = None
    qty: int | None = None
    price_cny: Decimal | None = None
    box_size: str | None = None
    pcs_per_box: int | None = None
    weight_kg: Decimal | None = None
    note: str | None = None
    box_detail: list[int] | None = None
    mix_group_id: str | None = None
    mix_box_size: str | None = None
    mix_pcs_per_box: int | None = None


class BulkUpdateItemSpecs(BaseModel):
    """Bulk update box specs for items matched by barcode."""

    barcode: str
    box_size: str | None = None
    pcs_per_box: int | None = None
    weight_kg: Decimal | None = None


class FactoryOrderItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factory_order_id: int
    barcode: str
    subject: str | None = None
    article_seller: str | None = None
    qty: int = 0
    assigned_qty: int = 0
    price_cny: Decimal = Decimal("0")
    box_size: str | None = None
    pcs_per_box: int | None = None
    weight_kg: Decimal | None = None
    note: str | None = None
    box_detail: list[int] | None = None
    mix_group_id: str | None = None
    mix_box_size: str | None = None
    mix_pcs_per_box: int | None = None
    remaining_qty: int | None = None  # computed: qty - assigned_qty
    is_delivered: bool = False


# --- FactoryOrder ---


class FactoryOrderCreate(BaseModel):
    order_number: str
    factory_name: str | None = None
    supplier_id: int | None = None
    order_date: date | None = None
    expected_ready_date: date | None = None
    total_cny: Decimal | None = None
    note: str | None = None
    items: list[FactoryOrderItemCreate] | None = None


class FactoryOrderUpdate(BaseModel):
    order_number: str | None = None
    factory_name: str | None = None
    supplier_id: int | None = None
    order_date: date | None = None
    expected_ready_date: date | None = None
    total_cny: Decimal | None = None
    note: str | None = None


class FactoryOrderSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    order_number: str
    factory_name: str | None = None
    supplier_id: int | None = None
    supplier: SupplierSchema | None = None
    order_date: date | None = None
    expected_ready_date: date | None = None
    total_cny: Decimal | None = None
    status: str = "FORMING"
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[FactoryOrderItemSchema] | None = None


# --- Factory Order Status Update ---


class FactoryOrderStatusUpdate(BaseModel):
    status: str  # FactoryOrderStatus value


# --- Factory Order History ---


class FactoryOrderHistorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    factory_order_id: int
    event_type: str
    old_status: str | None = None
    new_status: str | None = None
    details: str | None = None
    changed_at: datetime
    changed_by: str | None = None


# --- Vehicle (CostOrder) status update ---


class VehicleStatusUpdate(BaseModel):
    status: str  # VehicleStatus value
    target_warehouse_id: int | None = None
    dt_number: str | None = None


# --- Vehicle CRUD ---


class VehicleCreate(BaseModel):
    order_no: str | None = None
    container_type: str = "truck1"

    @field_validator("order_no")
    @classmethod
    def validate_order_no(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if "/" in v or "\\" in v:
            msg = "order_no must not contain slashes (/ or \\)"
            raise ValueError(msg)
        return v

    delivery_cost_cny: Decimal = Decimal("0")
    delivery_cost_usd: Decimal = Decimal("0")
    delivery_cost_rub: Decimal = Decimal("0")
    rate_cny: Decimal = Decimal("12.5")
    rate_usd: Decimal = Decimal("92")
    rate_eur: Decimal = Decimal("98")
    country: str = "CHINA"
    ship_date: date | None = None
    invoice_no: str | None = None
    payment_ref: str | None = None
    target_warehouse_id: int | None = None
    note: str | None = None
    vehicle_name: str | None = None
    plate_number: str | None = None

    @field_validator("country")
    @classmethod
    def validate_country(cls, v: str) -> str:
        if v not in ("CHINA", "RUSSIA"):
            msg = "country must be CHINA or RUSSIA"
            raise ValueError(msg)
        return v


class VehicleUpdate(BaseModel):
    """Partial update for vehicle."""

    container_type: str | None = None
    delivery_cost_cny: Decimal | None = None
    delivery_cost_usd: Decimal | None = None
    delivery_cost_rub: Decimal | None = None
    rate_cny: Decimal | None = None
    rate_usd: Decimal | None = None
    rate_eur: Decimal | None = None
    country: str | None = None
    ship_date: date | None = None
    actual_ship_date: date | None = None
    estimated_arrival_date: date | None = None
    invoice_no: str | None = None
    payment_ref: str | None = None
    dt_number: str | None = None
    target_warehouse_id: int | None = None
    note: str | None = None
    vehicle_name: str | None = None
    plate_number: str | None = None

    @field_validator("country")
    @classmethod
    def validate_country(cls, v: str | None) -> str | None:
        if v is not None and v not in ("CHINA", "RUSSIA"):
            msg = "country must be CHINA or RUSSIA"
            raise ValueError(msg)
        return v


class VehicleItemSchema(BaseModel):
    """CostOrderItem with factory order context."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_no: str
    barcode: str
    subject: str | None = None
    article_seller: str | None = None
    qty: int = 0
    price_cny: Decimal = Decimal("0")
    weight_kg: Decimal | None = None
    volume_m3: Decimal | None = None
    cost_rub: Decimal | None = None
    delivery_rub: Decimal | None = None
    duty_rub: Decimal | None = None
    vat_rub: Decimal | None = None
    total_rub: Decimal | None = None
    factory_order_item_id: int | None = None
    # Enriched from factory order item
    factory_order_id: int | None = None
    box_size: str | None = None
    pcs_per_box: int | None = None
    box_detail: list[int] | None = None
    mix_group_id: str | None = None
    mix_box_size: str | None = None
    mix_pcs_per_box: int | None = None
    factory_order_number: str | None = None
    # Drift detection vs FactoryOrderItem (sc17): >0 если суммарное qty по этому foi
    # в активных машинах превышает план фабрики. Frontend показывает оранжевую точку.
    qty_drift: int | None = None
    fo_qty: int | None = None  # FactoryOrderItem.qty (план)
    fo_assigned: int | None = None  # FactoryOrderItem.assigned_qty (распределено)
    # sc18: позиция добавлена после отгрузки (vehicle ≥ SHIPPED). Frontend помечает.
    added_after_ship: bool = False


class VehicleItemUpdate(BaseModel):
    """Per-vehicle edit for a CostOrderItem.

    qty: при изменении синхронизирует FactoryOrderItem.assigned_qty (sc17).
    mode="strict" (default): 422 если delta превышает план фабрики.
    mode="extend_plan": автоматически расширяет FactoryOrderItem.qty + history запись.
    box_*_override: сохраняются как per-vehicle override (план фабрики не трогается).
    """

    qty: int | None = None
    box_size_override: str | None = None
    pcs_per_box_override: int | None = None
    box_detail_override: list[int] | None = None
    mode: Literal["strict", "extend_plan"] = "strict"

    @field_validator("qty")
    @classmethod
    def validate_qty(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            msg = "qty must be >= 0"
            raise ValueError(msg)
        return v


class VehicleCostSummary(BaseModel):
    """Aggregated cost breakdown for a vehicle."""

    total_cost_rub: Decimal = Decimal("0")
    total_delivery_rub: Decimal = Decimal("0")
    total_duty_rub: Decimal = Decimal("0")
    total_vat_rub: Decimal = Decimal("0")
    total_rub: Decimal = Decimal("0")


class VehiclePriceResyncItem(BaseModel):
    """One item whose price differs between vehicle and factory order."""

    cost_item_id: int
    barcode: str
    article_seller: str | None = None
    subject: str | None = None
    qty: int
    old_price_cny: Decimal
    new_price_cny: Decimal
    delta_sum_cny: Decimal  # (new - old) * qty


class VehiclePriceResyncPreview(BaseModel):
    """Preview of price resync: items that will change + totals."""

    vehicle_status: str | None = None
    total_items: int  # all vehicle items with a factory order link
    unlinked_items: int  # items without factory_order_item_id (cannot be synced)
    changed_items: int  # items where price differs
    sum_delta_cny: Decimal  # total Σ (new - old) * qty across changed items
    items: list[VehiclePriceResyncItem] = []


class VehiclePriceResyncApplyResult(BaseModel):
    applied: int


class BulkPriceUpdateItem(BaseModel):
    """One item in a bulk FactoryOrderItem price update request."""

    factory_order_item_id: int
    new_price_cny: Decimal


class BulkPriceUpdateRequest(BaseModel):
    items: list[BulkPriceUpdateItem]


class BulkPriceUpdateResult(BaseModel):
    updated: int
    not_found: list[int] = []  # foi_ids that don't belong to project or don't exist


class VehicleSchema(BaseModel):
    """CostOrder enriched for supply chain context."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_no: str
    status: str | None = None
    transport_type: str | None = "AUTO"
    container_type: str | None = None
    ship_date: date | None = None
    actual_ship_date: date | None = None
    actual_arrival_date: date | None = None
    estimated_arrival_date: date | None = None
    delivery_cost_cny: Decimal = Decimal("0")
    delivery_cost_usd: Decimal = Decimal("0")
    delivery_cost_rub: Decimal = Decimal("0")
    rate_cny: Decimal = Decimal("1")
    rate_usd: Decimal = Decimal("1")
    rate_eur: Decimal = Decimal("1")
    country: str = "CHINA"
    invoice_no: str | None = None
    payment_ref: str | None = None
    note: str | None = None
    dt_number: str | None = None
    vehicle_name: str | None = None
    plate_number: str | None = None
    target_warehouse_id: int | None = None
    inbound_receipt_id: int | None = None
    created_at: datetime | None = None
    items: list[VehicleItemSchema] = []
    # Aggregated fields
    items_count: int = 0
    total_qty: int = 0
    total_cny: Decimal = Decimal("0")
    total_weight_kg: Decimal | None = None
    total_volume_m3: Decimal | None = None
    # Cost summary
    cost_summary: VehicleCostSummary | None = None


class AddItemsToVehicleRequest(BaseModel):
    """Add items from factory orders to a vehicle."""

    items: list["AddItemToVehicle"]


class AddItemToVehicle(BaseModel):
    factory_order_item_id: int
    qty: int
    box_size_override: str | None = None
    pcs_per_box_override: int | None = None


class PostShipmentAddItem(BaseModel):
    """Add one item to a vehicle in status >= SHIPPED."""

    factory_order_item_id: int
    qty: int
    box_size_override: str | None = None
    pcs_per_box_override: int | None = None
    mode: Literal["strict", "extend_plan"] = "strict"

    @field_validator("qty")
    @classmethod
    def validate_qty(cls, v: int) -> int:
        if v <= 0:
            msg = "qty must be > 0"
            raise ValueError(msg)
        return v


class PostShipmentItemsRequest(BaseModel):
    """sc18: добавление позиции в машину после отгрузки.

    Применяется для vehicle.status >= SHIPPED. Создаёт CostOrderItem с
    `added_after_ship=True`. Логика по статусам:
    - SHIPPED/CUSTOMS: только cost-side (recalc costs, sync assigned_qty).
    - DISPATCHED: + добавить в существующий InboundReceipt (expected_qty).
    - DELIVERED: + создать отдельный InboundReceipt (DRAFT) для ручной приёмки.
    """

    items: list[PostShipmentAddItem]


# --- Mix Groups ---


class MixGroupItemInput(BaseModel):
    """One item in a mix box: id + how many of this SKU per box."""

    id: int
    pcs_per_box: int


class SetMixGroupRequest(BaseModel):
    """Group items into a single mix box with dimensions."""

    items: list[MixGroupItemInput]
    box_size: str


class SetMixGroupResponse(BaseModel):
    mix_group_id: str
    item_ids: list[int]
    box_size: str


# --- Split factory order to vehicles ---


class SplitItem(BaseModel):
    """One item assignment: which factory_order_item, how many, to which vehicle."""

    factory_order_item_id: int
    qty: int
    vehicle_order_no: str  # CostOrder.order_no — existing or new


class SplitToVehiclesRequest(BaseModel):
    """Request to split factory order items across vehicles."""

    assignments: list[SplitItem]


# --- Vehicle Documents ---


class VehicleDocumentSchema(BaseModel):
    """Vehicle document metadata (file stored in MinIO)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    order_no: str
    doc_type: str
    filename: str
    file_url: str
    file_size: int = 0
    note: str | None = None
    created_at: datetime | None = None


# --- Supplier Catalog ---


class SupplierCatalogSummary(BaseModel):
    orders_count: int
    sku_count: int
    total_qty: int
    total_amount: Decimal
    delivered_amount: Decimal


class SkuOrderHistoryEntry(BaseModel):
    factory_order_id: int
    order_number: str
    order_date: date | None = None
    qty: int
    price_cny: Decimal
    amount: Decimal
    vehicle_order_no: str | None = None
    vehicle_status: str | None = None
    is_delivered: bool


class SupplierCatalogItem(BaseModel):
    barcode: str
    article_seller: str | None = None
    subject: str | None = None
    brand: str | None = None
    box_size: str | None = None
    pcs_per_box: int | None = None
    weight_kg: Decimal | None = None
    total_qty: int
    last_price: Decimal
    avg_price: Decimal
    total_amount: Decimal
    orders_count: int
    last_order_date: date | None = None
    delivered_qty: int
    distributed_qty: int = 0
    order_history: list[SkuOrderHistoryEntry]


class SupplierCatalogSubjectGroup(BaseModel):
    subject: str
    sku_count: int
    total_qty: int
    total_amount: Decimal
    delivered_qty: int
    distributed_qty: int = 0
    items: list[SupplierCatalogItem]


class SupplierCatalogSupplierInfo(BaseModel):
    id: int
    name: str
    country: str
    currency: str


class SupplierCatalogResponse(BaseModel):
    supplier: SupplierCatalogSupplierInfo
    summary: SupplierCatalogSummary
    subjects: list[SupplierCatalogSubjectGroup]


# --- Shipment Matrix (Отгрузочная карта) ---


class ShipmentMatrixVehicle(BaseModel):
    order_no: str
    status: str | None = None
    container_type: str | None = None
    ship_date: date | None = None


class ShipmentMatrixItem(BaseModel):
    barcode: str
    article_seller: str | None = None
    subject: str | None = None
    brand: str | None = None
    total_qty: int
    total_boxes: int
    pcs_per_box: int | None = None
    remaining_qty: int
    shipped_pct: Decimal
    # qty уехавших в машинах со статусом >= SHIPPED (реально отгружено с фабрики)
    really_shipped_qty: int = 0
    # самая свежая дата заказа — для расчёта «возраста» и приоритета в UI
    latest_order_date: date | None = None
    vehicle_allocations: dict[str, int]  # order_no → qty


class ShipmentMatrixSubjectGroup(BaseModel):
    subject: str
    items: list[ShipmentMatrixItem]
    total_qty: int
    total_boxes: int
    remaining_qty: int
    shipped_pct: Decimal


class ShipmentMatrixSummary(BaseModel):
    total_qty: int
    total_boxes: int
    shipped_qty: int  # разложено в машины (включая FORMING)
    really_shipped_qty: int = 0  # реально уехало (машины >= SHIPPED)
    remaining_qty: int


class ShipmentMatrixResponse(BaseModel):
    supplier: SupplierCatalogSupplierInfo
    vehicles: list[ShipmentMatrixVehicle]
    summary: ShipmentMatrixSummary
    subjects: list[ShipmentMatrixSubjectGroup]


# --- Vehicle Status History ---


class VehicleStatusHistorySchema(BaseModel):
    """Audit log entry for vehicle status transition."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    order_no: str
    old_status: str | None = None
    new_status: str
    changed_at: datetime
    changed_by: str | None = None
    comment: str | None = None

"""
Supply Chain schemas: FactoryOrder, FactoryOrderItem, Vehicle.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

# --- Container → Transport mapping ---

CONTAINER_TRANSPORT_MAP: dict[str, str] = {
    "truck1": "AUTO",
    "truck2": "AUTO",
    "20ft": "CONTAINER",
    "40ft": "CONTAINER",
    "40ft_hc": "CONTAINER",
}

VALID_CONTAINER_TYPES = set(CONTAINER_TRANSPORT_MAP.keys())


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
    remaining_qty: int | None = None  # computed: qty - assigned_qty


# --- FactoryOrder ---


class FactoryOrderCreate(BaseModel):
    order_number: str
    factory_name: str | None = None
    order_date: date | None = None
    expected_ready_date: date | None = None
    total_cny: Decimal | None = None
    note: str | None = None
    items: list[FactoryOrderItemCreate] | None = None


class FactoryOrderUpdate(BaseModel):
    order_number: str | None = None
    factory_name: str | None = None
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
    order_date: date | None = None
    expected_ready_date: date | None = None
    total_cny: Decimal | None = None
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[FactoryOrderItemSchema] | None = None


# --- Vehicle (CostOrder) status update ---


class VehicleStatusUpdate(BaseModel):
    status: str  # VehicleStatus value
    target_warehouse_id: int | None = None
    dt_number: str | None = None


# --- Vehicle CRUD ---


class VehicleCreate(BaseModel):
    order_no: str
    container_type: str = "truck1"
    delivery_cost_cny: Decimal = Decimal("0")
    delivery_cost_usd: Decimal = Decimal("0")
    rate_cny: Decimal = Decimal("12.5")
    rate_usd: Decimal = Decimal("92")
    rate_eur: Decimal = Decimal("98")
    ship_date: date | None = None
    invoice_no: str | None = None
    payment_ref: str | None = None
    target_warehouse_id: int | None = None
    note: str | None = None


class VehicleUpdate(BaseModel):
    """Partial update for vehicle."""

    container_type: str | None = None
    delivery_cost_cny: Decimal | None = None
    delivery_cost_usd: Decimal | None = None
    rate_cny: Decimal | None = None
    rate_usd: Decimal | None = None
    rate_eur: Decimal | None = None
    ship_date: date | None = None
    actual_ship_date: date | None = None
    estimated_arrival_date: date | None = None
    invoice_no: str | None = None
    payment_ref: str | None = None
    dt_number: str | None = None
    target_warehouse_id: int | None = None
    note: str | None = None


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
    box_size: str | None = None
    pcs_per_box: int | None = None
    factory_order_number: str | None = None


class VehicleCostSummary(BaseModel):
    """Aggregated cost breakdown for a vehicle."""

    total_cost_rub: Decimal = Decimal("0")
    total_delivery_rub: Decimal = Decimal("0")
    total_duty_rub: Decimal = Decimal("0")
    total_vat_rub: Decimal = Decimal("0")
    total_rub: Decimal = Decimal("0")


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
    rate_cny: Decimal = Decimal("1")
    rate_usd: Decimal = Decimal("1")
    rate_eur: Decimal = Decimal("1")
    invoice_no: str | None = None
    payment_ref: str | None = None
    note: str | None = None
    dt_number: str | None = None
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

"""
Cost schemas.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class NomenclatureSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    barcode: str
    brand: str | None = None
    subject: str | None = None
    article_seller: str | None = None
    article_wb: int | None = None
    imt_id: int | None = None
    volume_l: Decimal | None = None
    area_m2: Decimal | None = None
    updated_at: datetime | None = None


class DutyRuleSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    subject: str
    basis: str
    rate: Decimal
    util_collect_rub: Decimal = Decimal("0")
    note: str | None = None


class DutyExceptionSchema(BaseModel):
    """Article-level duty override. Overrides only basis+rate of the category
    rule; util_collect_rub stays sourced from the category's DutyRule."""

    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    article_seller: str
    basis: str
    rate: Decimal
    note: str | None = None


class CostOrderItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    order_no: str
    barcode: str
    subject: str | None = None
    article_seller: str | None = None
    qty: int = 1
    price_cny: Decimal = Decimal("0")
    weight_kg: Decimal | None = None
    area_m2: Decimal | None = None
    volume_m3: Decimal | None = None
    cost_rub: Decimal | None = None
    delivery_rub: Decimal | None = None
    duty_rub: Decimal | None = None
    vat_rub: Decimal | None = None
    util_rub: Decimal | None = None
    total_rub: Decimal | None = None
    total_cny: Decimal | None = None
    unrecognized: bool = False
    factory_order_item_id: int | None = None


class CostOrderSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    order_no: str
    invoice_no: str | None = None
    ship_date: date | None = None
    actual_arrival_date: date | None = None
    transport_type: str | None = "AUTO"
    delivery_cost_cny: Decimal = Decimal("0")
    delivery_cost_usd: Decimal = Decimal("0")
    rate_cny: Decimal = Decimal("1")
    rate_eur: Decimal = Decimal("1")
    rate_usd: Decimal = Decimal("1")
    note: str | None = None
    dt_number: str | None = None
    created_at: datetime | None = None
    status: str | None = None
    target_warehouse_id: int | None = None
    inbound_receipt_id: int | None = None
    items: list[CostOrderItemSchema] | None = None


class CostUploadResult(BaseModel):
    order_no: str
    items_count: int
    recognized: int
    unrecognized: int


class BulkAreaItem(BaseModel):
    barcode: str
    area_m2: Decimal


class BulkAreaUpdate(BaseModel):
    items: list[BulkAreaItem]


class BulkWeightItem(BaseModel):
    barcode: str
    weight_kg: Decimal


class BulkWeightUpdate(BaseModel):
    items: list[BulkWeightItem]


class VatRateUpdate(BaseModel):
    """Input: update project VAT rate."""

    vat_rate: Decimal


class CostOrderCreate(BaseModel):
    """Input: create/update cost order."""

    order_no: str
    invoice_no: str | None = None
    ship_date: date | None = None
    actual_arrival_date: date | None = None
    transport_type: str | None = "AUTO"
    delivery_cost_cny: Decimal = Decimal("0")
    delivery_cost_usd: Decimal = Decimal("0")
    rate_cny: Decimal = Decimal("1")
    rate_eur: Decimal = Decimal("1")
    rate_usd: Decimal = Decimal("1")
    note: str | None = None
    dt_number: str | None = None
    status: str | None = None
    target_warehouse_id: int | None = None


# ─── Valuation analytics (FIFO / moving / lifetime) ──────────────────────────


class CostLayerSchema(BaseModel):
    """One batch in the SKU ledger (received / consumed / remaining)."""

    order_no: str
    avail_date: date | None = None
    qty: int
    remaining: int
    consumed: int
    unit_cost: float
    arrival_known: bool


class MonthlyCogsSchema(BaseModel):
    """Per-month COGS for a SKU under all three methods."""

    month: str
    qty: int  # net = sold − returned
    sold: int
    returned: int
    cogs_fifo: float
    cogs_avg: float
    cogs_moving: float


class SkuValuationSchema(BaseModel):
    """Full per-SKU valuation analytics for the cost page drill-down."""

    sku: str
    barcode: str | None = None
    article_wb: int | None = None
    brand: str = ""
    subject: str = ""
    lifetime_avg: float
    eff_now: dict[str, float]
    on_hand_qty: int
    on_hand_value: dict[str, float]
    total_received: int
    total_sold: int
    is_estimated: bool
    warnings: list[str] = []
    ledger: list[CostLayerSchema] = []
    monthly: list[MonthlyCogsSchema] = []


class ValuationSummaryRow(BaseModel):
    """One SKU in the project-wide distortion summary."""

    sku: str
    barcode: str | None = None
    article_wb: int | None = None
    brand: str = ""
    subject: str = ""
    qty: int
    cogs_current: float
    cogs_fifo: float
    distortion: float  # cogs_fifo − cogs_current (per window)
    is_estimated: bool


class OpeningBalanceItem(BaseModel):
    barcode: str
    qty: int
    unit_cost: Decimal
    as_of_date: date | None = None
    note: str | None = None


class OpeningBalanceUpdate(BaseModel):
    items: list[OpeningBalanceItem]


class ArrivalDateUpdate(BaseModel):
    actual_arrival_date: date | None = None


class ValuationMethodUpdate(BaseModel):
    method: str  # lifetime_avg | fifo | moving_avg


class ValuationStartUpdate(BaseModel):
    """Data-start cutoff: ignore sales before this date (incomplete early data)."""

    start_date: date | None = None

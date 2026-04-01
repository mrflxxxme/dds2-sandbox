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
    updated_at: datetime | None = None


class DutyRuleSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    subject: str
    basis: str
    rate: Decimal
    util_collect_rub: Decimal = Decimal("0")
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

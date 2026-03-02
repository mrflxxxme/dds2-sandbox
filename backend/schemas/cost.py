"""
Cost schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class NomenclatureSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    barcode: str
    brand: Optional[str] = None
    subject: Optional[str] = None
    article_seller: Optional[str] = None
    article_wb: Optional[int] = None
    volume_l: Optional[Decimal] = None
    updated_at: Optional[datetime] = None


class DutyRuleSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    subject: str
    basis: str
    rate: Decimal
    util_collect_rub: Decimal = Decimal("0")
    note: Optional[str] = None


class CostOrderItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    order_no: str
    barcode: str
    subject: Optional[str] = None
    article_seller: Optional[str] = None
    qty: int = 1
    price_cny: Decimal = Decimal("0")
    weight_kg: Optional[Decimal] = None
    area_m2: Optional[Decimal] = None
    volume_m3: Optional[Decimal] = None
    cost_rub: Optional[Decimal] = None
    delivery_rub: Optional[Decimal] = None
    duty_rub: Optional[Decimal] = None
    vat_rub: Optional[Decimal] = None
    util_rub: Optional[Decimal] = None
    total_rub: Optional[Decimal] = None
    total_cny: Optional[Decimal] = None
    unrecognized: bool = False


class CostOrderSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    order_no: str
    invoice_no: Optional[str] = None
    ship_date: Optional[date] = None
    actual_arrival_date: Optional[date] = None
    transport_type: Optional[str] = "AUTO"
    delivery_cost_cny: Decimal = Decimal("0")
    delivery_cost_usd: Decimal = Decimal("0")
    rate_cny: Decimal = Decimal("1")
    rate_eur: Decimal = Decimal("1")
    rate_usd: Decimal = Decimal("1")
    note: Optional[str] = None
    dt_number: Optional[str] = None
    created_at: Optional[datetime] = None
    items: Optional[List[CostOrderItemSchema]] = None


class CostUploadResult(BaseModel):
    order_no: str
    items_count: int
    recognized: int
    unrecognized: int

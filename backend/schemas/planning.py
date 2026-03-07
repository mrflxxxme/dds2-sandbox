"""
Planning schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class OrderSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    order_name: Optional[str] = None
    category: Optional[str] = None
    transport_type: Optional[str] = None
    order_no: Optional[int] = None
    supplier: Optional[str] = None
    planned_ship_date: Optional[date] = None
    actual_ship_date: Optional[date] = None
    order_amount: Optional[Decimal] = None
    deposit: Optional[Decimal] = None
    logistics_cny: Optional[Decimal] = None
    customs_rub: Optional[Decimal] = None
    source_file: Optional[str] = None


class LeadTimeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    direction: str
    days: int


class PlannedPaymentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    pay_date: Optional[date] = None
    order_no: Optional[int] = None
    direction: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    fx_rate: Optional[Decimal] = None
    amount_rub: Optional[Decimal] = None
    paid_rub: Decimal = Decimal("0")
    paid_amount: Decimal = Decimal("0")  # fact in original currency
    is_paid: bool = False


class PlannedIncomeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    date: date
    amount_rub: Decimal
    source: str = "WB"


class CustomsTopupSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    topup_txn_id: str
    date: date
    amount_rub: Decimal
    purpose: Optional[str] = None
    account: Optional[str] = None
    counterparty_account: Optional[str] = None
    allocated: Optional[Decimal] = None
    remaining: Optional[Decimal] = None


class CustomsAllocSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    topup_txn_id: str
    pay_date: Optional[date] = None
    pay_amount: Optional[Decimal] = None
    order_no: Optional[int] = None
    alloc_amount: Optional[Decimal] = None
    comment: Optional[str] = None


class CashflowDailyRow(BaseModel):
    date: date
    planned_income: Decimal
    planned_expense: Decimal
    net: Decimal
    deficit_running: Decimal


class OrderSummarySchema(BaseModel):
    order_no: int
    order_name: Optional[str] = None
    total_planned: float = 0
    total_paid: float = 0
    payments: List[PlannedPaymentSchema] = []


class PaymentFactLinkSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    payment_id: int
    txn_id: str
    amount_rub: Decimal
    note: Optional[str] = None


class CustomsDTSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    dt_number: str
    dt_date: date
    amount_rub: Decimal
    order_no: Optional[int] = None
    note: Optional[str] = None


class WbPayoutSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    request_id: str
    amount_rub: Decimal
    currency: str = "RUB"
    created_at: datetime
    wb_status_raw: Optional[str] = None
    status: str = "PENDING"
    bank_comment: Optional[str] = None
    matched_txn_id: Optional[str] = None
    matched_at: Optional[datetime] = None
    imported_at: Optional[datetime] = None

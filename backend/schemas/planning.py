"""
Planning schemas.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class OrderSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    order_name: str | None = None
    category: str | None = None
    transport_type: str | None = None
    order_no: int | None = None
    supplier: str | None = None
    planned_ship_date: date | None = None
    actual_ship_date: date | None = None
    order_amount: Decimal | None = None
    deposit: Decimal | None = None
    logistics_cny: Decimal | None = None
    customs_rub: Decimal | None = None
    source_file: str | None = None


class LeadTimeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    direction: str
    days: int


class PlannedPaymentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    pay_date: date | None = None
    order_no: int | None = None
    direction: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    fx_rate: Decimal | None = None
    amount_rub: Decimal | None = None
    paid_rub: Decimal = Decimal("0")
    paid_amount: Decimal = Decimal("0")  # fact in original currency
    is_paid: bool = False


class PlannedIncomeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    date: date
    amount_rub: Decimal
    source: str = "WB"


class CustomsTopupSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    topup_txn_id: str
    date: date
    amount_rub: Decimal
    purpose: str | None = None
    account: str | None = None
    counterparty_account: str | None = None
    allocated: Decimal | None = None
    remaining: Decimal | None = None


class CustomsAllocSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    topup_txn_id: str
    pay_date: date | None = None
    pay_amount: Decimal | None = None
    order_no: int | None = None
    alloc_amount: Decimal | None = None
    comment: str | None = None


class CashflowDailyRow(BaseModel):
    date: date
    planned_income: Decimal
    planned_expense: Decimal
    net: Decimal
    deficit_running: Decimal


class OrderSummarySchema(BaseModel):
    order_no: int
    order_name: str | None = None
    total_planned: float = 0
    total_paid: float = 0
    payments: list[PlannedPaymentSchema] = []


class PaymentFactLinkSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    payment_id: int
    txn_id: str
    amount_rub: Decimal
    note: str | None = None


class CustomsDTSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    dt_number: str
    dt_date: date
    amount_rub: Decimal
    order_no: int | None = None
    note: str | None = None


class WbPayoutSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    request_id: str
    amount_rub: Decimal
    currency: str = "RUB"
    created_at: datetime
    wb_status_raw: str | None = None
    status: str = "PENDING"
    bank_comment: str | None = None
    matched_txn_id: str | None = None
    matched_at: datetime | None = None
    imported_at: datetime | None = None


class FactLinkCreate(BaseModel):
    """Input: create a fact link between payment and transaction."""

    payment_id: int
    txn_id: str
    amount_rub: Decimal
    note: str | None = None


class CustomsDTUpdate(BaseModel):
    """Input: update customs DT (assign order_no)."""

    order_no: int | None = None
    note: str | None = None


class WbReconcileRequest(BaseModel):
    """Input: manually reconcile WB payout with a transaction."""

    txn_id: str


class BrandPlanSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    brand: str
    year: int
    month: int
    plan_amount: Decimal = Decimal("0")
    created_at: datetime | None = None


class PlanFactDayRow(BaseModel):
    dt: date
    fact_day: Decimal
    plan_day: Decimal
    fact_cumulative: Decimal
    plan_cumulative: Decimal
    pct: float | None = None


class PlanFactBrandRow(BaseModel):
    brand: str
    plan_month: Decimal
    debt_prev: Decimal
    plan_adjusted: Decimal
    fact_mtd: Decimal
    pct: float | None = None
    forecast: Decimal
    days_in_month: int
    current_day: int

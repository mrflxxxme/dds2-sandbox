from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class AccountSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    account: str
    bank: str
    currency: str
    account_type: Optional[str] = None
    is_our_account: bool = True
    account_name: Optional[str] = None
    is_customs_payee: bool = False


class CounterpartyCategorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    cp_key: str
    cp_name: Optional[str] = None
    cat_lvl1: Optional[str] = None
    cat_lvl2: Optional[str] = None
    note: Optional[str] = None


class OverrideSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    txn_id: str
    cat_lvl1: Optional[str] = None
    cat_lvl2: Optional[str] = None
    order_id: Optional[str] = None
    comment: Optional[str] = None
    updated_at: Optional[datetime] = None


class TransactionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    date: datetime
    bank: str
    account: str
    currency: str
    counterparty: Optional[str] = None
    inn: Optional[str] = None
    counterparty_account: Optional[str] = None
    purpose: Optional[str] = None
    income: Decimal
    expense: Decimal
    txn_id: str
    cp_key: Optional[str] = None
    net: Optional[Decimal] = None
    is_internal: bool
    is_fx: bool
    event_type2: Optional[str] = None
    is_cashflow2: int
    cat_lvl1_2: Optional[str] = None
    cat_lvl2_2: Optional[str] = None
    status: Optional[str] = None
    purpose_tag: Optional[str] = None
    invoice_id: Optional[str] = None
    annex_id: Optional[str] = None


class TransactionFilter(BaseModel):
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    account: Optional[str] = None
    currency: Optional[str] = None
    counterparty: Optional[str] = None
    status: Optional[str] = None
    is_cashflow2: Optional[int] = None
    cat_lvl1_2: Optional[str] = None
    limit: int = 500
    offset: int = 0


class CategoryAssignment(BaseModel):
    txn_id: str
    cat_lvl1: str
    cat_lvl2: Optional[str] = None
    scope: str = "txn"   # 'txn' or 'cp'
    comment: Optional[str] = None
    cp_key: Optional[str] = None


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
    is_paid: bool = False


class PlannedIncomeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    date: date
    amount_rub: Decimal
    source: str = "WB"


class OpeningBalanceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    date_open: date
    account: str
    currency: str
    opening_balance: Decimal


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


class ImportLogSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    source_type: str
    imported_at: datetime
    rows_raw: int
    rows_inserted: int
    rows_skipped: int
    status: str
    error_msg: Optional[str] = None


class DashboardBalances(BaseModel):
    account: str
    bank: str
    currency: str
    account_name: Optional[str]
    balance: Decimal


class DdsMonthRow(BaseModel):
    cat_lvl1: Optional[str]
    cat_lvl2: Optional[str]
    total: Decimal


class CashflowDailyRow(BaseModel):
    date: date
    planned_income: Decimal
    planned_expense: Decimal
    net: Decimal
    deficit_running: Decimal

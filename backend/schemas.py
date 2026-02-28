"""
Pydantic schemas — API contracts for all DDS endpoints.
Every endpoint must use these as response_model.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


# ─── Generic ──────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    """Generic message response."""
    message: str


class StatusResponse(BaseModel):
    """Generic status response."""
    status: str


class DeleteResponse(BaseModel):
    """Response for delete operations."""
    deleted: bool
    id: Optional[int] = None


# ─── Auth ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


# ─── Accounts & References ────────────────────────────────────────────────────

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


class OpeningBalanceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    date_open: date
    account: str
    currency: str
    opening_balance: Decimal


class CategoryRefSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    direction: str
    cat_lvl1: str
    cat_lvl2: str
    sort_order: int = 0


# ─── Transactions ─────────────────────────────────────────────────────────────

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


class BulkCategoryAssignment(BaseModel):
    cp_key: str
    cat_lvl1: str
    cat_lvl2: Optional[str] = None


class CategoryAssignByIds(BaseModel):
    txn_ids: List[str]
    cat_lvl1: str
    cat_lvl2: Optional[str] = None


class UnassignedGroupRow(BaseModel):
    cp_key: Optional[str] = None
    counterparty: Optional[str] = None
    count: int
    total_income: float = 0
    total_expense: float = 0


# ─── Import ───────────────────────────────────────────────────────────────────

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
    file_url: Optional[str] = None


class ImportResult(BaseModel):
    """Response after file upload."""
    import_id: int
    filename: str
    source_type: str
    rows_raw: int
    rows_inserted: int
    rows_skipped: int
    status: str
    error_msg: Optional[str] = None


# ─── Reports ──────────────────────────────────────────────────────────────────

class BalanceRow(BaseModel):
    account: str
    bank: str
    currency: str
    account_name: Optional[str] = None
    balance: float


class DdsMonthRow(BaseModel):
    cat_lvl1: Optional[str] = None
    cat_lvl2: Optional[str] = None
    income: float = 0
    expense: float = 0
    net: float = 0


class FxControlRow(BaseModel):
    date: datetime
    account: str
    currency: str
    counterparty: Optional[str] = None
    purpose: Optional[str] = None
    income: Decimal
    expense: Decimal
    net: Optional[Decimal] = None
    txn_id: str


class BalanceDailyRow(BaseModel):
    date: str
    daily_net: float
    balance: float


class IncomeDailyRow(BaseModel):
    date: str
    bank: str
    income: float


class IncomeByCategoryRow(BaseModel):
    date: str
    category: str
    income: float


# keep backward compat alias
DashboardBalances = BalanceRow


# ─── Planning ─────────────────────────────────────────────────────────────────

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


# ─── WB Payouts ───────────────────────────────────────────────────────────────

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


# ─── Cost / Себестоимость ─────────────────────────────────────────────────────

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

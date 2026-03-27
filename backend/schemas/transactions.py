"""
Transaction schemas.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class TransactionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    date: datetime
    bank: str
    account: str
    currency: str
    counterparty: str | None = None
    inn: str | None = None
    counterparty_account: str | None = None
    purpose: str | None = None
    income: Decimal
    expense: Decimal
    txn_id: str
    cp_key: str | None = None
    net: Decimal | None = None
    is_internal: bool
    is_fx: bool
    event_type2: str | None = None
    is_cashflow2: int
    cat_lvl1_2: str | None = None
    cat_lvl2_2: str | None = None
    status: str | None = None
    purpose_tag: str | None = None
    invoice_id: str | None = None
    annex_id: str | None = None


class TransactionFilter(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    account: str | None = None
    currency: str | None = None
    counterparty: str | None = None
    status: str | None = None
    is_cashflow2: int | None = None
    cat_lvl1_2: str | None = None
    limit: int = 500
    offset: int = 0


class CategoryAssignment(BaseModel):
    txn_id: str
    cat_lvl1: str
    cat_lvl2: str | None = None
    scope: str = "txn"  # 'txn' or 'cp'
    comment: str | None = None
    cp_key: str | None = None


class BulkCategoryAssignment(BaseModel):
    cp_key: str
    cat_lvl1: str
    cat_lvl2: str | None = None


class CategoryAssignByIds(BaseModel):
    txn_ids: list[str]
    cat_lvl1: str
    cat_lvl2: str | None = None


class UnassignedGroupRow(BaseModel):
    cp_key: str | None = None
    counterparty: str | None = None
    count: int
    total_income: float = 0
    total_expense: float = 0


class AutoRuleCreate(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=200)
    cat_lvl1: str = Field(..., min_length=1, max_length=200)
    cat_lvl2: str | None = None
    direction: str = "expense"
    priority: int = Field(default=0, ge=0, le=1000)

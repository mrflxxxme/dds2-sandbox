"""
Transaction schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


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

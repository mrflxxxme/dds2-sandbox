"""
Reference schemas: Account, CounterpartyCategory, Override, OpeningBalance, CategoryRef.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
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
    direction: str = "expense"
    cat_lvl1: str
    cat_lvl2: Optional[str] = None


class CategoryRefCreate(BaseModel):
    """Input: add a category reference."""
    cat_lvl1: str
    cat_lvl2: Optional[str] = None
    direction: str = "expense"
    sort_order: int = 0

"""
Report schemas.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel


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


# backward compat alias
DashboardBalances = BalanceRow

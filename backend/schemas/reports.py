"""
Report schemas.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class BalanceRow(BaseModel):
    account: str
    bank: str
    currency: str
    account_name: str | None = None
    balance: float


class DdsMonthRow(BaseModel):
    cat_lvl1: str | None = None
    cat_lvl2: str | None = None
    income: float = 0
    expense: float = 0
    net: float = 0


class FxControlRow(BaseModel):
    date: datetime
    account: str
    currency: str
    counterparty: str | None = None
    purpose: str | None = None
    income: Decimal
    expense: Decimal
    net: Decimal | None = None
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


# ─── Cost-DNA report ─────────────────────────────────────────────────────────


class CostDnaCategory(BaseModel):
    """Per-category (subject) breakdown for the Cost-DNA report."""

    category: str  # subject_name from wb_finance_rows
    revenue: float
    revenue_share_pct: float  # % of total project revenue

    # Cost components as % of revenue (None if no cost_orders for category)
    cost_factory_pct: float | None = None
    cost_duty_pct: float | None = None
    cost_delivery_pct: float | None = None
    cost_vat_pct: float | None = None
    cost_total_pct: float | None = None
    has_cost_data: bool = False

    # Marketplace fees as % of revenue
    mp_commission_pct: float = 0
    mp_logistics_pct: float = 0
    mp_storage_pct: float = 0
    mp_advertising_pct: float = 0
    mp_other_pct: float = 0
    mp_total_pct: float = 0

    # Tax as % of revenue
    tax_pct: float = 0

    # Margin (None if cost data missing — cannot compute)
    margin_pct: float | None = None
    margin_pct_prev: float | None = None
    margin_trend: str | None = None  # "up" | "down" | None


class CostDnaTotals(BaseModel):
    """Totals row for the Cost-DNA report (sum across all categories)."""

    revenue: float
    cost_factory_pct: float = 0
    cost_duty_pct: float = 0
    cost_delivery_pct: float = 0
    cost_vat_pct: float = 0
    cost_total_pct: float = 0
    mp_commission_pct: float = 0
    mp_logistics_pct: float = 0
    mp_storage_pct: float = 0
    mp_advertising_pct: float = 0
    mp_other_pct: float = 0
    mp_total_pct: float = 0
    tax_pct: float = 0
    margin_pct: float = 0
    margin_pct_prev: float | None = None
    margin_trend: str | None = None


class CostDnaResponse(BaseModel):
    """Cost-DNA report response."""

    period_days: int
    date_from: str  # YYYY-MM-DD
    date_to: str
    prev_date_from: str
    prev_date_to: str
    categories: list[CostDnaCategory]
    totals: CostDnaTotals
    has_tax_settings: bool  # False → UI tooltip "налоги не настроены"


# ─── Counterparty Turnovers report ───────────────────────────────────────────


class CounterpartyTurnoversMonth(BaseModel):
    """Monthly bucket for a single counterparty row."""

    month: str  # "2026-01"
    in_sum: Decimal = Field(alias="in", default=Decimal("0"))
    out_sum: Decimal = Field(alias="out", default=Decimal("0"))
    net: Decimal = Field(default=Decimal("0"))

    model_config = ConfigDict(populate_by_name=True)


class CounterpartyTurnoversRow(BaseModel):
    """One row in the counterparty-turnovers pivot table."""

    counterparty_id: int
    inn: str | None = None
    name: str
    primary_type: str
    months: list[CounterpartyTurnoversMonth] = Field(default_factory=list)
    total_in: Decimal = Decimal("0")
    total_out: Decimal = Decimal("0")
    total_net: Decimal = Decimal("0")
    tx_count: int = 0


class CounterpartyTurnoversRequest(BaseModel):
    """Query params for GET /reports/counterparty-turnovers."""

    project_id: int
    date_from: date
    date_to: date
    type: str | None = None  # CounterpartyType filter
    currency: str = "RUB"
    min_turnover: Decimal = Decimal("0")


class CounterpartyTurnoversResponse(BaseModel):
    """Full counterparty-turnovers report response."""

    rows: list[CounterpartyTurnoversRow]
    period_from: date
    period_to: date
    currency: str

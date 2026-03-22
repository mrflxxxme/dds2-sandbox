"""Schemas for the Capital analysis report."""

from pydantic import BaseModel


class PriceRecommendation(BaseModel):
    type: str
    label: str
    current_roi: float
    roi_at_minus_10: float | None = None
    roi_at_minus_20: float | None = None


class CapitalSummary(BaseModel):
    total_capital: float
    liquid_capital: float
    liquid_pct: float
    transition_capital: float
    transition_pct: float
    illiquid_capital: float
    illiquid_pct: float
    roi_monthly: float
    roi_trend: float


class CapitalTrendDay(BaseModel):
    date: str
    liquid: float
    transition: float
    illiquid: float
    total: float
    roi_monthly: float


class CapitalGroupRow(BaseModel):
    group_key: str
    group_type: str
    nm_id: int | None = None
    vendor_code: str | None = None
    capital: float
    liquid_pct: float
    illiquid_pct: float
    frozen_amount: float
    roi_monthly: float
    turnover_days: float
    sales_per_day: float
    margin: float
    drr: float
    recommendation: PriceRecommendation
    children_count: int


class CapitalResponse(BaseModel):
    summary: CapitalSummary
    trend: list[CapitalTrendDay]
    groups: list[CapitalGroupRow]
    total_products: int
    period_days: int
    elasticity: float

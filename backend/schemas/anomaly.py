# ruff: noqa: RUF001
"""Schemas for the Anomalies report."""

from pydantic import BaseModel


class AnomalyMetrics(BaseModel):
    """Key metrics that prove the anomaly."""

    margin: float | None = None
    was_margin: float | None = None  # previous period margin
    drr: float | None = None
    adv_sum: float | None = None
    turnover_days: float | None = None
    stocks_wb: int | None = None
    days_left: float | None = None
    restock_qty: int | None = None
    frozen_value: float | None = None
    buyout_pct: float | None = None
    avg_price: float | None = None
    orders_sum: float | None = None
    appetite_weekly: float | None = None  # weekly ad spend


class AnomalyItem(BaseModel):
    nm_id: int
    vendor_code: str
    brand: str
    subject: str
    severity: str  # "critical" or "warning"
    anomaly_type: str  # hit_loss, hit_oos, toxic_ads, dead_stock, low_buyout
    title: str
    description: str
    loss_amount: float | None = None
    metrics: AnomalyMetrics
    action: str


class AnomalySummary(BaseModel):
    total_loss: float = 0
    oos_risk_count: int = 0
    frozen_capital: float = 0
    healthy_count: int = 0


class AnomaliesResponse(BaseModel):
    summary: AnomalySummary
    anomalies: list[AnomalyItem]
    total_products: int
    period_days: int

"""
Integration models: IntegrationKey, SyncLog, WbFunnelDaily, WbCostOverride.
"""

from datetime import datetime, date, timezone

from backend.utils.time import utcnow
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    String, Integer, Boolean, DateTime, Date, Numeric, Text,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class IntegrationKey(Base):
    """Encrypted API keys for external services (WB, OZON, etc.)."""
    __tablename__ = "integration_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    service: Mapped[str] = mapped_column(String(50), nullable=False)  # "wb", "ozon"
    label: Mapped[Optional[str]] = mapped_column(String(200))  # user-friendly name
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet-encrypted
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("project_id", "service", "label", name="uq_integration_project_service_label"),
    )


class SyncLog(Base):
    """Log of integration sync operations."""
    __tablename__ = "sync_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    integration_id: Mapped[int] = mapped_column(Integer, ForeignKey("integration_keys.id"))
    service: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "sales", "payouts", "orders"
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")  # RUNNING, OK, ERROR
    rows_fetched: Mapped[int] = mapped_column(Integer, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[Optional[str]] = mapped_column(Text)


class WbFunnelDaily(Base):
    """Daily WB sales-funnel + advertising stats per nmId."""
    __tablename__ = "wb_funnel_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    date: Mapped[date] = mapped_column(Date, nullable=False)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    vendor_code: Mapped[Optional[str]] = mapped_column(String(100))
    subject: Mapped[Optional[str]] = mapped_column(String(200))
    brand: Mapped[Optional[str]] = mapped_column(String(200))

    # Funnel
    open_card: Mapped[int] = mapped_column(Integer, default=0)
    add_to_cart: Mapped[int] = mapped_column(Integer, default=0)
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    orders_sum_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    buyout_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    cart_to_order_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    add_to_cart_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    avg_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    stocks_wb: Mapped[int] = mapped_column(Integer, default=0)
    stocks_mp: Mapped[int] = mapped_column(Integer, default=0)

    # Advertising
    adv_views: Mapped[int] = mapped_column(Integer, default=0)
    adv_clicks: Mapped[int] = mapped_column(Integer, default=0)
    adv_sum: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    # Cost price (filled from last order or override)
    cost_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))

    __table_args__ = (
        UniqueConstraint("project_id", "date", "nm_id", name="uq_funnel_daily"),
        Index("ix_funnel_project_date", "project_id", "date"),
    )


class WbCostOverride(Base):
    """Manual cost price per nmId (used if no order data available)."""
    __tablename__ = "wb_cost_override"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "nm_id", name="uq_cost_override_nm"),
    )

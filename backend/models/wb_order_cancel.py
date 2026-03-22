"""
Model: WbOrderCancelDaily — daily cancel stats per article from WB Orders API.

Aggregated from /api/v1/supplier/orders: counts total and cancelled orders
per (project_id, nm_id, dt) to compute accurate buyout_pct in BDR rates.
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.time import utcnow


class WbOrderCancelDaily(Base):
    """Daily order cancel stats per article.

    Stores total_orders and cancelled_orders per (nm_id, dt) to adjust
    buyout_pct = sale_qty / (sale_qty + ret_qty + cancel_qty).
    """

    __tablename__ = "wb_order_cancel_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dt: Mapped[date] = mapped_column(Date, nullable=False)

    total_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "nm_id", "dt", name="uq_wb_order_cancel_daily"),
        Index("ix_wb_order_cancel_daily_project_dt", "project_id", "dt"),
    )

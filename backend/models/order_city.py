"""Order city mapping — links WB order srid to delivery city from Excel upload."""
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class OrderCityMap(Base):
    __tablename__ = "order_city_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    srid: Mapped[str] = mapped_column(String(100), nullable=False)
    city: Mapped[str] = mapped_column(String(200), nullable=False)
    okrug: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.utcnow()
    )

    __table_args__ = (
        UniqueConstraint("project_id", "srid", name="uq_order_city_project_srid"),
        Index("ix_order_city_project", "project_id"),
    )
